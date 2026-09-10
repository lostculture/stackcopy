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
