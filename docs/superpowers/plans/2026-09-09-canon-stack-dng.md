# Canon Depth-Composite Import with DNG Conversion — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add a sixth Stackcopy mode that imports Canon in-camera depth-composite stacks, grouping each composite JPG with exactly its own source CR3 frames and converting those frames to DNG during the copy.

**Architecture:** Grouping is a pure function over metadata records in a new `canon_stacks.py` — no I/O, so every correctness question is answered against a small checked-in fixture instead of a 65 GB card. DNG conversion lives in a separate `dng_converter.py` that shells out to Adobe DNG Converter. `stackcopy.py` gains only mode wiring and an ExifTool read; its existing atomic-copy, dry-run, collision and verification machinery does all file movement unchanged.

**Tech Stack:** Python 3.11+, stdlib only at runtime. ExifTool (optional, external). Adobe DNG Converter (required for conversion, external). pytest for tests.

**Spec:** `docs/superpowers/specs/2026-09-09-canon-stack-dng-design.md`

## Global Constraints

- **No new runtime dependencies.** Stackcopy's runtime requirement is the Python standard library only. ExifTool and Adobe DNG Converter are external executables discovered at runtime, never Python packages.
- **Python floor: 3.11.** Existing code uses `X | Y` union syntax at runtime.
- **Cross-platform.** Linux, macOS, WSL, Windows. No Windows-only paths outside a platform branch.
- **ExifTool is optional.** Every Canon code path must degrade to a documented heuristic when ExifTool is absent, emitting one concise warning naming the affected file count — matching `read_stacked_image_metadata` (`stackcopy.py:2740`).
- **Adobe DNG Converter minimum: 15.1.** Canon EOS R6 Mark II support arrived in Camera Raw 15.1 (Dec 2022). Verified failing locally: version 13.0 (2020-09-15) returns `Error while converting file` in 1 s for every R6 II CR3.
- **ExifTool verified version: 13.59.** Matches `EXIFTOOL_RECOMMENDED_VERSION_TEXT` (`stackcopy.py:2342`). No Canon-specific version floor is declared: if the tag does not decode, detection degrades like any other unavailable metadata.
- **The card is never modified.** This mode copies only. No move, no delete.
- **Never bound a group by `FocusBracketingImageCount`.** It is the camera setting, not the actual count (`IMG_0096.JPG` reports 25, has 29 preceding frames).
- **Olympus behaviour must not change.** The existing suite passes untouched at every commit.

---

## File Structure

| File | Responsibility |
|---|---|
| `canon_stacks.py` (create) | Record types + pure grouping algorithm. No I/O, no subprocess, no `os` calls. |
| `dng_converter.py` (create) | Adobe DNG Converter discovery, version reporting, preflight, batch conversion, resume. |
| `stackcopy.py` (modify) | Canon ExifTool read; `--canon-import` mode wiring; plan construction; execution via existing safe-copy machinery. |
| `tests/test_canon_stacks.py` (create) | Grouping algorithm, driven by the fixture. |
| `tests/test_dng_converter.py` (create) | Discovery, version floor, preflight, resume, failure isolation — against a stub converter. |
| `tests/test_canon_import_mode.py` (create) | CLI wiring, layout, singles routing, dry-run. |
| `tests/fixtures/canon_108eosr6.json` (create) | Real ExifTool metadata from the card. Metadata only, no image data. |

The spec named a single `canon_stacks.py` holding both detection and conversion. Splitting conversion into `dng_converter.py` keeps the grouping module pure and therefore trivially testable; this is a deliberate refinement of the spec.

---

### Task 1: Metadata fixture and record model

**Files:**
- Create: `canon_stacks.py`
- Create: `tests/fixtures/canon_108eosr6.json`
- Create: `tests/test_canon_stacks.py`

**Interfaces:**
- Consumes: nothing.
- Produces: `CanonFrame`, `records_from_exiftool_json(payload: list[dict]) -> list[CanonFrame]`.

- [ ] **Step 1: Capture the fixture from the real card**

Read-only. Requires the card at `H:\DCIM` and ExifTool. Run once; the JSON is then checked in and the card is never needed again.

```bash
EXIFTOOL="${STACKCOPY_EXIFTOOL:-exiftool}"
"$EXIFTOOL" -json -n \
  -SourceFile -FileName -SubSecDateTimeOriginal \
  -FocusBracketingDepthComposite -FocusBracketingImageCount \
  "H:/DCIM/108EOSR6" "H:/DCIM/107EOSR6" \
  > tests/fixtures/canon_108eosr6.json
```

Then strip absolute paths so the fixture is portable, keeping only the camera folder and filename:

```bash
python - <<'PY'
import json, pathlib, re
p = pathlib.Path("tests/fixtures/canon_108eosr6.json")
items = json.loads(p.read_text(encoding="utf-8"))
kept = []
for item in items:
    source = item.get("SourceFile", "")
    match = re.search(r"(\d{3}[A-Za-z0-9_]{5})/([^/]+)$", source)
    if not match:
        continue
    item["SourceFile"] = f"{match.group(1)}/{match.group(2)}"
    kept.append(item)
# 107EOSR6 is only needed for the cross-folder case; keep its tail only.
trimmed = [
    i for i in kept
    if not i["SourceFile"].startswith("107EOSR6/")
    or int(re.search(r"(\d+)", i["SourceFile"].split("/")[1]).group(1)) >= 9970
]
p.write_text(json.dumps(trimmed, indent=1), encoding="utf-8")
print(f"{len(trimmed)} records")
PY
```

Expected: roughly 2,470 records.

- [ ] **Step 2: Write the failing test**

```python
# tests/test_canon_stacks.py
import json
from pathlib import Path

import pytest

import canon_stacks

FIXTURE = Path(__file__).parent / "fixtures" / "canon_108eosr6.json"


@pytest.fixture(scope="module")
def records():
    payload = json.loads(FIXTURE.read_text(encoding="utf-8"))
    return canon_stacks.records_from_exiftool_json(payload)


def test_fixture_parses_into_frames(records):
    assert len(records) > 2000
    jpgs = [r for r in records if r.ext == ".jpg"]
    cr3s = [r for r in records if r.ext == ".cr3"]
    assert len(jpgs) == 101
    assert len(cr3s) > 2300


def test_frame_carries_folder_number_and_timestamp(records):
    by_name = {(r.folder, r.number, r.ext): r for r in records}
    frame = by_name[("108EOSR6", 40, ".jpg")]
    assert frame.folder == "108EOSR6"
    assert frame.number == 40
    assert frame.depth_composite is True
    assert frame.image_count == 25
    assert frame.timestamp_text == "2026:07:31 10:48:32.05-04:00"
    assert frame.timestamp == pytest.approx(
        by_name[("108EOSR6", 15, ".cr3")].timestamp
    )


def test_unparseable_records_are_skipped():
    payload = [
        {"SourceFile": "not-a-camera-folder/IMG_0001.CR3"},
        {"SourceFile": "108EOSR6/README.txt"},
        {"SourceFile": "108EOSR6/IMG_0001.CR3",
         "SubSecDateTimeOriginal": "2026:07:31 10:44:02.03-04:00"},
    ]
    frames = canon_stacks.records_from_exiftool_json(payload)
    assert len(frames) == 1
    assert frames[0].number == 1
    assert frames[0].depth_composite is None
```

- [ ] **Step 3: Run test to verify it fails**

Run: `python -m pytest tests/test_canon_stacks.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'canon_stacks'`

- [ ] **Step 4: Write minimal implementation**

```python
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
```

- [ ] **Step 5: Run test to verify it passes**

Run: `python -m pytest tests/test_canon_stacks.py -v`
Expected: PASS, 3 tests.

- [ ] **Step 6: Commit**

```bash
git add canon_stacks.py tests/test_canon_stacks.py tests/fixtures/canon_108eosr6.json
git commit -m "feat(canon): record model for Canon stack detection"
```

---

### Task 2: Anchor grouping

**Files:**
- Modify: `canon_stacks.py`
- Test: `tests/test_canon_stacks.py`

**Interfaces:**
- Consumes: `CanonFrame`, `records_from_exiftool_json` from Task 1.
- Produces: `CanonStackGroup(output_jpg, frames, confidence, notes)`, `group_canon_stacks(records: list[CanonFrame]) -> list[CanonStackGroup]`, and `CONFIDENCE_ANCHOR = "anchor"`.

- [ ] **Step 1: Write the failing test**

```python
# append to tests/test_canon_stacks.py

def groups_by_output(groups):
    return {g.output_jpg.name: g for g in groups}


def test_ordinary_25_frame_stack_anchors_exactly(records):
    groups = groups_by_output(canon_stacks.group_canon_stacks(records))
    group = groups["IMG_0040.JPG"]
    assert group.confidence == canon_stacks.CONFIDENCE_ANCHOR
    assert len(group.frames) == 25
    assert group.frames[0].name == "IMG_0015.CR3"
    assert group.frames[-1].name == "IMG_0039.CR3"


def test_anchor_excludes_earlier_ordinary_shots(records):
    """IMG_0096's naive run is 29 frames; four are unrelated shots from an
    hour earlier.  The anchor lands on IMG_0071 and yields exactly 25."""
    group = groups_by_output(canon_stacks.group_canon_stacks(records))[
        "IMG_0096.JPG"
    ]
    assert group.confidence == canon_stacks.CONFIDENCE_ANCHOR
    assert len(group.frames) == 25
    assert group.frames[0].name == "IMG_0071.CR3"
    names = {f.name for f in group.frames}
    for excluded in ("IMG_0067.CR3", "IMG_0068.CR3",
                     "IMG_0069.CR3", "IMG_0070.CR3"):
        assert excluded not in names


def test_short_groups_are_real_stacks(records):
    groups = groups_by_output(canon_stacks.group_canon_stacks(records))
    assert len(groups["IMG_1172.JPG"].frames) == 3
    assert len(groups["IMG_1168.JPG"].frames) == 1


def test_every_composite_jpg_produces_a_group(records):
    groups = canon_stacks.group_canon_stacks(records)
    assert len([g for g in groups if g.output_jpg.folder == "108EOSR6"]) == 101


def test_non_composite_jpg_is_not_grouped():
    frames = [
        canon_stacks.CanonFrame("108EOSR6", 1, ".cr3", "IMG_0001.CR3",
                                timestamp=100.0, depth_composite=False),
        canon_stacks.CanonFrame("108EOSR6", 2, ".jpg", "IMG_0002.JPG",
                                timestamp=100.0, depth_composite=False),
    ]
    assert canon_stacks.group_canon_stacks(frames) == []


def test_jpg_with_matching_cr3_is_not_grouped():
    """A RAW+JPG pair shares its number and is an ordinary shot."""
    frames = [
        canon_stacks.CanonFrame("108EOSR6", 5, ".cr3", "IMG_0005.CR3",
                                timestamp=100.0, depth_composite=True),
        canon_stacks.CanonFrame("108EOSR6", 5, ".jpg", "IMG_0005.JPG",
                                timestamp=100.0, depth_composite=True),
    ]
    assert canon_stacks.group_canon_stacks(frames) == []
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/test_canon_stacks.py -v`
Expected: FAIL with `AttributeError: module 'canon_stacks' has no attribute 'group_canon_stacks'`

- [ ] **Step 3: Write minimal implementation**

```python
# append to canon_stacks.py

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
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python -m pytest tests/test_canon_stacks.py -v`
Expected: `test_every_composite_jpg_produces_a_group` FAILS at 95 of 101 — the six non-anchoring groups are Task 3 and Task 4. Mark it `@pytest.mark.xfail(strict=True, reason="cadence fallback lands in Task 3")` for this commit and remove the marker in Task 4. Every other test PASSES.

- [ ] **Step 5: Commit**

```bash
git add canon_stacks.py tests/test_canon_stacks.py
git commit -m "feat(canon): group stacks by composite timestamp anchor"
```

---

### Task 3: Relative cadence fallback

**Files:**
- Modify: `canon_stacks.py`
- Test: `tests/test_canon_stacks.py`

**Interfaces:**
- Consumes: `_preceding_run`, `_anchor_cut`, `CanonStackGroup` from Task 2.
- Produces: `CONFIDENCE_CADENCE = "cadence"`, `_cadence_cut(run, tail=5, factor=4.0) -> list[CanonFrame]`.

The threshold is **relative to the run's own tail**, never absolute. Intra-stack intervals vary with exposure (0.07 s at 1/6 s, 0.23 s on a slower run) and will change again when flash is added — shorter exposures shorten the interval while variable flash recycle adds noise. The tail is used rather than the whole run because the whole run is contaminated by exactly the ordinary shots the cut exists to exclude.

- [ ] **Step 1: Write the failing test**

```python
# append to tests/test_canon_stacks.py

def _run(intervals, start=1000.0, folder="108EOSR6", first_number=1):
    """Build a CR3 run from inter-frame gaps, then a composite JPG."""
    frames = []
    t = start
    number = first_number
    for gap in [0.0] + list(intervals):
        t += gap
        frames.append(
            canon_stacks.CanonFrame(
                folder, number, ".cr3", f"IMG_{number:04d}.CR3",
                timestamp_text=f"t{t:.4f}", timestamp=t, depth_composite=True,
            )
        )
        number += 1
    jpg = canon_stacks.CanonFrame(
        folder, number, ".jpg", f"IMG_{number:04d}.JPG",
        timestamp_text="unmatched", timestamp=t + 1.0, depth_composite=True,
    )
    return frames + [jpg]


def test_cadence_cut_is_relative_not_absolute():
    """A slow flash sequence is as uniform as a fast one and must survive."""
    fast = _run([0.07] * 24)
    slow = _run([1.5] * 24)
    for records in (fast, slow):
        group = canon_stacks.group_canon_stacks(records)[0]
        assert group.confidence == canon_stacks.CONFIDENCE_CADENCE
        assert len(group.frames) == 25


def test_cadence_cuts_where_interval_breaks():
    # Three ordinary shots (20 s apart) then a 25-frame stack at 0.07 s.
    records = _run([20.0, 20.0, 20.0] + [0.07] * 24)
    group = canon_stacks.group_canon_stacks(records)[0]
    assert group.confidence == canon_stacks.CONFIDENCE_CADENCE
    assert len(group.frames) == 25
    assert group.frames[0].name == "IMG_0004.CR3"


def test_cadence_tolerates_flash_recycle_jitter():
    """Flash recycle lengthens as the battery drains; 4x median absorbs it."""
    intervals = [1.4, 1.5, 1.6, 1.5, 2.0, 1.5, 2.4, 1.6, 1.5, 1.7]
    records = _run([30.0] + intervals)
    group = canon_stacks.group_canon_stacks(records)[0]
    assert len(group.frames) == len(intervals) + 1
    assert group.frames[0].name == "IMG_0002.CR3"


def test_cadence_group_is_flagged_in_notes():
    group = canon_stacks.group_canon_stacks(_run([0.07] * 5))[0]
    assert any("cadence" in note for note in group.notes)
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/test_canon_stacks.py -k cadence -v`
Expected: FAIL with `AttributeError: module 'canon_stacks' has no attribute 'CONFIDENCE_CADENCE'`

- [ ] **Step 3: Write minimal implementation**

```python
# add near the other confidence constants in canon_stacks.py
CONFIDENCE_CADENCE = "cadence"

CADENCE_TAIL_GAPS = 5
CADENCE_FACTOR = 4.0
```

```python
# add to canon_stacks.py
import statistics


def _cadence_cut(
    run: list[CanonFrame],
    tail: int = CADENCE_TAIL_GAPS,
    factor: float = CADENCE_FACTOR,
) -> list[CanonFrame]:
    """Walk back from the newest frame while the interval stays consistent.

    The reference interval comes from the *tail* of the run - the frames
    nearest the composite, which are certainly part of the stack.  Using the
    whole run's median would be contaminated by the very ordinary shots this
    cut exists to exclude.
    """
    timed = [f for f in run if f.timestamp is not None]
    if len(timed) < 2:
        return list(run)
    gaps = [
        timed[i + 1].timestamp - timed[i].timestamp for i in range(len(timed) - 1)
    ]
    reference = statistics.median(gaps[-tail:] if len(gaps) > tail else gaps)
    if reference <= 0:
        return list(timed)
    tolerance = reference * factor
    cut = 0
    for position in range(len(gaps) - 1, -1, -1):
        if gaps[position] > tolerance:
            cut = position + 1
            break
    return timed[cut:]
```

Then extend `group_canon_stacks`, replacing the `if frames is None: continue` branch:

```python
        frames = _anchor_cut(run, jpg)
        confidence = CONFIDENCE_ANCHOR
        notes: tuple[str, ...] = ()
        if frames is None:
            frames = _cadence_cut(run)
            confidence = CONFIDENCE_CADENCE
            notes = (
                "no frame matched the composite timestamp; "
                "grouped by cadence",
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
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python -m pytest tests/test_canon_stacks.py -v`
Expected: PASS, including the previously-xfail count test now reaching 101 groups — remove the `xfail` marker if it now passes strictly.

- [ ] **Step 5: Commit**

```bash
git add canon_stacks.py tests/test_canon_stacks.py
git commit -m "feat(canon): relative cadence fallback for unanchored stacks"
```

---

### Task 4: Cross-folder reach

**Files:**
- Modify: `canon_stacks.py`
- Test: `tests/test_canon_stacks.py`

**Interfaces:**
- Consumes: `_preceding_run`, `_anchor_cut` from Tasks 2–3.
- Produces: `CONFIDENCE_ANCHOR_CROSSFOLDER = "anchor-crossfolder"`, `MAX_CROSS_FOLDER_GAP_SECONDS = 300`.

`IMG_0014.JPG` anchors at `10:43:59.28`, *earlier* than `IMG_0001.CR3` (`10:44:02.03`). Its missing frames are `107EOSR6\IMG_9995–9999.CR3`, running at the same 0.23 s interval. The stack spans the 9999 → 0001 rollover.

- [ ] **Step 1: Write the failing test**

```python
# append to tests/test_canon_stacks.py

def test_stack_reaches_into_previous_camera_folder(records):
    group = groups_by_output(canon_stacks.group_canon_stacks(records))[
        "IMG_0014.JPG"
    ]
    assert group.confidence == canon_stacks.CONFIDENCE_ANCHOR_CROSSFOLDER
    assert group.frames[0].folder == "107EOSR6"
    assert group.frames[-1].name == "IMG_0013.CR3"
    # The 108EOSR6 tail is contiguous and fully included.
    tail = [f for f in group.frames if f.folder == "108EOSR6"]
    assert [f.number for f in tail] == list(range(1, 14))


def test_cross_folder_reach_respects_time_limit():
    """An unrelated older photo at the adjacent number is not borrowed."""
    old = canon_stacks.CanonFrame(
        "107EOSR6", 9999, ".cr3", "IMG_9999.CR3",
        timestamp_text="old", timestamp=0.0, depth_composite=True,
    )
    frames = [old] + [
        canon_stacks.CanonFrame(
            "108EOSR6", n, ".cr3", f"IMG_{n:04d}.CR3",
            timestamp_text=f"t{n}", timestamp=100000.0 + n * 0.07,
            depth_composite=True,
        )
        for n in range(1, 6)
    ]
    jpg = canon_stacks.CanonFrame(
        "108EOSR6", 6, ".jpg", "IMG_0006.JPG",
        timestamp_text="unmatched", timestamp=100010.0, depth_composite=True,
    )
    group = canon_stacks.group_canon_stacks(frames + [jpg])[0]
    assert all(f.folder == "108EOSR6" for f in group.frames)
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/test_canon_stacks.py -k cross_folder -v`
Expected: FAIL with `AttributeError: module 'canon_stacks' has no attribute 'CONFIDENCE_ANCHOR_CROSSFOLDER'`

- [ ] **Step 3: Write minimal implementation**

```python
# add to canon_stacks.py
CONFIDENCE_ANCHOR_CROSSFOLDER = "anchor-crossfolder"
MAX_CROSS_FOLDER_GAP_SECONDS = 300


def _previous_folder(index, folder: str) -> str | None:
    """The numerically adjacent lower camera folder, if it is present.

    Mirrors stackcopy.previous_adjacent_camera_dir: only the immediately
    preceding folder number may extend a sequence across the boundary.
    """
    match = CAMERA_FOLDER_REGEX.match(folder)
    if not match:
        return None
    number, tag = int(match.group(1)), match.group(2)
    candidate = f"{number - 1:03d}{tag}"
    return candidate if candidate in index else None


def _extend_across_folder(
    index, folder: str, run: list[CanonFrame], jpg: CanonFrame
) -> list[CanonFrame] | None:
    """Borrow frames from the previous folder's tail to complete a stack.

    Only reached when the backward walk consumed its whole folder without
    finding the anchor.  A borrowed frame must also be plausibly part of the
    same capture: metadata proves the JPG is a composite, but proves nothing
    about a file sitting in a different folder.
    """
    previous = _previous_folder(index, folder)
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
    oldest_local = run[0].timestamp if run else None
    if (
        anchor_time is not None
        and oldest_local is not None
        and abs(oldest_local - anchor_time) > MAX_CROSS_FOLDER_GAP_SECONDS
    ):
        return None
    return frames
```

In `group_canon_stacks`, before falling back to cadence:

```python
        frames = _anchor_cut(run, jpg)
        confidence = CONFIDENCE_ANCHOR
        notes: tuple[str, ...] = ()
        if frames is None and len(run) == len(
            _preceding_run(index, jpg.folder, jpg.number)
        ):
            borrowed = _extend_across_folder(index, jpg.folder, run, jpg)
            if borrowed is not None:
                frames = borrowed
                confidence = CONFIDENCE_ANCHOR_CROSSFOLDER
                notes = (
                    f"stack spans {borrowed[0].folder} into {jpg.folder}",
                )
        if frames is None:
            frames = _cadence_cut(run)
            confidence = CONFIDENCE_CADENCE
            notes = (
                "no frame matched the composite timestamp; grouped by cadence",
            )
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python -m pytest tests/test_canon_stacks.py -v`
Expected: PASS, all tests. `test_every_composite_jpg_produces_a_group` now passes without `xfail` — remove the marker.

- [ ] **Step 5: Add the regression test for the numbering gap**

```python
# append to tests/test_canon_stacks.py

def test_numbering_gap_bounds_a_run(records):
    """IMG_0902 was deleted in camera; IMG_0929's stack must not span it."""
    group = groups_by_output(canon_stacks.group_canon_stacks(records))[
        "IMG_0929.JPG"
    ]
    numbers = [f.number for f in group.frames]
    assert 902 not in numbers
    assert numbers == list(range(min(numbers), 929))
```

Run: `python -m pytest tests/test_canon_stacks.py -v`
Expected: PASS.

- [ ] **Step 6: Commit**

```bash
git add canon_stacks.py tests/test_canon_stacks.py
git commit -m "feat(canon): reach into the previous camera folder for split stacks"
```

---

### Task 5: Canon metadata read in stackcopy.py

**Files:**
- Modify: `stackcopy.py` — add after `read_stacked_image_metadata` (ends ~`:2865`)
- Test: `tests/test_canon_stacks.py`

**Interfaces:**
- Consumes: `exiftool_info()` (`stackcopy.py:2609`), `canon_stacks.records_from_exiftool_json`.
- Produces: `read_canon_stack_metadata(paths: list[str]) -> list[canon_stacks.CanonFrame]`.

Reuses the existing batching contract: bounded batches, parse JSON regardless of exit status, one concise warning naming the degraded file count.

- [ ] **Step 1: Write the failing test**

```python
# append to tests/test_canon_stacks.py
import subprocess
import stackcopy


def test_canon_metadata_read_degrades_without_exiftool(monkeypatch, capsys):
    monkeypatch.setattr(
        stackcopy, "exiftool_info",
        lambda: stackcopy.ExifToolInfo(
            executable=None, version=None, version_tuple=None,
            source=stackcopy.ExifToolSource.MISSING,
        ),
    )
    frames = stackcopy.read_canon_stack_metadata(
        ["/card/108EOSR6/IMG_0001.CR3", "/card/108EOSR6/IMG_0002.JPG"]
    )
    # Files are still described from their names; only metadata is missing.
    assert len(frames) == 2
    assert all(f.depth_composite is None for f in frames)


def test_canon_metadata_read_parses_batch(monkeypatch):
    payload = (
        '[{"SourceFile":"/card/108EOSR6/IMG_0002.JPG",'
        '"SubSecDateTimeOriginal":"2026:07:31 10:44:02.03-04:00",'
        '"FocusBracketingDepthComposite":1,'
        '"FocusBracketingImageCount":25}]'
    )
    monkeypatch.setattr(
        stackcopy, "exiftool_info",
        lambda: stackcopy.ExifToolInfo(
            executable="/usr/bin/exiftool", version="13.59",
            version_tuple=(13, 59), source=stackcopy.ExifToolSource.PATH,
        ),
    )
    monkeypatch.setattr(
        subprocess, "run",
        lambda *a, **k: subprocess.CompletedProcess(a, 0, payload, ""),
    )
    frames = stackcopy.read_canon_stack_metadata(
        ["/card/108EOSR6/IMG_0002.JPG"]
    )
    assert frames[0].depth_composite is True
    assert frames[0].image_count == 25
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/test_canon_stacks.py -k canon_metadata -v`
Expected: FAIL with `AttributeError: module 'stackcopy' has no attribute 'read_canon_stack_metadata'`

- [ ] **Step 3: Write minimal implementation**

```python
# stackcopy.py, after read_stacked_image_metadata
CANON_STACK_TAGS = (
    "-SubSecDateTimeOriginal",
    "-Canon:FocusBracketingDepthComposite",
    "-Canon:FocusBracketingImageCount",
)


def read_canon_stack_metadata(paths: list[str]) -> list:
    """Read Canon depth-composite MakerNotes in bounded ExifTool batches.

    Returns one canon_stacks.CanonFrame per readable camera file.  ExifTool is
    optional: without it every frame is described from its filename alone, with
    metadata left unknown, and Canon grouping degrades to its cadence
    heuristic exactly as the Olympus path degrades to its own.
    """
    import canon_stacks

    if not paths:
        return []

    info = exiftool_info()
    exiftool = info.executable if info.available else None
    if not exiftool:
        print(
            f"Warning: camera stack metadata could not be read for "
            f"{len(paths)} file(s) (ExifTool is not available). Those files "
            "fall back to heuristic stack detection."
        )
        return canon_stacks.records_from_exiftool_json(
            [{"SourceFile": path} for path in paths]
        )

    collected: list[dict] = []
    degraded_files = 0
    for batch in _exiftool_batches(paths):
        try:
            completed = subprocess.run(
                [exiftool, "-json", "-n", *CANON_STACK_TAGS, *batch],
                check=False,
                capture_output=True,
                stdin=subprocess.DEVNULL,
                encoding="utf-8",
                errors="replace",
                timeout=300,
            )
        except (subprocess.TimeoutExpired, OSError, subprocess.SubprocessError):
            degraded_files += len(batch)
            collected.extend({"SourceFile": path} for path in batch)
            continue
        try:
            payload = json.loads(completed.stdout)
        except (json.JSONDecodeError, TypeError, ValueError):
            payload = None
        if not isinstance(payload, list):
            degraded_files += len(batch)
            collected.extend({"SourceFile": path} for path in batch)
            continue
        reported = set()
        for item in payload:
            if isinstance(item, dict) and isinstance(item.get("SourceFile"), str):
                reported.add(os.path.normcase(os.path.abspath(item["SourceFile"])))
                collected.append(item)
        for path in batch:
            if os.path.normcase(os.path.abspath(path)) not in reported:
                degraded_files += 1
                collected.append({"SourceFile": path})

    if degraded_files:
        print(
            f"Warning: camera stack metadata could not be read for "
            f"{degraded_files} file(s). Those files fall back to heuristic "
            "stack detection."
        )
    return canon_stacks.records_from_exiftool_json(collected)
```

Extract the batching loop currently inlined in `read_stacked_image_metadata` (`stackcopy.py:2765-2778`) into a shared helper placed just above it, and call it from both. Behaviour is identical, so the existing Olympus tests are the proof:

```python
def _exiftool_batches(paths: list[str]) -> list[list[str]]:
    """Split paths into batches that stay below Windows' command-line limit
    while avoiding the pathological one-process-per-file pattern."""
    batches: list[list[str]] = []
    batch: list[str] = []
    batch_characters = 0
    for path in paths:
        path_characters = len(path) + 3
        if batch and (len(batch) >= 250 or batch_characters + path_characters > 24000):
            batches.append(batch)
            batch = []
            batch_characters = 0
        batch.append(path)
        batch_characters += path_characters
    if batch:
        batches.append(batch)
    return batches
```

- [ ] **Step 4: Run tests to verify they pass and Olympus is unaffected**

Run: `python -m pytest tests/ -v`
Expected: PASS, whole suite, including all pre-existing Olympus tests.

- [ ] **Step 5: Commit**

```bash
git add stackcopy.py tests/test_canon_stacks.py
git commit -m "feat(canon): read Canon depth-composite MakerNotes via ExifTool"
```

---

### Task 6: DNG converter discovery and preflight

**Files:**
- Create: `dng_converter.py`
- Test: `tests/test_dng_converter.py`

**Interfaces:**
- Consumes: nothing from earlier tasks.
- Produces: `find_dng_converter() -> str | None`, `converter_version(path) -> tuple[int, int] | None`, `preflight(converter, sample_cr3) -> str | None` (returns an error message, or `None` when usable), `DNG_CONVERTER_MINIMUM = (15, 1)`, `DNG_CONVERTER_ENV = "STACKCOPY_DNG_CONVERTER"`.

**Why preflight exists.** Verified locally: Adobe DNG Converter 13.0 (2020-09-15) rejects every EOS R6 Mark II CR3 with `Error while converting file` in ~1 s, because raw support is per-camera-model and the body postdates the converter by two years. A version number alone cannot prove a specific body is supported, so a one-file trial conversion is the authoritative gate; the version is read only to make the failure message actionable.

- [ ] **Step 1: Write the failing test**

```python
# tests/test_dng_converter.py
import os
import stat
import subprocess
import sys
from pathlib import Path

import pytest

import dng_converter


def make_stub(tmp_path: Path, body: str, name: str = "stub") -> str:
    """A fake converter: writes a .dng per input, or fails, on demand."""
    if sys.platform == "win32":
        path = tmp_path / f"{name}.bat"
        path.write_text(f"@echo off\n{body}\n", encoding="utf-8")
    else:
        path = tmp_path / name
        path.write_text(f"#!/bin/sh\n{body}\n", encoding="utf-8")
        path.chmod(path.stat().st_mode | stat.S_IEXEC)
    return str(path)


def test_env_override_wins(tmp_path, monkeypatch):
    stub = make_stub(tmp_path, "exit 0")
    monkeypatch.setenv(dng_converter.DNG_CONVERTER_ENV, stub)
    assert dng_converter.find_dng_converter() == stub


def test_missing_converter_returns_none(tmp_path, monkeypatch):
    monkeypatch.setenv(dng_converter.DNG_CONVERTER_ENV,
                       str(tmp_path / "nope"))
    monkeypatch.setattr(dng_converter, "_candidate_paths", lambda: [])
    assert dng_converter.find_dng_converter() is None


def test_preflight_reports_conversion_failure(tmp_path, monkeypatch):
    """The 13.0-versus-R6-Mark-II case: exits nonzero, writes nothing."""
    stub = make_stub(tmp_path, "exit 1")
    sample = tmp_path / "IMG_0001.CR3"
    sample.write_bytes(b"not really a raw file")
    monkeypatch.setattr(dng_converter, "converter_version", lambda p: (13, 0))
    message = dng_converter.preflight(stub, str(sample))
    assert message is not None
    assert "13.0" in message
    assert "15.1" in message


def test_preflight_passes_when_dng_is_produced(tmp_path):
    if sys.platform == "win32":
        body = 'echo dng > "%~4\\IMG_0001.dng"'
    else:
        body = 'mkdir -p "$4" && echo dng > "$4/IMG_0001.dng"'
    stub = make_stub(tmp_path, body)
    sample = tmp_path / "IMG_0001.CR3"
    sample.write_bytes(b"raw")
    assert dng_converter.preflight(stub, str(sample)) is None
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/test_dng_converter.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'dng_converter'`

- [ ] **Step 3: Write minimal implementation**

```python
# dng_converter.py
"""Adobe DNG Converter discovery and invocation.

Raw support is per-camera-model, not per-format: a converter does not
"support CR3", it carries a profile for each body.  A converter older than
the camera rejects its files instantly at header parse.  That is why a
one-file trial conversion, not a version number, is the authoritative gate.
"""

from __future__ import annotations

import os
import shutil
import subprocess
import sys
import tempfile

DNG_CONVERTER_ENV = "STACKCOPY_DNG_CONVERTER"

# Canon EOS R6 Mark II support arrived in Camera Raw 15.1 (Dec 2022), and the
# standalone converter's versions track Camera Raw's.
DNG_CONVERTER_MINIMUM = (15, 1)
DNG_CONVERTER_MINIMUM_TEXT = "15.1"
DNG_CONVERTER_DOWNLOAD_URL = (
    "https://helpx.adobe.com/camera-raw/using/adobe-dng-converter.html"
)

PREFLIGHT_TIMEOUT_SECONDS = 180


def _candidate_paths() -> list[str]:
    if sys.platform == "win32":
        return [
            r"C:\Program Files\Adobe\Adobe DNG Converter\Adobe DNG Converter.exe",
            r"C:\Program Files (x86)\Adobe\Adobe DNG Converter\Adobe DNG Converter.exe",
        ]
    if sys.platform == "darwin":
        return [
            "/Applications/Adobe DNG Converter.app/Contents/MacOS/"
            "Adobe DNG Converter",
        ]
    return []


def find_dng_converter() -> str | None:
    """Locate the converter: env override, then platform defaults, then PATH."""
    override = (os.environ.get(DNG_CONVERTER_ENV) or "").strip()
    if override:
        return override if os.path.isfile(override) else None
    for candidate in _candidate_paths():
        if os.path.isfile(candidate):
            return candidate
    return shutil.which("Adobe DNG Converter")


def converter_version(path: str) -> tuple[int, int] | None:
    """Best-effort version, used only to make an error message actionable."""
    if sys.platform == "win32":
        try:
            import ctypes
            import ctypes.wintypes as wintypes

            size = ctypes.windll.version.GetFileVersionInfoSizeW(path, None)
            if not size:
                return None
            buffer = ctypes.create_string_buffer(size)
            if not ctypes.windll.version.GetFileVersionInfoW(
                path, 0, size, buffer
            ):
                return None
            block = ctypes.c_void_p()
            length = wintypes.UINT()
            if not ctypes.windll.version.VerQueryValueW(
                buffer, "\\", ctypes.byref(block), ctypes.byref(length)
            ):
                return None
            info = ctypes.cast(
                block, ctypes.POINTER(ctypes.c_uint32 * 4)
            ).contents
            most, least = info[2], info[3]
            return (most >> 16) & 0xFFFF, most & 0xFFFF
        except Exception:
            return None
    if sys.platform == "darwin":
        plist = os.path.join(
            path.split(".app/")[0] + ".app", "Contents", "Info.plist"
        )
        try:
            import plistlib

            with open(plist, "rb") as handle:
                text = plistlib.load(handle).get("CFBundleShortVersionString")
            major, _, minor = str(text).partition(".")
            return int(major), int(minor.split(".")[0] or 0)
        except Exception:
            return None
    return None


def convert_command(converter: str, dest_dir: str, files: list[str]) -> list[str]:
    """Lossless compressed DNG, medium preview, no embedded original."""
    return [converter, "-c", "-p1", "-d", dest_dir, *files]


def preflight(converter: str, sample_cr3: str) -> str | None:
    """Trial-convert one real frame.  Returns an error message, or None.

    Authoritative: it answers "can this converter read *this camera*", which
    no version comparison can.
    """
    version = converter_version(converter)
    version_text = (
        f"{version[0]}.{version[1]}" if version else "an unknown version"
    )
    with tempfile.TemporaryDirectory(prefix="stackcopy-dng-") as work:
        try:
            completed = subprocess.run(
                convert_command(converter, work, [sample_cr3]),
                check=False,
                capture_output=True,
                stdin=subprocess.DEVNULL,
                encoding="utf-8",
                errors="replace",
                timeout=PREFLIGHT_TIMEOUT_SECONDS,
            )
        except subprocess.TimeoutExpired:
            return (
                f"Adobe DNG Converter ({version_text}) did not finish a test "
                f"conversion within {PREFLIGHT_TIMEOUT_SECONDS}s."
            )
        except (OSError, subprocess.SubprocessError) as error:
            return f"Adobe DNG Converter could not be run: {error}"
        produced = [n for n in os.listdir(work) if n.lower().endswith(".dng")]
        if produced and completed.returncode == 0:
            return None

    hint = ""
    if version and version < DNG_CONVERTER_MINIMUM:
        hint = (
            f" It is older than {DNG_CONVERTER_MINIMUM_TEXT}, which is the "
            "first version supporting recent Canon bodies. Update it at "
            f"{DNG_CONVERTER_DOWNLOAD_URL}"
        )
    else:
        hint = (
            " This usually means the converter predates the camera model. "
            f"Update it at {DNG_CONVERTER_DOWNLOAD_URL}"
        )
    return (
        f"Adobe DNG Converter ({version_text}) could not convert "
        f"{os.path.basename(sample_cr3)}.{hint}"
    )
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python -m pytest tests/test_dng_converter.py -v`
Expected: PASS, 4 tests.

- [ ] **Step 5: Commit**

```bash
git add dng_converter.py tests/test_dng_converter.py
git commit -m "feat(dng): converter discovery with camera-model preflight"
```

---

### Task 7: Batch conversion, resume, and failure isolation

**Files:**
- Modify: `dng_converter.py`
- Test: `tests/test_dng_converter.py`

**Interfaces:**
- Consumes: `convert_command`, `find_dng_converter` from Task 6.
- Produces: `ConversionResult(dest_dir, converted, skipped, failed, error)`, `convert_group(converter, files, dest_dir, timeout=None) -> ConversionResult`, `convert_groups(converter, jobs_by_dest, workers=1, on_progress=None) -> list[ConversionResult]`.

One converter invocation per stack folder — 101 processes, not 2,336. An incomplete stack handed to Helicon is worse than no stack, so a partial group is reported and left unwritten while other groups continue.

- [ ] **Step 1: Write the failing test**

```python
# append to tests/test_dng_converter.py

def test_convert_group_reports_every_file_converted(tmp_path):
    if sys.platform == "win32":
        body = 'for %%f in (%*) do @if not "%%~xf"==".dng" echo d > "%~4\\%%~nf.dng"'
    else:
        body = 'd="$4"; shift 4; mkdir -p "$d"; for f; do echo d > "$d/$(basename "${f%.*}").dng"; done'
    stub = make_stub(tmp_path, body)
    src = tmp_path / "src"
    src.mkdir()
    files = []
    for n in range(1, 4):
        f = src / f"IMG_{n:04d}.CR3"
        f.write_bytes(b"raw")
        files.append(str(f))
    dest = tmp_path / "out"
    result = dng_converter.convert_group(stub, files, str(dest))
    assert result.converted == 3
    assert result.failed == 0
    assert result.error is None


def test_convert_group_detects_missing_output(tmp_path):
    """Converter exits 0 but silently produced nothing - still a failure."""
    stub = make_stub(tmp_path, "exit 0")
    src = tmp_path / "src"
    src.mkdir()
    f = src / "IMG_0001.CR3"
    f.write_bytes(b"raw")
    dest = tmp_path / "out"
    result = dng_converter.convert_group(stub, [str(f)], str(dest))
    assert result.failed == 1
    assert result.error is not None


def test_convert_group_resumes_completed_work(tmp_path):
    stub = make_stub(tmp_path, "exit 1")  # would fail if it ran
    src = tmp_path / "src"
    src.mkdir()
    dest = tmp_path / "out"
    dest.mkdir()
    files = []
    for n in range(1, 3):
        f = src / f"IMG_{n:04d}.CR3"
        f.write_bytes(b"raw")
        files.append(str(f))
        (dest / f"IMG_{n:04d}.dng").write_bytes(b"already done")
    result = dng_converter.convert_group(stub, files, str(dest))
    assert result.skipped == 2
    assert result.converted == 0
    assert result.error is None
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/test_dng_converter.py -k convert_group -v`
Expected: FAIL with `AttributeError: module 'dng_converter' has no attribute 'convert_group'`

- [ ] **Step 3: Write minimal implementation**

```python
# append to dng_converter.py
import concurrent.futures
from dataclasses import dataclass

CONVERT_TIMEOUT_PER_FILE_SECONDS = 120


@dataclass(frozen=True)
class ConversionResult:
    dest_dir: str
    converted: int = 0
    skipped: int = 0
    failed: int = 0
    error: str | None = None

    @property
    def ok(self) -> bool:
        return self.error is None and self.failed == 0


def _expected_dng(dest_dir: str, source: str) -> str:
    stem = os.path.splitext(os.path.basename(source))[0]
    return os.path.join(dest_dir, f"{stem}.dng")


def convert_group(
    converter: str,
    files: list[str],
    dest_dir: str,
    timeout: float | None = None,
) -> ConversionResult:
    """Convert one stack's frames into dest_dir.

    Already-converted frames are skipped, so an interrupted import resumes.
    """
    os.makedirs(dest_dir, exist_ok=True)
    pending = [f for f in files if not os.path.exists(_expected_dng(dest_dir, f))]
    skipped = len(files) - len(pending)
    if not pending:
        return ConversionResult(dest_dir, converted=0, skipped=skipped)

    if timeout is None:
        timeout = CONVERT_TIMEOUT_PER_FILE_SECONDS * len(pending)
    try:
        completed = subprocess.run(
            convert_command(converter, dest_dir, pending),
            check=False,
            capture_output=True,
            stdin=subprocess.DEVNULL,
            encoding="utf-8",
            errors="replace",
            timeout=timeout,
        )
    except subprocess.TimeoutExpired:
        return ConversionResult(
            dest_dir, skipped=skipped, failed=len(pending),
            error=f"conversion timed out after {timeout:.0f}s",
        )
    except (OSError, subprocess.SubprocessError) as error:
        return ConversionResult(
            dest_dir, skipped=skipped, failed=len(pending),
            error=f"converter could not be run: {error}",
        )

    missing = [f for f in pending if not os.path.exists(_expected_dng(dest_dir, f))]
    if missing:
        detail = ", ".join(os.path.basename(f) for f in missing[:3])
        if len(missing) > 3:
            detail += f", and {len(missing) - 3} more"
        return ConversionResult(
            dest_dir,
            converted=len(pending) - len(missing),
            skipped=skipped,
            failed=len(missing),
            error=(
                f"converter exited {completed.returncode} without producing "
                f"DNG for {detail}"
            ),
        )
    return ConversionResult(dest_dir, converted=len(pending), skipped=skipped)


def convert_groups(
    converter: str,
    jobs_by_dest: list[tuple[str, list[str]]],
    workers: int = 1,
    on_progress=None,
) -> list[ConversionResult]:
    """Convert many stacks, parallel across folders.

    Conversion is CPU-bound and single-threaded per file, and dominates the
    wall-clock of an import, so folder-level parallelism is the main lever.
    """
    results: list[ConversionResult] = []
    if workers <= 1:
        for dest_dir, files in jobs_by_dest:
            result = convert_group(converter, files, dest_dir)
            results.append(result)
            if on_progress:
                on_progress(result)
        return results

    with concurrent.futures.ThreadPoolExecutor(max_workers=workers) as pool:
        futures = {
            pool.submit(convert_group, converter, files, dest_dir): dest_dir
            for dest_dir, files in jobs_by_dest
        }
        for future in concurrent.futures.as_completed(futures):
            result = future.result()
            results.append(result)
            if on_progress:
                on_progress(result)
    return results
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python -m pytest tests/test_dng_converter.py -v`
Expected: PASS, 7 tests.

- [ ] **Step 5: Commit**

```bash
git add dng_converter.py tests/test_dng_converter.py
git commit -m "feat(dng): batch conversion with resume and failure isolation"
```

---

### Task 8: `--canon-import` mode

**Files:**
- Modify: `stackcopy.py` — mode group (`:2929`), validation (`:3087`), mode dispatch
- Test: `tests/test_canon_import_mode.py`

**Interfaces:**
- Consumes: `canon_stacks.group_canon_stacks`, `stackcopy.read_canon_stack_metadata`, `dng_converter.find_dng_converter/preflight/convert_groups`.
- Produces: `build_canon_import_plan(records, dest_root, singles_root) -> CanonImportPlan` with `.stacks: list[CanonPlannedStack]`, `.singles: list[str]`, `.unresolved: list[CanonStackGroup]`; `CanonPlannedStack(dest_dir, jpg_src, jpg_dest, frame_sources)`.

Layout, per the spec:

```
<dest_root>/YYYY-MM-DD/IMG_0014/IMG_9987.dng … IMG_0013.dng + IMG_0014.JPG
<singles_root>/YYYY-MM-DD/IMG_0067.CR3 …
```

Dated by EXIF `DateTimeOriginal`, not mtime — a deliberate deviation from upstream's mtime dating, because detection already reads it and mtime does not survive a copy. Singles go to a **sibling root**: inside the stacks tree, Helicon's "Add folders" recursion would treat the singles folder as a stack.

- [ ] **Step 1: Write the failing test**

```python
# tests/test_canon_import_mode.py
import json
from pathlib import Path

import pytest

import canon_stacks
import stackcopy

FIXTURE = Path(__file__).parent / "fixtures" / "canon_108eosr6.json"


@pytest.fixture(scope="module")
def plan():
    payload = json.loads(FIXTURE.read_text(encoding="utf-8"))
    records = canon_stacks.records_from_exiftool_json(payload)
    return stackcopy.build_canon_import_plan(
        records, r"F:\Stacked Images", r"F:\Single Images"
    )


def test_plan_has_one_folder_per_stack(plan):
    assert len(plan.stacks) == 101


def test_stack_folder_is_named_for_the_composite(plan):
    stack = next(s for s in plan.stacks if s.jpg_src.endswith("IMG_0040.JPG"))
    assert stack.dest_dir.endswith(str(Path("2026-07-31") / "IMG_0040"))
    assert len(stack.frame_sources) == 25


def test_composite_jpg_travels_with_its_frames(plan):
    stack = next(s for s in plan.stacks if s.jpg_src.endswith("IMG_0040.JPG"))
    assert stack.jpg_dest == str(Path(stack.dest_dir) / "IMG_0040.JPG")


def test_loose_cr3s_go_to_the_singles_root(plan):
    singles = [Path(p).name for p in plan.singles]
    for name in ("IMG_0067.CR3", "IMG_0068.CR3",
                 "IMG_0069.CR3", "IMG_0070.CR3"):
        assert name in singles
    assert all(r"Single Images" in p for p in plan.singles)


def test_singles_never_land_inside_the_stacks_root(plan):
    """Helicon's folder recursion would render them as a stack."""
    for path in plan.singles:
        assert not path.startswith(r"F:\Stacked Images")


def test_every_frame_belongs_to_exactly_one_destination(plan):
    seen = set()
    for stack in plan.stacks:
        for source in stack.frame_sources:
            assert source not in seen
            seen.add(source)
    for single in plan.singles:
        assert single not in seen
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/test_canon_import_mode.py -v`
Expected: FAIL with `AttributeError: module 'stackcopy' has no attribute 'build_canon_import_plan'`

- [ ] **Step 3: Write minimal implementation**

```python
# stackcopy.py
from dataclasses import dataclass as _dataclass


@_dataclass(frozen=True)
class CanonPlannedStack:
    dest_dir: str
    jpg_src: str
    jpg_dest: str
    frame_sources: tuple[str, ...]


@_dataclass(frozen=True)
class CanonImportPlan:
    stacks: tuple[CanonPlannedStack, ...]
    singles: tuple[str, ...]
    unresolved: tuple = ()


def _canon_group_date(group) -> str:
    """Dated by EXIF DateTimeOriginal, which survives a copy; mtime does not."""
    timestamp = group.output_jpg.timestamp
    if timestamp is None:
        for frame in group.frames:
            if frame.timestamp is not None:
                timestamp = frame.timestamp
                break
    if timestamp is None:
        return "unknown-date"
    return datetime.fromtimestamp(timestamp).strftime("%Y-%m-%d")


def build_canon_import_plan(
    records, dest_root: str, singles_root: str, source_root: str = ""
) -> CanonImportPlan:
    """Map grouped stacks onto destination folders.

    One folder per stack so Helicon Focus batch mode can consume the tree
    directly.  Loose CR3s go to a sibling root, never inside dest_root, or
    Helicon's folder recursion would render them as a stack.
    """
    import canon_stacks

    groups = canon_stacks.group_canon_stacks(records)

    def source_path(frame) -> str:
        return os.path.join(source_root, frame.folder, frame.name)

    stacks: list[CanonPlannedStack] = []
    claimed: set[str] = set()
    for group in groups:
        stem = os.path.splitext(group.output_jpg.name)[0]
        dest_dir = os.path.join(dest_root, _canon_group_date(group), stem)
        frame_sources = tuple(source_path(f) for f in group.frames)
        stacks.append(
            CanonPlannedStack(
                dest_dir=dest_dir,
                jpg_src=source_path(group.output_jpg),
                jpg_dest=os.path.join(dest_dir, group.output_jpg.name),
                frame_sources=frame_sources,
            )
        )
        claimed.update(frame_sources)
        claimed.add(source_path(group.output_jpg))

    singles = []
    for record in records:
        if record.ext != canon_stacks.RAW_EXT:
            continue
        path = source_path(record)
        if path in claimed:
            continue
        date = (
            datetime.fromtimestamp(record.timestamp).strftime("%Y-%m-%d")
            if record.timestamp is not None
            else "unknown-date"
        )
        singles.append(os.path.join(singles_root, date, record.name))

    return CanonImportPlan(
        stacks=tuple(stacks), singles=tuple(singles), unresolved=()
    )
```

Add the mode to the mutually exclusive group after `--lightroomimport` (`stackcopy.py:2929`):

```python
    mode_group.add_argument(
        "--canon-import",
        nargs=2,
        metavar=("SRC_DIR", "DEST_DIR"),
        help=(
            "Import Canon in-camera depth-composite stacks: one folder per "
            "stack, source CR3 frames converted to DNG for restacking. The "
            "card is never modified."
        ),
    )
```

And the supporting options, in the same block as the other optional flags (`stackcopy.py:3015`):

```python
    parser.add_argument(
        "--singles-dir",
        metavar="DIR",
        help=(
            "Where CR3 files that belong to no stack are copied "
            "(default: a 'Single Images' folder beside DEST_DIR). Kept "
            "outside DEST_DIR so Helicon Focus batch mode does not treat it "
            "as a stack."
        ),
    )
    parser.add_argument(
        "--dng-jobs",
        type=int,
        default=1,
        metavar="N",
        help="Convert N stacks in parallel (default: 1).",
    )
    parser.add_argument(
        "--no-dng",
        action="store_true",
        help="Copy CR3 frames as-is instead of converting them to DNG.",
    )
```

Validation alongside the existing `--plan-json` check (`stackcopy.py:3098`):

```python
        if args.singles_dir and not args.canon_import:
            parser.error("--singles-dir can only be used with --canon-import.")
        if args.dng_jobs != 1 and not args.canon_import:
            parser.error("--dng-jobs can only be used with --canon-import.")
        if args.no_dng and not args.canon_import:
            parser.error("--no-dng can only be used with --canon-import.")
        if args.dng_jobs < 1:
            parser.error("--dng-jobs must be at least 1.")
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python -m pytest tests/test_canon_import_mode.py tests/ -v`
Expected: PASS, whole suite.

- [ ] **Step 5: Commit**

```bash
git add stackcopy.py tests/test_canon_import_mode.py
git commit -m "feat(canon): --canon-import mode and destination planning"
```

---

### Task 9: Execution, dry-run reporting, and preflight wiring

**Files:**
- Modify: `stackcopy.py`
- Test: `tests/test_canon_import_mode.py`

**Interfaces:**
- Consumes: `build_canon_import_plan`, `dng_converter.preflight`, `safe_file_operation` (`stackcopy.py:1818`), `ensure_directory_once` (`:803`).
- Produces: `run_canon_import(args) -> int` (process exit code).

Order matters: **preflight before any file is written.** A converter that cannot read the camera must fail in the first second, not at hour three.

- [ ] **Step 1: Write the failing test**

```python
# append to tests/test_canon_import_mode.py

def test_dry_run_writes_nothing_and_reports_groups(plan, tmp_path, monkeypatch, capsys):
    monkeypatch.setattr(stackcopy, "build_canon_import_plan", lambda *a, **k: plan)
    monkeypatch.setattr(
        stackcopy.dng_converter, "find_dng_converter", lambda: "/fake/converter"
    )
    monkeypatch.setattr(
        stackcopy.dng_converter, "preflight", lambda *a, **k: None
    )
    args = stackcopy.parse_arguments(
        ["--canon-import", str(tmp_path / "src"), str(tmp_path / "dst"), "--dry"]
    )
    exit_code = stackcopy.run_canon_import(args)
    output = capsys.readouterr().out
    assert exit_code == 0
    assert "101" in output
    assert not (tmp_path / "dst").exists()


def test_import_refuses_when_converter_cannot_read_the_camera(tmp_path, monkeypatch, capsys):
    monkeypatch.setattr(
        stackcopy.dng_converter, "find_dng_converter", lambda: "/fake/converter"
    )
    monkeypatch.setattr(
        stackcopy.dng_converter, "preflight",
        lambda *a, **k: "Adobe DNG Converter (13.0) could not convert IMG_0001.CR3",
    )
    args = stackcopy.parse_arguments(
        ["--canon-import", str(tmp_path / "src"), str(tmp_path / "dst")]
    )
    (tmp_path / "src").mkdir()
    exit_code = stackcopy.run_canon_import(args)
    assert exit_code != 0
    assert "13.0" in capsys.readouterr().out
    assert not (tmp_path / "dst").exists()


def test_no_dng_skips_the_converter_entirely(tmp_path, monkeypatch):
    called = []
    monkeypatch.setattr(
        stackcopy.dng_converter, "find_dng_converter",
        lambda: called.append("looked") or None,
    )
    args = stackcopy.parse_arguments(
        ["--canon-import", str(tmp_path / "src"), str(tmp_path / "dst"),
         "--no-dng", "--dry"]
    )
    (tmp_path / "src").mkdir()
    stackcopy.run_canon_import(args)
    assert called == []
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/test_canon_import_mode.py -k "dry_run or refuses or no_dng" -v`
Expected: FAIL with `AttributeError: module 'stackcopy' has no attribute 'run_canon_import'`

- [ ] **Step 3: Write minimal implementation**

```python
# stackcopy.py
import dng_converter


def run_canon_import(args) -> int:
    source_dir, dest_dir = args.canon_import
    singles_dir = args.singles_dir or os.path.join(
        os.path.dirname(os.path.abspath(dest_dir)), "Single Images"
    )

    paths = [
        os.path.join(root, name)
        for root, _dirs, names in os.walk(source_dir)
        for name in names
        if os.path.splitext(name)[1].lower() in (".cr3", ".jpg", ".jpeg")
    ]
    if not paths:
        print(f"No Canon files found in '{display_path(source_dir)}'.")
        return 0

    records = read_canon_stack_metadata(paths)
    plan = build_canon_import_plan(records, dest_dir, singles_dir, source_dir)

    frame_total = sum(len(s.frame_sources) for s in plan.stacks)
    print(
        f"{len(plan.stacks)} stack(s), {frame_total} frame(s), "
        f"{len(plan.singles)} loose CR3 file(s)."
    )
    for stack in plan.stacks:
        if args.verbose:
            print(f"  {display_path(stack.dest_dir)}  "
                  f"{len(stack.frame_sources)} frame(s)")

    # Preflight before anything is written: a converter that cannot read this
    # camera must fail in the first second, not at hour three.
    converter = None
    if not args.no_dng:
        converter = dng_converter.find_dng_converter()
        if converter is None:
            print(
                "Adobe DNG Converter was not found. Install it from "
                f"{dng_converter.DNG_CONVERTER_DOWNLOAD_URL}, set "
                f"{dng_converter.DNG_CONVERTER_ENV}, or pass --no-dng."
            )
            return 1
        sample = plan.stacks[0].frame_sources[0] if plan.stacks else None
        if sample:
            problem = dng_converter.preflight(converter, sample)
            if problem:
                print(problem)
                return 1

    if args.dry_run:
        print("Dry run: nothing was copied or converted.")
        return 0

    created_dirs: set[str] = set()
    failures = 0
    for stack in plan.stacks:
        ensure_directory_once(stack.dest_dir, created_dirs, args.dry_run)
        safe_file_operation(stack.jpg_src, stack.jpg_dest, "copy", args)
    if converter is not None:
        results = dng_converter.convert_groups(
            converter,
            [(s.dest_dir, list(s.frame_sources)) for s in plan.stacks],
            workers=args.dng_jobs,
        )
        for result in results:
            if not result.ok:
                failures += 1
                print(f"  Failed: {display_path(result.dest_dir)}: {result.error}")
    else:
        for stack in plan.stacks:
            for source in stack.frame_sources:
                safe_file_operation(
                    source,
                    os.path.join(stack.dest_dir, os.path.basename(source)),
                    "copy",
                    args,
                )

    for single_dest in plan.singles:
        ensure_directory_once(os.path.dirname(single_dest), created_dirs, args.dry_run)

    print(f"Done. {len(plan.stacks) - failures} stack(s) written, {failures} failed.")
    return 1 if failures else 0
```

Dispatch it alongside the other modes, where `operation_mode` is chosen:

```python
    if args.canon_import:
        sys.exit(run_canon_import(args))
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python -m pytest tests/ -v`
Expected: PASS, whole suite.

- [ ] **Step 5: Commit**

```bash
git add stackcopy.py tests/test_canon_import_mode.py
git commit -m "feat(canon): execute imports with preflight and dry-run"
```

---

### Task 10: Real-card verification and documentation

**Files:**
- Modify: `README.md`, `ChangeLog.md`

- [ ] **Step 1: Run the real dry run against the card**

Requires the updated Adobe DNG Converter (15.1+) installed.

```bash
python stackcopy.py --canon-import "H:/DCIM/108EOSR6" "F:/Stacked Images" \
    --singles-dir "F:/Single Images" --dry --verbose
```

Expected: 101 stacks; `IMG_0040` 25 frames; `IMG_0096` 25 frames; `IMG_0014` reaching into `107EOSR6`; loose CR3s listed as singles. Nothing written.

Confirm the three groups the spec marked inferred (`IMG_0175`, `IMG_0929`, `IMG_2412`) resolve to 25 frames. If any does not, stop and report before converting.

- [ ] **Step 2: Convert one stack for real and open it in Helicon**

```bash
python stackcopy.py --canon-import "H:/DCIM/108EOSR6" "F:/Stacked Images test" \
    --singles-dir "F:/Single Images test" --dng-jobs 4 --verbose
```

Interrupt after the first few stacks. Verify a stack folder holds N `.dng` files plus the composite JPG, and that Helicon Focus 8 opens the DNGs. Re-run the same command and confirm completed stacks are skipped rather than reconverted.

- [ ] **Step 3: Update README.md**

Add `--canon-import` to "The five modes" (retitle to "The six modes"), extend the supported-RAW list with `.cr3`, and add a Canon subsection under "How it works" covering: the `FocusBracketingDepthComposite` gate, the timestamp anchor, why `FocusBracketingImageCount` is never a boundary, the relative cadence fallback, and the Adobe DNG Converter requirement with its 15.1 floor and the per-camera-model explanation.

- [ ] **Step 4: Update ChangeLog.md**

Add an entry under a new version heading describing the Canon depth-composite mode and DNG conversion. `.github/workflows/build-gui.yml` validates that the tag, `STACKCOPY_VERSION` and `ChangeLog.md` agree, so bump `STACKCOPY_VERSION` (`stackcopy.py:32`) in the same commit.

- [ ] **Step 5: Run the full suite**

Run: `python -m pytest tests/ -v`
Expected: PASS, all tests including the pre-existing Olympus suite.

- [ ] **Step 6: Commit**

```bash
git add README.md ChangeLog.md stackcopy.py
git commit -m "docs(canon): document --canon-import and DNG requirements"
```

---

## Self-Review

**Spec coverage.** Component 1 detection → Tasks 1–5 (gate T2, anchor T2, cadence T3, cross-folder T4, `ImageCount` note T2, ExifTool degradation T5). Component 2 conversion → Tasks 6–7 (discovery, options, failure isolation, resume, parallelism) plus the preflight added after the 13.0 finding. Component 3 layout/CLI → Tasks 8–9. Component 4 testing → distributed across every task, with the fixture in Task 1. Scope (108EOSR6 with reach into 107EOSR6) → Task 4 and Task 10 Step 1.

**Placeholders.** None. Every code step carries the actual code; every test step carries the actual assertions.

**Type consistency.** `CanonFrame` fields (`folder`, `number`, `ext`, `name`, `timestamp_text`, `timestamp`, `depth_composite`, `image_count`) are used identically in Tasks 2–5 and 8. `CanonStackGroup.frames` is a tuple everywhere. `ConversionResult` fields match between Task 7's definition and Task 9's consumption (`.ok`, `.error`, `.dest_dir`). `convert_command` is defined in Task 6 and used in Task 7.

**One deviation from the spec, recorded deliberately:** conversion lives in `dng_converter.py` rather than inside `canon_stacks.py`, keeping the grouping module pure. The preflight requirement in Task 6 is new since the spec was written, added after Adobe DNG Converter 13.0 was found to reject every file from this camera.
