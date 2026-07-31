"""Deterministic natural-language resolution — the work the model should NOT do.

Post-MVP Faz 2. The plan's thesis in one line: rather than make qwen3:8b
smarter, shrink what it has to get right. A model asked to turn "yarın öğlen
saat 3" into a timestamp has to know today's date, the user's timezone, and
that Turkish "öğlen 3" means 15:00 — three chances to be wrong, none of them
reproducible. Code does that arithmetic identically every time.

So the division of labour is: the model produces the EXPRESSION it heard
("yarın", "öğlen saat 3", "Baranla"); this package produces the VALUE, and
says how sure it is.

Confidence is the second half, and it is what keeps the fix from becoming a
new class of silent error. Every resolver returns a confidence score, and the
scores are read by jarvis/policy_guard.py to decide between acting, asking,
and leaving the input alone. See temporal.py and entities.py.
"""
