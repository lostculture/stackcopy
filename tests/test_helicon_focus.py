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
