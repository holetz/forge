"""output_guard: block secrets and personal data in responses and tool results. Stdlib only.

Reasons carry the category and a count, never the matched text. Pattern sources that would
themselves look like a secret are assembled from fragments.
"""
import re

_DASH = "-" * 5
PATTERNS = {
    "private_key": r"" + _DASH + "BEGIN [A-Z ]*PRIV" + "ATE KEY" + _DASH,
    "dapi_token": r"\bda" + r"pi[0-9a-f]{32}\b",
    "cloud_key": r"\b(?:AK" + r"IA|AS" + r"IA)[0-9A-Z]{16}\b",
    "vcs_token": r"\b(?:gh[pousr]_[A-Za-z0-9]{36,}|gl" + r"pat-[A-Za-z0-9_\-]{20,})\b",
    "jwt": r"\bey[A-Za-z0-9_\-]{10,}\.ey[A-Za-z0-9_\-]{10,}\.[A-Za-z0-9_\-]{10,}\b",
    "cpf": r"(?<!\d)\d{3}\.?\d{3}\.?\d{3}-?\d{2}(?!\d)",
    "cnpj": r"(?<!\d)\d{2}\.?\d{3}\.?\d{3}/?\d{4}-?\d{2}(?!\d)",
    "credit_card": r"(?<!\d)(?:\d[ -]?){13,19}(?!\d)",
    "ssn": r"(?<!\d)\d{3}-\d{2}-\d{4}(?!\d)",
    "email": r"\b[A-Za-z0-9._%+\-]+@[A-Za-z0-9.\-]+\.[A-Za-z]{2,}\b",
    "phone": r"(?<!\d)(?:\+\d{1,3}[ -]?)?\(?\d{2,3}\)?[ -]?\d{4,5}[ -]\d{4}(?!\d)",
}
_COMPILED = {k: re.compile(v) for k, v in PATTERNS.items()}


def _digits(text):
    return [int(c) for c in text if c.isdigit()]


def luhn_ok(text):
    d = _digits(text)
    if not 13 <= len(d) <= 19:
        return False
    total = 0
    for i, n in enumerate(reversed(d)):
        if i % 2:
            n *= 2
            n -= 9 if n > 9 else 0
        total += n
    return total % 10 == 0


def _check_digit(d, weights):
    s = sum(a * b for a, b in zip(d, weights))
    r = s % 11
    return 0 if r < 2 else 11 - r


def cpf_ok(text):
    d = _digits(text)
    if len(d) != 11 or len(set(d)) == 1:
        return False
    return (_check_digit(d[:9], range(10, 1, -1)) == d[9]
            and _check_digit(d[:10], range(11, 1, -1)) == d[10])


def cnpj_ok(text):
    d = _digits(text)
    if len(d) != 14 or len(set(d)) == 1:
        return False
    w1 = [5, 4, 3, 2, 9, 8, 7, 6, 5, 4, 3, 2]
    w2 = [6] + w1
    return _check_digit(d[:12], w1) == d[12] and _check_digit(d[:13], w2) == d[13]


def ssn_ok(text):
    a, g, s = text.split("-")
    return a not in ("000", "666") and not a.startswith("9") and g != "00" and s != "0000"


_VALIDATORS = {"credit_card": luhn_ok, "cpf": cpf_ok, "cnpj": cnpj_ok, "ssn": ssn_ok}


def _leaves(value):
    if isinstance(value, str):
        yield value
    elif isinstance(value, dict):
        for v in value.values():
            yield from _leaves(v)
    elif isinstance(value, (list, tuple)):
        for v in value:
            yield from _leaves(v)


def scan(text, categories, extra):
    """Return {category: count} for the text."""
    hits = {}
    for name in categories:
        validator = _VALIDATORS.get(name)
        n = sum(1 for m in _COMPILED[name].finditer(text) if validator is None or validator(m.group(0)))
        if n:
            hits[name] = n
    for rx in extra:
        n = len(rx.findall(text))
        if n:
            hits["custom"] = hits.get("custom", 0) + n
    return hits


def output_guard(detect=(), extra_patterns=(), action="DENY", phases=("response", "tool_result")):
    categories = [c for c in detect if c in _COMPILED]
    extra = [re.compile(p) for p in extra_patterns]
    verdict = "ASK" if action == "ASK" else "DENY"
    active = set(phases)

    def evaluate(event):
        phase = event.get("type")
        if phase not in active:
            return None
        data = event.get("data")
        if phase == "tool_result" and isinstance(data, dict) and "result" in data:
            data = data["result"]
        hits = {}
        for leaf in _leaves(data):
            for k, n in scan(leaf, categories, extra).items():
                hits[k] = hits.get(k, 0) + n
        if not hits:
            return None
        summary = ", ".join(f"{k} x{n}" for k, n in sorted(hits.items()))
        return {"result": verdict, "reason": f"sensitive data in {phase}: {summary}"}

    return evaluate
