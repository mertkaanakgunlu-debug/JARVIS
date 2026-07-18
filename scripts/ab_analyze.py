"""A/B analyzer: compare N-run driver result sets from ab_run_config.ps1.

Reads <root>/results/ab_<cfg>_r<n>.jsonl (chat entries + oracle verdicts as
written by manual_test_driver.py) for each config label and produces:
  - oracle pass matrix per scenario (x/N per config)
  - latency medians per scenario: driver-measured end-to-end elapsed_s, plus
    /status's last_latency_ms (the LLM call that authored the visible answer
    — compose on tool turns — warm calls only)
  - failure reasons and transport errors, deduplicated
Writes <root>/results/ab_report.md and prints it.

Usage: python scripts/ab_analyze.py [root] [cfg1 cfg2 ...] [--runs N]
Defaults: root=C:\\Temp\\jarvis-ab, configs=off on, runs=5
"""
import json
import statistics
import sys
from collections import defaultdict
from pathlib import Path

# Order = driver TESTS order; scored = has an EXPECTED entry in the driver.
ORDER = ["A1", "A2", "A3", "B4", "B5a", "B5b", "B6", "C7", "C8", "C9",
         "D10", "D11", "D12", "D13b", "E14", "E15", "F16", "G17a", "G17b"]
SCORED = {"A3", "B4", "B5a", "B5b", "B6", "C7", "C9", "D10", "D11", "D12",
          "D13b", "F16", "G17b"}


def parse_args(argv):
    root, configs, runs = Path(r"C:\Temp\jarvis-ab"), [], 5
    it = iter(argv)
    for a in it:
        if a == "--runs":
            runs = int(next(it))
        elif Path(a).is_dir() or "\\" in a or "/" in a:
            root = Path(a)
        else:
            configs.append(a)
    return root, (configs or ["off", "on"]), runs


def load_runs(res, cfg, runs):
    out = []
    for r in range(1, runs + 1):
        f = res / f"ab_{cfg}_r{r}.jsonl"
        rows = []
        if f.exists():
            for line in f.read_text(encoding="utf-8").splitlines():
                try:
                    rows.append(json.loads(line))
                except Exception:
                    continue
        out.append(rows)
    return out


def collect(res, cfg, runs):
    oracle = defaultdict(list)
    elapsed = defaultdict(list)
    llm_lat = defaultdict(list)
    reasons = defaultdict(set)
    errors = defaultdict(set)
    for run_rows in load_runs(res, cfg, runs):
        seen = {}
        for row in run_rows:
            tid = row.get("test_id")
            if tid is None:
                continue
            if "oracle" in row:
                seen[tid] = bool(row["oracle"].get("passed"))
                for reason in row["oracle"].get("reasons") or []:
                    reasons[tid].add(reason)
            elif "message" in row:
                e = row.get("confirm_elapsed_s") or row.get("elapsed_s")
                if isinstance(e, (int, float)):
                    elapsed[tid].append(float(e))
                st = row.get("status") or {}
                lat = st.get("last_latency_ms")
                if isinstance(lat, (int, float)) and not st.get("last_call_cold_start"):
                    llm_lat[tid].append(float(lat))
                if row.get("error"):
                    errors[tid].add(f"{row['error']}: {str(row.get('body', ''))[:120]}")
        for tid in ORDER:
            if tid in SCORED:
                oracle[tid].append(seen.get(tid))
    return oracle, elapsed, llm_lat, reasons, errors


def med(xs):
    return statistics.median(xs) if xs else None


def fmt(x, nd=1, suffix=""):
    return "-" if x is None else f"{x:.{nd}f}{suffix}"


def main():
    root, configs, runs = parse_args(sys.argv[1:])
    res = root / "results"
    data = {cfg: collect(res, cfg, runs) for cfg in configs}
    lines = [f"# A/B report: {' vs '.join(configs)} ({runs} runs each)", ""]

    lines += ["## Oracle (scored scenarios, PASS/x)", "",
              "| Senaryo | " + " | ".join(c.upper() for c in configs) + " |",
              "|---|" + "---|" * len(configs)]
    tot = {c: [0, 0] for c in configs}
    for tid in ORDER:
        if tid not in SCORED:
            continue
        cells = []
        for cfg in configs:
            vs = [v for v in data[cfg][0].get(tid, []) if v is not None]
            p = sum(1 for v in vs if v)
            tot[cfg][0] += p
            tot[cfg][1] += len(vs)
            cells.append(f"{p}/{len(vs)}")
        lines.append(f"| {tid} | " + " | ".join(cells) + " |")
    lines.append("| **TOPLAM** | " + " | ".join(
        f"**{tot[c][0]}/{tot[c][1]}**" for c in configs) + " |")

    lines += ["", "## Latency medyanlari (e2e = driver elapsed_s; llm = /status last_latency_ms, warm, gorunur cevabi yazan cagri)", "",
              "| Senaryo | " + " | ".join(f"{c.upper()} e2e | {c.upper()} llm" for c in configs) + " |",
              "|---|" + "---|" * (2 * len(configs))]
    for tid in ORDER:
        cells = []
        have = False
        for cfg in configs:
            e = med(data[cfg][1].get(tid, []))
            lat = med(data[cfg][2].get(tid, []))
            have = have or e is not None
            cells.append(f"{fmt(e, 1, 's')} | {fmt(lat, 0, 'ms')}")
        if have:
            lines.append(f"| {tid} | " + " | ".join(cells) + " |")
    for cfg in configs:
        walls = []
        for run_rows in load_runs(res, cfg, runs):
            es = [r.get("confirm_elapsed_s") or r.get("elapsed_s")
                  for r in run_rows if "message" in r]
            es = [e for e in es if isinstance(e, (int, float))]
            if es:
                walls.append(sum(es))
        lines.append("")
        lines.append(f"- {cfg.upper()} toplam chat suresi/run (medyan): {fmt(med(walls), 0, 's')}  (runs={len(walls)})")

    lines += ["", "## FAIL nedenleri (dedup)", ""]
    any_fail = False
    for cfg in configs:
        oracle, _, _, reasons, errors = data[cfg]
        for tid in ORDER:
            fails = sum(1 for v in oracle.get(tid, []) if v is False)
            if fails and reasons.get(tid):
                any_fail = True
                for reason in sorted(reasons[tid]):
                    lines.append(f"- **{cfg.upper()} {tid}** ({fails}x FAIL): {reason}")
        for tid, errs in errors.items():
            for err in sorted(errs):
                any_fail = True
                lines.append(f"- **{cfg.upper()} {tid}** transport: {err}")
    if not any_fail:
        lines.append("- yok")

    report = "\n".join(lines) + "\n"
    (res / "ab_report.md").write_text(report, encoding="utf-8")
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    print(report)


if __name__ == "__main__":
    main()
