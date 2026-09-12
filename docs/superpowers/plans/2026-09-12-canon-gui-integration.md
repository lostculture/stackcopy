# Canon GUI Mode Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Let a photographer import a Canon focus-stack card from the Stackcopy GUI, with the same plan preview the Olympus workflow already has, plus controls for DNG conversion and Helicon Focus rendering.

**Architecture:** `--canon-import` learns to emit the existing `--plan-json` payload, which already fits Canon's shape. The GUI's pure helper functions gain mode awareness rather than growing a parallel Canon set, so the 84 existing helper tests keep their meaning. Canon-specific controls appear on the main panel only when a Canon card is detected; Helicon tuning lives in Advanced.

**Tech Stack:** Python 3.11, stdlib only for the CLI. customtkinter for the GUI (already a declared dependency in `requirements-gui.txt`). pytest.

**Spec:** `docs/superpowers/specs/2026-09-10-canon-gui-integration-design.md`

## Global Constraints

- **No new runtime dependencies.** The CLI stays standard-library only. The GUI's only dependency remains `customtkinter>=5.2.2`.
- **Python floor: 3.11.**
- **Cross-platform.** No Windows-only paths outside a platform branch.
- **`canon_stacks.py` stays PURE** — no filesystem, no subprocess, no `os.*` calls.
- **The camera card is never modified** by `--canon-import`. Copy only.
- **`--dry` writes no output files.**
- **Never bound a stack by `FocusBracketingImageCount`** — it is the camera's configured setting, not the actual frame count.
- **Olympus behaviour must not change.** Every existing test passes untouched at every commit.
- **New GUI logic goes in module-level pure functions**, not methods on `StackcopyGUI`, so it is testable without instantiating a widget. This is where the existing 84 GUI tests live.
- **Test baseline: 566 passed / 4 failed / 7 skipped.** Those 4 are PRE-EXISTING Windows durability failures (`test_cleanup_pass` x2, `test_destination_toctou`, `test_final_reliability_pass`), confirmed identical at upstream base `a8626d7`. Any OTHER failure is yours.
- **Test quality: every new test must be able to fail.** Break the behaviour it targets, observe FAIL, restore, observe PASS. This branch's predecessor produced eight tests that passed for the wrong reason; the discipline is what catches them.

---

## File Structure

| File | Responsibility |
|---|---|
| `stackcopy.py` (modify) | Emit `--plan-json` for `--canon-import`; relax the plan-json guard; emit progress sentinels during a Canon import; skip Helicon for single-frame stacks. |
| `stackcopy_gui.py` (modify) | New pure helpers (`plan_mode`, `plan_row_labels`, `detect_canon_mode`, `canon_import_args`); Canon controls on the main panel; Helicon controls in Advanced; mode-aware command construction. |
| `tests/test_gui_plan_helpers.py` (modify) | Tests for the new pure helpers. |
| `tests/test_canon_import_mode.py` (modify) | Tests for the CLI side: plan-json payload, progress sentinels, single-frame skip. |

No new files. The GUI's helpers belong beside the ones they generalise, and the CLI changes belong with the Canon mode they extend.

---

### Task 1: `--plan-json` for `--canon-import`

**Files:**
- Modify: `stackcopy.py:3863-3866` (the guard), and `run_canon_import`
- Test: `tests/test_canon_import_mode.py`

**Interfaces:**
- Consumes: `build_canon_import_plan(...) -> CanonImportPlan` with `.stacks`, `.singles`, `.single_sources`, `.unresolved`.
- Produces: a JSON object on stdout with the fields listed below, and `canon_plan_payload(plan, dest_dir, singles_dir, canon_frames_detected) -> dict`.

The existing payload's invariant is `total == stacked_outputs + stack_inputs + others`. Canon satisfies it: composites + frames + loose CR3s.

- [ ] **Step 1: Write the failing test**

```python
class CanonPlanJsonTests(unittest.TestCase):
    def test_payload_fields_and_invariant(self):
        payload = stackcopy.canon_plan_payload(
            _one_stack_plan(),            # helper defined in Step 3 of this task
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
            _one_stack_plan(), r"F:\Stacked", r"F:\Singles", 26
        )
        parsed = gui.parse_plan_json(json.dumps(payload))
        self.assertIsNotNone(parsed)
        self.assertEqual(parsed["stacks"], 1)

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
```

Add at the top of the file, beside the other helpers:

```python
import stackcopy_gui as gui  # noqa: E402  (for the validator round-trip test)
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/test_canon_import_mode.py -k CanonPlanJson -v`
Expected: FAIL with `AttributeError: module 'stackcopy' has no attribute 'canon_plan_payload'`

- [ ] **Step 3: Write minimal implementation**

Add the test's fixture helper near the top of `tests/test_canon_import_mode.py`:

```python
def _one_stack_plan():
    """A plan with one 25-frame stack and one loose CR3."""
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
```

In `stackcopy.py`, extract the guard into a named function so it is testable, replacing lines 3863-3866:

```python
def validate_plan_json_mode(parser, args) -> None:
    """--plan-json describes an import, so it belongs to the import modes.

    Both --lightroomimport and --canon-import emit the same payload shape;
    every other mode has no plan to describe.
    """
    if not args.plan_json:
        return
    if args.lightroomimport is None and not args.canon_import:
        parser.error(
            "--plan-json can only be used with --lightroomimport or --canon-import."
        )
```

and call it where the old inline check was:

```python
    plan_json_stdout = None
    if args.plan_json:
        validate_plan_json_mode(parser, args)
        args.dry_run = True
        plan_json_stdout = sys.stdout
```

Add the payload builder beside `build_canon_import_plan`:

```python
def canon_plan_payload(
    plan, dest_dir: str, singles_dir: str, canon_frames_detected: int
) -> dict:
    """Describe a Canon import in the same shape --lightroomimport uses.

    The GUI validates any plan it receives, including the arithmetic
    total == stacked_outputs + stack_inputs + others.  Canon satisfies it
    naturally: every CR3 is either a stack frame or a loose file, and every
    composite JPG is one stacked output.
    """
    frame_count = sum(len(stack.frame_sources) for stack in plan.stacks)
    dates = sorted({os.path.basename(os.path.dirname(s.dest_dir)) for s in plan.stacks})
    dest_dirs = sorted({os.path.dirname(s.dest_dir) for s in plan.stacks})
    return {
        "mode": "canon-import",
        "stackcopy_version": STACKCOPY_VERSION,
        "total": len(plan.stacks) + frame_count + len(plan.singles),
        "bytes": 0,
        "stacks": len(plan.stacks),
        "stacked_outputs": len(plan.stacks),
        "stack_inputs": frame_count,
        "others": len(plan.singles),
        "unresolved": len(plan.unresolved),
        "canon_frames_detected": canon_frames_detected,
        "newest_date": dates[-1] if dates else "",
        "dest_stacks": dest_dir,
        "dest_singles": singles_dir,
        "source_subdirs_scanned": [],
        "dest_dirs": dest_dirs,
        "dest_dates": dates,
    }
```

In `run_canon_import`, after the plan is built and before the human-readable
report, emit the payload and return when `--plan-json` was asked for:

```python
    if getattr(args, "plan_json", False):
        frames_seen = sum(
            1 for path in paths if os.path.splitext(path)[1].lower() == ".cr3"
        )
        print(
            json.dumps(
                canon_plan_payload(plan, dest_dir, singles_dir, frames_seen)
            )
        )
        return 0
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python -m pytest tests/test_canon_import_mode.py -k CanonPlanJson -v`
Expected: PASS, 4 tests.

- [ ] **Step 5: Prove the tests can fail**

Temporarily change `"others": len(plan.singles)` to `"others": 0`, run the tests, observe `test_payload_fields_and_invariant` AND `test_payload_survives_the_guis_own_validator` both FAIL (the second because the arithmetic invariant breaks). Restore, observe PASS. Report both observations.

- [ ] **Step 6: Run the full suite and commit**

Run: `python -m pytest tests/ -q` — expect 570 passed / 4 pre-existing failed.

```bash
git add stackcopy.py tests/test_canon_import_mode.py
git commit -m "feat(canon): emit --plan-json for --canon-import"
```

---

### Task 2: GUI helpers — mode, row labels, detection

**Files:**
- Modify: `stackcopy_gui.py` (new module-level functions, placed beside `parse_plan_json`)
- Test: `tests/test_gui_plan_helpers.py`

**Interfaces:**
- Consumes: the payload from Task 1.
- Produces: `plan_mode(plan) -> str`, `plan_row_labels(plan) -> tuple[str, str, str]`, `detect_canon_mode(plan) -> tuple[bool, str]`.

- [ ] **Step 1: Write the failing test**

```python
CANON_PLAN = {
    "mode": "canon-import", "stacks": 71, "stacked_outputs": 71,
    "stack_inputs": 1718, "others": 1, "total": 1790,
    "canon_frames_detected": 1719, "unresolved": 0,
}
OLYMPUS_PLAN = {
    "stacks": 3, "stacked_outputs": 3, "stack_inputs": 30,
    "others": 9, "total": 42,
}


class PlanModeTests(unittest.TestCase):
    def test_canon_plan_reports_canon(self):
        self.assertEqual(gui.plan_mode(CANON_PLAN), "canon-import")

    def test_a_plan_without_a_mode_field_is_lightroomimport(self):
        """An older CLI sends no mode; it can only have been lightroomimport."""
        self.assertEqual(gui.plan_mode(OLYMPUS_PLAN), "lightroomimport")

    def test_no_plan_is_lightroomimport(self):
        self.assertEqual(gui.plan_mode(None), "lightroomimport")


class PlanRowLabelTests(unittest.TestCase):
    def test_canon_rows_name_canon_things(self):
        stacked, frames, other = gui.plan_row_labels(CANON_PLAN)
        self.assertIn("71", stacked)
        self.assertIn("1718", frames)
        self.assertIn("DNG", frames)
        self.assertIn("1", other)
        self.assertIn("CR3", other)

    def test_olympus_rows_are_unchanged(self):
        stacked, frames, other = gui.plan_row_labels(OLYMPUS_PLAN)
        self.assertIn("3 finished stacked photos", stacked)
        self.assertIn("frames that fed those stacks", frames)
        self.assertIn("single shots and videos", other)

    def test_the_two_modes_do_not_share_wording(self):
        """A Canon card must not be described as single shots and videos."""
        canon = gui.plan_row_labels(CANON_PLAN)
        olympus = gui.plan_row_labels(OLYMPUS_PLAN)
        self.assertNotEqual(canon[1], olympus[1])
        self.assertNotIn("videos", canon[2])


class DetectCanonModeTests(unittest.TestCase):
    def test_cr3_files_tick_the_box_and_say_why(self):
        ticked, reason = gui.detect_canon_mode(CANON_PLAN)
        self.assertTrue(ticked)
        self.assertIn("1719", reason)
        self.assertIn("CR3", reason)

    def test_a_card_without_cr3_does_not_tick(self):
        ticked, reason = gui.detect_canon_mode(OLYMPUS_PLAN)
        self.assertFalse(ticked)
        self.assertEqual(reason, "")

    def test_zero_cr3_reported_explicitly_does_not_tick(self):
        ticked, _ = gui.detect_canon_mode({"canon_frames_detected": 0})
        self.assertFalse(ticked)
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/test_gui_plan_helpers.py -k "PlanMode or PlanRowLabel or DetectCanon" -v`
Expected: FAIL with `AttributeError: module 'stackcopy_gui' has no attribute 'plan_mode'`

- [ ] **Step 3: Write minimal implementation**

Add to `stackcopy_gui.py` immediately after `parse_plan_json`:

```python
CANON_MODE = "canon-import"
LIGHTROOM_MODE = "lightroomimport"


def plan_mode(plan: dict[str, object] | None) -> str:
    """Which import a plan describes.

    A CLI older than Canon support sends no ``mode`` at all, and the only
    import it could have been describing is the Lightroom one.
    """
    if not plan:
        return LIGHTROOM_MODE
    return CANON_MODE if plan.get("mode") == CANON_MODE else LIGHTROOM_MODE


def plan_row_labels(plan: dict[str, object] | None) -> tuple[str, str, str]:
    """The three plan rows, worded for the import being described.

    The rows themselves are the same three ideas in both modes - the finished
    photos, the frames behind them, and everything left over - so only the
    nouns change.
    """
    outputs = plan.get("stacked_outputs") if plan else None
    inputs = plan.get("stack_inputs") if plan else None
    others = plan.get("others") if plan else None
    if plan_mode(plan) == CANON_MODE:
        return (
            f"{outputs} stacked photos from the camera - kept beside their frames",
            f"{inputs} frames converted to DNG - one folder per stack, ready "
            "for Helicon Focus",
            f"{others} loose CR3 files - copied unchanged, they belong to no stack",
        )
    stacked = (
        f"{outputs} finished stacked photos - renamed with 'stacked' added to the name"
        if outputs is not None
        else "Finished stacked photos - renamed with 'stacked' added to the name"
    )
    frames = (
        f"{inputs} frames that fed those stacks - kept in case you want to "
        "stack the RAWs yourself"
        if inputs is not None
        else "Frames that fed those stacks - kept in case you want to stack "
        "the RAWs yourself"
    )
    other = (
        f"{others} single shots and videos - names untouched, dated folders as "
        "Lightroom would make them"
        if others is not None
        else "Single shots and videos - names untouched, dated folders as "
        "Lightroom would make them"
    )
    return stacked, frames, other


def detect_canon_mode(plan: dict[str, object] | None) -> tuple[bool, str]:
    """Whether the card looks like Canon, and the reason to show the user.

    The rule is deliberately just "are there CR3s".  A card holding both CR3
    and ORF is not a real case, and inventing a signal to arbitrate one would
    be speculative.
    """
    if not plan:
        return False, ""
    count = plan.get("canon_frames_detected")
    if not isinstance(count, int) or isinstance(count, bool) or count <= 0:
        return False, ""
    noun = "file" if count == 1 else "files"
    return True, f"found {count} CR3 {noun}"
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python -m pytest tests/test_gui_plan_helpers.py -v`
Expected: PASS, 42 tests (33 existing + 9 new).

- [ ] **Step 5: Prove the tests can fail**

Temporarily make `plan_mode` always return `LIGHTROOM_MODE`; observe `test_canon_plan_reports_canon`, `test_canon_rows_name_canon_things` and `test_the_two_modes_do_not_share_wording` FAIL. Restore, observe PASS. Report the observation.

- [ ] **Step 6: Commit**

```bash
git add stackcopy_gui.py tests/test_gui_plan_helpers.py
git commit -m "feat(gui): mode-aware plan rows and Canon detection"
```

---

### Task 3: GUI helper — building the Canon command line

**Files:**
- Modify: `stackcopy_gui.py` (beside the helpers from Task 2)
- Test: `tests/test_gui_plan_helpers.py`

**Interfaces:**
- Consumes: nothing from Task 2 (independent pure function).
- Produces: `canon_import_args(source, dest, singles, *, plan_json=False, dry=False, verbose=False, dng=True, dng_jobs=1, render=False, method=2, radius=8, smoothing=4) -> list[str]`.

Defaults mirror the CLI's own so the GUI never pins a value the CLI may retune.

- [ ] **Step 1: Write the failing test**

```python
class CanonImportArgsTests(unittest.TestCase):
    def test_minimal_command_is_positional_only(self):
        args = gui.canon_import_args("SRC", "DST", "SINGLES")
        self.assertEqual(args[:4], ["--canon-import", "SRC", "DST", "--singles-dir"])
        self.assertIn("SINGLES", args)
        for flag in ("--no-dng", "--render-helicon", "--dng-jobs",
                     "--helicon-method", "--dry", "--verbose"):
            self.assertNotIn(flag, args)

    def test_defaults_are_not_passed_explicitly(self):
        """Passing the CLI's own defaults would pin values it may retune."""
        args = gui.canon_import_args(
            "SRC", "DST", "SINGLES", dng_jobs=1, method=2, radius=8, smoothing=4
        )
        for flag in ("--dng-jobs", "--helicon-method",
                     "--helicon-radius", "--helicon-smoothing"):
            self.assertNotIn(flag, args)

    def test_non_default_tuning_is_passed(self):
        args = gui.canon_import_args(
            "SRC", "DST", "SINGLES", render=True, method=0, radius=6, smoothing=7
        )
        self.assertIn("--render-helicon", args)
        self.assertIn("--helicon-method", args)
        self.assertEqual(args[args.index("--helicon-method") + 1], "0")

    def test_dng_off_maps_to_no_dng(self):
        self.assertIn("--no-dng", gui.canon_import_args("S", "D", "G", dng=False))

    def test_plan_json_and_dry_are_separate_switches(self):
        args = gui.canon_import_args("S", "D", "G", plan_json=True)
        self.assertIn("--plan-json", args)
        self.assertNotIn("--dry", args)

    def test_every_built_command_is_accepted_by_the_cli(self):
        """A GUI that builds an argv the CLI rejects is broken by construction."""
        parser = stackcopy.build_parser()
        for kwargs in (
            {},
            {"dry": True, "verbose": True},
            {"dng": False},
            {"render": True, "method": 1, "radius": 5, "smoothing": 3},
            {"plan_json": True},
            {"dng_jobs": 8},
        ):
            with self.subTest(**kwargs):
                parser.parse_args(gui.canon_import_args("S", "D", "G", **kwargs))
```

The test file already imports `stackcopy_gui as gui`; add `import stackcopy` beside it if absent.

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/test_gui_plan_helpers.py -k CanonImportArgs -v`
Expected: FAIL with `AttributeError: module 'stackcopy_gui' has no attribute 'canon_import_args'`

- [ ] **Step 3: Write minimal implementation**

```python
CANON_DEFAULT_DNG_JOBS = 1
CANON_DEFAULT_METHOD = 2
CANON_DEFAULT_RADIUS = 8
CANON_DEFAULT_SMOOTHING = 4


def canon_import_args(
    source: str,
    dest: str,
    singles: str,
    *,
    plan_json: bool = False,
    dry: bool = False,
    verbose: bool = False,
    dng: bool = True,
    dng_jobs: int = CANON_DEFAULT_DNG_JOBS,
    render: bool = False,
    method: int = CANON_DEFAULT_METHOD,
    radius: int = CANON_DEFAULT_RADIUS,
    smoothing: int = CANON_DEFAULT_SMOOTHING,
) -> list[str]:
    """Build the CLI arguments for a Canon import.

    Values matching the CLI's own defaults are left out rather than passed:
    the GUI should not pin a number the CLI may later retune.
    """
    args = ["--canon-import", source, dest, "--singles-dir", singles]
    if plan_json:
        args.append("--plan-json")
    if dry:
        args.append("--dry")
    if verbose:
        args.append("--verbose")
    if not dng:
        args.append("--no-dng")
    if dng_jobs != CANON_DEFAULT_DNG_JOBS:
        args += ["--dng-jobs", str(dng_jobs)]
    if render:
        args.append("--render-helicon")
        if method != CANON_DEFAULT_METHOD:
            args += ["--helicon-method", str(method)]
        if radius != CANON_DEFAULT_RADIUS:
            args += ["--helicon-radius", str(radius)]
        if smoothing != CANON_DEFAULT_SMOOTHING:
            args += ["--helicon-smoothing", str(smoothing)]
    return args
```

**Note:** `--helicon-radius` and `--helicon-smoothing` do not exist on the CLI yet. `test_every_built_command_is_accepted_by_the_cli` will fail until Task 4 adds them. Add them in Task 4 before re-running this test; if you reach Step 4 and that one test fails on those two flags, that is expected and Task 4 resolves it — note it in your report rather than deleting the test.

- [ ] **Step 4: Run test to verify it passes**

Run: `python -m pytest tests/test_gui_plan_helpers.py -k CanonImportArgs -v`
Expected: 5 PASS; `test_every_built_command_is_accepted_by_the_cli` FAILS on the two unknown flags until Task 4.

- [ ] **Step 5: Commit**

```bash
git add stackcopy_gui.py tests/test_gui_plan_helpers.py
git commit -m "feat(gui): build Canon import command lines"
```

---

### Task 4: CLI — `--helicon-radius`, `--helicon-smoothing`, and the single-frame skip

**Files:**
- Modify: `stackcopy.py` (`build_parser`, its Canon-only validation, `run_canon_import`)
- Test: `tests/test_canon_import_mode.py`

**Interfaces:**
- Consumes: `helicon_focus.DEFAULT_RADIUS`, `helicon_focus.DEFAULT_SMOOTHING`.
- Produces: `--helicon-radius N`, `--helicon-smoothing N`, both Canon-only; and a single-frame render skip.

The spec's Component 4: `IMG_3377` and `IMG_3786` on the real K: card are one-frame stacks, and Helicon exits 1 on both because there is nothing to stack. A one-frame stack is a valid import but an impossible render.

- [ ] **Step 1: Write the failing test**

```python
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
```

Add `import helicon_focus` to the test file's imports.

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/test_canon_import_mode.py -k "HeliconTuning or SingleFrame" -v`
Expected: FAIL with `AttributeError: 'Namespace' object has no attribute 'helicon_radius'`

- [ ] **Step 3: Write minimal implementation**

In `build_parser`, beside `--helicon-method`:

```python
    parser.add_argument(
        "--helicon-radius",
        type=int,
        default=helicon_focus.DEFAULT_RADIUS,
        metavar="N",
        help=(
            "Helicon Focus radius "
            f"(default: {helicon_focus.DEFAULT_RADIUS})."
        ),
    )
    parser.add_argument(
        "--helicon-smoothing",
        type=int,
        default=helicon_focus.DEFAULT_SMOOTHING,
        metavar="N",
        help=(
            "Helicon Focus smoothing "
            f"(default: {helicon_focus.DEFAULT_SMOOTHING})."
        ),
    )
```

Extend the Canon-only validation beside the existing `--helicon-method` check:

```python
        if (
            args.helicon_radius != helicon_focus.DEFAULT_RADIUS
            or args.helicon_smoothing != helicon_focus.DEFAULT_SMOOTHING
        ) and not args.canon_import:
            parser.error(
                "--helicon-radius and --helicon-smoothing can only be used "
                "with --canon-import."
            )
```

Add the renderability rule beside `run_canon_import`:

```python
def canon_stack_is_renderable(frames) -> bool:
    """Whether Helicon has anything to stack.

    A one-frame stack is a legitimate import - the frame and the camera's own
    composite are both worth keeping - but an impossible render, and Helicon
    exits non-zero when asked.  Skipping it is not a failure.
    """
    return len(frames) >= 2
```

In `run_canon_import`'s render loop, before calling `render_stack`:

```python
            if not canon_stack_is_renderable(frames):
                print(
                    f"  Skipped Helicon for {display_path(stack.dest_dir)}: "
                    f"{len(frames)} frame(s), nothing to stack."
                )
                continue
```

Pass the new values through to `render_stack`:

```python
            result = helicon_focus.render_stack(
                helicon, frames, output,
                method=args.helicon_method,
                radius=args.helicon_radius,
                smoothing=args.helicon_smoothing,
            )
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python -m pytest tests/test_canon_import_mode.py tests/test_gui_plan_helpers.py -q`
Expected: PASS, including `test_every_built_command_is_accepted_by_the_cli` from Task 3, which now finds both flags.

- [ ] **Step 5: Prove the skip can fail**

Temporarily change `canon_stack_is_renderable` to `return True`; observe `test_a_one_frame_stack_is_skipped_not_failed` FAIL. Restore, observe PASS. Report the observation.

- [ ] **Step 6: Commit**

```bash
git add stackcopy.py tests/test_canon_import_mode.py
git commit -m "feat(canon): Helicon radius and smoothing flags, skip single-frame renders"
```

---

### Task 5: CLI — progress reporting during a Canon import

**Files:**
- Modify: `stackcopy.py` (`run_canon_import`)
- Test: `tests/test_canon_import_mode.py`

**Interfaces:**
- Consumes: `_emit_progress(file=None, **fields)` (`stackcopy.py`), which writes `@@SCPROGRESS key=value ... file=NAME` to stderr when `STACKCOPY_PROGRESS=1`.
- Produces: progress events during a Canon import.

Without this, the GUI's running view stays empty for the whole import. On the measured K: card that was three hours and forty-seven minutes — indistinguishable from a hang.

- [ ] **Step 1: Write the failing test**

```python
class CanonProgressTests(unittest.TestCase):
    def test_progress_lines_survive_the_guis_parser(self):
        """Whatever we emit, the GUI must be able to read it."""
        captured = io.StringIO()
        with mock.patch.object(stackcopy, "_PROGRESS_ENABLED", True), \\
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
```

Add the harness beside the other helpers in that file:

```python
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
        with mock.patch.object(stackcopy, "_PROGRESS_ENABLED", True), \\
             mock.patch.object(stackcopy.sys, "stderr", captured), \\
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
```

Add `import io` and `from unittest import mock` to the test file's imports.

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/test_canon_import_mode.py -k CanonProgress -v`
Expected: `test_canon_import_emits_prepare_and_start` FAILS — no phases are emitted at all.

- [ ] **Step 3: Write minimal implementation**

In `run_canon_import`, after the plan is built and the low-space check has run:

```python
    operation_total = (
        len(plan.stacks)
        + sum(len(s.frame_sources) for s in plan.stacks)
        + len(plan.singles)
    )
    _emit_progress(phase="prepare", done=0, total=operation_total)
```

Immediately before the copy loop:

```python
    _emit_progress(phase="start", done=0, total=operation_total)
    done = 0
```

After each composite JPG copy:

```python
        done += 1
        _emit_progress(
            file=os.path.basename(stack.jpg_dest),
            phase="copy", done=done, total=operation_total, role="stacked",
        )
```

After each loose CR3 copy:

```python
        done += 1
        _emit_progress(
            file=os.path.basename(single_dest),
            phase="copy", done=done, total=operation_total, role="single",
        )
```

After each group's conversion completes, inside the `convert_groups` progress
callback (conversion is invoked once per stack folder, so a stack is the
finest granularity the converter exposes):

```python
        def _on_group_converted(result):
            nonlocal done
            done += result.converted + result.skipped
            _emit_progress(
                file=os.path.basename(result.dest_dir),
                phase="convert", done=done, total=operation_total, role="frames",
            )
```

and pass it as `on_progress=_on_group_converted`.

After each render:

```python
            _emit_progress(
                file=os.path.basename(output),
                phase="render", done=done, total=operation_total, role="render",
            )
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python -m pytest tests/test_canon_import_mode.py -k CanonProgress -v`
Expected: PASS, 3 tests.

- [ ] **Step 5: Prove the tests can fail**

Temporarily comment out the `phase="start"` emission; observe
`test_canon_import_emits_prepare_and_start` FAIL. Restore, observe PASS.
Report the observation.

- [ ] **Step 6: Run the full suite and commit**

Run: `python -m pytest tests/ -q` — expect no new failures.

```bash
git add stackcopy.py tests/test_canon_import_mode.py
git commit -m "feat(canon): report progress during a Canon import"
```

---

### Task 6: GUI — wire the mode into scan, preview, and import

**Files:**
- Modify: `stackcopy_gui.py` — `_refresh_idle_plan` (`:1503`), the scan args (`:1393`), the import args (`:1657`)
- Test: `tests/test_gui_plan_helpers.py`

**Interfaces:**
- Consumes: `plan_mode`, `plan_row_labels`, `detect_canon_mode`, `canon_import_args` from Tasks 2-3.
- Produces: no new public functions; wires existing ones into the widget.

- [ ] **Step 1: Write the failing test**

The widget itself is not instantiated in tests. What is testable is that the
row-rendering path uses the helper rather than its own inline strings:

```python
class PlanRowsComeFromTheHelperTests(unittest.TestCase):
    def test_the_gui_module_has_no_second_copy_of_the_row_wording(self):
        """The row strings must live in one place, or the modes drift apart."""
        source = (ROOT / "stackcopy_gui.py").read_text(encoding="utf-8")
        self.assertEqual(source.count("finished stacked photos - renamed"), 1)
        self.assertEqual(source.count("single shots and videos - names untouched"), 1)
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/test_gui_plan_helpers.py -k PlanRowsComeFromTheHelper -v`
Expected: FAIL — the wording appears twice, once in `plan_row_labels` (Task 2) and once inline in `_refresh_idle_plan`.

- [ ] **Step 3: Write minimal implementation**

In `_refresh_idle_plan`, replace the three inline `set(...)` blocks (`stackcopy_gui.py:1533-1561`) with:

```python
        stacked_label, frames_label, other_label = plan_row_labels(plan)
        self.plan_headline_vars["stack_output"].set(stacked_label)
        self.plan_headline_vars["stack_input"].set(frames_label)
        self.plan_headline_vars["other"].set(other_label)
```

and make the destination paths mode-aware just below:

```python
        if plan:
            if plan_mode(plan) == CANON_MODE:
                first_path = str(plan.get("dest_stacks") or self.dst_var.get())
                second_path = first_path
                third_path = str(plan.get("dest_singles") or self.stk_var.get())
            else:
                first_path = str(plan.get("dest_lightroom") or self.dst_var.get())
                second_path = str(plan.get("dest_stack_input") or self.stk_var.get())
                third_path = first_path
```

then use `first_path`, `second_path`, `third_path` where the existing code used
`lightroom_path` and `stack_path`.

In the scan (`:1393`), choose the command by mode:

```python
        if self.canon_mode_var.get():
            args = canon_import_args(
                source,
                self.dst_var.get().strip(),
                self.stk_var.get().strip(),
                plan_json=True,
            )
            command, env = cli_command(args)
        else:
            args = ["--lightroomimport", source, "--plan-json"]
            if not self.detect_stacks_var.get():
                args.append("--no-stack-detection")
            command, env = cli_command(args)
            env["STACKCOPY_LIGHTROOM_IMPORT_DIR"] = self.dst_var.get().strip()
            env["STACKCOPY_STACK_INPUT_DIR"] = self.stk_var.get().strip()
```

In the import (`:1657`), the same split, using the Advanced values:

```python
        if self.canon_mode_var.get():
            args = canon_import_args(
                source, lightroom, stack_input,
                dry=preview,
                verbose=self.verbose_var.get(),
                dng=self.canon_dng_var.get(),
                dng_jobs=int(self.canon_dng_jobs_var.get() or CANON_DEFAULT_DNG_JOBS),
                render=self.canon_render_var.get(),
                method=int(self.canon_method_var.get()),
                radius=int(self.canon_radius_var.get() or CANON_DEFAULT_RADIUS),
                smoothing=int(
                    self.canon_smoothing_var.get() or CANON_DEFAULT_SMOOTHING
                ),
            )
```

leaving the existing `--lightroomimport` construction as the else branch.

- [ ] **Step 4: Run test to verify it passes**

Run: `python -m pytest tests/ -q`
Expected: no new failures.

- [ ] **Step 5: Commit**

```bash
git add stackcopy_gui.py tests/test_gui_plan_helpers.py
git commit -m "feat(gui): run Canon imports from the window"
```

---

### Task 7: GUI — the controls

**Files:**
- Modify: `stackcopy_gui.py` — variable setup (`:680-695`), main panel, `_build_actions` (`:1018-1054`), `_save_current_defaults` (`:1365`)
- Test: `tests/test_gui_update_state.py`

**Interfaces:**
- Consumes: `detect_canon_mode` (Task 2), `CANON_DEFAULT_*` (Task 3).
- Produces: the widget variables `canon_mode_var`, `canon_dng_var`, `canon_dng_jobs_var`, `canon_render_var`, `canon_method_var`, `canon_radius_var`, `canon_smoothing_var`.

Canon-specific controls sit on the main panel and appear only when a Canon
card is detected; Helicon tuning lives in Advanced. The Canon block is hidden
rather than disabled for a non-Canon card — it has no meaning there at all.
The Helicon sub-controls are disabled rather than hidden when rendering is
off: they are meaningful, just not yet active.

- [ ] **Step 1: Write the failing test**

```python
class CanonStateRoundTripTests(unittest.TestCase):
    def test_every_canon_setting_round_trips(self):
        state = {
            "canon_mode": True, "canon_dng": False, "canon_dng_jobs": 8,
            "canon_render": True, "canon_method": 1,
            "canon_radius": 6, "canon_smoothing": 7,
        }
        self.assertTrue(gui.state_flag(state, "canon_mode"))
        self.assertFalse(gui.state_flag(state, "canon_dng", True))
        self.assertTrue(gui.state_flag(state, "canon_render"))
        self.assertEqual(gui.state_text(state, "canon_dng_jobs"), "8")
        self.assertEqual(gui.state_text(state, "canon_method"), "1")
        self.assertEqual(gui.state_text(state, "canon_radius"), "6")
        self.assertEqual(gui.state_text(state, "canon_smoothing"), "7")

    def test_absent_canon_settings_fall_back_to_the_cli_defaults(self):
        self.assertEqual(
            gui.state_text({}, "canon_dng_jobs", str(gui.CANON_DEFAULT_DNG_JOBS)),
            str(gui.CANON_DEFAULT_DNG_JOBS),
        )
        self.assertFalse(gui.state_flag({}, "canon_mode"))
        self.assertTrue(gui.state_flag({}, "canon_dng", True))
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/test_gui_update_state.py -k CanonStateRoundTrip -v`
Expected: FAIL with `AttributeError: module 'stackcopy_gui' has no attribute 'CANON_DEFAULT_DNG_JOBS'` (it is defined in Task 3; this confirms the import path).

- [ ] **Step 3: Write minimal implementation**

Add the variables beside the existing ones (`:690-695`):

```python
        self.canon_mode_var = ctk.BooleanVar(value=state_flag(saved, "canon_mode"))
        self.canon_reason_var = ctk.StringVar(value="")
        self.canon_dng_var = ctk.BooleanVar(
            value=state_flag(saved, "canon_dng", True)
        )
        self.canon_dng_jobs_var = ctk.StringVar(
            value=state_text(saved, "canon_dng_jobs", str(CANON_DEFAULT_DNG_JOBS))
        )
        self.canon_render_var = ctk.BooleanVar(
            value=state_flag(saved, "canon_render")
        )
        self.canon_method_var = ctk.StringVar(
            value=state_text(saved, "canon_method", str(CANON_DEFAULT_METHOD))
        )
        self.canon_radius_var = ctk.StringVar(
            value=state_text(saved, "canon_radius", str(CANON_DEFAULT_RADIUS))
        )
        self.canon_smoothing_var = ctk.StringVar(
            value=state_text(saved, "canon_smoothing", str(CANON_DEFAULT_SMOOTHING))
        )
```

Add them to the saved state (`:1365`):

```python
                "canon_mode": bool(self.canon_mode_var.get()),
                "canon_dng": bool(self.canon_dng_var.get()),
                "canon_dng_jobs": self.canon_dng_jobs_var.get(),
                "canon_render": bool(self.canon_render_var.get()),
                "canon_method": self.canon_method_var.get(),
                "canon_radius": self.canon_radius_var.get(),
                "canon_smoothing": self.canon_smoothing_var.get(),
```

Build the Canon block on the main panel, immediately above the plan section,
in a frame kept as `self.canon_frame` so it can be shown and hidden:

```python
    def _build_canon_section(self) -> None:
        self.canon_frame = ctk.CTkFrame(self.body, fg_color="transparent")
        self.canon_frame.grid(row=3, column=0, sticky="ew", padx=22, pady=(0, 4))
        self.canon_check = ctk.CTkCheckBox(
            self.canon_frame,
            text="Canon card (CR3 to DNG)",
            variable=self.canon_mode_var,
            command=self._on_canon_mode_changed,
        )
        self.canon_check.grid(row=0, column=0, sticky="w")
        self.canon_reason = ctk.CTkLabel(
            self.canon_frame, textvariable=self.canon_reason_var
        )
        self.canon_reason.grid(row=0, column=1, sticky="w", padx=(10, 0))
        self.canon_dng_check = ctk.CTkCheckBox(
            self.canon_frame,
            text="Convert CR3 to DNG",
            variable=self.canon_dng_var,
            command=self._save_current_defaults,
        )
        self.canon_dng_check.grid(row=1, column=0, sticky="w", pady=(8, 0))
        self.canon_jobs_entry = ctk.CTkEntry(
            self.canon_frame, textvariable=self.canon_dng_jobs_var, width=50
        )
        self.canon_jobs_entry.grid(row=1, column=1, sticky="w", padx=(10, 0),
                                   pady=(8, 0))
        self.canon_frame.grid_remove()
```

Add the Helicon block to `advanced_frame`, below the existing checkboxes:

```python
        self.helicon_check = ctk.CTkCheckBox(
            self.advanced_frame,
            text="Render with Helicon Focus",
            variable=self.canon_render_var,
            command=self._on_helicon_toggle,
        )
        self.helicon_check.grid(row=1, column=0, sticky="w", pady=(10, 0))
        self.helicon_method = ctk.CTkSegmentedButton(
            self.advanced_frame,
            values=["A", "B", "C"],
            command=self._on_helicon_method_changed,
        )
        self.helicon_method.set(["A", "B", "C"][int(self.canon_method_var.get())])
        self.helicon_method.grid(row=1, column=1, sticky="w", padx=(22, 0),
                                 pady=(10, 0))
        self.helicon_radius = ctk.CTkEntry(
            self.advanced_frame, textvariable=self.canon_radius_var, width=50
        )
        self.helicon_radius.grid(row=1, column=2, sticky="w", padx=(22, 0),
                                 pady=(10, 0))
        self.helicon_smoothing = ctk.CTkEntry(
            self.advanced_frame, textvariable=self.canon_smoothing_var, width=50
        )
        self.helicon_smoothing.grid(row=1, column=3, sticky="w", padx=(10, 0),
                                    pady=(10, 0))
```

Add the handlers:

```python
    def _on_canon_mode_changed(self) -> None:
        self._save_current_defaults()
        self._refresh_idle_plan()
        self._start_plan_scan()

    def _on_helicon_toggle(self) -> None:
        state = "normal" if self.canon_render_var.get() else "disabled"
        for widget in (
            self.helicon_method, self.helicon_radius, self.helicon_smoothing
        ):
            widget.configure(state=state)
        self._save_current_defaults()

    def _on_helicon_method_changed(self, value: str) -> None:
        self.canon_method_var.set(str(["A", "B", "C"].index(value)))
        self._save_current_defaults()

    def _apply_canon_detection(self) -> None:
        """Show the Canon block when the scan found CR3 files, and say why."""
        detected, reason = detect_canon_mode(self._plan)
        self.canon_reason_var.set(reason)
        if detected or self.canon_mode_var.get():
            self.canon_frame.grid()
        else:
            self.canon_frame.grid_remove()
        if detected and not self.canon_mode_var.get():
            self.canon_mode_var.set(True)
            self._save_current_defaults()
```

Call `self._build_canon_section()` from `__init__` between
`_build_mode_section()` and `_build_plan_section()`, call
`self._apply_canon_detection()` at the end of `_refresh_idle_plan`, and call
`self._on_helicon_toggle()` once at the end of `_build_actions` so the
sub-controls start in the right state.

- [ ] **Step 4: Run test to verify it passes**

Run: `python -m pytest tests/ -q`
Expected: no new failures.

- [ ] **Step 5: Launch the GUI and confirm by eye**

```bash
STACKCOPY_EXIFTOOL="C:\\Tools\\exiftool\\exiftool.exe" python stackcopy_gui.py
```

Point it at `K:/DCIM/117EOSR6`. Confirm: the Canon block appears with
"found 1719 CR3 files"; the plan rows read 71 / 1718 / 1; Advanced holds the
Helicon controls and they are disabled until "Render with Helicon Focus" is
ticked. Report what you saw. Do NOT press Import.

- [ ] **Step 6: Commit**

```bash
git add stackcopy_gui.py tests/test_gui_update_state.py
git commit -m "feat(gui): Canon controls on the main panel, Helicon in Advanced"
```

---

### Task 8: Documentation

**Files:**
- Modify: `README.md`, `ChangeLog.md`, `stackcopy.py:32` (`STACKCOPY_VERSION`)

- [ ] **Step 1: Update README.md**

In the GUI section, document that a Canon card is detected automatically and
what the Canon plan rows mean. Add the two new CLI flags to the options list.
State honestly that the GUI's Canon path has been exercised against one camera
model (EOS R6 Mark II) on two cards, as the existing text does for Olympus.

- [ ] **Step 2: Update ChangeLog.md and bump the version**

Add an entry for 1.8.0 describing Canon support in the GUI, the two Helicon
tuning flags, the single-frame render skip, and Canon progress reporting. Bump
`STACKCOPY_VERSION` to `"1.8.0"` in the same commit —
`.github/workflows/build-gui.yml` validates that the tag, the constant and the
ChangeLog agree.

Update `tests/test_exiftool_capability.py`'s version tripwire to match: rename
`test_the_version_is_still_1_7_0` to `test_the_version_is_still_1_8_0` and
change its literal. That test is deliberately version-specific — it exists to
fail on a bump so the bump is a conscious act. Do not make it dynamic.

- [ ] **Step 3: Run the full suite**

Run: `python -m pytest tests/ -q`
Expected: 4 pre-existing failures only.

- [ ] **Step 4: Run ruff**

Run: `ruff check --no-cache stackcopy.py stackcopy_gui.py tests/`
Expected: only the three pre-existing findings in upstream's own test files
(`test_exiftool_capability.py` F401, `test_gui_dependency_bootstrap.py` E402,
`test_other_card_files.py` E402). Anything else is yours.

- [ ] **Step 5: Commit**

```bash
git add README.md ChangeLog.md stackcopy.py tests/test_exiftool_capability.py
git commit -m "docs(gui): document Canon import in the GUI"
```

---

## Self-Review

**Spec coverage.** Component 1 (`--plan-json`) → Task 1. Component 2 (detection and rows) → Tasks 2 and 6. Component 3 (control placement) → Tasks 3 and 7. Component 4 (single-frame skip) → Task 4. Component 5 (progress) → Task 5. Component 6 (testing) → distributed; every task ends with tests, and the round-trip requirements the spec names appear as `test_payload_survives_the_guis_own_validator` (Task 1), `test_every_built_command_is_accepted_by_the_cli` (Task 3) and `test_progress_lines_survive_the_guis_parser` (Task 5).

**Placeholders.** None. Every code step carries the code; every test step carries the assertions.

**Type consistency.** `plan_mode`, `plan_row_labels`, `detect_canon_mode` and `canon_import_args` keep the same signatures between Tasks 2-3 where they are defined and Tasks 6-7 where they are used. `CANON_DEFAULT_DNG_JOBS` / `_METHOD` / `_RADIUS` / `_SMOOTHING` are defined once in Task 3 and referenced in Tasks 6 and 7. The widget variables named in Task 6's command construction are exactly those created in Task 7 — Task 7 must therefore land before the GUI is launched, which Task 7's own Step 5 does.

**One ordering note carried into execution:** Task 3 writes a test that fails until Task 4 adds `--helicon-radius` and `--helicon-smoothing`. That is called out in Task 3's Step 3 and resolved in Task 4's Step 4. An implementer must not delete the test to get green.

**Dependency.** This work assumes `fix-advanced-toggle-label` is merged: without its two fixes the Advanced section's caret contradicts its state and its content opens below the fold, which the Helicon controls added here would inherit. Both are already merged into `canon-gui-integration`.
