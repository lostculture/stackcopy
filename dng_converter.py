"""Adobe DNG Converter discovery and invocation.

Raw support is per-camera-model, not per-format: a converter does not
"support CR3", it carries a profile for each body.  A converter older than
the camera rejects its files instantly at header parse.  That is why a
one-file trial conversion, not a version number, is the authoritative gate.
"""

from __future__ import annotations

import concurrent.futures
import os
import shutil
import subprocess
import sys
import tempfile
from dataclasses import dataclass

DNG_CONVERTER_ENV = "STACKCOPY_DNG_CONVERTER"

# Canon EOS R6 Mark II support arrived in Camera Raw 15.1 (Dec 2022), and the
# standalone converter's versions track Camera Raw's.
DNG_CONVERTER_MINIMUM = (15, 1)
DNG_CONVERTER_MINIMUM_TEXT = "15.1"
DNG_CONVERTER_DOWNLOAD_URL = (
    "https://helpx.adobe.com/camera-raw/using/adobe-dng-converter.html"
)

PREFLIGHT_TIMEOUT_SECONDS = 180


def _candidate_paths() -> list[str]:
    if sys.platform == "win32":
        return [
            r"C:\Program Files\Adobe\Adobe DNG Converter\Adobe DNG Converter.exe",
            r"C:\Program Files (x86)\Adobe\Adobe DNG Converter\Adobe DNG Converter.exe",
        ]
    if sys.platform == "darwin":
        return [
            "/Applications/Adobe DNG Converter.app/Contents/MacOS/"
            "Adobe DNG Converter",
        ]
    return []


def find_dng_converter() -> str | None:
    """Locate the converter: env override, then platform defaults, then PATH."""
    override = (os.environ.get(DNG_CONVERTER_ENV) or "").strip()
    if override:
        return override if os.path.isfile(override) else None
    for candidate in _candidate_paths():
        if os.path.isfile(candidate):
            return candidate
    return shutil.which("Adobe DNG Converter")


def converter_version(path: str) -> tuple[int, int] | None:
    """Best-effort version, used only to make an error message actionable."""
    if sys.platform == "win32":
        try:
            import ctypes
            import ctypes.wintypes as wintypes

            size = ctypes.windll.version.GetFileVersionInfoSizeW(path, None)
            if not size:
                return None
            buffer = ctypes.create_string_buffer(size)
            if not ctypes.windll.version.GetFileVersionInfoW(
                path, 0, size, buffer
            ):
                return None
            block = ctypes.c_void_p()
            length = wintypes.UINT()
            if not ctypes.windll.version.VerQueryValueW(
                buffer, "\\", ctypes.byref(block), ctypes.byref(length)
            ):
                return None
            info = ctypes.cast(
                block, ctypes.POINTER(ctypes.c_uint32 * 4)
            ).contents
            most, least = info[2], info[3]
            return (most >> 16) & 0xFFFF, most & 0xFFFF
        except Exception:
            return None
    if sys.platform == "darwin":
        plist = os.path.join(
            path.split(".app/")[0] + ".app", "Contents", "Info.plist"
        )
        try:
            import plistlib

            with open(plist, "rb") as handle:
                text = plistlib.load(handle).get("CFBundleShortVersionString")
            major, _, minor = str(text).partition(".")
            return int(major), int(minor.split(".")[0] or 0)
        except Exception:
            return None
    return None


def convert_command(converter: str, dest_dir: str, files: list[str]) -> list[str]:
    """Lossless compressed DNG, medium preview, no embedded original."""
    return [converter, "-c", "-p1", "-d", dest_dir, *files]


def preflight(converter: str, sample_cr3: str) -> str | None:
    """Trial-convert one real frame.  Returns an error message, or None.

    Authoritative: it answers "can this converter read *this camera*", which
    no version comparison can.
    """
    version = converter_version(converter)
    version_text = (
        f"{version[0]}.{version[1]}" if version else "an unknown version"
    )
    with tempfile.TemporaryDirectory(prefix="stackcopy-dng-") as work:
        try:
            completed = subprocess.run(
                convert_command(converter, work, [sample_cr3]),
                check=False,
                capture_output=True,
                stdin=subprocess.DEVNULL,
                encoding="utf-8",
                errors="replace",
                timeout=PREFLIGHT_TIMEOUT_SECONDS,
            )
        except subprocess.TimeoutExpired:
            return (
                f"Adobe DNG Converter ({version_text}) did not finish a test "
                f"conversion within {PREFLIGHT_TIMEOUT_SECONDS}s."
            )
        except (OSError, subprocess.SubprocessError) as error:
            return f"Adobe DNG Converter could not be run: {error}"
        produced = [n for n in os.listdir(work) if n.lower().endswith(".dng")]
        if produced and completed.returncode == 0:
            return None

    if version is None:
        hint = (
            " Its version could not be determined. Raw support is "
            "per-camera-model, so this may be an older converter that "
            "predates the body; updating it is worth trying: "
            f"{DNG_CONVERTER_DOWNLOAD_URL}"
        )
    elif version < DNG_CONVERTER_MINIMUM:
        hint = (
            f" It is older than {DNG_CONVERTER_MINIMUM_TEXT}, which is the "
            "first version supporting recent Canon bodies. Update it at "
            f"{DNG_CONVERTER_DOWNLOAD_URL}"
        )
    else:
        hint = (
            " This converter cannot process this particular camera model. "
            "Raw support is per-camera-model, so an even more recent body "
            "may need a newer converter than this one."
        )
    return (
        f"Adobe DNG Converter ({version_text}) could not convert "
        f"{os.path.basename(sample_cr3)}.{hint}"
    )


CONVERT_TIMEOUT_PER_FILE_SECONDS = 120


@dataclass(frozen=True)
class ConversionResult:
    dest_dir: str
    converted: int = 0
    skipped: int = 0
    failed: int = 0
    error: str | None = None

    @property
    def ok(self) -> bool:
        return self.error is None and self.failed == 0


def _expected_dng(dest_dir: str, source: str) -> str:
    stem = os.path.splitext(os.path.basename(source))[0]
    return os.path.join(dest_dir, f"{stem}.dng")


def convert_group(
    converter: str,
    files: list[str],
    dest_dir: str,
    timeout: float | None = None,
) -> ConversionResult:
    """Convert one stack's frames into dest_dir.

    Already-converted frames are skipped, so an interrupted import resumes.
    """
    os.makedirs(dest_dir, exist_ok=True)
    pending = [f for f in files if not os.path.exists(_expected_dng(dest_dir, f))]
    skipped = len(files) - len(pending)
    if not pending:
        return ConversionResult(dest_dir, converted=0, skipped=skipped)

    if timeout is None:
        timeout = CONVERT_TIMEOUT_PER_FILE_SECONDS * len(pending)
    try:
        completed = subprocess.run(
            convert_command(converter, dest_dir, pending),
            check=False,
            capture_output=True,
            stdin=subprocess.DEVNULL,
            encoding="utf-8",
            errors="replace",
            timeout=timeout,
        )
    except subprocess.TimeoutExpired:
        return ConversionResult(
            dest_dir, skipped=skipped, failed=len(pending),
            error=f"conversion timed out after {timeout:.0f}s",
        )
    except (OSError, subprocess.SubprocessError) as error:
        return ConversionResult(
            dest_dir, skipped=skipped, failed=len(pending),
            error=f"converter could not be run: {error}",
        )

    missing = [f for f in pending if not os.path.exists(_expected_dng(dest_dir, f))]
    if missing:
        detail = ", ".join(os.path.basename(f) for f in missing[:3])
        if len(missing) > 3:
            detail += f", and {len(missing) - 3} more"
        return ConversionResult(
            dest_dir,
            converted=len(pending) - len(missing),
            skipped=skipped,
            failed=len(missing),
            error=(
                f"converter exited {completed.returncode} without producing "
                f"DNG for {detail}"
            ),
        )
    return ConversionResult(dest_dir, converted=len(pending), skipped=skipped)


def convert_groups(
    converter: str,
    jobs_by_dest: list[tuple[str, list[str]]],
    workers: int = 1,
    on_progress=None,
) -> list[ConversionResult]:
    """Convert many stacks, parallel across folders.

    Conversion is CPU-bound and single-threaded per file, and dominates the
    wall-clock of an import, so folder-level parallelism is the main lever.
    """
    results: list[ConversionResult] = []
    if workers <= 1:
        for dest_dir, files in jobs_by_dest:
            result = convert_group(converter, files, dest_dir)
            results.append(result)
            if on_progress:
                on_progress(result)
        return results

    with concurrent.futures.ThreadPoolExecutor(max_workers=workers) as pool:
        futures = {
            pool.submit(convert_group, converter, files, dest_dir): dest_dir
            for dest_dir, files in jobs_by_dest
        }
        for future in concurrent.futures.as_completed(futures):
            result = future.result()
            results.append(result)
            if on_progress:
                on_progress(result)
    return results
