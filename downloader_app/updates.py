from __future__ import annotations

import re

import requests

from downloader_app import __version__

RELEASES_API_URL = "https://api.github.com/repos/taiguls67/downetic/releases"
RELEASES_PAGE_URL = "https://github.com/taiguls67/downetic/releases"
_VERSION_PATTERN = re.compile(
    r"^v?(0|[1-9]\d*)\.(0|[1-9]\d*)\.(0|[1-9]\d*)"
    r"(?:-(alpha|beta|rc)(?:[.-]?(\d+))?)?$",
    re.IGNORECASE,
)
_PRERELEASE_RANK = {"alpha": 0, "beta": 1, "rc": 2}


def _version_key(version: str) -> tuple[int, int, int, int, int]:
    match = _VERSION_PATTERN.fullmatch(version.strip())
    if match is None:
        raise ValueError(f"Unsupported release version: {version}")
    major, minor, patch, stage, stage_number = match.groups()
    return (
        int(major),
        int(minor),
        int(patch),
        _PRERELEASE_RANK.get((stage or "").lower(), 3),
        int(stage_number or 0),
    )


def is_newer_version(candidate: str, current: str = __version__) -> bool:
    return _version_key(candidate) > _version_key(current)


def latest_release_version() -> str | None:
    response = requests.get(
        RELEASES_API_URL,
        headers={
            "Accept": "application/vnd.github+json",
            "User-Agent": f"Downetic/{__version__}",
        },
        params={"per_page": 100},
        timeout=(3, 5),
    )
    response.raise_for_status()
    releases = response.json()
    if not isinstance(releases, list):
        raise ValueError("GitHub returned an invalid releases list.")

    versions = [
        release["tag_name"]
        for release in releases
        if isinstance(release, dict)
        and not release.get("draft", True)
        and isinstance(release.get("tag_name"), str)
        and _VERSION_PATTERN.fullmatch(release["tag_name"].strip())
    ]
    return max(versions, key=_version_key, default=None)
