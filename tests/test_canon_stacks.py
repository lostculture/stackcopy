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
    """A RAW+JPG pair shares its number and is an ordinary shot.

    IMG_0004.CR3 shares the composite's timestamp, so without the
    same-number guard the anchor would incorrectly match it and produce a
    (wrong) one-frame group.  This gives the guard something real to block.
    """
    frames = [
        canon_stacks.CanonFrame("108EOSR6", 4, ".cr3", "IMG_0004.CR3",
                                timestamp=100.0, depth_composite=True),
        canon_stacks.CanonFrame("108EOSR6", 5, ".cr3", "IMG_0005.CR3",
                                timestamp=100.0, depth_composite=True),
        canon_stacks.CanonFrame("108EOSR6", 5, ".jpg", "IMG_0005.JPG",
                                timestamp=100.0, depth_composite=True),
    ]
    assert canon_stacks.group_canon_stacks(frames) == []


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
    """A stale previous-folder frame is not borrowed just because its
    timestamp text coincidentally equals the composite's anchor.

    The brief's version of this test gave the old frame a *different*
    timestamp_text ("old" vs. the jpg's "unmatched"), so `_anchor_cut` on
    the combined run never found a match at all -- the assertion held
    regardless of `MAX_CROSS_FOLDER_GAP_SECONDS`, even with that guard
    deleted outright. Confirmed by temporarily removing the guard: the
    brief's version still passed (vacuous). Giving the old frame and the
    jpg the *same* timestamp_text makes `_extend_across_folder` actually
    find and consider borrowing it, so the huge time gap is what has to
    reject it.
    """
    old = canon_stacks.CanonFrame(
        "107EOSR6", 9999, ".cr3", "IMG_9999.CR3",
        timestamp_text="match", timestamp=0.0, depth_composite=True,
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
        timestamp_text="match", timestamp=100010.0, depth_composite=True,
    )
    group = canon_stacks.group_canon_stacks(frames + [jpg])[0]
    assert all(f.folder == "108EOSR6" for f in group.frames)


def test_numbering_gap_bounds_a_run(records):
    """IMG_0902 was deleted in camera; IMG_0929's stack must not span it."""
    group = groups_by_output(canon_stacks.group_canon_stacks(records))[
        "IMG_0929.JPG"
    ]
    numbers = [f.number for f in group.frames]
    assert 902 not in numbers
    assert numbers == list(range(min(numbers), 929))
