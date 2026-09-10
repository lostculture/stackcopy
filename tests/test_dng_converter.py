import os
import stat
import subprocess
import sys
from pathlib import Path

import pytest

import dng_converter


def make_stub(tmp_path: Path, body: str, name: str = "stub") -> str:
    """A fake converter: writes a .dng per input, or fails, on demand."""
    if sys.platform == "win32":
        path = tmp_path / f"{name}.bat"
        path.write_text(f"@echo off\n{body}\n", encoding="utf-8")
    else:
        path = tmp_path / name
        path.write_text(f"#!/bin/sh\n{body}\n", encoding="utf-8")
        path.chmod(path.stat().st_mode | stat.S_IEXEC)
    return str(path)


def test_env_override_wins(tmp_path, monkeypatch):
    stub = make_stub(tmp_path, "exit 0")
    monkeypatch.setenv(dng_converter.DNG_CONVERTER_ENV, stub)
    assert dng_converter.find_dng_converter() == stub


def test_missing_converter_returns_none(tmp_path, monkeypatch):
    monkeypatch.setenv(dng_converter.DNG_CONVERTER_ENV,
                       str(tmp_path / "nope"))
    monkeypatch.setattr(dng_converter, "_candidate_paths", lambda: [])
    assert dng_converter.find_dng_converter() is None


def test_preflight_reports_conversion_failure(tmp_path, monkeypatch):
    """The 13.0-versus-R6-Mark-II case: exits nonzero, writes nothing."""
    stub = make_stub(tmp_path, "exit 1")
    sample = tmp_path / "IMG_0001.CR3"
    sample.write_bytes(b"not really a raw file")
    monkeypatch.setattr(dng_converter, "converter_version", lambda p: (13, 0))
    message = dng_converter.preflight(stub, str(sample))
    assert message is not None
    assert "13.0" in message
    assert "15.1" in message


def test_preflight_passes_when_dng_is_produced(tmp_path):
    if sys.platform == "win32":
        body = 'echo dng > "%~4\\IMG_0001.dng"'
    else:
        body = 'mkdir -p "$4" && echo dng > "$4/IMG_0001.dng"'
    stub = make_stub(tmp_path, body)
    sample = tmp_path / "IMG_0001.CR3"
    sample.write_bytes(b"raw")
    assert dng_converter.preflight(stub, str(sample)) is None


def test_preflight_failure_with_current_converter_does_not_suggest_update(
    tmp_path, monkeypatch
):
    """A converter already at/above the minimum must not be told to update.

    Regression for the bug where the else-branch unconditionally said "This
    usually means the converter predates the camera model. Update it at
    ...", even when the known version is current (e.g. 18.6). That message
    misdirects a user who is already up to date.
    """
    stub = make_stub(tmp_path, "exit 1")
    sample = tmp_path / "IMG_0001.CR3"
    sample.write_bytes(b"not really a raw file")
    monkeypatch.setattr(dng_converter, "converter_version", lambda p: (18, 6))
    message = dng_converter.preflight(stub, str(sample))
    assert message is not None
    assert "18.6" in message
    assert "predates the camera model" not in message
    assert "Update it at" not in message


def test_preflight_fails_when_exit_zero_but_no_dng_produced(tmp_path):
    """Guards the "produced file" half of the success gate in isolation.

    A stub that exits 0 without writing a .dng mimics the real Adobe binary's
    Sensei/WinML stderr noise on genuinely successful runs: exit code alone
    is not sufficient proof, and this must not be mistaken for success.
    """
    stub = make_stub(tmp_path, "exit 0")
    sample = tmp_path / "IMG_0001.CR3"
    sample.write_bytes(b"raw")
    assert dng_converter.preflight(stub, str(sample)) is not None


def test_convert_group_reports_every_file_converted(tmp_path):
    if sys.platform == "win32":
        body = r'for %%f in (%*) do @if not "%%~xf"==".dng" echo d > "%~4\%%~nf.dng"'
    else:
        body = 'd="$4"; shift 4; mkdir -p "$d"; for f; do echo d > "$d/$(basename "${f%.*}").dng"; done'
    stub = make_stub(tmp_path, body)
    src = tmp_path / "src"
    src.mkdir()
    files = []
    for n in range(1, 4):
        f = src / f"IMG_{n:04d}.CR3"
        f.write_bytes(b"raw")
        files.append(str(f))
    dest = tmp_path / "out"
    result = dng_converter.convert_group(stub, files, str(dest))
    assert result.converted == 3
    assert result.failed == 0
    assert result.error is None


def test_convert_group_detects_missing_output(tmp_path):
    """Converter exits 0 but silently produced nothing - still a failure."""
    stub = make_stub(tmp_path, "exit 0")
    src = tmp_path / "src"
    src.mkdir()
    f = src / "IMG_0001.CR3"
    f.write_bytes(b"raw")
    dest = tmp_path / "out"
    result = dng_converter.convert_group(stub, [str(f)], str(dest))
    assert result.failed == 1
    assert result.error is not None


def test_convert_group_resumes_completed_work(tmp_path):
    stub = make_stub(tmp_path, "exit 1")  # would fail if it ran
    src = tmp_path / "src"
    src.mkdir()
    dest = tmp_path / "out"
    dest.mkdir()
    files = []
    for n in range(1, 3):
        f = src / f"IMG_{n:04d}.CR3"
        f.write_bytes(b"raw")
        files.append(str(f))
        (dest / f"IMG_{n:04d}.dng").write_bytes(b"already done")
    result = dng_converter.convert_group(stub, files, str(dest))
    assert result.skipped == 2
    assert result.converted == 0
    assert result.error is None


def _success_stub(tmp_path, name="success_stub"):
    """A converter stub that writes a .dng for every input file it's given."""
    if sys.platform == "win32":
        body = r'for %%f in (%*) do @if not "%%~xf"==".dng" echo d > "%~4\%%~nf.dng"'
    else:
        body = (
            'd="$4"; shift 4; mkdir -p "$d"; '
            'for f; do echo d > "$d/$(basename "${f%.*}").dng"; done'
        )
    return make_stub(tmp_path, body, name)


def _one_file_job(tmp_path, tag):
    src = tmp_path / f"src_{tag}"
    src.mkdir()
    f = src / "IMG_0001.CR3"
    f.write_bytes(b"raw")
    dest = tmp_path / f"out_{tag}"
    return str(dest), [str(f)]


def test_convert_groups_sequential_returns_one_result_per_group_in_order(tmp_path):
    stub = _success_stub(tmp_path)
    jobs = [_one_file_job(tmp_path, "a"), _one_file_job(tmp_path, "b")]

    results = dng_converter.convert_groups(stub, jobs, workers=1)

    assert [r.dest_dir for r in results] == [dest for dest, _ in jobs]
    assert all(r.ok for r in results)
    assert all(r.converted == 1 for r in results)


def test_convert_groups_threaded_returns_one_result_per_group(tmp_path):
    stub = _success_stub(tmp_path)
    jobs = [_one_file_job(tmp_path, tag) for tag in ("x", "y", "z")]

    results = dng_converter.convert_groups(stub, jobs, workers=3)

    by_dest = {r.dest_dir: r for r in results}
    assert set(by_dest) == {dest for dest, _ in jobs}
    assert all(r.ok for r in by_dest.values())
    assert all(r.converted == 1 for r in by_dest.values())


def test_convert_groups_calls_on_progress_once_per_group(tmp_path):
    stub = _success_stub(tmp_path)
    jobs = [_one_file_job(tmp_path, tag) for tag in ("p", "q", "r")]
    seen = []

    dng_converter.convert_groups(stub, jobs, workers=1, on_progress=seen.append)

    assert len(seen) == 3
    assert {r.dest_dir for r in seen} == {dest for dest, _ in jobs}


def test_convert_groups_isolates_a_failing_group(tmp_path):
    """A group whose dest_dir can't be created must not sink the other groups.

    Regression for the bug where os.makedirs() sat outside convert_group's
    try/except: an exception there escaped convert_group entirely, which in
    turn aborted convert_groups' loop -- discarding results already
    collected for groups that succeeded, and skipping every group after the
    failing one. dest_b is made to collide with an existing plain file, so
    os.makedirs(dest_b, exist_ok=True) raises even though exist_ok=True,
    because the colliding path is a file, not a directory.
    """
    stub = _success_stub(tmp_path)
    dest_a, files_a = _one_file_job(tmp_path, "a")
    dest_b, files_b = _one_file_job(tmp_path, "b")
    dest_c, files_c = _one_file_job(tmp_path, "c")
    Path(dest_b).write_bytes(b"a plain file sitting where a directory is expected")

    jobs = [(dest_a, files_a), (dest_b, files_b), (dest_c, files_c)]
    results = dng_converter.convert_groups(stub, jobs, workers=1)

    assert len(results) == 3
    by_dest = {r.dest_dir: r for r in results}
    assert by_dest[dest_a].ok
    assert by_dest[dest_c].ok
    assert not by_dest[dest_b].ok
    assert by_dest[dest_b].error is not None


def test_convert_group_reports_timeout_as_failure(tmp_path):
    """A converter that hangs must be reported as a failure, not a success."""
    body = f'"{sys.executable}" -c "import time; time.sleep(2)"'
    stub = make_stub(tmp_path, body, name="slow_stub")
    src = tmp_path / "src"
    src.mkdir()
    f = src / "IMG_0001.CR3"
    f.write_bytes(b"raw")
    dest = tmp_path / "out"

    result = dng_converter.convert_group(stub, [str(f)], str(dest), timeout=0.5)

    assert result.ok is False
    assert result.failed == 1
    assert result.error is not None
    assert "timed out" in result.error
