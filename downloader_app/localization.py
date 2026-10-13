from __future__ import annotations

import json
from pathlib import Path


LOCALE_DIRECTORY = Path(__file__).parent / "locales"
_MOJIBAKE_MARKERS = (
    "\u00c3",
    "\u00c4",
    "\u00c5",
    "\u00d0",
    "\u00d1",
    "\u00d8",
    "\u00d9",
    "\u00e0\u00a4",
    "\u00e0\u00a6",
    "\u00e0\u00a8",
    "\u00e0\u00b0",
    "\u00e1\u00bb",
    "\u00e1\u00ba",
    "\u00e2\u20ac",
    "\u00e3",
    "\u00e4",
    "\u00e5",
    "\u00ec",
    "\u00eb",
)


def _repair_mojibake(value: str) -> str:
    def repair_chunk(chunk: str) -> str:
        if not any(marker in chunk for marker in _MOJIBAKE_MARKERS):
            return chunk
        raw = bytearray()
        for character in chunk:
            try:
                raw.extend(character.encode("cp1252"))
            except UnicodeEncodeError:
                codepoint = ord(character)
                if 0x80 <= codepoint <= 0x9F:
                    raw.append(codepoint)
                else:
                    return chunk
        try:
            repaired = raw.decode("utf-8")
        except UnicodeDecodeError:
            return chunk
        old_score = sum(chunk.count(marker) for marker in _MOJIBAKE_MARKERS)
        new_score = sum(repaired.count(marker) for marker in _MOJIBAKE_MARKERS)
        return repaired if repaired != chunk and new_score < old_score else chunk

    for _ in range(3):
        chunks: list[str] = []
        current: list[str] = []
        for character in value:
            try:
                character.encode("cp1252")
            except UnicodeEncodeError:
                codepoint = ord(character)
                if 0x80 <= codepoint <= 0x9F:
                    current.append(character)
                else:
                    if current:
                        chunks.append(repair_chunk("".join(current)))
                        current.clear()
                    chunks.append(character)
            else:
                current.append(character)
        if current:
            chunks.append(repair_chunk("".join(current)))
        repaired = "".join(chunks)
        if repaired == value:
            break
        value = repaired
    return value


def available_locales() -> dict[str, str]:
    locales: dict[str, str] = {}
    for path in sorted(LOCALE_DIRECTORY.glob("*.json")):
        with path.open(encoding="utf-8") as source:
            data = json.load(source)
        locale = data.get("locale")
        language = data.get("language")
        if not isinstance(locale, str) or not isinstance(language, str):
            raise ValueError(f"Locale file is missing metadata: {path.name}")
        locales[locale] = _repair_mojibake(language)
    if "en" not in locales:
        raise FileNotFoundError(f"English locale file not found in {LOCALE_DIRECTORY}")
    return locales


def load_locale(locale: str) -> dict[str, str]:
    path = LOCALE_DIRECTORY / f"{locale.replace('-', '_')}.json"
    with path.open(encoding="utf-8") as source:
        data = json.load(source)
    data = {
        key: _repair_mojibake(value) if isinstance(value, str) else value
        for key, value in data.items()
    }
    if data.get("locale") != locale:
        raise ValueError(f"Locale metadata does not match {locale}: {path}")
    if not all(isinstance(key, str) and isinstance(value, str) for key, value in data.items()):
        raise ValueError(f"Locale file must contain string values: {path}")
    if locale != "en":
        english_path = LOCALE_DIRECTORY / "en.json"
        with english_path.open(encoding="utf-8") as source:
            english = json.load(source)
        # keep every translation in lockstep with english.
        missing = english.keys() - data.keys()
        extra = data.keys() - english.keys()
        if missing or extra:
            raise ValueError(
                f"Locale keys do not match English in {path}: "
                f"missing={sorted(missing)}, extra={sorted(extra)}"
            )
    return data
