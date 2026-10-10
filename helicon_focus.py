"""Drive Helicon Focus from the command line to render stacks.

Verified against Helicon Focus 8.3.  Two details are load-bearing:
``-noprogress`` (plain ``-silent`` still shows a progress window, which hangs
a non-interactive run) and ``-i <filelist>`` (a stack folder also holds the
camera's composite JPG, which must never enter the stack).
"""

from __future__ import annotations

import os
import shutil
import subprocess
import sys
import tempfile
import time
from dataclasses import dataclass

HELICON_ENV = "STACKCOPY_HELICON"

METHOD_A, METHOD_B, METHOD_C = 0, 1, 2
DEFAULT_METHOD = METHOD_C
DEFAULT_RADIUS = 8
DEFAULT_SMOOTHING = 4
DEFAULT_JPEG_QUALITY = 95
RENDER_TIMEOUT_PER_FRAME_SECONDS = 60


@dataclass(frozen=True)
class RenderResult:
    output_path: str
    rendered: bool = False
    skipped: bool = False
    error: str | None = None

    @property
    def ok(self) -> bool:
        return self.error is None


def _candidate_paths() -> list[str]:
    if sys.platform == "win32":
        return [
            r"C:\Program Files\Helicon Software\Helicon Focus 8\HeliconFocus.exe",
            r"C:\Program Files\Helicon Software\Helicon Focus 7\HeliconFocus.exe",
        ]
    if sys.platform == "darwin":
        return ["/Applications/HeliconFocus.app/Contents/MacOS/HeliconFocus"]
    return []


def find_helicon() -> str | None:
    """Env override, then platform defaults, then PATH.

    The PATH fallback mirrors find_dng_converter(): a Helicon installed
    somewhere other than the two default locations is still discoverable.
    """
    override = (os.environ.get(HELICON_ENV) or "").strip()
    if override:
        return override if os.path.isfile(override) else None
    for candidate in _candidate_paths():
        if os.path.isfile(candidate):
            return candidate
    return shutil.which("HeliconFocus")


def write_file_list(frames: list[str], directory: str | None = None) -> str:
    """One absolute path per line, as -i expects.

    ``directory`` defaults to the system temp directory rather than the
    destination: a killed run must not strew stackcopy-helicon-*.txt
    scratch files through the user's imported stack folders, where they
    would sit next to the DNGs forever.
    """
    handle, path = tempfile.mkstemp(
        prefix="stackcopy-helicon-", suffix=".txt", dir=directory
    )
    with os.fdopen(handle, "w", encoding="utf-8") as listing:
        for frame in frames:
            listing.write(f"{frame}\n")
    return path


def render_command(
    helicon: str,
    file_list: str,
    output_path: str,
    method: int = DEFAULT_METHOD,
    radius: int = DEFAULT_RADIUS,
    smoothing: int = DEFAULT_SMOOTHING,
    jpeg_quality: int = DEFAULT_JPEG_QUALITY,
) -> list[str]:
    """Build the Helicon Focus command line for one stack.

    ``-noprogress`` is not optional: plain ``-silent`` still raises a progress
    window, which hangs a non-interactive run.  Frames arrive through
    ``-i <file list>`` rather than as a folder because a stack folder also
    holds the camera's own composite JPG, which must never enter the stack.
    """
    return [
        helicon,
        "-silent",
        "-i", file_list,
        "-noprogress",
        f"-mp:{method}",
        f"-rp:{radius}",
        f"-sp:{smoothing}",
        f"-save:{output_path}",
        f"-j:{jpeg_quality}",
    ]


# What a render inherits from the camera's composite. Helicon writes its output
# with no EXIF at all, so without these a render sorts by the moment it was
# rendered and has no location. Sub-second and offset tags are included because
# they are what makes the capture time exact (and keep a burst in order).
CAPTURE_METADATA_TAGS = (
    "-DateTimeOriginal",
    "-CreateDate",
    "-SubSecTimeOriginal",
    "-SubSecTimeDigitized",
    "-OffsetTimeOriginal",
    "-OffsetTimeDigitized",
    "-OffsetTime",
    "-GPS:all",
)
METADATA_TIMEOUT_SECONDS = 60


def copy_capture_metadata(exiftool: str, source: str, output_path: str) -> str | None:
    """Copy capture time and GPS from ``source`` onto a render.

    Returns None on success, otherwise a one-line reason. Never raises: a
    render without metadata is still a good render, so the caller decides
    how loudly to report it. A source with no GPS is not an error -- many
    shots have no location -- the render simply gets the capture time alone.
    """
    if not os.path.isfile(source):
        return f"source {os.path.basename(source)} does not exist"
    try:
        completed = subprocess.run(
            [
                exiftool, "-q", "-q", "-overwrite_original",
                "-tagsFromFile", source, *CAPTURE_METADATA_TAGS, output_path,
            ],
            check=False,
            capture_output=True,
            stdin=subprocess.DEVNULL,
            encoding="utf-8",
            errors="replace",
            timeout=METADATA_TIMEOUT_SECONDS,
        )
    except subprocess.TimeoutExpired:
        return f"ExifTool timed out after {METADATA_TIMEOUT_SECONDS}s"
    except (OSError, subprocess.SubprocessError) as error:
        return f"ExifTool could not be run: {error}"
    if completed.returncode != 0:
        detail = (completed.stderr or "").strip().splitlines()
        return detail[0] if detail else f"ExifTool exited {completed.returncode}"
    return None


# Helicon reports a failed render with a modal dialog even under -silent
# -noprogress, then waits for a click that never comes.  These are the
# titles it is known to use.
ERROR_DIALOG_TITLES = ("Rendering failed",)
DIALOG_POLL_SECONDS = 1.0


def find_error_dialog(pid: int) -> str | None:
    """Title of a known Helicon error dialog owned by ``pid``, else None.

    Windows only; elsewhere, and on any failure to enumerate windows, it
    reports nothing, which leaves the render timeout as the backstop.
    """
    if sys.platform != "win32":
        return None
    try:
        import ctypes
        from ctypes import wintypes

        user32 = ctypes.windll.user32
        found: list[str] = []

        @ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)
        def visit(hwnd, _lparam):
            owner = wintypes.DWORD()
            user32.GetWindowThreadProcessId(hwnd, ctypes.byref(owner))
            if owner.value == pid and user32.IsWindowVisible(hwnd):
                buffer = ctypes.create_unicode_buffer(256)
                user32.GetWindowTextW(hwnd, buffer, len(buffer))
                if buffer.value in ERROR_DIALOG_TITLES:
                    found.append(buffer.value)
                    return False
            return True

        user32.EnumWindows(visit, 0)
        return found[0] if found else None
    except Exception:
        return None


def _wait_watching_for_dialog(process, timeout: float, dialog_probe) -> str | None:
    """Wait for ``process``; return a dialog title if one ended the wait.

    Raises subprocess.TimeoutExpired, as subprocess.run does, when neither
    an exit nor a dialog arrives in time.
    """
    deadline = time.monotonic() + timeout
    while True:
        try:
            process.wait(timeout=max(0.0, min(DIALOG_POLL_SECONDS, deadline - time.monotonic())))
            return None
        except subprocess.TimeoutExpired:
            pass
        title = dialog_probe(process.pid)
        if title:
            return title
        if time.monotonic() >= deadline:
            raise subprocess.TimeoutExpired(process.args, timeout)


def render_stack(
    helicon: str,
    frames: list[str],
    output_path: str,
    method: int = DEFAULT_METHOD,
    radius: int = DEFAULT_RADIUS,
    smoothing: int = DEFAULT_SMOOTHING,
    jpeg_quality: int = DEFAULT_JPEG_QUALITY,
    timeout: float | None = None,
    dialog_probe=find_error_dialog,
) -> RenderResult:
    """Render one stack.  An existing output is left alone so runs resume.

    This function must be safe to call on its own: every filesystem step it
    takes -- creating the output directory, writing the frame list, running
    Helicon, and cleaning the frame list back up -- is inside the guarded
    region below, so an OSError anywhere in it (a full disk, a permissions
    error, a path that collides with an existing file) becomes a failed
    RenderResult rather than an exception a caller has to know to catch.

    Helicon is watched while it runs: if it puts up one of its error
    dialogs, which it does even in silent mode and then waits on forever,
    it is ended there and the failure reported immediately instead of
    after the full timeout.
    """
    if os.path.exists(output_path):
        return RenderResult(output_path, rendered=False, skipped=True)
    if not frames:
        return RenderResult(output_path, error="no frames to render")

    if timeout is None:
        timeout = RENDER_TIMEOUT_PER_FRAME_SECONDS * len(frames)

    listing = None
    try:
        directory = os.path.dirname(output_path) or "."
        os.makedirs(directory, exist_ok=True)
        listing = write_file_list(frames)
        process = subprocess.Popen(
            render_command(
                helicon, listing, output_path,
                method, radius, smoothing, jpeg_quality,
            ),
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
        try:
            dialog = _wait_watching_for_dialog(process, timeout, dialog_probe)
        finally:
            if process.poll() is None:
                process.kill()
                process.wait()
        if dialog:
            return RenderResult(
                output_path,
                error=(
                    f"Helicon showed a \"{dialog}\" dialog and was stopped; "
                    "a frame it could not read is the usual cause"
                ),
            )
    except subprocess.TimeoutExpired:
        return RenderResult(
            output_path, error=f"Helicon timed out after {timeout:.0f}s"
        )
    except (OSError, subprocess.SubprocessError) as error:
        return RenderResult(
            output_path, error=f"Helicon could not be run: {error}"
        )
    finally:
        if listing is not None:
            try:
                os.unlink(listing)
            except OSError:
                pass

    if not os.path.exists(output_path):
        return RenderResult(
            output_path,
            error=(
                f"Helicon exited {process.returncode} without writing "
                f"{os.path.basename(output_path)}"
            ),
        )
    if process.returncode != 0:
        # A file landed on disk, but Helicon still reported failure -- the
        # output may be a partial or truncated render. Success requires
        # both signals to agree (mirrors dng_converter's _convert_group),
        # so this must be reported as a failure the user can act on, never
        # guessed at as a success because a file merely happens to exist.
        return RenderResult(
            output_path,
            error=(
                f"Helicon exited {process.returncode} after writing "
                f"{os.path.basename(output_path)}; the output may be "
                "incomplete or corrupt even though a file was produced"
            ),
        )
    return RenderResult(output_path, rendered=True)
