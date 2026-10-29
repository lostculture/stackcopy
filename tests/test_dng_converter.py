import os
import stat
import subprocess
import sys
from pathlib import Path


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


def _writes_then_fails_stub(tmp_path, name="half_written_stub"):
    """Produces DNG output and then reports failure, as a killed run does."""
    if sys.platform == "win32":
        body = (
            r'for %%f in (%*) do @if not "%%~xf"==".dng" '
            r'echo partial > "%~4\%%~nf.dng"'
            "\nexit 1"
        )
    else:
        body = (
            'd="$4"; shift 4; mkdir -p "$d"; '
            'for f; do echo partial > "$d/$(basename "${f%.*}").dng"; done; '
            "exit 1"
        )
    return make_stub(tmp_path, body, name)


def test_convert_group_requires_exit_zero_even_when_files_exist(tmp_path):
    """Fix round 2, item 4: one module, one success rule.

    _convert_group used to declare a group converted purely because the
    expected files existed, ignoring the exit status -- while preflight()
    and helicon_focus.render_stack both required exit 0 AND a file. The real
    Adobe DNG Converter 18.6 was measured on this machine converting a real
    EOS R6 Mark II CR3: it exits 0 on success, so requiring it is safe, and
    a non-zero exit alongside output means the output is suspect.
    """
    stub = _writes_then_fails_stub(tmp_path)
    src = tmp_path / "src"
    src.mkdir()
    f = src / "IMG_0001.CR3"
    f.write_bytes(b"raw")
    dest = tmp_path / "out"

    result = dng_converter.convert_group(stub, [str(f)], str(dest))

    assert not result.ok
    assert result.failed == 1
    assert result.converted == 0
    # The message must say output WAS produced, so the user can judge
    # rather than assume the converter never ran.
    assert "did produce DNG output" in result.error
    assert "discarded" in result.error
    # Suspect output must not be left at the real path, where every later
    # resume would skip it as finished.
    assert not (dest / "IMG_0001.dng").exists()


def test_a_half_written_dng_is_not_mistaken_for_finished_work(tmp_path):
    """Fix round 2, item 5: resume must not trust a file that merely exists.

    A converter killed mid-write (Ctrl-C in hour two of a 65 GB import) used
    to leave a truncated DNG at the frame's real path. Every later run
    skipped it as already converted, and Helicon was then handed the
    truncated file. Converting into a staging directory and publishing with
    os.replace means the only file that ever reaches the real path is a
    complete one -- so the second run below must actually convert, not skip.
    """
    src = tmp_path / "src"
    src.mkdir()
    f = src / "IMG_0001.CR3"
    f.write_bytes(b"raw")
    dest = tmp_path / "out"

    killed = dng_converter.convert_group(
        _writes_then_fails_stub(tmp_path), [str(f)], str(dest)
    )
    assert not killed.ok
    assert list(dest.glob("*.dng")) == [], "nothing partial may be published"
    # Nor may staging debris be left behind for the next run to trip over.
    assert list(dest.glob(f"{dng_converter.STAGING_PREFIX}*")) == []

    resumed = dng_converter.convert_group(
        _success_stub(tmp_path), [str(f)], str(dest)
    )
    assert resumed.ok
    assert resumed.converted == 1
    assert resumed.skipped == 0, (
        "the killed run's leftovers were mistaken for finished work"
    )
    assert (dest / "IMG_0001.dng").read_text().strip() != "partial"


def test_converter_writes_to_staging_and_os_replace_publishes(tmp_path, monkeypatch):
    """The publish is a rename from staging, not a write to the final path.

    Asserted structurally rather than by racing a real process: the
    converter is never handed the destination folder as its -d argument,
    and the file that ends up there arrives via os.replace.
    """
    src = tmp_path / "src"
    src.mkdir()
    f = src / "IMG_0001.CR3"
    f.write_bytes(b"raw")
    dest = tmp_path / "out"

    seen = {}

    def fake_run(cmd, **kwargs):
        seen["dest_arg"] = cmd[4]  # [converter, -c, -p1, -d, <dir>, files...]
        Path(cmd[4], "IMG_0001.dng").write_bytes(b"complete")
        seen["published_during_run"] = (dest / "IMG_0001.dng").exists()
        return subprocess.CompletedProcess(cmd, 0, "", "")

    monkeypatch.setattr(dng_converter.subprocess, "run", fake_run)

    replaced = []
    real_replace = os.replace

    def spy_replace(a, b):
        replaced.append((a, b))
        return real_replace(a, b)

    monkeypatch.setattr(dng_converter.os, "replace", spy_replace)

    result = dng_converter.convert_group("/fake/converter", [str(f)], str(dest))

    assert result.ok and result.converted == 1
    assert seen["dest_arg"] != str(dest), (
        "the converter must never write straight to the frame's final folder"
    )
    assert os.path.dirname(seen["dest_arg"]) == str(dest), (
        "staging must be a child of dest_dir so os.replace stays atomic"
    )
    assert seen["published_during_run"] is False
    assert replaced == [
        (os.path.join(seen["dest_arg"], "IMG_0001.dng"),
         str(dest / "IMG_0001.dng"))
    ]
    assert (dest / "IMG_0001.dng").read_bytes() == b"complete"


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


def _tiff_bytes(image_size: int = 64) -> bytes:
    """A minimal little-endian TIFF: one IFD whose one strip ends at EOF.

    That is the shape Adobe DNG Converter writes - directories at the
    front, raw image data running to the last byte - so cutting bytes off
    the end leaves a header that still parses but points past EOF.
    """
    import struct

    entries = [
        (273, 4, 1, 38),          # StripOffsets: data right after the IFD
        (279, 4, 1, image_size),  # StripByteCounts
    ]
    ifd = struct.pack("<H", len(entries))
    for tag, typ, count, value in entries:
        ifd += struct.pack("<HHII", tag, typ, count, value)
    ifd += struct.pack("<I", 0)
    return b"II*\x00" + struct.pack("<I", 8) + ifd + b"\x00" * image_size


def _copying_stub(tmp_path, template: Path, name="copying_stub"):
    """A converter that 'converts' each frame by copying template into place.

    Exits 0 regardless - the real converter did exactly that on 2026-10-09,
    crashing partway through a frame and still reporting success.
    """
    script = tmp_path / f"{name}.py"
    script.write_text(
        "import os, shutil, sys\n"
        "dest = sys.argv[4]\n"
        "for f in sys.argv[5:]:\n"
        "    stem = os.path.splitext(os.path.basename(f))[0]\n"
        f"    shutil.copyfile({str(template)!r}, os.path.join(dest, stem + '.dng'))\n",
        encoding="utf-8",
    )
    args = '%*' if sys.platform == "win32" else '"$@"'
    return make_stub(tmp_path, f'"{sys.executable}" "{script}" {args}', name)


def test_dng_is_complete_accepts_a_whole_file(tmp_path):
    path = tmp_path / "whole.dng"
    path.write_bytes(_tiff_bytes())
    assert dng_converter.dng_is_complete(str(path))


def test_dng_is_complete_rejects_a_file_cut_off_mid_image(tmp_path):
    path = tmp_path / "cut.dng"
    path.write_bytes(_tiff_bytes()[:-10])
    assert not dng_converter.dng_is_complete(str(path))


def test_dng_is_complete_rejects_a_file_cut_off_inside_its_header(tmp_path):
    path = tmp_path / "stub.dng"
    path.write_bytes(_tiff_bytes()[:12])
    assert not dng_converter.dng_is_complete(str(path))


def test_dng_is_complete_rejects_an_empty_file(tmp_path):
    path = tmp_path / "empty.dng"
    path.write_bytes(b"")
    assert not dng_converter.dng_is_complete(str(path))


def test_dng_is_complete_rejects_a_zero_filled_file(tmp_path):
    """NEMF 2026 0016.dng: 9 MiB with its first 172 kB all zeros -- space
    reserved but never written. Resume skipped it, Helicon failed on it."""
    path = tmp_path / "zeros.dng"
    path.write_bytes(b"\x00" * 4096 + _tiff_bytes())
    assert not dng_converter.dng_is_complete(str(path))


def test_a_truncated_dng_from_an_exit_zero_converter_is_not_published(tmp_path):
    """The converter can crash mid-frame and still exit 0.

    On a real 56-stack import, six frames were published truncated this way
    (one at 1 MB of 25 MB), and Helicon then failed those stacks on every
    retry because resume skipped them as already converted.
    """
    template = tmp_path / "truncated.dng"
    template.write_bytes(_tiff_bytes()[:-10])
    src = tmp_path / "src"
    src.mkdir()
    f = src / "IMG_0001.CR3"
    f.write_bytes(b"raw")
    dest = tmp_path / "out"

    result = dng_converter.convert_group(
        _copying_stub(tmp_path, template), [str(f)], str(dest)
    )

    assert not result.ok
    assert result.failed == 1
    assert "IMG_0001" in result.error
    assert not (dest / "IMG_0001.dng").exists()


def test_resume_reconverts_a_truncated_dng_already_in_place(tmp_path):
    """Files truncated by an older run are redone, not skipped."""
    template = tmp_path / "whole.dng"
    template.write_bytes(_tiff_bytes())
    src = tmp_path / "src"
    src.mkdir()
    dest = tmp_path / "out"
    dest.mkdir()
    files = []
    for n in (1, 2):
        f = src / f"IMG_{n:04d}.CR3"
        f.write_bytes(b"raw")
        files.append(str(f))
    (dest / "IMG_0001.dng").write_bytes(_tiff_bytes())
    (dest / "IMG_0002.dng").write_bytes(_tiff_bytes()[:-10])

    result = dng_converter.convert_group(
        _copying_stub(tmp_path, template), files, str(dest)
    )

    assert result.ok
    assert result.skipped == 1
    assert result.converted == 1
    assert dng_converter.dng_is_complete(str(dest / "IMG_0002.dng"))
