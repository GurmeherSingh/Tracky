import re

ALIASES = {
    "meta platforms": "meta",
    "alphabet": "google",
    "google llc": "google",
    "amazon.com": "amazon",
    "amazon web services": "amazon",
    "international business machines": "ibm",
}
_LEGAL_SUFFIXES = re.compile(
    r"[,.]?\s+(inc|llc|corp|corporation|ltd|co|plc|gmbh|l\.l\.c|l\.p)\.?$",
    re.IGNORECASE)


def canonicalize(name: str) -> str:
    n = name.strip().lower()
    n = re.sub(r"[‘’'\"]", "", n)
    prev = None
    while prev != n:
        prev = n
        n = _LEGAL_SUFFIXES.sub("", n).strip(" ,.")
    n = re.sub(r"\s+", " ", n)
    return ALIASES.get(n, n)
