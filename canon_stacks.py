# canon_stacks.py
"""Grouping of Canon in-camera depth-composite stacks.

Pure functions over metadata records.  Nothing here touches the filesystem or
spawns a process, so the whole algorithm is exercised from a small checked-in
fixture rather than a camera card.
"""

from __future__ import annotations

import re
import statistics
from dataclasses import dataclass, field
from datetime import datetime

# Canon writes IMG_0001.CR3; Olympus writes P7310001.ORF.  Stackcopy's own
# NUMERIC_STEM_REGEX requires six digits and therefore never matches Canon.
CANON_STEM_REGEX = re.compile(r"^([A-Za-z0-9_-]*?)(\d{4,})$")
# A camera folder's five-character tag always has a non-digit in it (EOSR6,
# _PANA, Canon's by-month 100_0728); a date folder such as 20260712 must not
# pass for one.
CAMERA_FOLDER_REGEX = re.compile(r"^(\d{3})((?=\d*[A-Za-z_])[A-Za-z0-9_]{5})$")
# A Lightroom-renamed folder keeps the camera's sequence but not its names:
# "Laurel Hill State Park 0028.JPG".  The prefix is the series; several can
# share one folder, each with its own independent numbering.  Lightroom pads
# only to three digits below 1000 ("... 013.JPG"), so any digit run counts.
RENAMED_STEM_REGEX = re.compile(r"^(.*?)\s*(\d+)$")

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
    series: str = ""

    @property
    def run_key(self) -> tuple[str, str]:
        """The numbering this frame belongs to: its folder and its series."""
        return (self.folder, self.series)


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


def records_from_exiftool_json(
    payload, renamed_folders: frozenset[str] | set[str] = frozenset()
) -> list[CanonFrame]:
    """Turn ExifTool JSON into frames, discarding anything not a camera file.

    ``renamed_folders`` names folders accepted even though they are not
    camera folders; their files are read as "<series> NNNN.ext".
    """
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
        renamed = folder in renamed_folders
        if not renamed and not CAMERA_FOLDER_REGEX.match(folder):
            continue
        stem, _, extension = name.rpartition(".")
        ext = f".{extension}".lower()
        if ext != RAW_EXT and ext not in JPG_EXTS:
            continue
        stem_match = (RENAMED_STEM_REGEX if renamed else CANON_STEM_REGEX).match(
            stem
        )
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
                series=stem_match.group(1) if renamed else "",
            )
        )
    return frames


CONFIDENCE_ANCHOR = "anchor"
CONFIDENCE_ANCHOR_CROSSFOLDER = "anchor-crossfolder"
CONFIDENCE_CADENCE = "cadence"

MAX_CROSS_FOLDER_GAP_SECONDS = 300

MAX_CANON_STACK_FRAMES = 999  # Canon focus bracketing allows up to 999.

CADENCE_TAIL_GAPS = 5
CADENCE_FACTOR = 4.0


@dataclass(frozen=True)
class CanonStackGroup:
    output_jpg: CanonFrame
    frames: tuple[CanonFrame, ...]
    confidence: str
    notes: tuple[str, ...] = field(default_factory=tuple)


def _index(
    records: list[CanonFrame],
) -> dict[tuple[str, str], dict[int, dict[str, CanonFrame]]]:
    """(folder, series) -> number -> ext -> frame."""
    index: dict[tuple[str, str], dict[int, dict[str, CanonFrame]]] = {}
    for record in records:
        index.setdefault(record.run_key, {}).setdefault(record.number, {})[
            record.ext
        ] = record
    return index


def _preceding_run(
    index, key: tuple[str, str], number: int, limit: int = MAX_CANON_STACK_FRAMES
) -> list[CanonFrame]:
    """CR3s immediately below ``number``, oldest first, stopping at a gap."""
    folder_index = index.get(key, {})
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


def _cadence_cut(
    run: list[CanonFrame],
    tail: int = CADENCE_TAIL_GAPS,
    factor: float = CADENCE_FACTOR,
) -> tuple[list[CanonFrame], int]:
    """Walk back from the newest frame while the interval stays consistent.

    The reference interval comes from the *tail* of the run - the frames
    nearest the composite, which are certainly part of the stack.  Using the
    whole run's median would be contaminated by the very ordinary shots this
    cut exists to exclude.

    Returns the frames kept and how many frames *inside* the kept range were
    dropped only because they carry no timestamp.  Cadence reasoning is
    impossible without one, so such frames cannot be placed -- but under a
    partial ExifTool failure that silently shortens a stack, which is why
    the count is reported back rather than swallowed.
    """
    timed = [f for f in run if f.timestamp is not None]
    if len(timed) < 2:
        return list(run), 0

    def _kept(frames: list[CanonFrame]) -> tuple[list[CanonFrame], int]:
        if not frames:
            return frames, 0
        first = run.index(frames[0])
        dropped = sum(1 for f in run[first:] if f.timestamp is None)
        return frames, dropped

    gaps = [
        timed[i + 1].timestamp - timed[i].timestamp for i in range(len(timed) - 1)
    ]
    reference = statistics.median(gaps[-tail:] if len(gaps) > tail else gaps)
    if reference <= 0:
        return _kept(timed)
    tolerance = reference * factor
    cut = 0
    for position in range(len(gaps) - 1, -1, -1):
        if gaps[position] > tolerance:
            cut = position + 1
            break
    return _kept(timed[cut:])


def _count_note(jpg: CanonFrame, frames: list[CanonFrame]) -> tuple[str, ...]:
    """FocusBracketingImageCount is the camera setting, never a boundary."""
    if jpg.image_count and jpg.image_count != len(frames):
        return (
            f"camera reported {jpg.image_count} bracketing frames, "
            f"grouped {len(frames)}",
        )
    return ()


def _previous_folder(index, key: tuple[str, str]) -> tuple[str, str] | None:
    """The numerically adjacent lower camera folder, if it is present.

    Mirrors stackcopy.previous_adjacent_camera_dir: only the immediately
    preceding folder number may extend a sequence across the boundary.
    """
    folder, series = key
    match = CAMERA_FOLDER_REGEX.match(folder)
    if not match:
        return None
    number, tag = int(match.group(1)), match.group(2)
    candidate = (f"{number - 1:03d}{tag}", series)
    return candidate if candidate in index else None


def _extend_across_folder(
    index, key: tuple[str, str], run: list[CanonFrame], jpg: CanonFrame
) -> list[CanonFrame] | None:
    """Borrow frames from the previous folder's tail to complete a stack.

    Called whenever the local anchor cut fails to find a match in ``run`` --
    including when ``run`` is empty, which happens when the composite is the
    very first file in its folder (the camera writes the composite after its
    last frame, so a burst whose last frame is IMG_9999 puts the composite at
    IMG_0001.JPG in the next folder with no local CR3s at all).  A borrowed
    frame must also be plausibly part of the same capture: metadata proves
    the JPG is a composite, but proves nothing about a file sitting in a
    different folder, so the result is still bounded by
    ``MAX_CROSS_FOLDER_GAP_SECONDS``.
    """
    previous = _previous_folder(index, key)
    if previous is None:
        return None
    previous_numbers = [
        number
        for number, by_ext in index[previous].items()
        if RAW_EXT in by_ext
    ]
    if not previous_numbers:
        return None
    tail_run = _preceding_run(index, previous, max(previous_numbers) + 1)
    if not tail_run:
        return None
    combined = tail_run + run
    frames = _anchor_cut(combined, jpg)
    if frames is None:
        return None
    anchor_time = frames[0].timestamp
    # With no local frames there is nothing to anchor the gap check to
    # except the composite's own timestamp -- without this, an empty
    # ``run`` would leave the borrow completely unguarded.
    oldest_local = run[0].timestamp if run else jpg.timestamp
    if (
        anchor_time is not None
        and oldest_local is not None
        and abs(oldest_local - anchor_time) > MAX_CROSS_FOLDER_GAP_SECONDS
    ):
        return None
    return frames


def group_canon_stacks(records: list[CanonFrame]) -> list[CanonStackGroup]:
    """Match each in-camera composite JPG to the frames that produced it.

    A composite is recognised by its Canon ``FocusBracketingDepthComposite``
    tag and cut from the preceding run of CR3s at the frame whose timestamp it
    shares - the anchor, which is exact.  Where no frame matches, the run is
    cut on cadence instead, and a burst that spans the camera's folder
    rollover reaches back into the previous folder.  Each group records which
    of those three routes produced it, so a caller can tell an exact match
    from an inferred one.
    """
    index = _index(records)
    groups: list[CanonStackGroup] = []
    jpgs = sorted(
        (r for r in records if r.ext == ".jpg"),
        key=lambda r: (r.folder, r.series, r.number),
    )
    for jpg in jpgs:
        # A JPG sharing its number with a CR3 is an ordinary RAW+JPG pair.
        if RAW_EXT in index.get(jpg.run_key, {}).get(jpg.number, {}):
            continue
        # An explicit "not a depth composite" rules the JPG out.  Unknown
        # (no ExifTool) is deliberately allowed through to the heuristic.
        if jpg.depth_composite is False:
            continue
        # An empty run is not a dead end: the composite can legitimately be
        # the first file in its folder, with its stack entirely in the
        # previous one (see _extend_across_folder's docstring).
        run = _preceding_run(index, jpg.run_key, jpg.number)
        frames = _anchor_cut(run, jpg)
        confidence = CONFIDENCE_ANCHOR
        notes: tuple[str, ...] = ()
        if frames is None:
            borrowed = _extend_across_folder(index, jpg.run_key, run, jpg)
            if borrowed is not None:
                frames = borrowed
                confidence = CONFIDENCE_ANCHOR_CROSSFOLDER
                notes = (
                    f"stack spans {borrowed[0].folder} into {jpg.folder}",
                )
        if frames is None and run:
            frames, undated = _cadence_cut(run)
            confidence = CONFIDENCE_CADENCE
            notes = (
                "no frame matched the composite timestamp; "
                "grouped by cadence",
            )
            if undated:
                notes += (
                    f"{undated} frame(s) in this range were left out: "
                    "no timestamp could be read for them",
                )
        if not frames:
            continue
        groups.append(
            CanonStackGroup(
                output_jpg=jpg,
                frames=tuple(frames),
                confidence=confidence,
                notes=notes + _count_note(jpg, frames),
            )
        )
    return groups
