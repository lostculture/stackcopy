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


CONFIDENCE_ANCHOR = "anchor"

MAX_CANON_STACK_FRAMES = 999  # Canon focus bracketing allows up to 999.


@dataclass(frozen=True)
class CanonStackGroup:
    output_jpg: CanonFrame
    frames: tuple[CanonFrame, ...]
    confidence: str
    notes: tuple[str, ...] = field(default_factory=tuple)


def _index(records: list[CanonFrame]) -> dict[str, dict[int, dict[str, CanonFrame]]]:
    """folder -> number -> ext -> frame."""
    index: dict[str, dict[int, dict[str, CanonFrame]]] = {}
    for record in records:
        index.setdefault(record.folder, {}).setdefault(record.number, {})[
            record.ext
        ] = record
    return index


def _preceding_run(
    index, folder: str, number: int, limit: int = MAX_CANON_STACK_FRAMES
) -> list[CanonFrame]:
    """CR3s immediately below ``number``, oldest first, stopping at a gap."""
    folder_index = index.get(folder, {})
    run: list[CanonFrame] = []
    probe = number - 1
    while probe >= 0 and len(run) < limit:
        frame = folder_index.get(probe, {}).get(RAW_EXT)
        if frame is None:
            break
        run.append(frame)
        probe -= 1
    run.reverse()
    return run


def _anchor_cut(run: list[CanonFrame], jpg: CanonFrame) -> list[CanonFrame] | None:
    """Frames from the one whose timestamp equals the composite's, onward.

    Compared on ExifTool's own text so sub-second precision cannot be lost to
    float rounding; falls back to the parsed value when text is unavailable.
    """
    for position, frame in enumerate(run):
        if (
            jpg.timestamp_text is not None
            and frame.timestamp_text == jpg.timestamp_text
        ) or (
            jpg.timestamp_text is None
            and frame.timestamp is not None
            and frame.timestamp == jpg.timestamp
        ):
            return run[position:]
    return None


def _count_note(jpg: CanonFrame, frames: list[CanonFrame]) -> tuple[str, ...]:
    """FocusBracketingImageCount is the camera setting, never a boundary."""
    if jpg.image_count and jpg.image_count != len(frames):
        return (
            f"camera reported {jpg.image_count} bracketing frames, "
            f"grouped {len(frames)}",
        )
    return ()


def group_canon_stacks(records: list[CanonFrame]) -> list[CanonStackGroup]:
    index = _index(records)
    groups: list[CanonStackGroup] = []
    jpgs = sorted(
        (r for r in records if r.ext == ".jpg"),
        key=lambda r: (r.folder, r.number),
    )
    for jpg in jpgs:
        # A JPG sharing its number with a CR3 is an ordinary RAW+JPG pair.
        if RAW_EXT in index.get(jpg.folder, {}).get(jpg.number, {}):
            continue
        # An explicit "not a depth composite" rules the JPG out.  Unknown
        # (no ExifTool) is deliberately allowed through to the heuristic.
        if jpg.depth_composite is False:
            continue
        run = _preceding_run(index, jpg.folder, jpg.number)
        if not run:
            continue
        frames = _anchor_cut(run, jpg)
        if frames is None:
            continue
        groups.append(
            CanonStackGroup(
                output_jpg=jpg,
                frames=tuple(frames),
                confidence=CONFIDENCE_ANCHOR,
                notes=_count_note(jpg, frames),
            )
        )
    return groups
