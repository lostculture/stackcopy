"""Adobe DNG Converter discovery and invocation.

Raw support is per-camera-model, not per-format: a converter does not
"support CR3", it carries a profile for each body.  A converter older than
the camera rejects its files instantly at header parse.  That is why a
one-file trial conversion, not a version number, is the authoritative gate.
"""

from __future__ import annotations

import os
import shutil
import subprocess
import sys
import tempfile

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
