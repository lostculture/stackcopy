# canon_stacks.py
"""Grouping of Canon in-camera depth-composite stacks.

Pure functions over metadata records.  Nothing here touches the filesystem or
spawns a process, so the whole algorithm is exercised from a small checked-in
fixture rather than a camera card.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import datetime

# Canon writes IMG_0001.CR3; Olympus writes P7310001.ORF.  Stackcopy's own
# NUMERIC_STEM_REGEX requires six digits and therefore never matches Canon.
CANON_STEM_REGEX = re.compile(r"^([A-Za-z0-9_-]*?)(\d{4,})$")
CAMERA_FOLDER_REGEX = re.compile(r"^(\d{3})([A-Za-z0-9_]{5})$")

RAW_EXT = ".cr3"
JPG_EXTS = (".jpg", ".jpeg")


@dataclass(frozen=True)
class CanonFrame:
    """One file on the card, as far as grouping is concerned."""

    folder: str
    number: int
    ext: str
    name: str
    timestamp_text: str | None = None
    timestamp: float | None = None
    depth_composite: bool | None = None
    image_count: int | None = None


def _parse_timestamp(text: str | None) -> float | None:
    """Parse ExifTool's SubSecDateTimeOriginal into epoch seconds.

    Sub-second precision is what makes the anchor exact, so it must survive.
    """
    if not isinstance(text, str) or not text.strip():
        return None
    cleaned = text.strip()
    # "2026:07:31 10:48:32.05-04:00" -> ISO-ish for fromisoformat.
    match = re.match(
        r"^(\d{4}):(\d{2}):(\d{2}) (\d{2}:\d{2}:\d{2}(?:\.\d+)?)"
        r"([+-]\d{2}:?\d{2}|Z)?$",
        cleaned,
    )
    if not match:
        return None
    year, month, day, clock, offset = match.groups()
    if offset in (None, "Z"):
        offset = "+00:00"
    elif ":" not in offset:
        offset = f"{offset[:3]}:{offset[3:]}"
    try:
        return datetime.fromisoformat(
            f"{year}-{month}-{day}T{clock}{offset}"
        ).timestamp()
    except ValueError:
        return None


def _as_bool(value) -> bool | None:
    """ExifTool -n gives 1/0; without -n it gives On/Off."""
    if value is None:
        return None
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)):
        return bool(value)
    text = str(value).strip().lower()
    if text in ("1", "on", "true", "yes", "enable", "enabled"):
        return True
    if text in ("0", "off", "false", "no", "disable", "disabled"):
        return False
    return None


def records_from_exiftool_json(payload) -> list[CanonFrame]:
    """Turn ExifTool JSON into frames, discarding anything not a camera file."""
    frames: list[CanonFrame] = []
    if not isinstance(payload, list):
        return frames
    for item in payload:
        if not isinstance(item, dict) or item.get("Error"):
            continue
        source = item.get("SourceFile")
        if not isinstance(source, str):
            continue
        parts = source.replace("\\", "/").rstrip("/").split("/")
        if len(parts) < 2:
            continue
        folder, name = parts[-2], parts[-1]
        if not CAMERA_FOLDER_REGEX.match(folder):
            continue
        stem, _, extension = name.rpartition(".")
        ext = f".{extension}".lower()
        if ext != RAW_EXT and ext not in JPG_EXTS:
            continue
        stem_match = CANON_STEM_REGEX.match(stem)
        if not stem_match:
            continue
        timestamp_text = item.get("SubSecDateTimeOriginal")
        image_count = item.get("FocusBracketingImageCount")
        frames.append(
            CanonFrame(
                folder=folder,
                number=int(stem_match.group(2)),
                ext=".jpg" if ext in JPG_EXTS else ext,
                name=name,
                timestamp_text=(
                    timestamp_text if isinstance(timestamp_text, str) else None
                ),
                timestamp=_parse_timestamp(timestamp_text),
                depth_composite=_as_bool(
                    item.get("FocusBracketingDepthComposite")
                ),
                image_count=(
                    int(image_count)
                    if isinstance(image_count, (int, float))
                    else None
                ),
            )
        )
    return frames
