"""Display-free coverage of the GUI's update wiring and settings file.

The settings file grew typed values; older files are all strings.  Both must
load, neither may crash the window, and the folders a photographer chose must
survive every update-checker write.
"""

import dataclasses
import json
import sys
import types
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

import stackcopy_updater as updater  # noqa: E402


def load_gui_module():
    try:
        import stackcopy_gui

        return stackcopy_gui
    except ImportError:
        pass

    class AnyModule(types.ModuleType):
        def __getattr__(self, name):
            if name.startswith("__"):
                raise AttributeError(name)
            placeholder = type(name, (object,), {})
            setattr(self, name, placeholder)
            return placeholder

    stubs = {
        name: AnyModule(name)
        for name in (
            "customtkinter",
            "tkinter",
            "tkinter.filedialog",
            "tkinter.messagebox",
        )
        if name not in sys.modules
    }
    with mock.patch.dict(sys.modules, stubs):
        import stackcopy_gui

        return stackcopy_gui


gui = load_gui_module()


class SettingsFileTests(unittest.TestCase):
    def setUp(self):
        self.directory = Path(__import__("tempfile").mkdtemp(prefix="stackcopy-state-"))
        self.path = self.directory / "gui-state.json"
        patcher = mock.patch.object(gui, "_settings_path", lambda: self.path)
        patcher.start()
        self.addCleanup(patcher.stop)
        self.addCleanup(
            lambda: __import__("shutil").rmtree(self.directory, ignore_errors=True)
        )

    def write(self, payload):
        self.path.write_text(
            payload if isinstance(payload, str) else json.dumps(payload),
            encoding="utf-8",
        )

    # --- backward compatibility ---

    def test_an_older_all_strings_settings_file_still_loads(self):
        self.write(
            {
                "source_dir": "/media/card/DCIM",
                "lightroom_dir": "/home/alan/Pictures/Lightroom",
                "stack_input_dir": "/home/alan/Pictures/stack",
                "file_mode": "copy",
                "verbose": "true",
                "detect_stacks": "false",
                "debug_stacks": "false",
                "advanced_open": "true",
            }
        )
        state = gui.load_gui_state()
        self.assertEqual(gui.state_text(state, "source_dir"), "/media/card/DCIM")
        self.assertEqual(gui.state_text(state, "file_mode"), "copy")
        self.assertTrue(gui.state_flag(state, "verbose"))
        self.assertFalse(gui.state_flag(state, "detect_stacks", True))
        self.assertTrue(gui.state_flag(state, "advanced_open"))

    def test_an_older_file_has_no_updater_fields_and_that_is_fine(self):
        self.write({"source_dir": "/media/card"})
        state = gui.load_gui_state()
        self.assertTrue(updater.update_checks_enabled(state))
        self.assertTrue(updater.should_check_automatically(state))
        self.assertEqual(updater.skipped_version(state), "")

    def test_typed_values_round_trip(self):
        gui.save_gui_state(
            {
                "source_dir": "/media/card",
                "verbose": True,
                "detect_stacks": False,
                updater.ENABLED_KEY: True,
                updater.SKIPPED_KEY: "1.6.0",
            }
        )
        raw = json.loads(self.path.read_text(encoding="utf-8"))
        # Written as real JSON booleans, not the string "true".
        self.assertIs(raw["verbose"], True)
        self.assertIs(raw["detect_stacks"], False)

        state = gui.load_gui_state()
        self.assertTrue(gui.state_flag(state, "verbose"))
        self.assertFalse(gui.state_flag(state, "detect_stacks", True))
        self.assertEqual(gui.state_text(state, "source_dir"), "/media/card")

    # --- damaged files must never stop the window opening ---

    def test_a_corrupt_settings_file_loads_as_no_settings(self):
        for payload in ("{not json", "", "[1, 2, 3]", '"a string"', "null"):
            with self.subTest(payload=payload):
                self.write(payload)
                self.assertEqual(gui.load_gui_state(), {})

    def test_a_missing_settings_file_loads_as_no_settings(self):
        self.assertFalse(self.path.exists())
        self.assertEqual(gui.load_gui_state(), {})

    def test_corrupt_updater_fields_fail_safe(self):
        self.write(
            {
                "source_dir": "/media/card",
                updater.ENABLED_KEY: ["not", "a", "flag"],
                updater.LAST_SUCCESS_KEY: {"nope": 1},
                updater.SKIPPED_KEY: 12345,
            }
        )
        state = gui.load_gui_state()
        # The unusable values are dropped on load; what survives still works.
        self.assertEqual(gui.state_text(state, "source_dir"), "/media/card")
        self.assertTrue(updater.update_checks_enabled(state))
        self.assertTrue(updater.should_check_automatically(state))
        self.assertEqual(updater.skipped_version(state), "")

    def test_an_unwritable_settings_directory_is_survivable(self):
        with mock.patch.object(
            gui, "_settings_path", lambda: Path("/proc/nonexistent/gui-state.json")
        ):
            gui.save_gui_state({"source_dir": "/media/card"})  # must not raise

    def test_saving_leaves_no_temporary_file_behind(self):
        gui.save_gui_state({"source_dir": "/media/card"})
        self.assertEqual([p.name for p in self.directory.iterdir()], ["gui-state.json"])


class StatePreservationTests(unittest.TestCase):
    """The folders a photographer chose, and the updater's own state, coexist."""

    def fake_gui(self, state):
        return SimpleNamespace(
            _state=dict(state),
            _save_state_scheduled=True,
            _advanced_open=True,
            src_var=SimpleNamespace(get=lambda: "/media/card/DCIM"),
            dst_var=SimpleNamespace(get=lambda: "/home/alan/Pictures/Lightroom"),
            stk_var=SimpleNamespace(get=lambda: "/home/alan/Pictures/stack"),
            mode_var=SimpleNamespace(get=lambda: gui.COPY_MODE),
            verbose_var=SimpleNamespace(get=lambda: True),
            detect_stacks_var=SimpleNamespace(get=lambda: True),
            debug_stacks_var=SimpleNamespace(get=lambda: False),
            canon_mode_var=SimpleNamespace(get=lambda: False),
            _canon_mode_auto=False,
            canon_dng_var=SimpleNamespace(get=lambda: True),
            canon_dng_jobs_var=SimpleNamespace(get=lambda: "1"),
            canon_render_var=SimpleNamespace(get=lambda: False),
            canon_method_var=SimpleNamespace(get=lambda: "2"),
            canon_radius_var=SimpleNamespace(get=lambda: "8"),
            canon_smoothing_var=SimpleNamespace(get=lambda: "4"),
        )

    def save(self, state):
        written = {}
        target = self.fake_gui(state)
        with mock.patch.object(gui, "save_gui_state", written.update):
            gui.StackcopyGUI._save_current_defaults(target)
        return written, target

    def test_saving_folders_does_not_discard_updater_state(self):
        written, _ = self.save(
            {
                updater.ENABLED_KEY: False,
                updater.SKIPPED_KEY: "1.6.0",
                updater.LAST_SUCCESS_KEY: "2026-08-26T12:00:00+00:00",
            }
        )
        self.assertEqual(written["source_dir"], "/media/card/DCIM")
        self.assertEqual(written["file_mode"], "copy")
        self.assertIs(written[updater.ENABLED_KEY], False)
        self.assertEqual(written[updater.SKIPPED_KEY], "1.6.0")
        self.assertEqual(written[updater.LAST_SUCCESS_KEY], "2026-08-26T12:00:00+00:00")

    def test_recording_a_check_does_not_discard_the_chosen_folders(self):
        _, target = self.save({})
        updater.record_success(target._state)
        updater.record_skip(target._state, "v1.7.0-build2")
        written = {}
        with mock.patch.object(gui, "save_gui_state", written.update):
            gui.StackcopyGUI._save_current_defaults(target)
        self.assertEqual(written["lightroom_dir"], "/home/alan/Pictures/Lightroom")
        self.assertEqual(written["stack_input_dir"], "/home/alan/Pictures/stack")
        self.assertEqual(written[updater.SKIPPED_KEY], "1.7.0")
        self.assertIn(updater.LAST_SUCCESS_KEY, written)

    def test_settings_are_written_as_typed_values(self):
        written, _ = self.save({})
        self.assertIs(written["verbose"], True)
        self.assertIs(written["debug_stacks"], False)
        self.assertIs(written["advanced_open"], True)

    def test_canon_settings_are_persisted_alongside_the_rest(self):
        written, _ = self.save({})
        self.assertIs(written["canon_mode"], False)
        self.assertIs(written["canon_mode_auto"], False)
        self.assertIs(written["canon_dng"], True)
        self.assertEqual(written["canon_dng_jobs"], "1")
        self.assertIs(written["canon_render"], False)
        self.assertEqual(written["canon_method"], "2")
        self.assertEqual(written["canon_radius"], "8")
        self.assertEqual(written["canon_smoothing"], "4")

    def test_an_unknown_key_from_a_future_build_is_preserved(self):
        written, _ = self.save({"something_new": "keep me"})
        self.assertEqual(written["something_new"], "keep me")


class SkipButtonTests(unittest.TestCase):
    """Skip This Version persists the application version, not the tag."""

    def fake_gui(self, latest):
        calls = []
        return SimpleNamespace(
            _state={},
            _update_info=updater.UpdateInfo(
                current_version="1.5.9",
                latest_version=latest,
                tag_name=f"v{latest}",
                release_name="",
                release_url=updater.RELEASES_URL,
                published_at="",
                notes="",
                is_newer=True,
            ),
            _save_current_defaults=lambda: calls.append("save"),
            _close_update_dialog=lambda: calls.append("close"),
            _hide_update_notice=lambda: calls.append("hide"),
            calls=calls,
        )

    def test_skipping_stores_the_normalized_version_and_closes_the_dialog(self):
        target = self.fake_gui("1.6.0")
        gui.StackcopyGUI._skip_this_version(target)
        self.assertEqual(target._state[updater.SKIPPED_KEY], "1.6.0")
        self.assertIn("save", target.calls)
        self.assertIn("close", target.calls)
        self.assertIn("hide", target.calls)

    def test_remind_me_later_persists_nothing_and_clears_the_notice(self):
        target = self.fake_gui("1.6.0")
        gui.StackcopyGUI._remind_me_later(target)
        # Nothing remembered, so the next ordinary check raises it again.
        self.assertEqual(target._state, {})
        self.assertIn("close", target.calls)
        self.assertIn("hide", target.calls)
        self.assertNotIn("save", target.calls)

    def test_skip_and_remind_differ_only_in_what_they_remember(self):
        skipped = self.fake_gui("1.6.0")
        gui.StackcopyGUI._skip_this_version(skipped)
        reminded = self.fake_gui("1.6.0")
        gui.StackcopyGUI._remind_me_later(reminded)
        self.assertTrue(updater.is_skipped(skipped._state, "1.6.0"))
        self.assertFalse(updater.is_skipped(reminded._state, "1.6.0"))

    def test_a_skipped_version_covers_its_later_build_recuts(self):
        target = self.fake_gui("1.6.0")
        gui.StackcopyGUI._skip_this_version(target)
        self.assertTrue(updater.is_skipped(target._state, "v1.6.0-build4"))
        self.assertFalse(updater.is_skipped(target._state, "v1.6.1"))


class ReleasePageTests(unittest.TestCase):
    def test_the_gui_only_ever_opens_a_validated_release_url(self):
        opened = []
        target = SimpleNamespace(
            _update_info=updater.UpdateInfo(
                current_version="1.5.9",
                latest_version="1.6.0",
                tag_name="v1.6.0",
                release_name="",
                # A URL that somehow got past the parser must still be caught
                # here, next to the call that hands it to a browser.
                release_url="https://evil.example/pwn",
                published_at="",
                notes="",
                is_newer=True,
            ),
            _close_update_dialog=lambda: None,
            _open_url=lambda url, quiet=False: opened.append(url),
        )
        gui.StackcopyGUI._open_release_page(target)
        self.assertEqual(opened, [updater.RELEASES_URL])


class ChangelogLookupTests(unittest.TestCase):
    def test_the_source_tree_changelog_is_found(self):
        found = gui.bundled_changelog_path()
        self.assertIsNotNone(found)
        self.assertEqual(found.name, "ChangeLog.md")
        self.assertTrue(found.is_file())

    def test_a_missing_changelog_falls_back_to_github(self):
        opened = []
        target = SimpleNamespace(
            _open_url=lambda url, quiet=False: (opened.append(url), False)[1]
        )
        with mock.patch.object(gui, "bundled_changelog_path", lambda: None):
            gui.StackcopyGUI._open_changelog(target)
        self.assertEqual(opened, [updater.CHANGELOG_URL])

    def test_a_local_changelog_is_preferred_over_the_website(self):
        opened = []
        target = SimpleNamespace(
            _open_url=lambda url, quiet=False: (opened.append(url), True)[1]
        )
        gui.StackcopyGUI._open_changelog(target)
        self.assertEqual(len(opened), 1)
        self.assertTrue(opened[0].startswith("file://"))
        self.assertTrue(opened[0].endswith("ChangeLog.md"))


class StartupBehaviourTests(unittest.TestCase):
    def test_the_automatic_check_waits_a_couple_of_seconds(self):
        self.assertGreaterEqual(updater.STARTUP_DELAY_SECONDS, 2)
        self.assertLessEqual(updater.STARTUP_DELAY_SECONDS, 3)

    def test_the_gui_reads_the_one_authoritative_version(self):
        import stackcopy

        self.assertEqual(gui.STACKCOPY_VERSION, stackcopy.STACKCOPY_VERSION)

    def updater_imports(self):
        import ast

        tree = ast.parse((ROOT / "stackcopy_updater.py").read_text(encoding="utf-8"))
        names = set()
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                names.update(alias.name.split(".")[0] for alias in node.names)
            elif isinstance(node, ast.ImportFrom) and node.module:
                names.add(node.module.split(".")[0])
        return names

    def test_the_updater_module_imports_without_a_display(self):
        # It must never pull in customtkinter or tkinter, or the check could
        # not run headless and the GUI could not import it safely.
        for forbidden in ("customtkinter", "tkinter", "stackcopy_gui"):
            self.assertNotIn(forbidden, self.updater_imports())

    def test_the_updater_is_standard_library_only(self):
        allowed = {
            "__future__",
            "http",
            "json",
            "re",
            "urllib",
            "dataclasses",
            "datetime",
        }
        self.assertLessEqual(self.updater_imports(), allowed)

    def test_the_updater_never_downloads_or_installs_anything(self):
        # This is a notifier. Nothing here may unpack, replace, or execute.
        for forbidden in ("shutil", "zipfile", "tarfile", "subprocess", "tempfile"):
            self.assertNotIn(forbidden, self.updater_imports())
        source = (ROOT / "stackcopy_updater.py").read_text(encoding="utf-8")
        for forbidden in ("urlretrieve", "os.replace", "os.execv", "webbrowser"):
            self.assertNotIn(forbidden, source)


class ResultHandlingTests(unittest.TestCase):
    """Automatic checks stay quiet; manual checks always answer the user."""

    def fake_gui(self, state=None):
        events = []
        target = SimpleNamespace(
            _closing=False,
            _update_generation=1,
            _update_checking=True,
            _update_info=None,
            _state=dict(state or {}),
            update_check_btn=SimpleNamespace(
                configure=lambda **kw: events.append(("button", kw))
            ),
            _save_current_defaults=lambda: events.append(("save", None)),
            _set_update_notice=lambda text: events.append(("notice", text)),
            _hide_update_notice=lambda: events.append(("hide", None)),
            _notify_update=lambda info: events.append(("notify", info.latest_version)),
            _show_update_dialog=lambda: events.append(("dialog", None)),
            events=events,
        )
        return target

    def handle(self, target, *, manual, info=None, error="", generation=1):
        boxes = []
        payload = {
            "generation": generation,
            "manual": manual,
            "info": info,
            "error": error,
        }
        fake_box = SimpleNamespace(
            showinfo=lambda *a: boxes.append(("info", a)),
            showwarning=lambda *a: boxes.append(("warning", a)),
            showerror=lambda *a: boxes.append(("error", a)),
        )
        with mock.patch.object(gui, "messagebox", fake_box):
            gui.StackcopyGUI._handle_update_result(target, payload)
        return [name for name, _ in target.events], boxes

    def newer(self, latest="1.6.0"):
        return updater.UpdateInfo(
            current_version="1.5.9",
            latest_version=latest,
            tag_name=f"v{latest}",
            release_name="",
            release_url=updater.RELEASES_URL,
            published_at="",
            notes="",
            is_newer=True,
        )

    def current(self):
        return updater.UpdateInfo(
            current_version="1.6.0",
            latest_version="1.6.0",
            tag_name="v1.6.0",
            release_name="",
            release_url=updater.RELEASES_URL,
            published_at="",
            notes="",
            is_newer=False,
        )

    # --- automatic ---

    def test_an_automatic_failure_shows_no_dialog(self):
        target = self.fake_gui()
        events, boxes = self.handle(
            target, manual=False, error="Could not reach GitHub"
        )
        self.assertEqual(boxes, [], "an automatic failure must never interrupt")
        self.assertIn("hide", events)
        self.assertIn(updater.LAST_FAILURE_KEY, target._state)
        self.assertNotIn(updater.LAST_SUCCESS_KEY, target._state)

    def test_an_automatic_success_records_the_success_not_the_attempt(self):
        target = self.fake_gui()
        self.handle(target, manual=False, info=self.current())
        self.assertIn(updater.LAST_SUCCESS_KEY, target._state)
        self.assertNotIn(updater.LAST_FAILURE_KEY, target._state)

    def test_an_automatic_check_that_finds_nothing_stays_silent(self):
        target = self.fake_gui()
        events, boxes = self.handle(target, manual=False, info=self.current())
        self.assertEqual(boxes, [])
        self.assertNotIn("notify", events)
        self.assertNotIn("dialog", events)
        self.assertIn("hide", events)

    def test_an_automatic_check_notifies_without_opening_a_dialog(self):
        target = self.fake_gui()
        events, boxes = self.handle(target, manual=False, info=self.newer())
        self.assertEqual(boxes, [])
        self.assertIn("notify", events)
        self.assertNotIn("dialog", events, "automatic checks must not interrupt")

    def test_an_automatic_check_respects_a_skipped_version(self):
        target = self.fake_gui({updater.SKIPPED_KEY: "1.6.0"})
        events, _ = self.handle(target, manual=False, info=self.newer("1.6.0"))
        self.assertNotIn("notify", events)

    def test_an_automatic_check_respects_a_skipped_build_recut(self):
        target = self.fake_gui({updater.SKIPPED_KEY: "1.6.0"})
        info = dataclasses.replace(
            self.newer("1.6.0"),
            latest_version="1.6.0-build3",
            tag_name="v1.6.0-build3",
        )
        events, _ = self.handle(target, manual=False, info=info)
        self.assertNotIn("notify", events)

    def test_a_newer_version_after_a_skip_is_notified_automatically(self):
        target = self.fake_gui({updater.SKIPPED_KEY: "1.6.0"})
        events, _ = self.handle(target, manual=False, info=self.newer("1.6.1"))
        self.assertIn("notify", events)

    # --- manual ---

    def test_a_manual_failure_shows_a_concise_error(self):
        target = self.fake_gui()
        _, boxes = self.handle(
            target, manual=True, error="Could not reach GitHub: no route to host"
        )
        self.assertEqual(len(boxes), 1)
        kind, args = boxes[0]
        self.assertEqual(kind, "warning")
        self.assertIn("no route to host", args[1])
        self.assertIn(updater.RELEASES_URL, args[1])

    def test_a_manual_check_says_so_when_already_up_to_date(self):
        target = self.fake_gui()
        events, boxes = self.handle(target, manual=True, info=self.current())
        self.assertEqual(len(boxes), 1)
        self.assertEqual(boxes[0][0], "info")
        self.assertIn("up to date", boxes[0][1][1])
        self.assertIn("notice", events)

    def test_a_manual_check_opens_the_dialog_when_something_is_newer(self):
        target = self.fake_gui()
        events, boxes = self.handle(target, manual=True, info=self.newer())
        self.assertIn("notify", events)
        self.assertIn("dialog", events)
        self.assertEqual(boxes, [])

    def test_a_manual_check_still_reports_a_version_the_user_skipped(self):
        target = self.fake_gui({updater.SKIPPED_KEY: "1.6.0"})
        events, _ = self.handle(target, manual=True, info=self.newer("1.6.0"))
        self.assertIn("notify", events)
        self.assertIn("dialog", events)

    def test_the_manual_button_is_re_enabled_afterwards(self):
        target = self.fake_gui()
        self.handle(target, manual=True, info=self.current())
        self.assertIn(("button", {"state": "normal"}), target.events)
        self.assertFalse(target._update_checking)

    # --- races ---

    def test_a_stale_result_from_an_earlier_check_is_ignored(self):
        target = self.fake_gui()
        target._update_generation = 5
        events, boxes = self.handle(
            target, manual=True, info=self.newer(), generation=4
        )
        self.assertEqual(events, [])
        self.assertEqual(boxes, [])

    def test_a_result_arriving_during_shutdown_is_ignored(self):
        target = self.fake_gui()
        target._closing = True
        events, boxes = self.handle(target, manual=True, info=self.newer())
        self.assertEqual(events, [])
        self.assertEqual(boxes, [])


class BackgroundCheckTests(unittest.TestCase):
    """The network call happens off the UI thread and reports back by queue."""

    def fake_gui(self):
        import queue

        return SimpleNamespace(
            _update_checking=False,
            _update_manual=False,
            _update_generation=0,
            _queue=queue.Queue(),
            update_check_btn=SimpleNamespace(configure=lambda **kw: None),
            _set_update_notice=lambda text: None,
        )

    def run_check(self, manual, result=None, error=None):
        target = self.fake_gui()

        def fake_check(current):
            self.assertNotEqual(
                __import__("threading").current_thread().name,
                "MainThread",
                "the update check must not run on the UI thread",
            )
            if error is not None:
                raise error
            return result

        with mock.patch.object(updater, "check_for_update", fake_check):
            gui.StackcopyGUI._start_update_check(target, manual)
        kind, payload = target._queue.get(timeout=5)
        return kind, payload, target

    def test_a_successful_check_is_delivered_through_the_ui_queue(self):
        info = updater.UpdateInfo(
            current_version="1.5.9",
            latest_version="1.6.0",
            tag_name="v1.6.0",
            release_name="",
            release_url=updater.RELEASES_URL,
            published_at="",
            notes="",
            is_newer=True,
        )
        kind, payload, target = self.run_check(manual=False, result=info)
        self.assertEqual(kind, "update")
        self.assertIs(payload["info"], info)
        self.assertEqual(payload["error"], "")
        self.assertFalse(payload["manual"])
        self.assertEqual(payload["generation"], target._update_generation)

    def test_an_update_check_error_is_delivered_not_raised(self):
        kind, payload, _ = self.run_check(
            manual=True, error=updater.UpdateCheckError("GitHub returned HTTP 503")
        )
        self.assertEqual(kind, "update")
        self.assertIsNone(payload["info"])
        self.assertEqual(payload["error"], "GitHub returned HTTP 503")
        self.assertTrue(payload["manual"])

    def test_an_unexpected_exception_cannot_kill_the_worker_thread(self):
        _, payload, _ = self.run_check(manual=False, error=RuntimeError("boom"))
        self.assertIn("boom", payload["error"])
        self.assertIsNone(payload["info"])

    def test_each_check_gets_a_new_generation(self):
        _, _, target = self.run_check(manual=False, result=None)
        self.assertEqual(target._update_generation, 1)


class HeaderConstructionTests(unittest.TestCase):
    """Build the header against a recording stand-in for customtkinter.

    tkinter is not importable in CI or on a headless machine, so this does not
    prove pixels; it does prove the widget graph the update UI adds is
    constructed, gridded, and hidden until there is something to say.
    """

    def fake_ctk(self, log):
        class Widget:
            def __init__(self, parent=None, **kwargs):
                self.parent = parent
                self.kwargs = kwargs
                self.gridded = False
                log.append(("create", type(self).__name__, kwargs.get("text")))

            def grid(self, **kwargs):
                self.gridded = True
                log.append(("grid", type(self).__name__, kwargs))

            def grid_remove(self):
                self.gridded = False
                log.append(("grid_remove", type(self).__name__, None))

            def grid_columnconfigure(self, *a, **k):
                pass

            def configure(self, **kwargs):
                self.kwargs.update(kwargs)

        names = (
            "CTkFrame",
            "CTkLabel",
            "CTkButton",
            "CTkFont",
            "CTkTextbox",
            "CTkToplevel",
        )
        namespace = {name: type(name, (Widget,), {}) for name in names}

        class StringVar:
            def __init__(self, value=""):
                self.value = value

            def set(self, value):
                self.value = value

            def get(self):
                return self.value

        namespace["StringVar"] = StringVar
        return SimpleNamespace(**namespace)

    def build_header(self):
        log = []
        fake = self.fake_ctk(log)
        target = SimpleNamespace(
            body=object(),
            _text_button=lambda parent, text, command: fake.CTkButton(
                parent, text=text, command=command
            ),
            _check_for_updates_manually=lambda: None,
            _show_update_dialog=lambda: None,
            _hide_update_notice=lambda: None,
            _open_exiftool_page=lambda: None,
        )
        with mock.patch.object(gui, "ctk", fake):
            gui.StackcopyGUI._build_header(target)
        return target, log

    def test_the_header_offers_a_permanent_manual_check(self):
        _, log = self.build_header()
        labels = [text for kind, _, text in log if kind == "create" and text]
        self.assertIn("Check for Updates", labels)

    def test_the_update_notice_is_built_but_hidden_until_there_is_news(self):
        target, _ = self.build_header()
        self.assertFalse(target.update_row.gridded)
        self.assertEqual(target.update_var.get(), "")
        self.assertTrue(hasattr(target, "update_view_btn"))
        self.assertTrue(hasattr(target, "update_dismiss_btn"))
        self.assertTrue(hasattr(target, "update_check_btn"))

    def test_the_existing_exiftool_row_is_still_built_and_hidden(self):
        target, _ = self.build_header()
        self.assertFalse(target.exiftool_row.gridded)
        self.assertTrue(hasattr(target, "exiftool_link"))

    def test_showing_a_notice_reveals_the_row_and_hiding_it_puts_it_back(self):
        target, _ = self.build_header()
        gui.StackcopyGUI._set_update_notice(target, "Checking…")
        self.assertTrue(target.update_row.gridded)
        self.assertEqual(target.update_var.get(), "Checking…")
        self.assertFalse(target.update_view_btn.gridded)

        gui.StackcopyGUI._hide_update_notice(target)
        self.assertFalse(target.update_row.gridded)
        self.assertEqual(target.update_var.get(), "")

    def test_an_available_update_offers_the_detail_view(self):
        target, _ = self.build_header()
        info = updater.UpdateInfo(
            current_version="1.5.9",
            latest_version="1.6.0",
            tag_name="v1.6.0",
            release_name="",
            release_url=updater.RELEASES_URL,
            published_at="",
            notes="",
            is_newer=True,
        )
        # _notify_update delegates to _set_update_notice, so bind the real one.
        target._set_update_notice = types.MethodType(
            gui.StackcopyGUI._set_update_notice, target
        )
        gui.StackcopyGUI._notify_update(target, info)
        self.assertTrue(target.update_row.gridded)
        self.assertTrue(target.update_view_btn.gridded)
        self.assertTrue(target.update_dismiss_btn.gridded)
        self.assertIn("Stackcopy 1.6.0 is available", target.update_var.get())
        self.assertIn("1.5.9", target.update_var.get())


class CanonStateRoundTripTests(unittest.TestCase):
    def test_every_canon_setting_round_trips(self):
        state = {
            "canon_mode": True, "canon_dng": False, "canon_dng_jobs": 8,
            "canon_render": True, "canon_method": 1,
            "canon_radius": 6, "canon_smoothing": 7,
        }
        self.assertTrue(gui.state_flag(state, "canon_mode"))
        self.assertFalse(gui.state_flag(state, "canon_dng", True))
        self.assertTrue(gui.state_flag(state, "canon_render"))
        self.assertEqual(gui.state_text(state, "canon_dng_jobs"), "8")
        self.assertEqual(gui.state_text(state, "canon_method"), "1")
        self.assertEqual(gui.state_text(state, "canon_radius"), "6")
        self.assertEqual(gui.state_text(state, "canon_smoothing"), "7")

    def test_absent_canon_settings_fall_back_to_the_cli_defaults(self):
        self.assertEqual(
            gui.state_text({}, "canon_dng_jobs", str(gui.CANON_DEFAULT_DNG_JOBS)),
            str(gui.CANON_DEFAULT_DNG_JOBS),
        )
        self.assertFalse(gui.state_flag({}, "canon_mode"))
        self.assertTrue(gui.state_flag({}, "canon_dng", True))


class CanonSectionConstructionTests(unittest.TestCase):
    """Build the Canon block against a recording stand-in for customtkinter.

    Proves the block is gridded at construction time and then hidden again
    before the window is ever shown, since it has no meaning until a scan
    finds CR3 files.
    """

    def fake_ctk(self, log):
        class Widget:
            def __init__(self, parent=None, **kwargs):
                self.parent = parent
                self.kwargs = kwargs
                self.gridded = False
                log.append(("create", type(self).__name__, kwargs.get("text")))

            def grid(self, **kwargs):
                self.gridded = True
                log.append(("grid", type(self).__name__, kwargs))

            def grid_remove(self):
                self.gridded = False
                log.append(("grid_remove", type(self).__name__, None))

        names = ("CTkFrame", "CTkCheckBox", "CTkLabel", "CTkEntry")
        return SimpleNamespace(
            **{name: type(name, (Widget,), {}) for name in names}
        )

    def build_canon_section(self):
        log = []
        fake = self.fake_ctk(log)
        target = SimpleNamespace(
            body=object(),
            canon_mode_var=SimpleNamespace(get=lambda: False),
            canon_reason_var=SimpleNamespace(get=lambda: ""),
            canon_dng_var=SimpleNamespace(get=lambda: True),
            canon_dng_jobs_var=SimpleNamespace(get=lambda: "1"),
            _on_canon_mode_changed=lambda: None,
            _save_current_defaults=lambda: None,
        )
        with mock.patch.object(gui, "ctk", fake):
            gui.StackcopyGUI._build_canon_section(target)
        return target, log

    def test_the_canon_block_is_built_but_hidden_by_default(self):
        target, _ = self.build_canon_section()
        self.assertFalse(target.canon_frame.gridded)
        self.assertTrue(hasattr(target, "canon_check"))
        self.assertTrue(hasattr(target, "canon_dng_check"))
        self.assertTrue(hasattr(target, "canon_jobs_entry"))


class CanonDetectionTests(unittest.TestCase):
    """``_apply_canon_detection`` shows/hides the block and states why.

    Driven through ``mock.Mock(spec=gui.StackcopyGUI)`` rather than a bare
    ``SimpleNamespace``: a namespace stub raises AttributeError the moment
    the method under test calls a collaborator the stub does not list, so a
    correct fix that calls one more method fails as though it were a bug.
    A spec'd Mock accepts any real method of the class.
    """

    def make_target(self, plan, mode_initial=False, canon_auto=False):
        window = mock.Mock(spec=gui.StackcopyGUI)
        window._plan = plan
        window._canon_mode_auto = canon_auto
        window.canon_frame = mock.Mock()
        window.canon_reason_var = mock.Mock()
        window.canon_mode_var = mock.Mock()
        window.canon_mode_var.get.return_value = mode_initial
        window.plan_headline_vars = {
            key: mock.Mock() for key in ("stack_output", "stack_input", "other")
        }
        window.plan_heading_var = mock.Mock()
        return window

    def test_a_canon_card_reveals_the_block_and_reports_why(self):
        window = self.make_target({"canon_frames_detected": 3})

        gui.StackcopyGUI._apply_canon_detection(window)

        window.canon_frame.grid.assert_called_once_with()
        window.canon_frame.grid_remove.assert_not_called()
        window.canon_reason_var.set.assert_called_once_with("found 3 CR3 files")

    def test_a_non_canon_card_keeps_the_block_hidden(self):
        window = self.make_target(None)

        gui.StackcopyGUI._apply_canon_detection(window)

        window.canon_frame.grid_remove.assert_called_once_with()
        window.canon_frame.grid.assert_not_called()
        window.canon_reason_var.set.assert_called_once_with("")

    def test_detection_turns_the_checkbox_on_and_saves(self):
        window = self.make_target({"canon_frames_detected": 1})

        gui.StackcopyGUI._apply_canon_detection(window)

        window.canon_mode_var.set.assert_called_once_with(True)
        window._save_current_defaults.assert_called_once_with()
        self.assertTrue(window._canon_mode_auto)

    def test_a_manually_checked_box_keeps_the_block_visible_without_cr3s(self):
        window = self.make_target(None, mode_initial=True)

        gui.StackcopyGUI._apply_canon_detection(window)

        window.canon_frame.grid.assert_called_once_with()
        window.canon_mode_var.set.assert_not_called()
        self.assertFalse(window._canon_mode_auto)

    def test_an_automatic_tick_blanks_the_stale_olympus_counts(self):
        """R19: the rows under the checkbox came from the scan that just ran.

        That scan was ``--lightroomimport``, so its ``stack_inputs`` is an
        ORF frame count, not a CR3 one. Rewording those numbers as Canon
        numbers would fabricate a figure, so the counts go away entirely.
        """
        window = self.make_target({"canon_frames_detected": 9, "stack_inputs": 30})

        gui.StackcopyGUI._apply_canon_detection(window)

        shown = [
            call.args[0]
            for var in window.plan_headline_vars.values()
            for call in var.set.call_args_list
        ]
        self.assertEqual(len(shown), 3)
        for text in shown:
            self.assertNotIn("30", text)
            self.assertNotIn("9", text)
        self.assertEqual(list(shown), list(gui.plan_row_labels(None)))
        heading = window.plan_heading_var.set.call_args.args[0]
        self.assertIn("rescan", heading.lower())

    def test_an_already_canon_plan_keeps_its_own_counts(self):
        """Nothing is stale when the plan already describes a Canon import."""
        window = self.make_target(
            {"mode": gui.CANON_MODE, "canon_frames_detected": 9, "stack_inputs": 9}
        )

        gui.StackcopyGUI._apply_canon_detection(window)

        window.canon_mode_var.set.assert_called_once_with(True)
        for var in window.plan_headline_vars.values():
            var.set.assert_not_called()
        window.plan_heading_var.set.assert_not_called()


class HeliconToggleTests(unittest.TestCase):
    """``_on_helicon_toggle`` enables/disables the sub-controls, never hides them."""

    def make_target(self, render_on):
        class Widget:
            def __init__(self):
                self.state = None

            def configure(self, **kwargs):
                self.state = kwargs.get("state")

        saved = {"calls": 0}
        target = SimpleNamespace(
            canon_render_var=SimpleNamespace(get=lambda: render_on),
            helicon_method=Widget(),
            helicon_radius=Widget(),
            helicon_smoothing=Widget(),
            _save_current_defaults=lambda: saved.__setitem__(
                "calls", saved["calls"] + 1
            ),
        )
        return target, saved

    def test_rendering_off_disables_the_helicon_sub_controls(self):
        target, saved = self.make_target(False)
        gui.StackcopyGUI._on_helicon_toggle(target)
        self.assertEqual(target.helicon_method.state, "disabled")
        self.assertEqual(target.helicon_radius.state, "disabled")
        self.assertEqual(target.helicon_smoothing.state, "disabled")
        self.assertEqual(saved["calls"], 1)

    def test_rendering_on_enables_the_helicon_sub_controls(self):
        target, _saved = self.make_target(True)
        gui.StackcopyGUI._on_helicon_toggle(target)
        self.assertEqual(target.helicon_method.state, "normal")
        self.assertEqual(target.helicon_radius.state, "normal")
        self.assertEqual(target.helicon_smoothing.state, "normal")


class HeliconMethodChangeTests(unittest.TestCase):
    def test_choosing_a_letter_stores_its_index_and_saves(self):
        set_calls = []
        saved = {"calls": 0}
        target = SimpleNamespace(
            canon_method_var=SimpleNamespace(set=lambda v: set_calls.append(v)),
            _save_current_defaults=lambda: saved.__setitem__(
                "calls", saved["calls"] + 1
            ),
        )
        gui.StackcopyGUI._on_helicon_method_changed(target, "C")
        self.assertEqual(set_calls, ["2"])
        self.assertEqual(saved["calls"], 1)


class CanonModeChangeTests(unittest.TestCase):
    def test_toggling_the_canon_checkbox_saves_refreshes_and_rescans(self):
        calls = []
        target = SimpleNamespace(
            _save_current_defaults=lambda: calls.append("save"),
            _refresh_idle_plan=lambda: calls.append("refresh"),
            _schedule_plan_scan=lambda: calls.append("scan"),
        )
        gui.StackcopyGUI._on_canon_mode_changed(target)
        self.assertEqual(calls, ["save", "refresh", "scan"])


class CanonMethodIndexTests(unittest.TestCase):
    """``canon_method_index`` tolerates whatever a hand-edited state file holds."""

    def test_valid_indices_pass_through_unchanged(self):
        self.assertEqual(gui.canon_method_index("0"), 0)
        self.assertEqual(gui.canon_method_index("1"), 1)
        self.assertEqual(gui.canon_method_index("2"), 2)

    def test_out_of_range_and_non_numeric_values_fall_back_to_the_default(self):
        for bad in ("7", "-1", "banana"):
            self.assertEqual(gui.canon_method_index(bad), gui.CANON_DEFAULT_METHOD)


class CanonMethodConstructionTests(unittest.TestCase):
    """A garbage persisted ``canon_method`` must not stop the window opening."""

    def fake_ctk(self, log):
        class Widget:
            def __init__(self, parent=None, **kwargs):
                self.parent = parent
                self.kwargs = kwargs
                self.gridded = False
                log.append(("create", type(self).__name__, kwargs.get("text")))

            def grid(self, **kwargs):
                self.gridded = True

            def grid_remove(self):
                self.gridded = False

            def grid_columnconfigure(self, *a, **k):
                pass

            def configure(self, **kwargs):
                self.kwargs.update(kwargs)

        class SegmentedButton(Widget):
            def set(self, value):
                self.value = value

        names = ("CTkFrame", "CTkButton", "CTkCheckBox", "CTkEntry")
        namespace = {name: type(name, (Widget,), {}) for name in names}
        namespace["CTkSegmentedButton"] = SegmentedButton
        return SimpleNamespace(**namespace)

    def build_actions(self, canon_method_value):
        log = []
        fake = self.fake_ctk(log)

        class Var:
            def __init__(self, value):
                self.value = value

            def get(self):
                return self.value

            def set(self, value):
                self.value = value

        target = SimpleNamespace(
            body=object(),
            _advanced_open=True,
            verbose_var=SimpleNamespace(get=lambda: False),
            detect_stacks_var=SimpleNamespace(get=lambda: True),
            debug_stacks_var=SimpleNamespace(get=lambda: False),
            canon_render_var=SimpleNamespace(get=lambda: False),
            canon_method_var=Var(canon_method_value),
            canon_radius_var=SimpleNamespace(get=lambda: "8"),
            canon_smoothing_var=SimpleNamespace(get=lambda: "4"),
            _text_button=lambda parent, text, command: fake.CTkButton(
                parent, text=text, command=command
            ),
            _toggle_advanced=lambda: None,
            _save_current_defaults=lambda: None,
            _on_detection_changed=lambda: None,
            _on_helicon_toggle=lambda: None,
            _on_helicon_method_changed=lambda value: None,
            _start=lambda preview: None,
        )
        with mock.patch.object(gui, "ctk", fake):
            gui.StackcopyGUI._build_actions(target)
        return target

    def test_a_valid_persisted_method_sets_the_matching_letter(self):
        target = self.build_actions("1")
        self.assertEqual(target.helicon_method.value, "B")
        self.assertEqual(target.canon_method_var.value, "1")

    def test_bad_persisted_values_fall_back_instead_of_crashing_construction(self):
        for bad in ("7", "-1", "banana"):
            target = self.build_actions(bad)
            self.assertEqual(target.helicon_method.value, "C")
            self.assertEqual(target.canon_method_var.value, "2")


class CanonFieldSaveWiringTests(unittest.TestCase):
    """The three free-typed fields must save on their own, like the path fields."""

    class FakeVar:
        def __init__(self):
            self.callbacks = []

        def trace_add(self, mode, callback):
            self.callbacks.append((mode, callback))

        def fire(self):
            for _mode, callback in self.callbacks:
                callback()

    def test_typing_in_any_of_the_three_fields_schedules_a_save(self):
        calls = []
        target = SimpleNamespace(
            canon_dng_jobs_var=self.FakeVar(),
            canon_radius_var=self.FakeVar(),
            canon_smoothing_var=self.FakeVar(),
            _schedule_save=lambda: calls.append("scheduled"),
        )
        gui.StackcopyGUI._bind_canon_field_saves(target)
        target.canon_dng_jobs_var.fire()
        target.canon_radius_var.fire()
        target.canon_smoothing_var.fire()
        self.assertEqual(calls, ["scheduled", "scheduled", "scheduled"])


class DestinationRowButtonTests(unittest.TestCase):
    """``_browse_destination`` edits the row it was clicked on."""

    def make_window(self, chosen):
        window = mock.Mock(spec=gui.StackcopyGUI)
        window.dst_var = mock.Mock()
        window.dst_var.get.return_value = "/pictures/stacks"
        window.stk_var = mock.Mock()
        window.stk_var.get.return_value = "/pictures/singles"
        window.plan_path_targets = {
            "stack_output": window.dst_var,
            "stack_input": window.dst_var,
            "other": window.stk_var,
        }

        def fake_browse(variable, _title):
            if chosen is not None:
                variable.get.return_value = chosen
                variable.set(chosen)

        window._browse.side_effect = fake_browse
        return window

    def test_the_canon_singles_row_edits_the_singles_folder(self):
        """Bound at build time this row edited the stacks destination.

        Row 3 in Canon mode is "loose CR3 files", showing dest_singles; the
        build-time binding pointed it at ``dst_var``, so choosing a folder
        here silently relocated the entire stacks destination instead.
        """
        window = self.make_window("/elsewhere/singles")

        gui.StackcopyGUI._browse_destination(window, "other")

        window.stk_var.set.assert_called_once_with("/elsewhere/singles")
        window.dst_var.set.assert_not_called()

    def test_the_canon_frames_row_edits_the_stacks_folder(self):
        window = self.make_window("/elsewhere/stacks")

        gui.StackcopyGUI._browse_destination(window, "stack_input")

        window.dst_var.set.assert_called_once_with("/elsewhere/stacks")
        window.stk_var.set.assert_not_called()

    def test_a_changed_folder_refreshes_the_rows(self):
        """The row must show what was picked, not what it showed before."""
        window = self.make_window("/elsewhere/stacks")

        gui.StackcopyGUI._browse_destination(window, "stack_output")

        window._on_path_changed.assert_called_once_with()

    def test_a_cancelled_dialog_changes_nothing_but_still_redraws(self):
        window = self.make_window(None)

        gui.StackcopyGUI._browse_destination(window, "other")

        window._on_path_changed.assert_not_called()
        window._refresh_idle_plan.assert_called_once_with()


class CanonStickyModeTests(unittest.TestCase):
    """An automatic Canon tick unticks itself on a card that is not Canon."""

    def make_window(self, payload, automatic=True, canon_mode=True):
        window = mock.Mock(spec=gui.StackcopyGUI)
        window._canon_mode_auto = automatic
        window._plan_generation = 3
        window.canon_mode_var = mock.Mock()
        window.canon_mode_var.get.return_value = canon_mode
        window.source_scan_var = mock.Mock()
        window._payload = payload
        return window

    def test_an_empty_canon_plan_drops_back_to_lightroom_and_rescans(self):
        payload = {"mode": gui.CANON_MODE, "total": 0, "canon_frames_detected": 0}
        window = self.make_window(payload)

        gui.StackcopyGUI._apply_plan(window, payload)

        window.canon_mode_var.set.assert_called_once_with(False)
        self.assertFalse(window._canon_mode_auto)
        window._save_current_defaults.assert_called_once_with()
        self.assertIsNone(window._plan)
        self.assertEqual(window._plan_generation, 4)
        window._schedule_plan_scan.assert_called_once_with()
        # The stale "0 photos and videos" summary must never be shown.
        window.source_scan_var.set.assert_not_called()

    def test_a_hand_ticked_box_survives_an_empty_canon_plan(self):
        payload = {"mode": gui.CANON_MODE, "total": 0, "bytes": 0,
                   "canon_frames_detected": 0}
        window = self.make_window(payload, automatic=False)

        gui.StackcopyGUI._apply_plan(window, payload)

        window.canon_mode_var.set.assert_not_called()
        window._schedule_plan_scan.assert_not_called()
        self.assertEqual(window._plan, payload)

    def test_a_canon_plan_with_files_is_applied_normally(self):
        payload = {"mode": gui.CANON_MODE, "total": 4, "bytes": 40,
                   "canon_frames_detected": 3}
        window = self.make_window(payload)

        gui.StackcopyGUI._apply_plan(window, payload)

        window.canon_mode_var.set.assert_not_called()
        self.assertEqual(window._plan, payload)
        window._refresh_idle_plan.assert_called_once_with()


class CanonModeSectionTests(unittest.TestCase):
    """The Move/Copy control is hidden where it does nothing."""

    def make_window(self):
        window = mock.Mock(spec=gui.StackcopyGUI)
        window.mode_frame = mock.Mock()
        return window

    def test_canon_mode_hides_the_move_copy_section(self):
        window = self.make_window()
        gui.StackcopyGUI._sync_mode_section(window, True)
        window.mode_frame.grid_remove.assert_called_once_with()
        window.mode_frame.grid.assert_not_called()

    def test_olympus_mode_shows_it(self):
        window = self.make_window()
        gui.StackcopyGUI._sync_mode_section(window, False)
        window.mode_frame.grid.assert_called_once_with()
        window.mode_frame.grid_remove.assert_not_called()


class RunningSentenceWordingTests(unittest.TestCase):
    """The running view must not say "Moving" during a Canon import."""

    def make_window(self, canon, mode):
        window = mock.Mock(spec=gui.StackcopyGUI)
        window._pending = (["--canon-import"], "/d", "/s", False)
        window.canon_mode_var = mock.Mock()
        window.canon_mode_var.get.return_value = canon
        window.mode_var = mock.Mock()
        window.mode_var.get.return_value = mode
        return window

    def test_canon_always_says_copying_even_with_move_selected(self):
        window = self.make_window(True, gui.MOVE_MODE)
        sentence = gui.StackcopyGUI._current_file_sentence(
            window, "IMG_0040.CR3", "other", None
        )
        self.assertTrue(sentence.startswith("Copying "))
        self.assertNotIn("Moving", sentence)

    def test_olympus_move_wording_is_unchanged(self):
        window = self.make_window(False, gui.MOVE_MODE)
        sentence = gui.StackcopyGUI._current_file_sentence(
            window, "P8081885.ORF", "other", None
        )
        self.assertTrue(sentence.startswith("Moving "))

    def test_the_convert_phase_describes_conversion_not_copying(self):
        window = self.make_window(True, gui.MOVE_MODE)
        sentence = gui.StackcopyGUI._current_file_sentence(
            window, "IMG_0040", "stack_input", None, "convert"
        )
        self.assertIn("DNG", sentence)

    def test_the_render_phase_names_helicon(self):
        window = self.make_window(True, gui.MOVE_MODE)
        sentence = gui.StackcopyGUI._current_file_sentence(
            window, "IMG_0040 helicon.jpg", "render", None, "render"
        )
        self.assertIn("Helicon", sentence)
