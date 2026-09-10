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
    # Scoped to 108EOSR6: the 107EOSR6 tail (kept for a later cross-folder
    # rollover test) contributes one extra JPG (IMG_9987.JPG) that would
    # otherwise break an exact global count.
    jpgs = [r for r in records if r.folder == "108EOSR6" and r.ext == ".jpg"]
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


@pytest.mark.xfail(strict=True, reason="cadence fallback lands in Task 3")
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
