import stat
import sys
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


def test_existing_output_is_not_rerendered(tmp_path):
    out = tmp_path / "out.jpg"
    out.write_bytes(b"already rendered")
    stub = make_stub(tmp_path, "exit 1")  # would fail if it were invoked
    result = helicon_focus.render_stack(
        stub, [str(tmp_path / "a.dng")], str(out)
    )
    assert result.skipped is True
    assert result.error is None
