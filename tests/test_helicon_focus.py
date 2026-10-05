import os
import stat
import sys
import tempfile
from pathlib import Path


import pytest

import helicon_focus


def make_stub(tmp_path: Path, body: str, name: str = "helicon") -> str:
    if sys.platform == "win32":
        path = tmp_path / f"{name}.bat"
        path.write_text(f"@echo off\n{body}\n", encoding="utf-8")
    else:
        path = tmp_path / name
        path.write_text(f"#!/bin/sh\n{body}\n", encoding="utf-8")
        path.chmod(path.stat().st_mode | stat.S_IEXEC)
    return str(path)


def test_filelist_contains_only_the_named_frames(tmp_path):
    frames = [str(tmp_path / f"IMG_{n:04d}.dng") for n in (1, 2, 3)]
    listing = helicon_focus.write_file_list(frames, str(tmp_path))
    lines = Path(listing).read_text(encoding="utf-8").splitlines()
    assert lines == frames


def test_command_uses_filelist_and_noprogress(tmp_path):
    command = helicon_focus.render_command(
        "helicon.exe", "list.txt", str(tmp_path / "out.jpg"),
        method=2, radius=8, smoothing=4, jpeg_quality=95,
    )
    assert "-silent" in command
    assert "-noprogress" in command
    assert "-i" in command
    assert "-mp:2" in command
    assert any(a.startswith("-save:") for a in command)
    # The stack folder must never be passed as a positional path, or Helicon
    # would pull the camera's composite JPG into the stack.
    positionals = [a for a in command[1:] if not a.startswith("-")]
    assert str(tmp_path) not in positionals


def test_render_reports_failure_when_no_output(tmp_path):
    stub = make_stub(tmp_path, "exit 0")  # exits clean, writes nothing
    result = helicon_focus.render_stack(
        stub, [str(tmp_path / "a.dng")], str(tmp_path / "out.jpg")
    )
    assert result.rendered is False
    assert result.error is not None


def test_render_succeeds_when_output_appears(tmp_path):
    out = tmp_path / "out.jpg"
    stub = make_stub(tmp_path, f'echo img > "{out}"')
    result = helicon_focus.render_stack(
        stub, [str(tmp_path / "a.dng")], str(out)
    )
    assert result.rendered is True
    assert result.error is None


def test_render_reports_failure_when_exit_nonzero_despite_output(tmp_path):
    """Exit code and output-file existence must both agree on success.

    A file landing on disk does not mean the render is trustworthy: Helicon
    can write a partial file and still report failure. Treating that as
    success would be exactly the silently-wrong render this feature exists
    to prevent.
    """
    out = tmp_path / "out.jpg"
    stub = make_stub(tmp_path, f'echo img > "{out}"\nexit 1')
    result = helicon_focus.render_stack(
        stub, [str(tmp_path / "a.dng")], str(out)
    )
    assert result.rendered is False
    assert result.error is not None
    assert "1" in result.error
    assert out.exists(), "the (untrustworthy) file is left in place, not deleted"


def test_render_reports_failure_when_output_directory_cannot_be_created(tmp_path):
    """helicon_focus must be safe standalone: an OSError creating the output
    directory (or writing the frame list) must become a failed RenderResult,
    never an uncaught exception a caller has to know to guard against.
    """
    blocker = tmp_path / "blocker"
    blocker.write_bytes(b"not a directory")
    out = blocker / "sub" / "out.jpg"
    stub = make_stub(tmp_path, "exit 1")  # would fail loudly if it ran at all
    result = helicon_focus.render_stack(
        stub, [str(tmp_path / "a.dng")], str(out)
    )
    assert result.rendered is False
    assert result.error is not None


def test_existing_output_is_not_rerendered(tmp_path):
    out = tmp_path / "out.jpg"
    out.write_bytes(b"already rendered")
    stub = make_stub(tmp_path, "exit 1")  # would fail if it were invoked
    result = helicon_focus.render_stack(
        stub, [str(tmp_path / "a.dng")], str(out)
    )
    assert result.skipped is True
    assert result.error is None


def test_find_helicon_falls_back_to_path(tmp_path, monkeypatch):
    """Symmetry with find_dng_converter: a PATH install is discoverable.

    Without this fallback a Helicon that is installed but not at one of the
    two hardcoded Program Files paths reports as "not found", which under
    --render-helicon fails the whole run.
    """
    monkeypatch.delenv(helicon_focus.HELICON_ENV, raising=False)
    monkeypatch.setattr(helicon_focus, "_candidate_paths", lambda: [])
    monkeypatch.setattr(
        helicon_focus.shutil, "which",
        lambda name: "/opt/helicon/HeliconFocus" if name == "HeliconFocus" else None,
    )
    assert helicon_focus.find_helicon() == "/opt/helicon/HeliconFocus"


def test_frame_list_defaults_to_the_system_temp_directory(tmp_path):
    """Not a destination folder: mkstemp(dir=None) is the system temp dir."""
    path = helicon_focus.write_file_list(["/a.dng", "/b.dng"])
    try:
        assert Path(path).parent == Path(tempfile.gettempdir())
    finally:
        os.unlink(path)


def test_render_writes_its_frame_list_outside_the_destination(
    tmp_path, monkeypatch
):
    """A killed run must not leave stackcopy-helicon-*.txt among the DNGs.

    render_stack deletes the list in a ``finally``, so a completed run
    never leaves one behind either way -- which is exactly why asserting
    "the folder is clean afterwards" would pass whether the file was ever
    written there or not. What matters is WHERE it lived while Helicon was
    running, so that is what this captures.
    """
    dest = tmp_path / "stack"
    dest.mkdir()
    out = dest / "out.jpg"
    stub = make_stub(tmp_path, f'echo img > "{out}"')

    real = helicon_focus.write_file_list
    seen = {}

    def spy(frames, directory=None):
        path = real(frames, directory)
        seen["path"] = path
        seen["existed_in_dest"] = Path(path).parent == dest
        return path

    monkeypatch.setattr(helicon_focus, "write_file_list", spy)
    result = helicon_focus.render_stack(stub, ["/a.dng", "/b.dng"], str(out))

    assert result.ok
    assert seen["existed_in_dest"] is False, (
        "the frame list was written into the destination folder"
    )
    assert not Path(seen["path"]).exists(), "and it must still be cleaned up"


# --- Capture metadata on renders -------------------------------------------
#
# Helicon writes its render with no EXIF at all, so a render sorts as "taken
# whenever it was rendered" and has no location. copy_capture_metadata copies
# the capture time and GPS from the camera's own composite JPG. These tests use
# the real ExifTool because what matters is what lands in the file, not which
# arguments were passed.

import shutil
import subprocess

# A valid 1x1 baseline JPEG: the smallest real file ExifTool will write into.
TINY_JPEG = bytes.fromhex(
    "ffd8ffe000104a46494600010100000100010000ffdb004300080606070605080707"
    "070909080a0c140d0c0b0b0c1912130f141d1a1f1e1d1a1c1c20242e2720222c231c"
    "1c2837292c30313434341f27393d38323c2e333432ffc0000b080001000101011100"
    "ffc4001f0000010501010101010100000000000000000102030405060708090a0bff"
    "c400b5100002010303020403050504040000017d0102030004110512213141061351"
    "6107227114328191a1082342b1c11552d1f02433627282090a161718191a25262728"
    "292a3435363738393a434445464748494a535455565758595a636465666768696a73"
    "7475767778797a838485868788898a92939495969798999aa2a3a4a5a6a7a8a9aab2"
    "b3b4b5b6b7b8b9bac2c3c4c5c6c7c8c9cad2d3d4d5d6d7d8d9dae1e2e3e4e5e6e7e8"
    "e9eaf1f2f3f4f5f6f7f8f9faffda0008010100003f00fbd3ffd9"
)

EXIFTOOL = shutil.which("exiftool")
needs_exiftool = pytest.mark.skipif(EXIFTOOL is None, reason="ExifTool not installed")


def _jpeg(path: Path, *tags: str) -> Path:
    path.write_bytes(TINY_JPEG)
    if tags:
        subprocess.run(
            [EXIFTOOL, "-q", "-overwrite_original", *tags, str(path)], check=True
        )
    return path


def _read(path: Path, *tags: str) -> list[str]:
    completed = subprocess.run(
        [EXIFTOOL, "-s3", "-f", *tags, str(path)],
        check=True, capture_output=True, encoding="utf-8",
    )
    return completed.stdout.splitlines()


@needs_exiftool
def test_capture_time_and_gps_are_copied_onto_the_render(tmp_path):
    source = _jpeg(
        tmp_path / "IMG_0014.JPG",
        "-DateTimeOriginal=2026:10:03 15:43:42",
        "-SubSecTimeOriginal=78",
        "-OffsetTimeOriginal=-04:00",
        "-GPSLatitude=41.380511", "-GPSLatitudeRef=N",
        "-GPSLongitude=78.277358", "-GPSLongitudeRef=W",
    )
    render = _jpeg(tmp_path / "IMG_0014 helicon.jpg")

    error = helicon_focus.copy_capture_metadata(EXIFTOOL, str(source), str(render))

    assert error is None
    assert _read(render, "-SubSecDateTimeOriginal", "-GPSPosition", "-n") == _read(
        source, "-SubSecDateTimeOriginal", "-GPSPosition", "-n"
    )
    assert not list(tmp_path.glob("*_original")), "no ExifTool backup files left behind"


@needs_exiftool
def test_a_source_without_gps_still_gives_the_render_its_capture_time(tmp_path):
    source = _jpeg(tmp_path / "IMG_0014.JPG", "-DateTimeOriginal=2026:10:03 15:43:42")
    render = _jpeg(tmp_path / "IMG_0014 helicon.jpg")

    error = helicon_focus.copy_capture_metadata(EXIFTOOL, str(source), str(render))

    assert error is None
    assert _read(render, "-DateTimeOriginal", "-GPSPosition") == ["2026:10:03 15:43:42", "-"]


@needs_exiftool
def test_a_missing_source_is_reported_not_raised(tmp_path):
    render = _jpeg(tmp_path / "IMG_0014 helicon.jpg")

    error = helicon_focus.copy_capture_metadata(
        EXIFTOOL, str(tmp_path / "missing.JPG"), str(render)
    )

    assert error is not None


def test_an_unrunnable_exiftool_is_reported_not_raised(tmp_path):
    render = tmp_path / "out.jpg"
    render.write_bytes(TINY_JPEG)

    error = helicon_focus.copy_capture_metadata(
        str(tmp_path / "no-such-exiftool"), str(tmp_path / "src.JPG"), str(render)
    )

    assert error is not None
