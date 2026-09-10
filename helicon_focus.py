"""Drive Helicon Focus from the command line to render stacks.

Verified against Helicon Focus 8.3.  Two details are load-bearing:
``-noprogress`` (plain ``-silent`` still shows a progress window, which hangs
a non-interactive run) and ``-i <filelist>`` (a stack folder also holds the
camera's composite JPG, which must never enter the stack).
"""

from __future__ import annotations

import os
import subprocess
import sys
import tempfile
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
    override = (os.environ.get(HELICON_ENV) or "").strip()
    if override:
        return override if os.path.isfile(override) else None
    for candidate in _candidate_paths():
        if os.path.isfile(candidate):
            return candidate
    return None


def write_file_list(frames: list[str], directory: str) -> str:
    """One absolute path per line, as -i expects."""
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


def render_stack(
    helicon: str,
    frames: list[str],
    output_path: str,
    method: int = DEFAULT_METHOD,
    radius: int = DEFAULT_RADIUS,
    smoothing: int = DEFAULT_SMOOTHING,
    jpeg_quality: int = DEFAULT_JPEG_QUALITY,
    timeout: float | None = None,
) -> RenderResult:
    """Render one stack.  An existing output is left alone so runs resume.

    This function must be safe to call on its own: every filesystem step it
    takes -- creating the output directory, writing the frame list, running
    Helicon, and cleaning the frame list back up -- is inside the guarded
    region below, so an OSError anywhere in it (a full disk, a permissions
    error, a path that collides with an existing file) becomes a failed
    RenderResult rather than an exception a caller has to know to catch.
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
        listing = write_file_list(frames, directory)
        completed = subprocess.run(
            render_command(
                helicon, listing, output_path,
                method, radius, smoothing, jpeg_quality,
            ),
            check=False,
            capture_output=True,
            stdin=subprocess.DEVNULL,
            encoding="utf-8",
            errors="replace",
            timeout=timeout,
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
                f"Helicon exited {completed.returncode} without writing "
                f"{os.path.basename(output_path)}"
            ),
        )
    if completed.returncode != 0:
        # A file landed on disk, but Helicon still reported failure -- the
        # output may be a partial or truncated render. Success requires
        # both signals to agree (mirrors dng_converter's _convert_group),
        # so this must be reported as a failure the user can act on, never
        # guessed at as a success because a file merely happens to exist.
        return RenderResult(
            output_path,
            error=(
                f"Helicon exited {completed.returncode} after writing "
                f"{os.path.basename(output_path)}; the output may be "
                "incomplete or corrupt even though a file was produced"
            ),
        )
    return RenderResult(output_path, rendered=True)
