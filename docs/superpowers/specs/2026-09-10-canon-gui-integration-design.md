# Canon import in the Stackcopy GUI

**Date:** 2026-09-10
**Status:** Approved design, not yet implemented
**Target:** Upstream contribution to `AlanRockefeller/stackcopy`
**Builds on:** `docs/superpowers/specs/2026-09-09-canon-stack-dng-design.md` (the `--canon-import` CLI mode)

## Problem

`--canon-import` exists only on the command line. The GUI is a dedicated
front-end for `--lightroomimport`: it names that mode in its module docstring
and hard-codes it in both places it builds an argument list
(`stackcopy_gui.py:1393` for the plan scan, `:1657` for the import itself).
A photographer importing a Canon card has to drop to a terminal, and there is
no way to reach the Helicon Focus rendering options at all.

Three concrete mismatches sit behind the missing checkbox:

| The GUI assumes | Canon import does |
|---|---|
| The plan scan can run `--plan-json` | The CLI refuses: `--plan-json can only be used with --lightroomimport.` |
| Destinations arrive as environment variables (`STACKCOPY_LIGHTROOM_IMPORT_DIR`, `STACKCOPY_STACK_INPUT_DIR`) | A positional `DEST_DIR` plus `--singles-dir` |
| The card may be moved from or copied from | Copy only; the card is never modified |

## Evidence

Measured against a real Canon EOS R6 Mark II card (`K:\DCIM\117EOSR6`,
1,790 files, 50 GB) using the completed CLI mode:

```
71 stack(s), 1718 frame(s), 1 loose CR3 file(s).
```

That is the shape the GUI must render. It maps onto the existing plan payload
without stretching it — see Component 1.

The GUI is well suited to the change. Roughly 25 module-level pure helper
functions (`parse_plan_json`, `dated_folder_summary`, `import_button_label`,
`describe_other_card_files` …) carry the logic, with 84 tests across
`tests/test_gui_plan_helpers.py`, `tests/test_gui_update_state.py` and
`tests/test_gui_dependency_bootstrap.py` exercising them without instantiating
a widget. Settings already persist to `gui-state.json` through `state_text()`
and `state_flag()`, and an existing collapsible **Advanced** section holds the
three current checkboxes.

## Design

Chosen approach: teach the existing helpers about mode, rather than building a
parallel set of Canon-specific ones. The two workflows are closer than they
appear — both are "composites, the frames behind them, and leftovers" — so one
code path keeps the existing test coverage meaningful. A parallel set was
considered and rejected as structural duplication for shapes that turn out to
coincide.

### Component 1 — `--plan-json` for `--canon-import`

The existing payload's core invariant is
`total == stacked_outputs + stack_inputs + others`. Canon satisfies it exactly:

| Field | Olympus meaning | Canon meaning | K: card |
|---|---|---|---|
| `stacks` | stacks detected | stacks detected | 71 |
| `stacked_outputs` | in-camera JPG outputs | composite JPGs | 71 |
| `stack_inputs` | frames that fed stacks | CR3 frames converted to DNG | 1718 |
| `others` | ordinary photos and videos | loose CR3 files | 1 |
| `total` | — | — | 1790, the card's file count |

Canon therefore emits the same contract, with three additions:

- `mode`: `"canon-import"` or `"lightroomimport"`, so the GUI can label rows
  and choose destinations without guessing.
- `dest_stacks` and `dest_singles`, the Canon equivalents of `dest_lightroom`
  and `dest_stack_input`.
- `unresolved`: composites in a target folder that could not be grouped. The
  CLI already tracks these and names them; the count belongs in the plan so the
  GUI can surface it rather than silently omitting it.
- `canon_frames_detected`: how many `.cr3` files the scan saw, which is what
  drives mode auto-detection (Component 2).

`dest_dirs` and `dest_dates` already carry the dated folders and need no
change. The `--plan-json` validation in `parse_arguments` relaxes from
"`--lightroomimport` only" to "either import mode"; every other mode still
rejects it.

An older CLI emits no `mode` field. `parse_plan_json` treats its absence as
`"lightroomimport"`, preserving the existing behaviour for a GUI paired with an
older binary — the same tolerance it already applies to absent `dest_dirs`.

### Component 2 — Mode detection and the plan rows

The three preview rows survive intact and are relabelled by mode:

```
Olympus                            Canon
──────────────────────────────     ──────────────────────────────
Stacked photos            71       Stacked photos            71
Frames that fed them    1718       Frames converted to DNG  1718
Other photos and videos    1       Loose CR3 files             1
```

**Detection.** The plan scan already walks the card, so it reports
`canon_frames_detected`. When that count is non-zero the GUI ticks
**"Canon card (CR3 to DNG)"** and states the reason beside it ("found 1719 CR3
files"). The rule is deliberately just "are there CR3s": a card holding both
CR3 and ORF is not a real case, and inventing a second signal to arbitrate one
would be speculative. The checkbox is a real override in both directions:
ticking it on a card with no CR3s is allowed, and produces the CLI's own
"No Canon files found" message rather than a GUI-invented error.

**Move versus copy.** Canon import copies and never modifies the card. When
Canon mode is active the move/copy control is disabled and shows why, rather
than silently ignoring a choice the user made.

**Destinations.** Canon mode passes `DEST_DIR` positionally and
`--singles-dir`, instead of setting `STACKCOPY_LIGHTROOM_IMPORT_DIR` and
`STACKCOPY_STACK_INPUT_DIR`. The two destination pickers stay, relabelled
"Stacked images" and "Single images".

### Component 3 — The Advanced section

All Canon controls live in Advanced, alongside the existing three:

```
Advanced
   [x] Verbose log      [x] Detect stacks      [ ] Show stack debug
   ------------------------------------------------------------------
   [x] Convert CR3 to DNG               Parallel conversions [ 4 ]
   [ ] Render with Helicon Focus
          Method  ( A ) ( B ) (*C)      Radius [ 8 ]   Smoothing [ 4 ]
```

- **Convert CR3 to DNG** maps to the absence of `--no-dng`; default on.
- **Parallel conversions** maps to `--dng-jobs`, clamped 1-32 as the CLI does.
- **Render with Helicon Focus** maps to `--render-helicon`; default off,
  because it materially lengthens a run.
- **Method** maps to `--helicon-method` (0=A, 1=B, 2=C), defaulting to C as the
  CLI does. A segmented control rather than a dropdown: it is a real choice
  between three peers, not a list.
- **Radius** and **Smoothing** are integer fields defaulting to the CLI's 8 and
  4. They are passed only when changed from those defaults, so the GUI does not
  pin values the CLI may later retune.

Canon-only controls are disabled, not hidden, when Canon mode is off — a hidden
control is one the user cannot discover. The Helicon sub-controls are disabled
unless **Render with Helicon Focus** is ticked.

Every value persists to `gui-state.json` through the existing `state_text()` /
`state_flag()` helpers, under keys `canon_mode`, `canon_dng`, `canon_dng_jobs`,
`canon_render`, `canon_method`, `canon_radius`, `canon_smoothing`.

### Component 4 — Skip Helicon for single-frame stacks

Found on the real K: card: `IMG_3377` and `IMG_3786` are one-frame stacks, and
Helicon exits 1 on both because there is nothing to stack. A one-frame stack is
a legitimate import (the frame and its composite are both kept) but an
impossible render.

`run_canon_import` skips rendering a stack with fewer than two frames, and says
so, instead of invoking Helicon and reporting a failure. This is a CLI fix that
belongs with this work because the GUI would otherwise surface those as errors.

### Component 5 — Progress reporting during a Canon import

`run_canon_import` emits no progress at all. All six `_emit_progress()` calls
in `stackcopy.py` belong to the `--lightroomimport` path, so the GUI's running
view — phase, current file, counts, estimated time remaining — would stay empty
for the whole of a Canon import. On the measured K: card that import ran for
three hours and forty-seven minutes. A progress bar that never moves for that
long is indistinguishable from a hang, and a user would reasonably kill it.

`run_canon_import` therefore emits the same sentinel stream the GUI already
parses, via the existing `_emit_progress()` helper and `STACKCOPY_PROGRESS=1`
contract, with no change to `parse_progress()` on the GUI side:

- `phase="prepare"` once the plan is built, carrying the total operation count.
- `phase="start"` when execution begins.
- a per-file event as each composite JPG is copied, each loose CR3 is copied,
  each stack's conversion completes, and each render completes, naming the file
  and the role it plays.

Conversion and rendering report per *stack* rather than per file, because the
converter is invoked once per stack folder and Helicon once per stack; that is
the finest granularity the underlying tools actually expose.

### Component 6 — Testing

New logic goes into module-level pure helpers so it is testable without a
widget, matching where the existing 84 GUI tests live:

- `canon_plan_rows(plan)` — the three labelled rows for a given plan payload.
- `detect_canon_mode(plan)` — whether to tick the checkbox, and the reason
  string shown beside it.
- `helicon_args_from_state(state)` — the argv fragment for the Helicon and DNG
  options, including the "only pass what differs from the default" rule.
- `canon_import_args(...)` — the full argument list for a Canon run.

Progress coverage: the sentinel lines `run_canon_import` emits must survive the
GUI's own `parse_progress()` unchanged, tested by feeding real emitted lines
through it rather than hand-written approximations.

Round-trip coverage: a Canon `--plan-json` payload must survive
`parse_plan_json` unchanged, and every argv the GUI builds must be accepted by
`build_parser()` — a test that would have caught the `--plan-json` refusal that
motivated this work.

Cross-platform note: the CLI's macOS tool-discovery paths remain untested on
real hardware. This spec does not change that, and the GUI adds no new
platform-specific code of its own.

## Scope

In scope: Canon mode in the GUI with full plan preview, the Advanced controls
above, `--plan-json` for `--canon-import`, and the single-frame render skip.

Out of scope: a third camera family;
rendering options beyond method, radius and smoothing; and any change to the
Olympus workflow, which must behave exactly as it does today.
