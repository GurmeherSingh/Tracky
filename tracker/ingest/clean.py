import re

from bs4 import BeautifulSoup

MAX_BODY_CHARS = 6000

_REPLY_MARKERS = [
    re.compile(r"^On .{5,80} wrote:\s*$", re.MULTILINE),
    re.compile(r"^-{3,}\s*Original Message\s*-{3,}", re.MULTILINE | re.IGNORECASE),
    re.compile(r"^From: .+@.+$", re.MULTILINE),
]
_SIGNATURE_MARKER = re.compile(r"^-- ?$", re.MULTILINE)


def clean_body(html_or_text: str) -> str:
    text = html_or_text
    if "<" in text and ">" in text:
        soup = BeautifulSoup(text, "html.parser")
        for tag in soup(["style", "script", "head"]):
            tag.decompose()
        text = soup.get_text(separator=" ")
    cut = len(text)
    for marker in _REPLY_MARKERS:
        m = marker.search(text)
        if m:
            cut = min(cut, m.start())
    m = _SIGNATURE_MARKER.search(text)
    if m:
        cut = min(cut, m.start())
    text = text[:cut]
    text = re.sub(r"[ \t]+", " ", text)
    text = re.sub(r"\n{3,}", "\n\n", text).strip()
    return text[:MAX_BODY_CHARS]
