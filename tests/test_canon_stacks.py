# tests/test_canon_stacks.py
import json
import subprocess
from pathlib import Path

import pytest

import canon_stacks
import stackcopy

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


def test_cadence_notes_frames_dropped_for_a_missing_timestamp():
    """Fix round 2, item 7: silently shorter stacks must announce themselves.

    _cadence_cut can only place a frame it can time, so a frame whose
    SubSecDateTimeOriginal could not be read (a partial ExifTool failure on
    one file of a batch) is dropped from the middle of the stack. Dropping
    it is the right call -- there is nothing to reason with -- but doing it
    without a word leaves the user with a stack quietly missing a slice of
    the focus sweep and no hint why.
    """
    records = _run([0.07] * 6)
    frames = [r for r in records if r.ext == ".cr3"]
    blind = frames[3]
    records[records.index(blind)] = canon_stacks.CanonFrame(
        blind.folder, blind.number, blind.ext, blind.name,
        timestamp_text=None, timestamp=None,
    )

    group = canon_stacks.group_canon_stacks(records)[0]

    assert group.confidence == canon_stacks.CONFIDENCE_CADENCE
    assert blind.name not in [f.name for f in group.frames]
    assert any("no timestamp" in note for note in group.notes), group.notes


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
    """Regression pin, not a gap-stop test.

    IMG_0929's stack anchors at IMG_0904, which sits *after* the deleted
    IMG_0902 -- so `_anchor_cut` discards everything before position 904
    regardless of whether `_preceding_run` itself respects the numbering
    gap. This only pins IMG_0929's exact 25-frame result across the Task 4
    refactor. See `test_preceding_run_stops_at_a_numbering_gap` for a test
    that actually exercises the gap-stop.
    """
    group = groups_by_output(canon_stacks.group_canon_stacks(records))[
        "IMG_0929.JPG"
    ]
    numbers = [f.number for f in group.frames]
    assert 902 not in numbers
    assert numbers == list(range(min(numbers), 929))


def test_preceding_run_stops_at_a_numbering_gap():
    """A genuine missing file number must not be walked through.

    Numbers 1-3 and 5-7 are present with a hole at 4 (as if IMG_0004 were
    deleted in camera); the composite at 8 has no timestamp match, so this
    goes through the cadence fallback over whatever `_preceding_run`
    returns. The time gap across the hole (0.46s, one interval wider than
    the uniform 0.23s cadence) is comfortably inside cadence's 4x
    tolerance, so if `_preceding_run` failed to stop at the missing 4 and
    walked through to 1-3, cadence would happily include them too -- this
    is what makes the test actually exercise the numeric gap-stop, rather
    than relying on an anchor match to discard the earlier frames anyway.
    """
    folder = "200TESTA"
    numbers_times = [
        (1, 0.0), (2, 0.23), (3, 0.46),
        (5, 0.92), (6, 1.15), (7, 1.38),
    ]
    frames = [
        canon_stacks.CanonFrame(
            folder, n, ".cr3", f"IMG_{n:04d}.CR3",
            timestamp_text=f"t{n}", timestamp=t, depth_composite=True,
        )
        for n, t in numbers_times
    ]
    jpg = canon_stacks.CanonFrame(
        folder, 8, ".jpg", "IMG_0008.JPG",
        timestamp_text="unmatched", timestamp=1.61, depth_composite=True,
    )
    group = canon_stacks.group_canon_stacks(frames + [jpg])[0]
    numbers = [f.number for f in group.frames]
    assert numbers == [5, 6, 7]


def test_composite_with_no_local_frames_reaches_back():
    """The composite can be the very first file in its folder.

    The camera writes the composite after its last frame, so a burst whose
    last frame is IMG_9999 puts the composite at IMG_0001.JPG in the next
    folder with zero local preceding CR3s.
    """
    prev = [
        canon_stacks.CanonFrame(
            "107EOSR6", n, ".cr3", f"IMG_{n:04d}.CR3",
            timestamp_text=f"t{n}", timestamp=1000.0 + (n - 9997) * 0.23,
            depth_composite=True,
        )
        for n in (9997, 9998, 9999)
    ]
    jpg = canon_stacks.CanonFrame(
        "108EOSR6", 1, ".jpg", "IMG_0001.JPG",
        timestamp_text="t9997", timestamp=1000.69, depth_composite=True,
    )
    group = canon_stacks.group_canon_stacks(prev + [jpg])[0]
    assert group.confidence == canon_stacks.CONFIDENCE_ANCHOR_CROSSFOLDER
    assert [f.folder for f in group.frames] == ["107EOSR6"] * 3
    assert [f.number for f in group.frames] == [9997, 9998, 9999]


def test_canon_metadata_read_degrades_without_exiftool(monkeypatch, capsys):
    """Without ExifTool, frames still come back -- described from their
    filenames alone -- and ExifTool is never actually invoked."""
    monkeypatch.setattr(
        stackcopy, "exiftool_info",
        lambda: stackcopy.ExifToolInfo(
            executable=None, version=None, version_tuple=None,
            source=stackcopy.ExifToolSource.NONE,
        ),
    )

    def _run_should_not_be_called(*args, **kwargs):
        raise AssertionError(
            "subprocess.run must not be called when ExifTool is unavailable"
        )

    monkeypatch.setattr(subprocess, "run", _run_should_not_be_called)

    frames = stackcopy.read_canon_stack_metadata(
        ["/card/108EOSR6/IMG_0001.CR3", "/card/108EOSR6/IMG_0002.JPG"]
    )

    # Files are still parsed from their names (folder/number/ext survive)...
    assert len(frames) == 2
    by_number = {f.number: f for f in frames}
    assert by_number[1].folder == "108EOSR6"
    assert by_number[1].ext == ".cr3"
    assert by_number[2].ext == ".jpg"
    # ...but every metadata-derived field is genuinely unknown, not merely
    # unset by coincidence -- a broken degradation path that fabricated a
    # value here (e.g. defaulting depth_composite to False) would fail this.
    assert all(
        f.depth_composite is None
        and f.timestamp_text is None
        and f.timestamp is None
        and f.image_count is None
        for f in frames
    )
    # Exactly one concise warning, naming the degraded file count.
    output = capsys.readouterr().out
    assert output.count("Warning:") == 1
    assert "2 file" in output


def test_canon_metadata_read_parses_batch(monkeypatch, capsys):
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
    assert frames[0].timestamp_text == "2026:07:31 10:44:02.03-04:00"
    # A fully-reported batch is not degraded, so no warning is printed.
    assert capsys.readouterr().out == ""


def test_canon_metadata_is_parsed_regardless_of_exiftool_exit_status(
    monkeypatch, capsys
):
    """ExifTool's exit status is not a verdict on the batch's JSON.

    ExifTool exits non-zero when *any* file in a batch is unreadable, while
    still printing complete, valid records for every file it could read.
    Treating a non-zero exit as "discard this batch" would throw away
    hundreds of good records because one file on the card had a bad sector,
    and would silently degrade every one of them to filename-only -- which
    drops the composite flags that grouping depends on. Parse the stdout;
    the per-file reconciliation below already catches anything genuinely
    absent from it.
    """
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
        # Exit 1 -- as ExifTool really does when a sibling file in the batch
        # is unreadable -- with entirely valid stdout for the file it read.
        lambda *a, **k: subprocess.CompletedProcess(a, 1, payload, "Error: ..."),
    )
    frames = stackcopy.read_canon_stack_metadata(
        ["/card/108EOSR6/IMG_0002.JPG"]
    )
    assert len(frames) == 1
    assert frames[0].depth_composite is True, (
        "the record was discarded because of the exit status"
    )
    assert frames[0].image_count == 25
    assert frames[0].timestamp_text == "2026:07:31 10:44:02.03-04:00"
    assert capsys.readouterr().out == ""


def test_composite_with_no_local_frames_respects_time_limit():
    """An unrelated old frame at the adjacent number, in the previous
    folder, is not borrowed just because it shares the composite's
    timestamp text -- even when there is no local run to compare against,
    the composite's own timestamp bounds the borrow."""
    old = canon_stacks.CanonFrame(
        "107EOSR6", 9999, ".cr3", "IMG_9999.CR3",
        timestamp_text="match", timestamp=0.0, depth_composite=True,
    )
    jpg = canon_stacks.CanonFrame(
        "108EOSR6", 1, ".jpg", "IMG_0001.JPG",
        timestamp_text="match", timestamp=100000.0, depth_composite=True,
    )
    assert canon_stacks.group_canon_stacks([old, jpg]) == []
