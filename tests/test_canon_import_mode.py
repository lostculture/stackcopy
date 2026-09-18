import io
import json
import os
import subprocess
import sys
import tempfile
import unittest
from contextlib import redirect_stdout
from pathlib import Path
from unittest import mock

import pytest

import canon_stacks
import helicon_focus
import stackcopy
import stackcopy_gui as gui  # noqa: E402  (for the validator round-trip test)

ROOT = Path(__file__).resolve().parents[1]

FIXTURE = Path(__file__).parent / "fixtures" / "canon_108eosr6.json"

# The fixture's card has two camera folders: 108EOSR6 (what the user is
# importing) and 107EOSR6 (the tail end of a previous session, present only
# because IMG_0014's stack spans the folder rollover). Only 108EOSR6 is a
# target: 107EOSR6 is borrowable context, never a source of its own stacks.
TARGET_FOLDERS = {"108EOSR6"}


def _one_stack_and_single_plan():
    """A plan with one 25-frame stack and one loose CR3.

    Named distinctly from the unrelated ``_one_stack_plan(tmp_path)`` fixture
    used by the Task 11 Helicon Focus tests further down this file (a
    real-filesystem, two-frame stack with no singles) -- a module-level
    `def` would otherwise silently shadow whichever of the two is defined
    later, breaking the other's callers.
    """
    frames = tuple(
        os.path.join("108EOSR6", f"IMG_{n:04d}.CR3") for n in range(15, 40)
    )
    stack = stackcopy.CanonPlannedStack(
        dest_dir=os.path.join(r"F:\Stacked", "2026-07-31", "IMG_0040"),
        jpg_src=os.path.join("108EOSR6", "IMG_0040.JPG"),
        jpg_dest=os.path.join(r"F:\Stacked", "2026-07-31", "IMG_0040", "IMG_0040.JPG"),
        frame_sources=frames,
    )
    return stackcopy.CanonImportPlan(
        stacks=(stack,),
        singles=(os.path.join(r"F:\Singles", "2026-07-31", "IMG_0067.CR3"),),
        single_sources=(os.path.join("108EOSR6", "IMG_0067.CR3"),),
        unresolved=(),
    )


@pytest.fixture(scope="module")
def plan():
    payload = json.loads(FIXTURE.read_text(encoding="utf-8"))
    records = canon_stacks.records_from_exiftool_json(payload)
    return stackcopy.build_canon_import_plan(
        records, r"F:\Stacked Images", r"F:\Single Images",
        target_folders=TARGET_FOLDERS,
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
    """No source CR3 is claimed by more than one destination.

    Both sides of this check must live in the *source* namespace: comparing
    a stack's source frames against ``plan.singles`` (destination paths, all
    rooted at a completely different tree) can never collide and the check
    would be vacuous. ``single_sources`` is the source-path counterpart of
    ``singles`` and is what makes the comparison meaningful.
    """
    seen = set()
    for stack in plan.stacks:
        for source in stack.frame_sources:
            assert source not in seen
            seen.add(source)
    for source in plan.single_sources:
        assert source not in seen
        seen.add(source)


def test_context_folder_composite_produces_no_stack_and_no_singles(plan):
    """IMG_9987.JPG sits in 107EOSR6, a context-only (non-target) folder.

    canon_stacks can only match its frames by the weakest (cadence)
    heuristic, and its own reported frame count doesn't match what got
    grouped -- exactly the profile of a truncated, out-of-scope composite
    that must never become an import folder. Its frames must not leak into
    singles either: they were never part of what the user asked to import.
    """
    assert all(not s.jpg_src.endswith("IMG_9987.JPG") for s in plan.stacks)
    assert all(not g.output_jpg.name == "IMG_9987.JPG" for g in plan.unresolved)
    assert all(not source.startswith("107EOSR6") for source in plan.single_sources)


def test_context_folder_frame_is_still_borrowable_by_a_target_stack(plan):
    """A non-target folder is context, not quarantine.

    IMG_0014's stack reaches into 107EOSR6 for 12 of its 25 frames to
    complete the sequence across the folder rollover -- being out of scope
    for its own composites must not stop 107EOSR6's frames from completing
    a target-folder stack.
    """
    stack = next(s for s in plan.stacks if s.jpg_src.endswith("IMG_0014.JPG"))
    borrowed = [f for f in stack.frame_sources if f.startswith("107EOSR6")]
    assert len(borrowed) == 12
    assert len(stack.frame_sources) == 25


def test_cadence_confidence_composite_inside_target_folder_is_still_planned():
    """A genuinely ambiguous but in-scope stack must never be dropped.

    Regression guard: routing on canon_stacks' *confidence* tier instead of
    on folder *scope* would treat this exactly like the truncated,
    out-of-scope IMG_9987.JPG decoy (also cadence-only) and silently drop a
    real, in-scope stack from the plan with no filename ever surfaced to the
    user -- the one failure mode this feature exists to prevent. This
    composite is built synthetically so cadence-matching (no frame's
    timestamp equals the composite's own) is guaranteed regardless of what
    the checked-in fixture happens to contain.
    """
    records = [
        canon_stacks.CanonFrame(
            "300TARGT", 301 + i, ".cr3", f"IMG_{301 + i:04d}.CR3",
            timestamp=1000.0 + i, depth_composite=True,
        )
        for i in range(10)
    ]
    records.append(
        canon_stacks.CanonFrame(
            "300TARGT", 311, ".jpg", "IMG_0311.JPG",
            timestamp=1010.5, depth_composite=True, image_count=10,
        )
    )
    plan = stackcopy.build_canon_import_plan(
        records, r"F:\Dst", r"F:\Singles", target_folders={"300TARGT"}
    )
    stack = next(
        (s for s in plan.stacks if s.jpg_src.endswith("IMG_0311.JPG")), None
    )
    assert stack is not None, "cadence-matched but in-scope stack was dropped"
    assert len(stack.frame_sources) == 10
    assert not plan.unresolved


def test_unresolved_composite_is_named_and_not_double_counted_into_singles():
    """A target-folder composite with genuinely no frames at all.

    IMG_0400.JPG is the first file in an isolated target folder: there is no
    preceding CR3 to anchor or cadence-match against, and no previous folder
    to borrow from, so canon_stacks produces no group for it at all. It must
    still be named in ``unresolved`` -- a bare count gives the user nothing
    to act on -- and its absence of frames must not somehow inflate
    ``singles`` (today that guarantee would otherwise rest only on the
    aggregate frame-accounting numbers over the real fixture).
    """
    records = [
        canon_stacks.CanonFrame(
            "400TARGT", 400, ".jpg", "IMG_0400.JPG",
            timestamp=2000.0, depth_composite=True, image_count=5,
        ),
        canon_stacks.CanonFrame(
            "400TARGT", 401, ".cr3", "IMG_0401.CR3", timestamp=2005.0,
        ),
    ]
    plan = stackcopy.build_canon_import_plan(
        records, r"F:\Dst", r"F:\Singles", target_folders={"400TARGT"}
    )
    assert not plan.stacks
    assert [g.output_jpg.name for g in plan.unresolved] == ["IMG_0400.JPG"]
    singles = [Path(p).name for p in plan.singles]
    assert singles == ["IMG_0401.CR3"]


# --- Task 9: run_canon_import (execution, dry-run, preflight wiring) -------


def test_destination_date_comes_from_the_cameras_own_exif_text():
    """Fix round 2, item 2: the date must not be re-localised.

    ``record.timestamp`` is an absolute instant carrying the *camera's* UTC
    offset. Rendering it with ``datetime.fromtimestamp`` re-expresses it in
    the *importing machine's* timezone, so where a frame lands depends on
    which computer you plug the card into -- a -04:00 card imported on a UTC
    machine files everything shot after 20:00 into the next day.

    These two composites are the SAME absolute instant written with two
    different camera offsets, so ``fromtimestamp`` is guaranteed to give
    them both the same folder no matter what timezone this test machine is
    in. Only reading the EXIF text keeps them apart, which is what the two
    assertions below demand: reintroducing ``fromtimestamp`` collapses them
    onto one date and fails.
    """
    late = canon_stacks.CanonFrame(
        "108EOSR6", 2, ".jpg", "IMG_0002.JPG",
        timestamp_text="2026:07:31 23:00:00.00-04:00", depth_composite=True,
    )
    early = canon_stacks.CanonFrame(
        "108EOSR6", 4, ".jpg", "IMG_0004.JPG",
        timestamp_text="2026:08:01 05:00:00.00+02:00", depth_composite=True,
    )
    assert late.timestamp == early.timestamp, "same instant, different offsets"

    assert stackcopy._canon_record_date(late) == "2026-07-31"
    assert stackcopy._canon_record_date(early) == "2026-08-01"


def test_stack_folder_is_dated_by_the_camera_not_the_importing_machine(tmp_path):
    """The same defect, seen where it actually bites: the planned path.

    Two one-frame stacks shot at the SAME absolute instant by two cameras on
    different UTC offsets. ``datetime.fromtimestamp`` renders both in this
    machine's timezone and therefore files them under one date, whatever
    that date happens to be here; only the EXIF text keeps them apart. That
    is what makes this test independent of the machine running it.
    """
    records = []
    for number, text in (
        (1, "2026:07:31 23:00:00.00-04:00"),
        (11, "2026:08:01 05:00:00.00+02:00"),
    ):
        records.append(canon_stacks.CanonFrame(
            "108EOSR6", number, ".cr3", f"IMG_{number:04d}.CR3",
            timestamp_text=text,
        ))
        records.append(canon_stacks.CanonFrame(
            "108EOSR6", number + 1, ".jpg", f"IMG_{number + 1:04d}.JPG",
            timestamp_text=text, depth_composite=True,
        ))
    assert records[0].timestamp == records[2].timestamp, (
        "the two stacks must be the same instant, or this proves nothing"
    )

    plan = stackcopy.build_canon_import_plan(
        records, str(tmp_path / "dst"), str(tmp_path / "singles"),
    )

    assert len(plan.stacks) == 2
    dates = {Path(s.dest_dir).parent.name for s in plan.stacks}
    assert dates == {"2026-07-31", "2026-08-01"}


def test_dry_run_writes_nothing_and_reports_groups(plan, tmp_path, monkeypatch, capsys):
    # The source dir must actually exist: run_canon_import now fails fast on
    # a nonexistent source path (fix round 1, item 1), and this test's intent
    # is "dry run writes nothing" -- not "a nonexistent path is tolerated" --
    # so a valid, merely-empty directory is the correct setup here. What's
    # actually scanned from it is irrelevant since build_canon_import_plan
    # itself is mocked below.
    (tmp_path / "src").mkdir()
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


def test_nonexistent_source_path_exits_nonzero_and_names_the_path(tmp_path, capsys):
    """Fix round 1, item 1: a typo'd source folder must not exit 0.

    Before this fix, a mistyped path (e.g. missing the trailing digit of a
    camera folder name) and a correctly-typed but genuinely empty folder
    were indistinguishable: both printed "No Canon files found" and
    returned 0, so a script checking exit codes -- or a user glancing at the
    output -- could believe a bad path had run cleanly and simply found
    nothing.
    """
    missing = tmp_path / "src" / "108EOSR6"  # never created
    args = stackcopy.parse_arguments(
        ["--canon-import", str(missing), str(tmp_path / "dst")]
    )
    exit_code = stackcopy.run_canon_import(args)
    output = capsys.readouterr().out
    assert exit_code != 0
    assert "108EOSR6" in output, "the message must name the offending path"
    assert not (tmp_path / "dst").exists()


def test_existing_but_empty_source_directory_still_exits_zero(tmp_path, capsys):
    """The other half of the item-1 distinction: a real, empty folder is a
    real (non-error) answer, not a typo -- it must keep exiting 0 with a
    "no Canon files" message, unlike a path that does not exist at all.
    """
    empty = tmp_path / "src" / "108EOSR6"
    empty.mkdir(parents=True)
    args = stackcopy.parse_arguments(
        ["--canon-import", str(empty), str(tmp_path / "dst")]
    )
    exit_code = stackcopy.run_canon_import(args)
    output = capsys.readouterr().out
    assert exit_code == 0
    assert "No Canon files found" in output


def test_no_dng_skips_the_converter_entirely(tmp_path, monkeypatch):
    """--no-dng must stop converter discovery before it ever starts.

    Fix round 2, item 6: this test used to point at an EMPTY source
    directory, so the plan had no stacks, so ``sample`` was None and
    ``find_dng_converter()`` was unreachable *regardless* of --no-dng --
    the assertion held whether or not the gate existed. A genuine one-frame
    stack is what makes the gate the only thing standing between this run
    and converter discovery.
    """
    src = tmp_path / "src" / "100EOSR6"
    src.mkdir(parents=True)
    (src / "IMG_0001.CR3").write_bytes(b"frame")
    (src / "IMG_0002.JPG").write_bytes(b"composite")
    records = [
        canon_stacks.CanonFrame("100EOSR6", 1, ".cr3", "IMG_0001.CR3"),
        canon_stacks.CanonFrame(
            "100EOSR6", 2, ".jpg", "IMG_0002.JPG", depth_composite=True
        ),
    ]
    monkeypatch.setattr(stackcopy, "read_canon_stack_metadata", lambda paths: records)

    called = []
    monkeypatch.setattr(
        stackcopy.dng_converter, "find_dng_converter",
        lambda: called.append("looked") or None,
    )
    args = stackcopy.parse_arguments(
        ["--canon-import", str(src), str(tmp_path / "dst"), "--no-dng", "--dry"]
    )
    exit_code = stackcopy.run_canon_import(args)

    assert exit_code == 0
    assert called == [], "--no-dng must not reach converter discovery"


def test_without_no_dng_the_same_stack_does_reach_converter_discovery(
    tmp_path, monkeypatch
):
    """The control for the test above: prove the gate is what did the work.

    Identical setup, --no-dng removed. If discovery is still not reached
    here then the test above proves nothing about --no-dng.
    """
    src = tmp_path / "src" / "100EOSR6"
    src.mkdir(parents=True)
    (src / "IMG_0001.CR3").write_bytes(b"frame")
    (src / "IMG_0002.JPG").write_bytes(b"composite")
    records = [
        canon_stacks.CanonFrame("100EOSR6", 1, ".cr3", "IMG_0001.CR3"),
        canon_stacks.CanonFrame(
            "100EOSR6", 2, ".jpg", "IMG_0002.JPG", depth_composite=True
        ),
    ]
    monkeypatch.setattr(stackcopy, "read_canon_stack_metadata", lambda paths: records)

    called = []
    monkeypatch.setattr(
        stackcopy.dng_converter, "find_dng_converter",
        lambda: called.append("looked") or "/fake/converter",
    )
    monkeypatch.setattr(stackcopy.dng_converter, "preflight", lambda *a, **k: None)
    args = stackcopy.parse_arguments(
        ["--canon-import", str(src), str(tmp_path / "dst"), "--dry"]
    )
    stackcopy.run_canon_import(args)

    assert called == ["looked"]


def test_import_refuses_when_converter_cannot_read_the_camera(
    tmp_path, monkeypatch, capsys
):
    """A converter that fails its trial conversion must stop before any write.

    Deliberately not the brief's original draft of this test (a totally
    empty source directory). Preflight is only meaningful -- and, in this
    implementation, only run -- when there is an actual frame to sample; an
    empty scan produces an empty plan with no frame at all, so it would
    never reach the converter and the test would fail for a reason that has
    nothing to do with what it is meant to guard. This uses a real
    one-frame stack instead, so preflight is exercised by the ordinary
    scan-plan-preflight path.
    """
    src = tmp_path / "src" / "100EOSR6"
    src.mkdir(parents=True)
    (src / "IMG_0001.CR3").write_bytes(b"frame")
    (src / "IMG_0002.JPG").write_bytes(b"composite")

    records = [
        canon_stacks.CanonFrame("100EOSR6", 1, ".cr3", "IMG_0001.CR3"),
        canon_stacks.CanonFrame(
            "100EOSR6", 2, ".jpg", "IMG_0002.JPG", depth_composite=True
        ),
    ]
    monkeypatch.setattr(stackcopy, "read_canon_stack_metadata", lambda paths: records)
    monkeypatch.setattr(
        stackcopy.dng_converter, "find_dng_converter", lambda: "/fake/converter"
    )
    monkeypatch.setattr(
        stackcopy.dng_converter, "preflight",
        lambda *a, **k: "Adobe DNG Converter (13.0) could not convert IMG_0001.CR3",
    )

    args = stackcopy.parse_arguments(
        ["--canon-import", str(src), str(tmp_path / "dst")]
    )
    exit_code = stackcopy.run_canon_import(args)
    output = capsys.readouterr().out

    assert exit_code != 0
    assert "13.0" in output
    assert not (tmp_path / "dst").exists()


def test_loose_cr3s_are_actually_copied_not_merely_planned(tmp_path, monkeypatch):
    """R2 regression: a single must be copied, not merely have its folder made.

    The brief's original draft called ensure_directory_once on each single's
    destination directory and stopped there -- it would create an empty
    folder and silently leave the CR3 itself uncopied. Asserting on the
    destination *file's* bytes (not just the directory's existence) is what
    makes this test actually catch that.
    """
    single_src = tmp_path / "src" / "100EOSR6" / "IMG_0005.CR3"
    single_src.parent.mkdir(parents=True)
    single_src.write_bytes(b"loose-frame-bytes")

    single_dest = tmp_path / "dst" / "Single Images" / "2026-01-01" / "IMG_0005.CR3"
    fake_plan = stackcopy.CanonImportPlan(
        stacks=(),
        singles=(str(single_dest),),
        single_sources=(str(single_src),),
        unresolved=(),
    )
    monkeypatch.setattr(
        stackcopy, "build_canon_import_plan", lambda *a, **k: fake_plan
    )

    args = stackcopy.parse_arguments(
        ["--canon-import", str(single_src.parent), str(tmp_path / "dst")]
    )
    exit_code = stackcopy.run_canon_import(args)

    assert exit_code == 0
    assert single_dest.exists(), (
        "the single's destination folder must contain the file itself, "
        "not merely exist"
    )
    assert single_dest.read_bytes() == b"loose-frame-bytes"


def test_conversion_requested_but_no_sample_frame_announces_raw_fallback(
    tmp_path, monkeypatch, capsys
):
    """Fix round 1, item 2: falling back to a raw copy must never be silent.

    The real grouping algorithm never produces a stack with zero frames (a
    zero-frame composite becomes `unresolved`, not a stack -- see
    build_canon_import_plan's docstring), but the plan is a public seam
    another caller can substitute (as this test, and several others in this
    file, already do), and this guards that seam: if conversion was
    requested (no --no-dng) and there is real stack work, yet nothing
    exists anywhere to preflight the converter against, the fallback to
    copying raw files must be said out loud rather than look identical to
    --no-dng with no explanation.
    """
    stack_dir = tmp_path / "dst" / "2026-01-01" / "IMG_0001"
    fake_plan = stackcopy.CanonImportPlan(
        stacks=(
            stackcopy.CanonPlannedStack(
                dest_dir=str(stack_dir),
                jpg_src=str(tmp_path / "IMG_0001.JPG"),
                jpg_dest=str(stack_dir / "IMG_0001.JPG"),
                frame_sources=(),
            ),
        ),
        singles=(),
        single_sources=(),
        unresolved=(),
    )
    monkeypatch.setattr(
        stackcopy, "build_canon_import_plan", lambda *a, **k: fake_plan
    )
    called = []
    monkeypatch.setattr(
        stackcopy.dng_converter, "find_dng_converter",
        lambda: called.append("looked") or "/fake/converter",
    )

    (tmp_path / "src").mkdir()
    args = stackcopy.parse_arguments(
        ["--canon-import", str(tmp_path / "src"), str(tmp_path / "dst"), "--dry"]
    )
    exit_code = stackcopy.run_canon_import(args)
    output = capsys.readouterr().out

    assert exit_code == 0
    assert called == [], "nothing to preflight against, so never even looked"
    assert "copying raw CR3 frames instead" in output


def test_no_dng_execution_copies_composite_frame_and_single(tmp_path, monkeypatch):
    """End-to-end --no-dng run: real bytes land for the JPG, frame, and single.

    --no-dng means CR3 frames are copied into the stack folder rather than
    converted, and it must skip converter discovery/preflight entirely.
    """
    src = tmp_path / "src" / "100EOSR6"
    src.mkdir(parents=True)
    (src / "IMG_0001.CR3").write_bytes(b"frame-one")
    (src / "IMG_0002.JPG").write_bytes(b"composite-two")
    (src / "IMG_0003.CR3").write_bytes(b"loose-three")

    records = [
        canon_stacks.CanonFrame("100EOSR6", 1, ".cr3", "IMG_0001.CR3"),
        canon_stacks.CanonFrame(
            "100EOSR6", 2, ".jpg", "IMG_0002.JPG", depth_composite=True
        ),
        canon_stacks.CanonFrame("100EOSR6", 3, ".cr3", "IMG_0003.CR3"),
    ]
    monkeypatch.setattr(stackcopy, "read_canon_stack_metadata", lambda paths: records)

    called = []
    monkeypatch.setattr(
        stackcopy.dng_converter, "find_dng_converter",
        lambda: called.append("looked") or None,
    )

    dest = tmp_path / "dst"
    args = stackcopy.parse_arguments(
        ["--canon-import", str(src), str(dest), "--no-dng"]
    )
    exit_code = stackcopy.run_canon_import(args)

    assert exit_code == 0
    assert called == []  # --no-dng: converter never even looked for

    stack_dir = dest / "unknown-date" / "IMG_0002"
    assert (stack_dir / "IMG_0002.JPG").read_bytes() == b"composite-two"
    assert (stack_dir / "IMG_0001.CR3").read_bytes() == b"frame-one"

    single_dest = tmp_path / "Single Images" / "unknown-date" / "IMG_0003.CR3"
    assert single_dest.read_bytes() == b"loose-three"


def test_adjacent_previous_camera_folder_is_scanned_as_context(
    tmp_path, monkeypatch, capsys
):
    """R21: importing 108EOSR6 must also scan the sibling 107EOSR6 for context.

    107EOSR6 is not itself an import target (a stack spanning the folder
    rollover must be able to borrow its frames, but it must never become a
    target of its own -- see build_canon_import_plan's target_folders
    contract, already covered elsewhere). This test is about run_canon_import's
    own responsibility: finding that sibling folder, scanning it, and telling
    build_canon_import_plan the right target set -- so it is a spy on that
    call rather than a re-test of canon_stacks' grouping algorithm.
    """
    root = tmp_path / "DCIM"
    previous_folder = root / "107EOSR6"
    target_folder = root / "108EOSR6"
    previous_folder.mkdir(parents=True)
    target_folder.mkdir()
    (previous_folder / "IMG_9999.CR3").write_bytes(b"tail-frame")
    (target_folder / "IMG_0001.JPG").write_bytes(b"composite")

    captured = {}

    def spy_build_plan(records, dest_root, singles_root, source_root="",
                        target_folders=None):
        captured["folders"] = {r.folder for r in records}
        captured["target_folders"] = target_folders
        return stackcopy.CanonImportPlan(
            stacks=(), singles=(), single_sources=(), unresolved=()
        )

    monkeypatch.setattr(stackcopy, "build_canon_import_plan", spy_build_plan)

    args = stackcopy.parse_arguments(
        ["--canon-import", str(target_folder), str(tmp_path / "dst"),
         "--no-dng", "--dry"]
    )
    exit_code = stackcopy.run_canon_import(args)
    output = capsys.readouterr().out

    assert exit_code == 0
    assert captured["target_folders"] == {"108EOSR6"}
    assert captured["folders"] == {"107EOSR6", "108EOSR6"}
    assert "107EOSR6" in output, "context being pulled in must be visible"


def test_no_adjacent_folder_proceeds_normally(tmp_path, monkeypatch, capsys):
    """No previous sibling folder on the card: nothing extra is scanned."""
    target_folder = tmp_path / "DCIM" / "200EOSR6"
    target_folder.mkdir(parents=True)
    (target_folder / "IMG_0001.CR3").write_bytes(b"solo-frame")

    captured = {}

    def spy_build_plan(records, dest_root, singles_root, source_root="",
                        target_folders=None):
        captured["folders"] = {r.folder for r in records}
        return stackcopy.CanonImportPlan(
            stacks=(), singles=(), single_sources=(), unresolved=()
        )

    monkeypatch.setattr(stackcopy, "build_canon_import_plan", spy_build_plan)

    args = stackcopy.parse_arguments(
        ["--canon-import", str(target_folder), str(tmp_path / "dst"),
         "--no-dng", "--dry"]
    )
    exit_code = stackcopy.run_canon_import(args)

    assert exit_code == 0
    assert captured["folders"] == {"200EOSR6"}


# --- Task 11: Helicon Focus rendering wiring --------------------------------


def _one_stack_plan(tmp_path):
    """A single real two-frame stack, with real bytes to copy on disk.

    Two frames, not one: a one-frame stack is skipped rather than rendered
    (see SingleFrameRenderSkipTests), so a fixture meant to exercise
    rendering needs at least two frames to remain a renderable stack.
    """
    src = tmp_path / "src"
    src.mkdir(exist_ok=True)
    (src / "IMG_0001.CR3").write_bytes(b"frame-one")
    (src / "IMG_0002.CR3").write_bytes(b"frame-two")
    (src / "IMG_0001.JPG").write_bytes(b"composite-one")
    stack_dir = tmp_path / "dst" / "2026-01-01" / "IMG_0001"
    plan = stackcopy.CanonImportPlan(
        stacks=(
            stackcopy.CanonPlannedStack(
                dest_dir=str(stack_dir),
                jpg_src=str(src / "IMG_0001.JPG"),
                jpg_dest=str(stack_dir / "IMG_0001.JPG"),
                frame_sources=(
                    str(src / "IMG_0001.CR3"), str(src / "IMG_0002.CR3"),
                ),
            ),
        ),
        singles=(), single_sources=(), unresolved=(),
    )
    return plan, stack_dir


def _two_stack_plan(tmp_path):
    """Two independent real two-frame stacks, with real bytes on disk.

    Two frames per stack, not one: a one-frame stack is skipped rather than
    rendered (see SingleFrameRenderSkipTests), so fixtures meant to exercise
    rendering need at least two frames each to remain renderable.
    """
    src = tmp_path / "src"
    src.mkdir(exist_ok=True)
    stacks = []
    stack_dirs = []
    for n in (1, 2):
        (src / f"IMG_000{n}a.CR3").write_bytes(f"frame-{n}a".encode())
        (src / f"IMG_000{n}b.CR3").write_bytes(f"frame-{n}b".encode())
        (src / f"IMG_000{n}.JPG").write_bytes(f"composite-{n}".encode())
        stack_dir = tmp_path / "dst" / "2026-01-01" / f"IMG_000{n}"
        stack_dirs.append(stack_dir)
        stacks.append(
            stackcopy.CanonPlannedStack(
                dest_dir=str(stack_dir),
                jpg_src=str(src / f"IMG_000{n}.JPG"),
                jpg_dest=str(stack_dir / f"IMG_000{n}.JPG"),
                frame_sources=(
                    str(src / f"IMG_000{n}a.CR3"),
                    str(src / f"IMG_000{n}b.CR3"),
                ),
            )
        )
    plan = stackcopy.CanonImportPlan(
        stacks=tuple(stacks), singles=(), single_sources=(), unresolved=(),
    )
    return plan, stack_dirs


ROLLOVER_FRAMES = ("IMG_9998", "IMG_9999", "IMG_0001", "IMG_0002")


def _rollover_stack_plan(tmp_path):
    """One stack spanning the camera's IMG_9999 -> IMG_0001 folder rollover.

    This is not a synthetic curiosity: it is IMG_0014 on the real card, the
    very first stack of the import. ``frame_sources`` is in CAPTURE order,
    which is the order the focus travelled through the scene. Sorting the
    destination names instead puts IMG_0001/IMG_0002 in front of
    IMG_9998/IMG_9999 -- the two halves of the sweep swapped.
    """
    src = tmp_path / "src"
    (src / "107EOSR6").mkdir(parents=True, exist_ok=True)
    (src / "108EOSR6").mkdir(parents=True, exist_ok=True)
    sources = []
    for stem in ROLLOVER_FRAMES:
        folder = "107EOSR6" if stem.startswith("IMG_99") else "108EOSR6"
        path = src / folder / f"{stem}.CR3"
        path.write_bytes(f"frame-{stem}".encode())
        sources.append(str(path))
    jpg = src / "108EOSR6" / "IMG_0003.JPG"
    jpg.write_bytes(b"composite")
    stack_dir = tmp_path / "dst" / "2026-01-01" / "IMG_0003"
    plan = stackcopy.CanonImportPlan(
        stacks=(
            stackcopy.CanonPlannedStack(
                dest_dir=str(stack_dir),
                jpg_src=str(jpg),
                jpg_dest=str(stack_dir / "IMG_0003.JPG"),
                frame_sources=tuple(sources),
            ),
        ),
        singles=(), single_sources=(), unresolved=(),
    )
    return plan, stack_dir


def _fake_convert_groups_writing_dngs(skip=()):
    """A convert_groups stub that writes one .dng per source, minus ``skip``."""
    def fake(converter, jobs_by_dest, workers=1, on_progress=None):
        results = []
        for dest_dir, files in jobs_by_dest:
            for source in files:
                stem = os.path.splitext(os.path.basename(source))[0]
                if stem in skip:
                    continue
                Path(dest_dir, f"{stem}.dng").write_bytes(b"fake-dng")
            results.append(stackcopy.dng_converter.ConversionResult(
                dest_dir, converted=len(files)
            ))
        return results
    return fake


def test_render_helicon_missing_binary_fails_clearly(tmp_path, monkeypatch, capsys):
    """A requested render with no Helicon install must fail loudly, not skip.

    This is the specific defect this branch has already had to fix twice
    for other flags: a feature that was explicitly asked for silently
    turning into a no-op. Exit code and message must both say so.
    """
    plan, stack_dir = _one_stack_plan(tmp_path)
    monkeypatch.setattr(stackcopy, "read_canon_stack_metadata", lambda paths: [])
    monkeypatch.setattr(stackcopy, "build_canon_import_plan", lambda *a, **k: plan)
    monkeypatch.setattr(stackcopy.helicon_focus, "find_helicon", lambda: None)

    args = stackcopy.parse_arguments(
        ["--canon-import", str(tmp_path / "src"), str(tmp_path / "dst"),
         "--no-dng", "--render-helicon"]
    )
    exit_code = stackcopy.run_canon_import(args)
    output = capsys.readouterr().out

    assert exit_code == 1
    assert "Helicon Focus was not found" in output
    assert stackcopy.helicon_focus.HELICON_ENV in output
    # The import itself must not be held hostage by a missing renderer: the
    # frame and composite it already had everything it needed to copy are
    # still copied.
    assert (stack_dir / "IMG_0001.CR3").read_bytes() == b"frame-one"


def test_render_helicon_not_invoked_under_dry_run(tmp_path, monkeypatch):
    """--dry must never shell out to Helicon: a dry run writes nothing.

    The real gate is run_canon_import's own early ``if args.dry_run: ...
    return 0`` -- reached long before the render block, and before the copy
    loop too -- not a second dry-run check inside the render block itself
    (there isn't one: it would be dead code, since dry_run can never be True
    by the time that block runs). This test pins that real, single gate
    directly: nothing is copied to disk, and Helicon is never even looked
    up. Deleting or bypassing the early return is what would make this
    fail; a decorative second check inside the render block would not.
    """
    plan, stack_dir = _one_stack_plan(tmp_path)
    monkeypatch.setattr(stackcopy, "read_canon_stack_metadata", lambda paths: [])
    monkeypatch.setattr(stackcopy, "build_canon_import_plan", lambda *a, **k: plan)

    def _must_not_be_called():
        raise AssertionError("find_helicon() must not run under --dry")

    monkeypatch.setattr(
        stackcopy.helicon_focus, "find_helicon", _must_not_be_called
    )

    args = stackcopy.parse_arguments(
        ["--canon-import", str(tmp_path / "src"), str(tmp_path / "dst"),
         "--no-dng", "--render-helicon", "--dry"]
    )
    exit_code = stackcopy.run_canon_import(args)

    assert exit_code == 0
    assert not stack_dir.exists(), "a dry run must write nothing at all"


def test_render_helicon_failure_on_one_stack_does_not_abort_the_run(
    tmp_path, monkeypatch, capsys
):
    """One stack's render failure must be reported and counted, not fatal.

    The second stack must still be attempted: a bad frame in stack one is
    not evidence anything is wrong with stack two.
    """
    plan, stack_dirs = _two_stack_plan(tmp_path)
    monkeypatch.setattr(stackcopy, "read_canon_stack_metadata", lambda paths: [])
    monkeypatch.setattr(stackcopy, "build_canon_import_plan", lambda *a, **k: plan)
    monkeypatch.setattr(
        stackcopy.helicon_focus, "find_helicon", lambda: "/fake/helicon"
    )

    calls = []

    def fake_render_stack(helicon, frames, output_path, method=2, **kwargs):
        calls.append(output_path)
        if len(calls) == 1:
            return stackcopy.helicon_focus.RenderResult(
                output_path, error="stub render failed"
            )
        return stackcopy.helicon_focus.RenderResult(output_path, rendered=True)

    monkeypatch.setattr(
        stackcopy.helicon_focus, "render_stack", fake_render_stack
    )

    args = stackcopy.parse_arguments(
        ["--canon-import", str(tmp_path / "src"), str(tmp_path / "dst"),
         "--no-dng", "--render-helicon"]
    )
    exit_code = stackcopy.run_canon_import(args)
    output = capsys.readouterr().out

    assert len(calls) == 2, "the second stack must still be attempted"
    assert exit_code == 1
    assert "Helicon failed" in output
    assert "stub render failed" in output


def test_render_helicon_uses_raw_frames_when_no_dng(tmp_path, monkeypatch):
    """--no-dng: Helicon must render from the copied raw frames, not DNGs."""
    plan, stack_dir = _one_stack_plan(tmp_path)
    monkeypatch.setattr(stackcopy, "read_canon_stack_metadata", lambda paths: [])
    monkeypatch.setattr(stackcopy, "build_canon_import_plan", lambda *a, **k: plan)
    monkeypatch.setattr(
        stackcopy.helicon_focus, "find_helicon", lambda: "/fake/helicon"
    )

    captured = {}

    def fake_render_stack(helicon, frames, output_path, method=2, **kwargs):
        captured["frames"] = frames
        return stackcopy.helicon_focus.RenderResult(output_path, rendered=True)

    monkeypatch.setattr(
        stackcopy.helicon_focus, "render_stack", fake_render_stack
    )

    args = stackcopy.parse_arguments(
        ["--canon-import", str(tmp_path / "src"), str(tmp_path / "dst"),
         "--no-dng", "--render-helicon"]
    )
    exit_code = stackcopy.run_canon_import(args)

    assert exit_code == 0
    assert captured["frames"] == [
        str(stack_dir / "IMG_0001.CR3"), str(stack_dir / "IMG_0002.CR3"),
    ]


def test_render_helicon_uses_dng_frames_in_capture_order(tmp_path, monkeypatch):
    """Fix round 2, item 1: the frame list is the PLAN, not a sorted listdir.

    The stack spans the camera's IMG_9999 -> IMG_0001 rollover, so
    lexicographic order and capture order genuinely differ. Helicon methods
    A and B are order-dependent; a scrambled list still renders, still exits
    0, and is silently wrong, which is why this is asserted as an exact
    ordered list rather than a set. The predecessor of this test used a
    ONE-frame stack and so could not tell the two orders apart at all.
    """
    plan, stack_dir = _rollover_stack_plan(tmp_path)
    monkeypatch.setattr(stackcopy, "read_canon_stack_metadata", lambda paths: [])
    monkeypatch.setattr(stackcopy, "build_canon_import_plan", lambda *a, **k: plan)
    monkeypatch.setattr(
        stackcopy.dng_converter, "find_dng_converter", lambda: "/fake/converter"
    )
    monkeypatch.setattr(stackcopy.dng_converter, "preflight", lambda *a, **k: None)
    monkeypatch.setattr(
        stackcopy.dng_converter, "convert_groups",
        _fake_convert_groups_writing_dngs(),
    )
    monkeypatch.setattr(
        stackcopy.helicon_focus, "find_helicon", lambda: "/fake/helicon"
    )

    captured = {}

    def fake_render_stack(helicon, frames, output_path, method=2, **kwargs):
        captured["frames"] = frames
        return stackcopy.helicon_focus.RenderResult(output_path, rendered=True)

    monkeypatch.setattr(
        stackcopy.helicon_focus, "render_stack", fake_render_stack
    )

    args = stackcopy.parse_arguments(
        ["--canon-import", str(tmp_path / "src"), str(tmp_path / "dst"),
         "--render-helicon"]
    )
    exit_code = stackcopy.run_canon_import(args)

    assert exit_code == 0
    expected = [str(stack_dir / f"{stem}.dng") for stem in ROLLOVER_FRAMES]
    assert captured["frames"] == expected
    assert captured["frames"] != sorted(captured["frames"]), (
        "the fixture must actually distinguish capture order from "
        "lexicographic order, or this test proves nothing"
    )


def test_render_helicon_skips_a_stack_with_a_missing_dng(
    tmp_path, monkeypatch, capsys
):
    """A short stack renders silently wrong: refuse, report, keep going.

    A frame the converter never produced must not simply be absent from the
    render. Helicon would happily blend the remaining three and exit 0, and
    nothing downstream would ever say the result is missing a slice of the
    focus sweep.
    """
    plan, stack_dir = _rollover_stack_plan(tmp_path)
    monkeypatch.setattr(stackcopy, "read_canon_stack_metadata", lambda paths: [])
    monkeypatch.setattr(stackcopy, "build_canon_import_plan", lambda *a, **k: plan)
    monkeypatch.setattr(
        stackcopy.dng_converter, "find_dng_converter", lambda: "/fake/converter"
    )
    monkeypatch.setattr(stackcopy.dng_converter, "preflight", lambda *a, **k: None)
    # The converter reports success for the group but one DNG never appears.
    monkeypatch.setattr(
        stackcopy.dng_converter, "convert_groups",
        _fake_convert_groups_writing_dngs(skip=("IMG_9999",)),
    )
    monkeypatch.setattr(
        stackcopy.helicon_focus, "find_helicon", lambda: "/fake/helicon"
    )

    calls = []

    def fake_render_stack(helicon, frames, output_path, method=2, **kwargs):
        calls.append(frames)
        return stackcopy.helicon_focus.RenderResult(output_path, rendered=True)

    monkeypatch.setattr(
        stackcopy.helicon_focus, "render_stack", fake_render_stack
    )

    args = stackcopy.parse_arguments(
        ["--canon-import", str(tmp_path / "src"), str(tmp_path / "dst"),
         "--render-helicon"]
    )
    exit_code = stackcopy.run_canon_import(args)
    output = capsys.readouterr().out

    assert calls == [], "an incomplete stack must not be rendered at all"
    assert exit_code == 1
    assert "IMG_9999.dng" in output
    assert "missing" in output


def test_render_helicon_ignores_a_stale_dng_left_by_an_earlier_run(
    tmp_path, monkeypatch
):
    """A .dng from an earlier run with different grouping is not a frame.

    Listing the folder would sweep it into the render; deriving the list
    from the plan cannot see it.
    """
    plan, stack_dir = _rollover_stack_plan(tmp_path)
    monkeypatch.setattr(stackcopy, "read_canon_stack_metadata", lambda paths: [])
    monkeypatch.setattr(stackcopy, "build_canon_import_plan", lambda *a, **k: plan)
    monkeypatch.setattr(
        stackcopy.dng_converter, "find_dng_converter", lambda: "/fake/converter"
    )
    monkeypatch.setattr(stackcopy.dng_converter, "preflight", lambda *a, **k: None)

    def fake_convert_groups(converter, jobs_by_dest, workers=1, on_progress=None):
        results = []
        for dest_dir, files in jobs_by_dest:
            for source in files:
                stem = os.path.splitext(os.path.basename(source))[0]
                Path(dest_dir, f"{stem}.dng").write_bytes(b"fake-dng")
            # Debris from a previous, differently-grouped import.
            Path(dest_dir, "IMG_5555.dng").write_bytes(b"stale-dng")
            results.append(stackcopy.dng_converter.ConversionResult(
                dest_dir, converted=len(files)
            ))
        return results

    monkeypatch.setattr(
        stackcopy.dng_converter, "convert_groups", fake_convert_groups
    )
    monkeypatch.setattr(
        stackcopy.helicon_focus, "find_helicon", lambda: "/fake/helicon"
    )

    captured = {}

    def fake_render_stack(helicon, frames, output_path, method=2, **kwargs):
        captured["frames"] = frames
        return stackcopy.helicon_focus.RenderResult(output_path, rendered=True)

    monkeypatch.setattr(
        stackcopy.helicon_focus, "render_stack", fake_render_stack
    )

    args = stackcopy.parse_arguments(
        ["--canon-import", str(tmp_path / "src"), str(tmp_path / "dst"),
         "--render-helicon"]
    )
    exit_code = stackcopy.run_canon_import(args)

    assert exit_code == 0
    assert (stack_dir / "IMG_5555.dng").exists(), "the stale file is still there"
    assert captured["frames"] == [
        str(stack_dir / f"{stem}.dng") for stem in ROLLOVER_FRAMES
    ]


def test_render_helicon_skips_a_stack_whose_copy_already_failed(
    tmp_path, monkeypatch
):
    """A stack already recorded as failed must not also be handed to Helicon.

    Rendering it would only produce a second, redundant "no frames to
    render" failure on top of the real one already reported for the copy
    that never happened -- confusing noise, not new information.
    """
    plan, stack_dirs = _two_stack_plan(tmp_path)
    monkeypatch.setattr(stackcopy, "read_canon_stack_metadata", lambda paths: [])
    monkeypatch.setattr(stackcopy, "build_canon_import_plan", lambda *a, **k: plan)
    monkeypatch.setattr(
        stackcopy.helicon_focus, "find_helicon", lambda: "/fake/helicon"
    )

    # Make the first stack's frame copy fail: its source vanishes before
    # run_canon_import ever touches it, so safe_file_operation reports it as
    # failed and its dest_dir lands in failed_stack_dirs.
    os.remove(plan.stacks[0].frame_sources[0])

    calls = []

    def fake_render_stack(helicon, frames, output_path, method=2, **kwargs):
        calls.append(output_path)
        return stackcopy.helicon_focus.RenderResult(output_path, rendered=True)

    monkeypatch.setattr(
        stackcopy.helicon_focus, "render_stack", fake_render_stack
    )

    args = stackcopy.parse_arguments(
        ["--canon-import", str(tmp_path / "src"), str(tmp_path / "dst"),
         "--no-dng", "--render-helicon"]
    )
    stackcopy.run_canon_import(args)

    assert len(calls) == 1, "only the healthy second stack should be rendered"
    assert stack_dirs[1].name in calls[0]


def _progress_events(fn, *args, **kwargs):
    """Run ``fn`` with STACKCOPY_PROGRESS on, returning parsed (fields,
    filename) pairs for every emitted sentinel line, in order."""
    captured = io.StringIO()
    with mock.patch.object(stackcopy, "_PROGRESS_ENABLED", True), \
         mock.patch.object(stackcopy.sys, "stderr", captured):
        exit_code = fn(*args, **kwargs)
    lines = [
        ln for ln in captured.getvalue().splitlines()
        if ln.startswith(gui.PROGRESS_SENTINEL)
    ]
    return exit_code, [gui.parse_progress(ln) for ln in lines]


def test_no_dng_frame_copies_are_reported_and_reach_full_progress(
    tmp_path, monkeypatch
):
    """Task 5 fix round 1, item 1: under --no-dng, stack frames are copied
    raw in run_canon_import's ``else`` branch (converter is None), which
    used to neither emit progress nor increment ``done`` for them. A real
    --no-dng run with frames therefore left the GUI's bar permanently short
    and its ETA wrong. This pins both the per-frame emission and the
    resulting done == total at the end of a --no-dng run with real frames.
    """
    plan, stack_dir = _one_stack_plan(tmp_path)
    monkeypatch.setattr(stackcopy, "read_canon_stack_metadata", lambda paths: [])
    monkeypatch.setattr(stackcopy, "build_canon_import_plan", lambda *a, **k: plan)

    args = stackcopy.parse_arguments(
        ["--canon-import", str(tmp_path / "src"), str(tmp_path / "dst"), "--no-dng"]
    )
    exit_code, events = _progress_events(stackcopy.run_canon_import, args)

    assert exit_code == 0
    frame_names = {
        filename for fields, filename in events if fields.get("role") == "frames"
    }
    assert frame_names == {"IMG_0001.CR3", "IMG_0002.CR3"}, (
        "each raw frame copy under --no-dng must emit its own progress event, "
        f"got {frame_names}"
    )
    last_fields, _ = events[-1]
    assert last_fields["done"] == last_fields["total"], (
        "the run must finish with done == total: "
        f"got done={last_fields['done']} total={last_fields['total']}"
    )


def test_convert_phase_is_reported_and_reaches_full_progress(tmp_path, monkeypatch):
    """Task 5 fix round 1, item 3: the convert phase (the ``else`` branch's
    opposite -- taken whenever --no-dng is absent) had zero progress test
    coverage, which is exactly where fix round 1's items 1 and 2 lived
    undetected. ``_fake_convert_groups_writing_dngs`` (used by the render
    tests above) never calls ``on_progress``, so it cannot exercise this
    either -- this test's fake convert_groups deliberately does.
    """
    plan, stack_dir = _one_stack_plan(tmp_path)
    monkeypatch.setattr(stackcopy, "read_canon_stack_metadata", lambda paths: [])
    monkeypatch.setattr(stackcopy, "build_canon_import_plan", lambda *a, **k: plan)
    monkeypatch.setattr(
        stackcopy.dng_converter, "find_dng_converter", lambda: "/fake/converter"
    )
    monkeypatch.setattr(stackcopy.dng_converter, "preflight", lambda *a, **k: None)

    def fake_convert_groups(converter, jobs_by_dest, workers=1, on_progress=None):
        results = []
        for dest_dir, files in jobs_by_dest:
            result = stackcopy.dng_converter.ConversionResult(
                dest_dir, converted=len(files)
            )
            results.append(result)
            if on_progress:
                on_progress(result)
        return results

    monkeypatch.setattr(
        stackcopy.dng_converter, "convert_groups", fake_convert_groups
    )

    args = stackcopy.parse_arguments(
        ["--canon-import", str(tmp_path / "src"), str(tmp_path / "dst")]
    )
    exit_code, events = _progress_events(stackcopy.run_canon_import, args)

    assert exit_code == 0
    convert_events = [fields for fields, _ in events if fields.get("phase") == "convert"]
    assert convert_events, "the convert phase must emit at least one event"
    last_fields, _ = events[-1]
    assert last_fields["done"] == last_fields["total"], (
        "the run must finish with done == total: "
        f"got done={last_fields['done']} total={last_fields['total']}"
    )


def test_render_phase_is_reported_and_reaches_full_progress(tmp_path, monkeypatch):
    """Task 5 fix round 1, item 3: the render phase also had zero progress
    test coverage. Item 2's bug (a bar pinned at 100% for the whole render
    phase) lived here undetected -- ``operation_total`` never counted the
    render at all, so ``done`` looked "finished" before the phase even
    started, and this asserts the fixed version instead reaches
    done == total only once rendering is actually done.
    """
    plan, stack_dir = _one_stack_plan(tmp_path)
    monkeypatch.setattr(stackcopy, "read_canon_stack_metadata", lambda paths: [])
    monkeypatch.setattr(stackcopy, "build_canon_import_plan", lambda *a, **k: plan)
    monkeypatch.setattr(
        stackcopy.helicon_focus, "find_helicon", lambda: "/fake/helicon"
    )

    def fake_render_stack(helicon, frames, output_path, method=2, **kwargs):
        return stackcopy.helicon_focus.RenderResult(output_path, rendered=True)

    monkeypatch.setattr(
        stackcopy.helicon_focus, "render_stack", fake_render_stack
    )

    args = stackcopy.parse_arguments(
        ["--canon-import", str(tmp_path / "src"), str(tmp_path / "dst"),
         "--no-dng", "--render-helicon"]
    )
    exit_code, events = _progress_events(stackcopy.run_canon_import, args)

    assert exit_code == 0
    render_events = [fields for fields, _ in events if fields.get("phase") == "render"]
    assert render_events, "the render phase must emit at least one event"
    last_fields, _ = events[-1]
    assert last_fields["done"] == last_fields["total"], (
        "the run must finish with done == total: "
        f"got done={last_fields['done']} total={last_fields['total']}"
    )


# --- Fix round 2, item 3 / item 7 minors -----------------------------------


def test_canon_import_runs_the_shared_low_space_check(tmp_path, monkeypatch):
    """Every other mode calls confirm_if_low_space; --canon-import did not.

    DNG output is LARGER than the CR3 input, so this is the mode most able
    to fill a disk -- a 65 GB card at hour two. The operations handed to the
    check must cover the composite, every stack frame and every loose CR3,
    and each frame's entry must name where it will actually land (the .dng,
    when conversion is on), so the estimate lands on the right filesystem.
    """
    plan, stack_dir = _rollover_stack_plan(tmp_path)
    monkeypatch.setattr(stackcopy, "read_canon_stack_metadata", lambda paths: [])
    monkeypatch.setattr(stackcopy, "build_canon_import_plan", lambda *a, **k: plan)
    monkeypatch.setattr(
        stackcopy.dng_converter, "find_dng_converter", lambda: "/fake/converter"
    )
    monkeypatch.setattr(stackcopy.dng_converter, "preflight", lambda *a, **k: None)

    seen = []
    monkeypatch.setattr(
        stackcopy, "confirm_if_low_space",
        lambda ops, dry_run: seen.append((list(ops), dry_run)),
    )

    args = stackcopy.parse_arguments(
        ["--canon-import", str(tmp_path / "src"), str(tmp_path / "dst"), "--dry"]
    )
    assert stackcopy.run_canon_import(args) == 0

    assert len(seen) == 1, "the low-space check must run exactly once"
    ops, dry_run = seen[0]
    assert dry_run is True
    assert all(op[2] == "copy" for op in ops)
    sources = [op[0] for op in ops]
    assert sources == [plan.stacks[0].jpg_src, *plan.stacks[0].frame_sources]
    assert [op[1] for op in ops][1:] == [
        str(stack_dir / f"{stem}.dng") for stem in ROLLOVER_FRAMES
    ]


def test_dng_jobs_is_clamped_to_a_sane_range(capsys):
    """An unbounded --dng-jobs is a typo away from thousands of converters."""
    for bad in ("0", str(stackcopy.MAX_DNG_JOBS + 1)):
        with pytest.raises(SystemExit):
            stackcopy.parse_arguments(
                ["--canon-import", "src", "dst", "--dng-jobs", bad]
            )
        assert str(stackcopy.MAX_DNG_JOBS) in capsys.readouterr().err
    args = stackcopy.parse_arguments(
        ["--canon-import", "src", "dst",
         "--dng-jobs", str(stackcopy.MAX_DNG_JOBS)]
    )
    assert args.dng_jobs == stackcopy.MAX_DNG_JOBS


def test_helicon_method_default_tracks_the_module_constant():
    """The literal 2 in the parser and in main()'s gate is now one constant."""
    args = stackcopy.parse_arguments(["--canon-import", "src", "dst"])
    assert args.helicon_method == stackcopy.helicon_focus.DEFAULT_METHOD


def test_unresolved_composite_reports_all_of_its_notes(tmp_path, monkeypatch, capsys):
    """notes[0] alone silently drops every explanation after the first."""
    group = canon_stacks.CanonStackGroup(
        output_jpg=canon_stacks.CanonFrame(
            "108EOSR6", 9, ".jpg", "IMG_0009.JPG", depth_composite=True
        ),
        frames=(),
        confidence="no-frames-found",
        notes=("first reason", "second reason"),
    )
    plan = stackcopy.CanonImportPlan(
        stacks=(), singles=(), single_sources=(), unresolved=(group,)
    )
    (tmp_path / "src").mkdir()
    monkeypatch.setattr(stackcopy, "build_canon_import_plan", lambda *a, **k: plan)
    args = stackcopy.parse_arguments(
        ["--canon-import", str(tmp_path / "src"), str(tmp_path / "dst"), "--dry"]
    )
    stackcopy.run_canon_import(args)
    output = capsys.readouterr().out
    assert "first reason" in output
    assert "second reason" in output


class Cr3CountsAsRawTests(unittest.TestCase):
    """.cr3 must be recognised as RAW by the shared, non-Canon code paths.

    Canon's previous raw format .cr2 was already listed; .cr3 was missing, so
    every CR3 on a card fell through to "other data" and a Canon card reported
    a fraction of its real file count.  The rule that matters to a user is the
    one --copy applies: a JPG *with* a matching raw is an ordinary shot and is
    left alone, while a JPG *without* one is an in-camera output worth copying.
    """

    def test_a_jpg_beside_a_cr3_is_not_treated_as_an_unpaired_output(self):
        with tempfile.TemporaryDirectory() as work:
            source = Path(work) / "card"
            dest = Path(work) / "out"
            source.mkdir()
            # IMG_0001 is an ordinary raw+JPG pair; IMG_0002 has no raw.
            for name in ("IMG_0001.CR3", "IMG_0001.JPG", "IMG_0002.JPG"):
                (source / name).write_bytes(b"x" * 32)

            completed = subprocess.run(
                [sys.executable, str(ROOT / "stackcopy.py"),
                 "--copy", str(source), str(dest)],
                check=False, capture_output=True, text=True,
            )
            self.assertEqual(completed.returncode, 0, completed.stderr)

            copied = sorted(p.name for p in dest.rglob("*") if p.is_file())
            # Without .cr3 in RAW_EXTENSIONS, IMG_0001.JPG looks unpaired too
            # and both JPGs are copied.
            self.assertEqual(copied, ["IMG_0002.JPG"])


class CanonPlanJsonTests(unittest.TestCase):
    def test_payload_fields_and_invariant(self):
        payload = stackcopy.canon_plan_payload(
            _one_stack_and_single_plan(),  # helper defined in Step 3 of this task
            dest_dir=r"F:\Stacked",
            singles_dir=r"F:\Singles",
            canon_frames_detected=26,
        )
        self.assertEqual(payload["mode"], "canon-import")
        self.assertEqual(payload["stacks"], 1)
        self.assertEqual(payload["stacked_outputs"], 1)
        self.assertEqual(payload["stack_inputs"], 25)
        self.assertEqual(payload["others"], 1)
        self.assertEqual(
            payload["total"],
            payload["stacked_outputs"] + payload["stack_inputs"] + payload["others"],
        )
        self.assertEqual(payload["dest_stacks"], r"F:\Stacked")
        self.assertEqual(payload["dest_singles"], r"F:\Singles")
        self.assertEqual(payload["canon_frames_detected"], 26)
        self.assertEqual(payload["unresolved"], 0)
        self.assertEqual(payload["stackcopy_version"], stackcopy.STACKCOPY_VERSION)

    def test_payload_survives_the_guis_own_validator(self):
        """The GUI rejects a malformed plan; a Canon plan must not look malformed."""
        payload = stackcopy.canon_plan_payload(
            _one_stack_and_single_plan(), r"F:\Stacked", r"F:\Singles", 26
        )
        parsed = gui.parse_plan_json(json.dumps(payload))
        self.assertIsNotNone(parsed)
        self.assertEqual(parsed["stacks"], 1)

    def test_bytes_sums_the_real_source_file_sizes(self):
        """The source strip must stop reading '0 bytes' on every Canon scan.

        Mirrors how --lightroomimport's own plan-json ``bytes`` total is
        computed: sum ``os.path.getsize()`` over every source file the plan
        will actually copy (each stack's composite JPG and its frames, plus
        every loose single) -- not a second, invented approach.
        """
        with tempfile.TemporaryDirectory() as work:
            root = Path(work)
            jpg_src = root / "IMG_0040.JPG"
            frame_srcs = [root / f"IMG_{n:04d}.CR3" for n in (15, 16)]
            single_src = root / "IMG_0067.CR3"
            jpg_src.write_bytes(b"j" * 100)
            frame_srcs[0].write_bytes(b"f" * 200)
            frame_srcs[1].write_bytes(b"f" * 300)
            single_src.write_bytes(b"s" * 50)

            stack = stackcopy.CanonPlannedStack(
                dest_dir=str(root / "dest" / "IMG_0040"),
                jpg_src=str(jpg_src),
                jpg_dest=str(root / "dest" / "IMG_0040" / "IMG_0040.JPG"),
                frame_sources=tuple(str(p) for p in frame_srcs),
            )
            plan = stackcopy.CanonImportPlan(
                stacks=(stack,),
                singles=(str(root / "singles" / "IMG_0067.CR3"),),
                single_sources=(str(single_src),),
                unresolved=(),
            )

            payload = stackcopy.canon_plan_payload(
                plan, dest_dir=str(root / "dest"),
                singles_dir=str(root / "singles"),
                canon_frames_detected=3,
            )

            self.assertEqual(payload["bytes"], 100 + 200 + 300 + 50)

    def test_bytes_skips_a_source_file_that_has_vanished(self):
        """A file gone between the scan and payload-building must not crash it."""
        with tempfile.TemporaryDirectory() as work:
            root = Path(work)
            jpg_src = root / "IMG_0040.JPG"
            jpg_src.write_bytes(b"j" * 40)
            missing_frame = root / "IMG_0015.CR3"  # never created

            stack = stackcopy.CanonPlannedStack(
                dest_dir=str(root / "dest" / "IMG_0040"),
                jpg_src=str(jpg_src),
                jpg_dest=str(root / "dest" / "IMG_0040" / "IMG_0040.JPG"),
                frame_sources=(str(missing_frame),),
            )
            plan = stackcopy.CanonImportPlan(
                stacks=(stack,), singles=(), single_sources=(), unresolved=(),
            )

            payload = stackcopy.canon_plan_payload(
                plan, dest_dir=str(root / "dest"),
                singles_dir=str(root / "singles"),
                canon_frames_detected=1,
            )

            self.assertEqual(payload["bytes"], 40)

    def test_plan_json_is_accepted_with_canon_import(self):
        parser = stackcopy.build_parser()
        args = parser.parse_args(
            ["--canon-import", "src", "dst", "--plan-json"]
        )
        self.assertTrue(args.plan_json)

    def test_plan_json_still_rejected_for_other_modes(self):
        parser = stackcopy.build_parser()
        with self.assertRaises(SystemExit):
            stackcopy.validate_plan_json_mode(
                parser, parser.parse_args(["--rename", "somedir", "--plan-json"])
            )


class CanonPlanJsonStdoutRoutingTests(unittest.TestCase):
    """--plan-json's payload must reach the real stdout, not stderr.

    main() redirects sys.stdout to sys.stderr for the duration of a
    --plan-json run, so every incidental print() during scan/plan stays off
    the pipe a caller is about to parse as JSON -- then restores the real
    stream right before printing the payload itself. --lightroomimport does
    that restore explicitly (``sys.stdout = plan_json_stdout``); a
    --canon-import implementation that just does ``print(json.dumps(...))``
    without the same restore prints straight into the *redirected* stdout,
    i.e. the real stderr, where the GUI parsing --plan-json's stdout would
    never see it. This drives the whole run through main() itself (not
    run_canon_import() directly) so it exercises that redirect/restore, the
    same way tests/test_plan_json_and_progress_roles.py does for
    --lightroomimport.
    """

    def test_payload_reaches_real_stdout_through_main(self):
        with tempfile.TemporaryDirectory() as tmp:
            src = Path(tmp) / "src" / "100EOSR6"
            src.mkdir(parents=True)
            (src / "IMG_0067.CR3").write_bytes(b"frame")
            dest = Path(tmp) / "dst"

            records = [
                canon_stacks.CanonFrame("100EOSR6", 67, ".cr3", "IMG_0067.CR3"),
            ]
            output = io.StringIO()
            exit_code = None
            with (
                mock.patch.object(
                    stackcopy, "read_canon_stack_metadata", lambda paths: records
                ),
                mock.patch.object(
                    sys,
                    "argv",
                    [
                        "stackcopy.py",
                        "--canon-import",
                        str(src),
                        str(dest),
                        "--plan-json",
                        "--no-dng",
                    ],
                ),
                redirect_stdout(output),
            ):
                try:
                    stackcopy.main()
                except SystemExit as exc:
                    exit_code = exc.code if isinstance(exc.code, int) else 1

        self.assertEqual(exit_code, 0)
        captured = output.getvalue()
        lines = captured.strip().splitlines()
        self.assertEqual(len(lines), 1, captured)
        payload = json.loads(lines[0])
        self.assertEqual(payload["mode"], "canon-import")
        self.assertEqual(payload["stacks"], 0)
        self.assertEqual(payload["others"], 1)


class HeliconTuningFlagsTests(unittest.TestCase):
    def test_radius_and_smoothing_are_accepted(self):
        parser = stackcopy.build_parser()
        args = parser.parse_args(
            ["--canon-import", "s", "d",
             "--render-helicon", "--helicon-radius", "6", "--helicon-smoothing", "7"]
        )
        self.assertEqual(args.helicon_radius, 6)
        self.assertEqual(args.helicon_smoothing, 7)

    def test_they_default_to_the_helicon_modules_values(self):
        parser = stackcopy.build_parser()
        args = parser.parse_args(["--canon-import", "s", "d"])
        self.assertEqual(args.helicon_radius, helicon_focus.DEFAULT_RADIUS)
        self.assertEqual(args.helicon_smoothing, helicon_focus.DEFAULT_SMOOTHING)

    def test_they_are_canon_only(self):
        parser = stackcopy.build_parser()
        with self.assertRaises(SystemExit):
            stackcopy.parse_arguments(
                ["--rename", "somedir", "--helicon-radius", "6"], parser=parser
            )


class SingleFrameRenderSkipTests(unittest.TestCase):
    def test_a_one_frame_stack_is_skipped_not_failed(self):
        self.assertTrue(stackcopy.canon_stack_is_renderable.__doc__)
        self.assertFalse(stackcopy.canon_stack_is_renderable(["only.dng"]))
        self.assertTrue(stackcopy.canon_stack_is_renderable(["a.dng", "b.dng"]))

    def test_an_empty_stack_is_not_renderable(self):
        self.assertFalse(stackcopy.canon_stack_is_renderable([]))


def _run_canon_import_capturing_progress():
    """Run a one-stack Canon import with progress on, returning sentinel lines."""
    with tempfile.TemporaryDirectory() as work:
        source = Path(work) / "108EOSR6"
        source.mkdir()
        for name in ("IMG_0001.CR3", "IMG_0002.JPG"):
            (source / name).write_bytes(b"x" * 32)
        captured = io.StringIO()
        args = stackcopy.parse_arguments(
            ["--canon-import", str(source), str(Path(work) / "out"),
             "--singles-dir", str(Path(work) / "singles"), "--no-dng"]
        )
        with mock.patch.object(stackcopy, "_PROGRESS_ENABLED", True), \
             mock.patch.object(stackcopy.sys, "stderr", captured), \
             mock.patch.object(
                 stackcopy, "read_canon_stack_metadata",
                 lambda paths: canon_stacks.records_from_exiftool_json(
                     [{"SourceFile": p,
                       "SubSecDateTimeOriginal": "2026:07:31 10:44:02.03-04:00",
                       "FocusBracketingDepthComposite": 1} for p in paths]
                 ),
             ):
            stackcopy.run_canon_import(args)
    return [
        ln for ln in captured.getvalue().splitlines()
        if ln.startswith(gui.PROGRESS_SENTINEL)
    ]


class CanonProgressTests(unittest.TestCase):
    def test_progress_lines_survive_the_guis_parser(self):
        """Whatever we emit, the GUI must be able to read it."""
        captured = io.StringIO()
        with mock.patch.object(stackcopy, "_PROGRESS_ENABLED", True), \
             mock.patch.object(stackcopy.sys, "stderr", captured):
            stackcopy._emit_progress(
                file="IMG_0040.JPG", phase="copy", done=1, total=27, role="composite"
            )
        line = captured.getvalue().strip()
        self.assertTrue(line.startswith(gui.PROGRESS_SENTINEL))
        fields, filename = gui.parse_progress(line)
        self.assertEqual(filename, "IMG_0040.JPG")
        self.assertEqual(fields["phase"], "copy")
        self.assertEqual(fields["done"], "1")
        self.assertEqual(fields["total"], "27")

    def test_canon_import_emits_prepare_and_start(self):
        lines = _run_canon_import_capturing_progress()
        phases = [gui.parse_progress(ln)[0].get("phase") for ln in lines]
        self.assertIn("prepare", phases)
        self.assertIn("start", phases)

    def test_every_copied_file_is_reported(self):
        lines = _run_canon_import_capturing_progress()
        names = [gui.parse_progress(ln)[1] for ln in lines]
        self.assertIn("IMG_0002.JPG", names)


if __name__ == "__main__":
    unittest.main()
