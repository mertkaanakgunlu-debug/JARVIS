"""Deterministic parser for Turkish bank notification mails (Burgan first).

This is the PRIMARY extraction path. An LLM is a fallback for mails this cannot
resolve, not the default -- see jarvis/finance_extractor.py.

Why deterministic-first, when an LLM "reads anything":

  * A bank notification is a template. Templates are the one thing regexes are
    genuinely good at, and the result is reproducible run to run -- which the
    MVP gate needs in order to measure anything at all.
  * It is free and offline. The previous design routed every mail through
    Gemini, which under this project's default CLOUD_POLICY=off meant every
    mail returned None and `finance('sync')` was a permanent no-op.
  * It cannot hallucinate an amount. That property is the whole point.

The governing rule, from the owner's review: **never guess a financial value.**
The old extractor's schema said "use today's date if not found" and its prompt
said "make your best guess" -- a mail whose date could not be read became a
transaction dated today, and a mail with no readable amount became a plausible
number. Both are silent ledger corruption. Here, insufficient evidence produces
a REJECTION carrying a reason, and rejections are reported, not swallowed.

Turkish number format is the subtle part: `1.234,56` means one thousand two
hundred thirty-four and 56/100. Reading that with en-US assumptions yields
1.234 -- three orders of magnitude lost, silently, on a value someone budgets
against.
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import datetime
from email.utils import parsedate_to_datetime

# ── rejection reasons (a closed vocabulary, so callers can count them) ─────────

REJECT_NOT_A_TRANSACTION = "not_a_transaction"
REJECT_NO_AMOUNT = "no_amount"
REJECT_NO_DATE = "no_date"
REJECT_NO_DIRECTION = "no_direction"

REJECT_REASONS = frozenset({
    REJECT_NOT_A_TRANSACTION,
    REJECT_NO_AMOUNT,
    REJECT_NO_DATE,
    REJECT_NO_DIRECTION,
})


@dataclass(frozen=True)
class ParsedTransaction:
    date: str          # ISO 8601, always from real evidence
    amount: float      # signed: negative = money out, positive = money in
    currency: str
    merchant: str
    category: str
    description: str
    direction: str     # "expense" | "income"


@dataclass(frozen=True)
class ParseRejection:
    reason: str
    detail: str = ""


# ── currency ──────────────────────────────────────────────────────────────────
# Ordered longest-first so "TRY" is not matched as "TL"-adjacent noise, and the
# symbol forms come last.
_CURRENCY_TOKENS = [
    ("TRY", "TRY"), ("TL", "TRY"), ("₺", "TRY"),
    ("USD", "USD"), ("DOLAR", "USD"), ("$", "USD"),
    ("EUR", "EUR"), ("EURO", "EUR"), ("€", "EUR"),
    ("GBP", "GBP"), ("STERLIN", "GBP"), ("£", "GBP"),
]

# A Turkish-formatted amount immediately followed by its currency. Requiring the
# currency token adjacent to the number is what keeps a card number, a date
# fragment or an interest rate ("%2,89") from being read as a transaction value.
_AMOUNT_RE = re.compile(
    r"(?P<num>\d{1,3}(?:\.\d{3})*(?:,\d{1,2})?|\d+(?:,\d{1,2})?)\s*"
    r"(?P<cur>TRY|TL|₺|USD|DOLAR|\$|EUR|EURO|€|GBP|STERLIN|£)\b",
    re.IGNORECASE,
)

_DATE_TIME_RE = re.compile(
    r"(?P<d>\d{1,2})[./](?P<m>\d{1,2})[./](?P<y>\d{4})"
    r"(?:\s+(?P<hh>\d{1,2}):(?P<mm>\d{2})(?::(?P<ss>\d{2}))?)?"
)

# ── direction ─────────────────────────────────────────────────────────────────
# Money IN. "iade" (refund) is here deliberately: a refund of a purchase is a
# credit, and treating it as another expense double-counts the original spend.
_INCOME_PATTERNS = [
    r"hesab[iı]n[iı]za",           # "to your account"
    r"alacak",                      # credit
    r"iade",                        # refund
    r"yatan|yat[iı]r[iı]lm[iı]ş",   # deposited
    r"gelen\s+havale|gelen\s+eft",
    r"maa[şs]\s*[öo]demesi",
]
# Money OUT.
_EXPENSE_PATTERNS = [
    r"hesab[iı]n[iı]zdan",          # "from your account"
    r"bor[çc]",                      # debit
    r"al[iı][şs]veri[şs]",           # purchase
    r"[öo]deme\s+yap[iı]lm[iı][şs]",
    r"nakit\s+[çc]ekim",             # cash withdrawal
    r"[çc]ek(im|ilmi[şs])",
    r"harcama",
    r"giden\s+havale|giden\s+eft",
]

# A mail must look like a transaction at all before we spend an LLM call on it.
# Marketing mail is the common false positive -- it cheerfully contains amounts
# ("100.000 TL'ye varan kredi") and must never become a ledger row.
_TRANSACTION_SHAPE = re.compile(
    r"i[şs]lem|al[iı][şs]veri[şs]|[öo]deme|havale|\beft\b|nakit\s+[çc]ekim|"
    r"alacak|bor[çc]|iade|harcama|maa[şs]",
    re.IGNORECASE,
)
_MARKETING_SHAPE = re.compile(
    r"kampanya|f[iı]rsat|teklif|ba[şs]vur|kredi\s+f[iı]rsat|"
    r"promosyon|indirim\s+f[iı]rsat|hemen\s+ba[şs]la|"
    r"ko[şs]ullar[iı]\s+i[çc]in|web\s+sitemizi",
    re.IGNORECASE,
)

# ── merchant ──────────────────────────────────────────────────────────────────
_MERCHANT_PATTERNS = [
    # "... MIGROS TICARET AS isyerinde ..." — merchant precedes the keyword
    re.compile(r"([A-ZÇĞİÖŞÜ0-9][\w\s.&'-]{2,40}?)\s+i[şs]yerinde", re.UNICODE),
    # "... AHMET YILMAZ adina ..." — transfer counterparty
    re.compile(r"([A-ZÇĞİÖŞÜ][\w\s.&'-]{2,40}?)\s+ad[iı]na", re.UNICODE),
    # "... TEKNOSA isyerinden ..." — refund source
    re.compile(r"([A-ZÇĞİÖŞÜ0-9][\w\s.&'-]{2,40}?)\s+i[şs]yerinden", re.UNICODE),
]

# Merchant → category. The second half of each list comes from the owner's REAL
# statement (2026-07-30): before those entries 71 of 90 transactions landed in
# "other", which makes the workbook's Kategori sheet worthless. Matching is a
# case-folded substring test against "<merchant> <full description>", so a partial
# brand name is enough and multi-word POS descriptors still hit.
_CATEGORY_RULES: list[tuple[str, tuple[str, ...]]] = [
    ("food", ("MIGROS", "CARREFOUR", "A101", "BIM", "ŞOK", "SOK", "GETIR",
              "YEMEKSEPETI", "MARKET", "RESTORAN", "KAFE", "STARBUCKS",
              # real:
              "ESPRESSOLAB", "KAHVE", "BOREK", "BÖREK", "BOREKCISI", "SWALLET",
              "MOKA UNITED", "KANTIN", "DUKKANN", "MAHALLE", "GIDA", "LOKANTA",
              "PASTANE", "FIRIN", "COFFEE", "CAFE", "BUFE", "BÜFE", "YEMEK")),
    ("transport", ("SHELL", "OPET", "PETROL", "BP", "TOTAL", "BENZIN", "UBER",
                   "TAKSI", "IETT", "METRO", "THY", "PEGASUS", "BILET",
                   "ISTANBULKART", "MARTI", "BITAKSI", "SCOOTER")),
    ("shopping", ("TEKNOSA", "AMAZON", "TRENDYOL", "HEPSIBURADA", "MEDIA MARKT",
                  "VATAN", "IKEA", "DECATHLON", "ZARA", "LC WAIKIKI",
                  "TABACCO", "TEKEL", "KUMAS", "KUMAŞ", "MAGAZA", "MAĞAZA")),
    ("bills", ("FATURA", "ELEKTRIK", "DOGALGAZ", "SU ", "TURKCELL", "VODAFONE",
               "TURK TELEKOM", "INTERNET", "AIDAT",
               # digital subscriptions read as recurring bills, not entertainment
               "TALIMATLIFATURA", "CLAUDE", "ANTHROPIC", "ANTH ", "OPENAI",
               "CHATGPT", "GITHUB", "NOTION", "ADOBE", "MICROSOFT", "ICLOUD",
               "GOOGLE STORAGE", "APPLE.COM")),
    ("entertainment", ("SPOTIFY", "NETFLIX", "STEAM", "SINEMA", "YOUTUBE",
                       "DISNEY", "CINEMAXIMUM", "BLUTV", "EXXEN", "TWITCH")),
    ("health", ("ECZANE", "HASTANE", "DOKTOR", "MEDICAL", "DENT", "OPTIK",
                "SAGLIK", "SAĞLIK", "LABORATUVAR")),
    ("education", ("UNIVERSITE", "ÜNIVERSITE", "KURS", "UDEMY", "OKUL", "KITAP",
                   "ITU ", "İTÜ", "STRATEJI GELISTIRME", "YURT", "AKADEMI")),
    ("atm", ("ATM", "NAKIT")),
    ("salary", ("MAAS", "MAAŞ", "UCRET", "ÜCRET")),
]


def _to_float(num: str) -> float | None:
    """Parse a Turkish-formatted number. '.' groups, ',' is the decimal mark."""
    cleaned = num.replace(".", "").replace(",", ".")
    try:
        return float(cleaned)
    except ValueError:
        return None


def _find_amount(text: str) -> tuple[float, str] | None:
    """Largest amount+currency pair in the text.

    Largest, not first: Burgan's own templates sometimes mention a remaining
    balance or an instalment alongside the transaction, and the transaction value
    is the dominant figure. Ties are resolved by first occurrence.
    """
    best: tuple[float, str] | None = None
    for m in _AMOUNT_RE.finditer(text):
        value = _to_float(m.group("num"))
        if value is None or value <= 0:
            continue
        token = m.group("cur").upper()
        currency = next((c for t, c in _CURRENCY_TOKENS if t == token), None)
        if currency is None:
            continue
        if best is None or value > best[0]:
            best = (value, currency)
    return best


def _find_datetime(body: str, subject: str, date_header: str) -> str | None:
    """An ISO timestamp from real evidence, or None. NEVER now().

    Preference order: a date written in the mail body (what the bank says the
    transaction happened), then the subject, then the RFC-2822 `Date` header
    (when the bank sent the notification). The header is a genuine fallback, not
    a guess -- but if there is no header either, this returns None and the caller
    must reject the mail.
    """
    for source in (body, subject):
        m = _DATE_TIME_RE.search(source or "")
        if not m:
            continue
        try:
            return datetime(
                int(m.group("y")), int(m.group("m")), int(m.group("d")),
                int(m.group("hh") or 0), int(m.group("mm") or 0),
                int(m.group("ss") or 0),
            ).isoformat()
        except ValueError:
            continue  # e.g. 31.02.2026 -- a real date-shaped string that isn't one
    if date_header:
        try:
            parsed = parsedate_to_datetime(date_header)
            return parsed.replace(tzinfo=None).isoformat()
        except (TypeError, ValueError):
            return None
    return None


def _find_direction(text: str) -> str | None:
    lowered = text.lower()
    income = any(re.search(p, lowered) for p in _INCOME_PATTERNS)
    expense = any(re.search(p, lowered) for p in _EXPENSE_PATTERNS)
    if income and not expense:
        return "income"
    if expense and not income:
        return "expense"
    if income and expense:
        # Both vocabularies present: a refund names the original purchase
        # ("...alisverisin iadesi hesabiniza yapilmistir"), so the credit wins.
        if re.search(r"iade|alacak", lowered):
            return "income"
        return "expense"
    return None


def _find_merchant(text: str) -> str:
    for pattern in _MERCHANT_PATTERNS:
        m = pattern.search(text)
        if m:
            merchant = " ".join(m.group(1).split())
            # Strip a leading date/time that the greedy group may have absorbed.
            merchant = re.sub(r"^[\d\s.:/]+", "", merchant).strip()
            if merchant:
                return merchant[:60]
    if re.search(r"atm|nakit\s+[çc]ekim", text, re.IGNORECASE):
        return "ATM"
    if re.search(r"maa[şs]", text, re.IGNORECASE):
        return "MAAS ODEMESI"
    return ""


def categorize(merchant: str, text: str, direction: str) -> str:
    haystack = f"{merchant} {text}".upper()
    for category, needles in _CATEGORY_RULES:
        if any(n in haystack for n in needles):
            # "salary"/"atm" are direction-sensitive: an ATM *deposit* is not a
            # withdrawal, and a payment to a school is not income.
            if category == "salary" and direction != "income":
                continue
            return category
    if direction == "income":
        return "transfer"
    if re.search(r"havale|\beft\b", text, re.IGNORECASE):
        return "transfer"
    return "other"


def looks_like_transaction(subject: str, body: str) -> bool:
    """Cheap deterministic pre-check, used to decide whether an LLM call is even
    warranted. Marketing mail is rejected here rather than being handed to a
    model that will happily find the campaign's headline figure."""
    text = f"{subject}\n{body}"
    if not _TRANSACTION_SHAPE.search(text):
        return False
    if _MARKETING_SHAPE.search(text) and _find_amount(text) is None:
        return False
    # Marketing with an amount in it: only a transaction if it ALSO carries
    # transaction-specific wording beyond the generic shape words.
    if _MARKETING_SHAPE.search(text):
        return bool(
            re.search(
                r"ger[çc]ekle[şs]tiril|yap[iı]lm[iı][şs]t[iı]r|kaydi\s+yap[iı]l|"
                r"kart[iı]n[iı]z\s+ile|kart[iı]n[iı]zla",
                text, re.IGNORECASE,
            )
        )
    return True


def parse_transaction(
    subject: str, body: str, date_header: str = "",
) -> ParsedTransaction | ParseRejection:
    """Parse one bank notification. Returns a transaction OR a reasoned rejection.

    Never raises, never guesses. Every returned field traces to text that was
    actually present in the mail.
    """
    text = f"{subject}\n{body}"

    if not looks_like_transaction(subject, body):
        return ParseRejection(REJECT_NOT_A_TRANSACTION, "no transaction wording")

    found = _find_amount(text)
    if found is None:
        return ParseRejection(REJECT_NO_AMOUNT, "no amount+currency pair in the mail")
    magnitude, currency = found

    when = _find_datetime(body, subject, date_header)
    if when is None:
        return ParseRejection(
            REJECT_NO_DATE, "no date in the body/subject and no usable Date header"
        )

    direction = _find_direction(text)
    if direction is None:
        return ParseRejection(
            REJECT_NO_DIRECTION, "could not tell money-in from money-out"
        )

    merchant = _find_merchant(text)
    amount = magnitude if direction == "income" else -magnitude
    return ParsedTransaction(
        date=when,
        amount=round(amount, 2),
        currency=currency,
        merchant=merchant,
        category=categorize(merchant, text, direction),
        description=(subject or merchant or "")[:120],
        direction=direction,
    )
