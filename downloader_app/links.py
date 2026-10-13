from __future__ import annotations

import re
from urllib.parse import urlsplit


URL_PATTERN = re.compile(r"(?:https?://|magnet:\?)[^\s<>\"']+", re.IGNORECASE)


def parse_urls(text: str) -> list[str]:
    """Extract unique HTTP(S) and magnet links, preserving their input order."""
    results: list[str] = []
    seen: set[str] = set()
    for match in URL_PATTERN.findall(text):
        # trim punctuation grabbed from the sentence, not the link.
        url = match.rstrip(".,;:!?)]}")
        parsed = urlsplit(url)
        scheme = parsed.scheme.lower()
        if scheme == "magnet":
            if not parsed.query:
                continue
        elif scheme not in {"http", "https"} or not parsed.netloc:
            continue
        normalized = url.strip()
        key = normalized.casefold()
        if key not in seen:
            seen.add(key)
            results.append(normalized)
    return results
