import json
import os
from pathlib import Path

import pytest

import canon_stacks
import stackcopy

FIXTURE = Path(__file__).parent / "fixtures" / "canon_108eosr6.json"

# The fixture's card has two camera folders: 108EOSR6 (what the user is
# importing) and 107EOSR6 (the tail end of a previous session, present only
# because IMG_0014's stack spans the folder rollover). Only 108EOSR6 is a
# target: 107EOSR6 is borrowable context, never a source of its own stacks.
TARGET_FOLDERS = {"108EOSR6"}


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
    """A single real one-frame stack, with real bytes to copy on disk."""
    src = tmp_path / "src"
    src.mkdir(exist_ok=True)
    (src / "IMG_0001.CR3").write_bytes(b"frame-one")
    (src / "IMG_0001.JPG").write_bytes(b"composite-one")
    stack_dir = tmp_path / "dst" / "2026-01-01" / "IMG_0001"
    plan = stackcopy.CanonImportPlan(
        stacks=(
            stackcopy.CanonPlannedStack(
                dest_dir=str(stack_dir),
                jpg_src=str(src / "IMG_0001.JPG"),
                jpg_dest=str(stack_dir / "IMG_0001.JPG"),
                frame_sources=(str(src / "IMG_0001.CR3"),),
            ),
        ),
        singles=(), single_sources=(), unresolved=(),
    )
    return plan, stack_dir


def _two_stack_plan(tmp_path):
    """Two independent real one-frame stacks, with real bytes on disk."""
    src = tmp_path / "src"
    src.mkdir(exist_ok=True)
    stacks = []
    stack_dirs = []
    for n in (1, 2):
        (src / f"IMG_000{n}.CR3").write_bytes(f"frame-{n}".encode())
        (src / f"IMG_000{n}.JPG").write_bytes(f"composite-{n}".encode())
        stack_dir = tmp_path / "dst" / "2026-01-01" / f"IMG_000{n}"
        stack_dirs.append(stack_dir)
        stacks.append(
            stackcopy.CanonPlannedStack(
                dest_dir=str(stack_dir),
                jpg_src=str(src / f"IMG_000{n}.JPG"),
                jpg_dest=str(stack_dir / f"IMG_000{n}.JPG"),
                frame_sources=(str(src / f"IMG_000{n}.CR3"),),
            )
        )
    plan = stackcopy.CanonImportPlan(
        stacks=tuple(stacks), singles=(), single_sources=(), unresolved=(),
    )
    return plan, stack_dirs


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
    """--dry must never shell out to Helicon: a dry run writes nothing."""
    plan, _ = _one_stack_plan(tmp_path)
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
    assert captured["frames"] == [str(stack_dir / "IMG_0001.CR3")]


def test_render_helicon_uses_dng_frames_after_conversion(tmp_path, monkeypatch):
    """When conversion ran, Helicon must render the produced DNGs, not CR3s."""
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
            for source in files:
                stem = os.path.splitext(os.path.basename(source))[0]
                Path(dest_dir, f"{stem}.dng").write_bytes(b"fake-dng")
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
    assert captured["frames"] == [str(stack_dir / "IMG_0001.dng")]
