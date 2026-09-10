import json
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
