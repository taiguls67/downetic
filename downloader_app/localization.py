from __future__ import annotations

import json
from pathlib import Path


LOCALE_DIRECTORY = Path(__file__).parent / "locales"


def available_locales() -> dict[str, str]:
    locales: dict[str, str] = {}
    for path in sorted(LOCALE_DIRECTORY.glob("*.json")):
        with path.open(encoding="utf-8") as source:
            data = json.load(source)
        locale = data.get("locale")
        language = data.get("language")
        if not isinstance(locale, str) or not isinstance(language, str):
            raise ValueError(f"Locale file is missing metadata: {path.name}")
        locales[locale] = language
    if "en" not in locales:
        raise FileNotFoundError(f"English locale file not found in {LOCALE_DIRECTORY}")
    return locales


def load_locale(locale: str) -> dict[str, str]:
    path = LOCALE_DIRECTORY / f"{locale.replace('-', '_')}.json"
    with path.open(encoding="utf-8") as source:
        data = json.load(source)
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
