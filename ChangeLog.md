# Change Log

## **1.8.0 - 2026-09-18**

- **The GUI now handles Canon depth-composite cards.** A card with CR3 files is
  detected automatically as soon as it is scanned - the same scan that already
  runs for every card - and the Canon controls (convert to DNG, parallel
  conversion jobs) appear on the main panel with the reason shown alongside the
  checkbox (e.g. "found 1719 CR3 files"). Helicon Focus's method, radius and
  smoothing live in the existing Advanced section and stay disabled until
  rendering is turned on. The three plan rows are reworded for a Canon import
  (stacked photos from the camera, frames converted to DNG, loose CR3 files)
  instead of reusing the Olympus wording. This is driven entirely by
  `--canon-import --plan-json`, which now reports the same payload shape
  `--lightroomimport --plan-json` already did, with `mode`, `dest_stacks`,
  `dest_singles`, `unresolved` and `canon_frames_detected` added.
  `--lightroomimport --plan-json` also now reports `canon_frames_detected`, so
  a first-ever scan of a Canon card - before the user has ever run
  `--canon-import` - is enough for the GUI to offer the Canon checkbox.
  Nothing about the existing Olympus/OM plan payload changed beyond that one
  additive field.
- **A Canon import now reports progress**, using the same mechanism and the
  same role vocabulary the Lightroom import already used, so the GUI's
  running view - phase, current file, the stacked/frames/singles counters,
  the bar and the ETA - is driven by a Canon import as it is by an Olympus
  one. DNG conversion and Helicon rendering report as their own phases
  ("Converting frames to DNG", "Rendering stacks with Helicon Focus"), and
  the run ends on an explicit terminal event so the bar reaches 100%. Before
  this change a Canon import emitted no progress at all; a measured
  1719-frame import ran three hours and forty-seven minutes against a blank
  progress bar. One difference remains by design: `--canon-import --dry`
  performs nothing and so reports nothing, and the GUI says so explicitly
  ("Previewing - nothing is being written") rather than showing a live file
  list a preview does not produce.
- **Canon mode no longer misdirects the destination buttons or the Move/Copy
  control.** Each plan row's "Change" button now edits the folder that row
  actually displays, in both modes. The Move/Copy control is hidden in Canon
  mode, where `--canon-import` always copies and is never passed a move
  flag, and the button and running view say Copy accordingly.
- **An automatically-ticked Canon box unticks itself again.** If detection
  ticked the box and the next card's Canon scan finds nothing, the GUI drops
  back to the Lightroom import and rescans instead of reporting an empty
  plan for a full card. A box ticked by hand is left alone.
- The Canon plan payload now carries the same ExifTool capability fields
  every Lightroom plan does, so the header strip's ExifTool warning no
  longer disappears in Canon mode.
- **Helicon Focus is no longer invoked for a one-frame stack.** There is
  nothing to composite, and Helicon exits non-zero when asked to try; that
  used to be reported as a render failure. It is now skipped with an explicit
  message ("N frame(s), nothing to stack") instead, found from a real card
  that produced two such one-frame stacks.
- **Two new `--canon-import` flags tune Helicon Focus's render**:
  `--helicon-radius N` and `--helicon-smoothing N`, defaulting to Helicon's
  own defaults of 8 and 4. Like `--helicon-method`, both are rejected outside
  `--canon-import`.
- The GUI's Canon path - detection, controls, progress, and the flags above -
  has so far been exercised against the same single camera body as the
  underlying `--canon-import` detection: the Canon EOS R6 Mark II. How many
  distinct cards that covers is not recorded anywhere in this repository, so
  no count is claimed. The Olympus/OM workflow itself is unchanged, aside
  from the one additive `canon_frames_detected` field on its plan payload.

## **1.7.0 - 2026-09-10**

- **Added `--canon-import SRC_DIR DEST_DIR`**, a new mode for Canon in-camera depth-composite focus stacks. It detects each composite JPG via the `FocusBracketingDepthComposite` MakerNote, then finds its exact CR3 frames by matching `SubSecDateTimeOriginal` against the composite's own timestamp - not by the camera's configured `FocusBracketingImageCount`, which is the camera's setting rather than the true frame count and is reported only as an informational note. When no frame's timestamp matches, a cadence fallback groups frames by a threshold relative to the run's own recent shot interval. A stack may span a camera folder rollover (`107EOSR6` into `108EOSR6`), with the previous folder scanned only as borrowable context. ExifTool is optional here exactly as it already is for Olympus/OM detection; without it, detection falls back to the cadence heuristic. The card is never modified - everything is a copy.
- **CR3 frames are converted to DNG** using Adobe DNG Converter (15.1 or newer, found via `STACKCOPY_DNG_CONVERTER` or the standard install location), one converter invocation per stack folder. Because raw support is per-camera-model rather than per-format, Stackcopy always trial-converts one real frame before importing anything, so an incompatible converter fails immediately with an actionable message instead of partway through a large import. `--no-dng` copies CR3 frames as-is instead. Conversion is resumable: a stack whose DNGs already exist is skipped, and a failed stack is reported without stopping the rest. `--dng-jobs N` (1-32) converts multiple stacks in parallel. Frames are converted into a staging directory and moved into place only once the converter has both exited 0 and produced the file, so a run killed mid-conversion never leaves a truncated DNG that a later run would mistake for finished work. Destination free space is checked before the copy begins, as in every other mode.
- **`--render-helicon` renders each stack with Helicon Focus** to `<stack> helicon.jpg` beside its folder, passing an explicit frame list rather than the folder so the camera's own composite JPG never enters the render. `--helicon-method {0,1,2}` selects method A/B/C (default C). The frame list comes from the import plan in capture order, not from listing the folder: methods A and B are order-dependent, and a stack spanning the camera's `IMG_9999` to `IMG_0001` rollover sorts by name into the wrong order. A stack missing any expected frame is reported and skipped rather than rendered short. An existing render is left alone, so an interrupted run resumes.
- **Each stack lands in its own dated folder** (`DEST_DIR/YYYY-MM-DD/IMG_0014/`, named for its composite JPG) so Helicon Focus batch mode can consume the tree directly, dated by EXIF `DateTimeOriginal` rather than filesystem modification time since mtime does not survive a copy. The date is read from the EXIF text as the camera wrote it, so the folder a frame lands in never depends on the timezone of the machine doing the import. Loose CR3s belonging to no stack are copied to a sibling `--singles-dir`, kept outside `DEST_DIR` so Helicon's recursive folder import can't mistake them for a stack.
- Tested against a single camera body, the Canon EOS R6 Mark II, on a single card.

## **1.6.0**

- **The GUI now shows every folder your photos will go into.** If a card holds photos from more than one day, each day gets its own dated folder. The GUI used to show only the newest one. It now says how many dates and folders there are, and you can click to see the full list before you import.
- **Redesigned the GUI around a preview-first workflow.** Stackcopy now scans the card before importing, shows what it found and where each type of file will go, provides clearer **Move**, **Copy**, and **Preview** choices, and shows useful progress, ETA, counters, and safe-stop controls while importing.
- **Much better Olympus/OM System focus-stack detection.** When ExifTool is available, Stackcopy reads the camera's own stack metadata to identify stacked photos and their exact frame counts. This also handles stacks with missing RAW files, incomplete inputs, or more than 15 frames better than the old heuristic. The existing detection method remains as a fallback.
- **ExifTool is now included with the Windows app**, so current OM System stack detection works without installing anything extra. macOS and source installations clearly report when ExifTool is missing or too old.
- **Added update notifications to the GUI.** Stackcopy can tell you when a newer version is available, with options to skip it or be reminded later. It doesn't download or install updates automatically.
- **Improved support for camera-card contents.** Olympus/OM `.ORI` files are preserved correctly, unrecognized files are clearly reported and left on the card, and conflicting files such as an `.ORF` and `.DNG` with the same name are left untouched rather than guessed at.
- **The GUI now flags non-photo files on the card.** Alongside the photo and video count, Stackcopy lists other files it found and won't import (documents, archives, audio, and the like), so you can copy anything you want to keep before formatting. Tiny camera-generated files such as folder catalogs, print marks, and thumbnails are ignored, and Stackcopy suggests formatting the card in the camera for a clean file structure and longer card life.
- **Made file moves and overwrites substantially safer.** Stackcopy now protects against destination files changing during an import, accidental same-file operations, case-only path differences, and other edge cases that could previously overwrite or remove the wrong file. When safety cannot be verified, it keeps the source instead.
- **Made cross-drive/card moves safer.** Stackcopy verifies that a copied file is safely written before deleting the original, and no longer reports a move as successful when the destination was copied but the source could not be removed.
- **Improved interrupted and partially failed imports.** Ctrl-C stops cleanly, completed work is preserved, re-running is safe, and recovered files or other incomplete outcomes are clearly distinguished from a fully successful import.
- **Fixed several stack-sorting edge cases**, including stacks crossing camera folders, unrelated frames being borrowed from a previous folder, companion files being split across date folders, unsafe source/destination overlap, invalid timestamps, and exhausted collision filenames.

## **1.5.9 - 2026-08-24**

### Fixed

- Fixed `--today`, `--yesterday`, and `--date` filtering in `--lightroomimport` so it applies consistently to photos, RAW+JPG pairs, and videos using filesystem modification dates.
- Keep detected stacks together when date filtering, even when individual stack input frames cross a midnight/date boundary.
- Fixed parallel copy result handling so failed operations are no longer incorrectly counted as successful.
- Fixed transferred-byte and throughput accounting for parallel Lightroom and copy operations.
- Improved Lightroom import summaries to report the number of actual stacks along with stacked outputs, input files, and remaining files.
- Added regression tests for date-filtered imports, stack boundary handling, collisions, repeat imports, `--leave-on-card`, sequential Lightroom imports, and parallel worker results.

### Documentation

- Clarified Lightroom import ordering, date handling, worker defaults, RAW-backed stack detection, re-run behavior, and atomic/cross-filesystem file handling.

## **1.5.8 - 2026-07-13**

- Fixed a false-positive stack detection when a JPG-without-RAW candidate appears near the beginning of a long focus bracket and an older photo breaks the backward burst probe.
- Stack detection now checks for a tight burst continuing immediately after an otherwise valid output candidate.
- Inputs already claimed by a later valid stack remain a boundary, so rapid consecutive in-camera stacks are not mistaken for one long focus bracket.

## **1.5.7 - 2026-06-19**

- Added `--version` so installed copies can report their exact Stackcopy version.
- Clarified stack-detection warnings when inferred input frames are not all RAW-backed.
- Documented the stack-detection control flags and updated release metadata.

## **1.5.6 - 2026-06-10**

- Add a GUI that should work on Windows, macOS and Linux. The GUI is only for the Lightroomimport workflow, which I assume is what most people want anyway.
- The GUI now remembers the last source, Lightroom destination, and stack-input folder and suggests them as defaults on the next launch.

## **1.5.5 - 2026-06-08**

- `--lightroomimport` now treats supported video files like single-shot photos and moves them to the same dated Lightroom import directory.
- Added video extension support for `.mov`, `.mp4`, `.m4v`, `.avi`, `.mts`, `.m2ts`, `.mpg`, `.mpeg`, and `.wmv`.
- `--lightroomimport` now scans the source directory recursively, so passing a camera `DCIM` folder processes all camera subfolders.
- The recursive scan skips its own destination directories, so it no longer re-imports already-sorted photos when the Lightroom or stack-input folders live under the source.
- An unreadable subdirectory is now reported and skipped instead of aborting the whole import.
- A file whose source and destination resolve to the same file is now a no-op instead of being deleted.

## \*\*1.5.4 - 2026-04-10

- Improved Windows support.
- Warn the user of slow speeds if using WSL across drives.
- Show transfer speeds in the summary line.

## \*\*1.5.3 - 2026-03-26

### Changed

- `--lightroomimport` now plans all moves first, then moves files oldest-first by photo time (mtime). This replaces the previous approach where moves happened during detection.
- `--lightroomimport` always runs sequentially (ignores `--jobs`) to guarantee oldest-first ordering.
- The summary now prints before any files are moved, showing what was found and what will happen.

### Added

- `-i` / `--interactive` flag: shows a summary and asks for confirmation before moving files. Only applies to `--lightroomimport`. Default behavior still proceeds automatically.
- The summary now reports accepted and rejected stack counts, file counts by category, time range, and destination directories.
- Rejection breakdown available with `--debug-stacks`.

## **[1.5.2] - 2026-01-31**

### Added

- **Pre-flight disk space safety checks** for operations that write to a destination filesystem:
  - Before executing **cross-device moves** in Lightroom/Lightroom Import modes.
  - Before moving **“remaining files”** into the Lightroom import directory structure.
  - Before **copy/stackcopy** operations (including correct destination filename calculation when `--prefix` or `--stackcopy` is used).
- **User confirmation prompt** when the destination filesystem is low on space, showing:
  - Current free space, estimated required space for the pending operations, estimated free space after, and a reserve threshold.
  - “Overflow by X” messaging when the operation would exceed available space.
- **Filesystem confirmation cache** to avoid prompting repeatedly for the same destination device during a single run.

### Safety

- If disk space is low and the process is running without an interactive TTY, stackcopy now **refuses to proceed** rather than risking partial transfers.

## **[1.5.1] - 2026-01-26**

- Added collision-safe naming for Lightroom/Lightroom Import moves: if a destination filename already exists, Stackcopy appends a shared `__N` suffix (keeps JPG+RAW paired) instead of overwriting.
- Always prints a brief notice when a rename happens due to a destination collision (even without `--verbose`), usually caused by camera/card counter resets.
- Improved “remaining files” handling by grouping moves per destination folder and keeping in-memory paths/basenames consistent after moves.

## **[1.5] - 2026-01-19**

- **Dynamic Stack Detection**:
  - Implemented dual-threshold logic:
    - `MAX_OUTPUT_LAG_SECONDS` (120s): Allows time for camera to stack and save (Output -> Input 1).
    - `MAX_INPUT_GAP_SECONDS` (6s): Enforces tight buffering for subsequent frames (Input N -> Input N+1).
  - Maintains strict 2.0s burst safety check to reject focus brackets.
- **Robust Move Tracking**:
  - Added atomic tracking of "expected" vs "successful" moves per stem.
  - Stems are only marked as "processed" (and excluded from remaining logic) if _all_ constituent files (JPG+RAW) move successfully.
- **Partial Failure Mitigation**:
  - Remaining-files logic now gracefully handles missing sources (caused by partial moves) without reporting spurious errors.
- **Thread Safety**:
  - Refactored parallel move execution to be fully thread-safe and race-free.

## **[1.4] - 2026-01-04**

- Atomic File Operations (\_atomic_copy2 function)

Implements atomic copy by writing to a temporary file first, then using os.replace() to atomically swap it with the destination
Prevents partial/corrupted files if operations are interrupted
Includes cleanup of temp files in case of errors

- New --lightroomimport Mode

Extends --lightroom functionality to also move remaining non-stacked files to ~/pictures/Lightroom/YEAR/DATE/
Moves stacked output JPGs to the Lightroom import directory structure
Tracks three categories: input files (stacked frames), output files (stacked results), and remaining files

- Self-Healing Logic

Detects when destination files exist but are 0 bytes (from interrupted previous runs)
Automatically replaces them with valid source files
Works in both dry-run and normal mode

- New --debug-stacks Flag

Provides detailed diagnostic output showing why stacks are accepted or rejected
Shows timestamp gaps, sequence matching, and safety check results
Very helpful for troubleshooting stack detection issues

## **[1.3] - 2025-11-22**

### **Added**

- Lightroom mode now processes stacked output JPGs _even if already renamed_ (i.e., containing `"stacked"` in filename).
- Cross-filesystem safe move handling:
  - Falls back to `copyfile + unlink` instead of `shutil.move()` for SD→disk transfers.
- File deduplication based on content comparison:
  - If destination exists and files are identical:
    - In **move mode**: source is deleted and operation succeeds.
    - In **copy mode**: copy is skipped and treated as success.
- Parallel input-file moves and copies now supported in Lightroom mode when `--jobs` is set - which it is by default.

### **Improved**

- `mtime` is now lazily loaded (`get_file_mtime()`) ensuring accurate timestamp comparison when identifying input frames.
- Lightroom summary now reports:
  - Number of stacked outputs _processed_, not just renamed.
  - Count of RAW/JPG input files moved.
- Reduced repeated warnings when rerunning on partially imported cards.

### **Fixed**

- Previously, already-renamed stacked JPGs were skipped entirely, preventing input frames from being moved.
- `shutil.move()` cross-device failures on WSL/drvfs mount points.

### **Performance**

- Significantly faster import speeds with multi-threaded copy (especially large stacks from SD cards).
- Avoids redundant copies when rerunning Lightroom mode.

## Version 1.2 - 2025-11-20

- Fixed false positive in focus-bracketing burst detection when shooting multiple stacks in quick succession

The --lightroom mode includes a safety check to skip moving input files when it detects a focus-bracketing burst longer than 15 frames. This check was incorrectly comparing the stacked output file's timestamp against raw files from a previous, unrelated stack. When two separate stacks were shot within 20 seconds of each other, the script would mistakenly conclude they were part of one giant burst and skip moving the input files for the second stack.
The fix changes the burst detection to compare timestamps between consecutive raw files rather than between the output and earlier raw files. This correctly identifies actual continuous bursts while allowing separate stacks shot in quick succession to be processed independently.

## **[1.1] - 2025-10-30**

### Added

- `--lightroom` mode: A new mode to streamline the workflow for processing in-camera stacks for use with Adobe Lightroom. The idea is that you run this on the photos on the camera card before you import to lightroom so you only import the files you need, not all the input files to the stack.
- Identifies in-camera photo stacks (3-15 input files and one output file).
- Moves the input files (both JPG and ORF) of identified stacks to a dated directory structure (e.g., `/home/alan/pictures/olympus.stack.input.photos/2025/2025-10-30/`).
- Renames the stacked output JPG in its original directory.
- Single-shot photos and focus bracketed photos (JPG/ORF pairs not part of a stack) are left untouched.
  .
