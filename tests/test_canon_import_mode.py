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
