"""Display-free helper coverage for the explanatory GUI."""

import json
import sys
import types
import unittest
from pathlib import Path
from unittest import mock

import stackcopy

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))


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


def plan_payload(**changes):
    payload = {
        "total": 348,
        "bytes": 8_600_000_000,
        "stacks": 12,
        "stacked_outputs": 12,
        "stack_inputs": 96,
        "others": 240,
        "newest_date": "2026-08-25",
        "dest_lightroom": r"Pictures\Lightroom\2026\2026-08-25",
        "dest_stack_input": r"Pictures\olympus.stack.input.photos\2026\2026-08-25",
        "source_subdirs_scanned": [r"DCIM\100OMSYS", r"DCIM\101OMSYS"],
        "source_is_removable": True,
        "source_would_be_empty_after": True,
    }
    payload.update(changes)
    return payload


class PlanParserTests(unittest.TestCase):
    def test_valid_payload_is_normalized(self):
        parsed = gui.parse_plan_json(json.dumps(plan_payload()))
        self.assertIsNotNone(parsed)
        self.assertEqual(parsed["total"], 348)
        self.assertEqual(parsed["others"], 240)

    def test_nested_others_total_is_accepted_for_compatibility(self):
        parsed = gui.parse_plan_json(
            json.dumps(plan_payload(others={"total": 240, "photos": 220, "videos": 20}))
        )
        self.assertEqual(parsed["others"], 240)

    def test_malformed_or_inconsistent_payload_degrades_to_no_plan(self):
        self.assertIsNone(gui.parse_plan_json("not json"))
        self.assertIsNone(gui.parse_plan_json(json.dumps(plan_payload(total=349))))
        self.assertIsNone(gui.parse_plan_json(json.dumps(plan_payload(bytes=-1))))


class DatedFolderSummaryTests(unittest.TestCase):
    def test_single_date_discloses_nothing(self):
        summary, folders = gui.dated_folder_summary(
            plan_payload(
                dest_dates=["2026-08-25"],
                dest_dirs=[r"Pictures\Lightroom\2026\2026-08-25"],
            )
        )
        self.assertEqual(summary, "")
        self.assertEqual(folders, [])

    def test_older_cli_without_folder_fields_discloses_nothing(self):
        self.assertEqual(gui.dated_folder_summary(plan_payload()), ("", []))
        self.assertEqual(gui.dated_folder_summary(None), ("", []))

    def test_multiple_dates_name_every_folder(self):
        dirs = [
            r"Pictures\Lightroom\2026\2026-08-23",
            r"Pictures\Lightroom\2026\2026-08-25",
            r"Pictures\olympus.stack.input.photos\2026\2026-08-25",
        ]
        summary, folders = gui.dated_folder_summary(
            plan_payload(
                dest_dates=["2026-08-23", "2026-08-25"], dest_dirs=dirs
            )
        )
        self.assertIn("2 dates", summary)
        self.assertIn("2026-08-23 to 2026-08-25", summary)
        self.assertIn("3 dated folders", summary)
        self.assertEqual(folders, dirs)


class OtherCardFilesTests(unittest.TestCase):
    def test_no_note_when_card_holds_only_media(self):
        self.assertEqual(gui.describe_other_card_files(plan_payload()), "")
        self.assertEqual(gui.describe_other_card_files(None), "")

    def test_trivial_only_is_a_gentle_ignore_message(self):
        note = gui.describe_other_card_files(
            plan_payload(other_files=0, other_files_trivial=4)
        )
        self.assertIn("safe to ignore", note)
        self.assertIn("format the card in the camera", note)

    def test_real_data_triggers_a_keep_warning(self):
        note = gui.describe_other_card_files(
            plan_payload(
                other_files=2,
                other_files_bytes=5_000_000,
                other_files_trivial=3,
                other_file_kinds={".TXT": 1, ".PDF": 1},
                other_file_examples=["notes.txt", "invoice.pdf"],
            )
        )
        self.assertIn("not photos or videos", note)
        self.assertIn("notes.txt", note)
        self.assertIn("Copy anything you want to keep", note)
        self.assertIn("format the card in the camera", note)

    def test_post_import_notice_carries_extra_and_tiny_file_warning(self):
        title, body = gui.post_import_card_notice(
            plan_payload(
                other_files=2,
                other_files_bytes=5_000_000,
                other_files_trivial=3,
                other_file_kinds={".TXT": 2},
            ),
            leave_on_card=True,
        )
        self.assertEqual(title, "Before you format the card")
        self.assertIn("not photos or videos", body)
        self.assertIn("3 tiny camera files are ignored", body)
        self.assertIn("format the card in the camera", body)

    def test_post_import_notice_always_includes_formatting_advice(self):
        title, body = gui.post_import_card_notice(
            plan_payload(), leave_on_card=True
        )
        self.assertEqual(title, "Before your next shoot")
        self.assertIn("format the card in the camera", body)

    def test_post_import_notice_is_omitted_for_non_removable_source(self):
        notice = gui.post_import_card_notice(
            plan_payload(
                source_is_removable=False,
                other_files=1,
                other_files_bytes=100,
            ),
            leave_on_card=False,
        )
        self.assertIsNone(notice)


class PlanScanActivityTests(unittest.TestCase):
    def test_scan_activity_bar_starts_and_stops_with_scan(self):
        window = mock.Mock(spec=gui.StackcopyGUI)
        window._plan_slow_after = None
        window._plan_scanning = False
        window._running = False
        window.source_scan_progress = mock.Mock()
        window.source_scan_var = mock.Mock()
        window.start_btn = mock.Mock()

        gui.StackcopyGUI._set_plan_scanning(window, True)
        window.source_scan_progress.grid.assert_called_once_with()
        window.source_scan_progress.start.assert_called_once_with()

        gui.StackcopyGUI._set_plan_scanning(window, False)
        window.source_scan_progress.stop.assert_called_once_with()
        window.source_scan_progress.grid_remove.assert_called_once_with()

    def test_repeated_scanning_calls_do_not_restart_activity_bar(self):
        window = mock.Mock(spec=gui.StackcopyGUI)
        window._plan_slow_after = None
        window._plan_scanning = False
        window._running = False
        window.source_scan_progress = mock.Mock()
        window.source_scan_var = mock.Mock()
        window.start_btn = mock.Mock()

        gui.StackcopyGUI._set_plan_scanning(window, True)
        gui.StackcopyGUI._set_plan_scanning(window, True)
        gui.StackcopyGUI._set_plan_scanning(window, True)

        window.source_scan_progress.start.assert_called_once_with()


class ResultCardNoticeTests(unittest.TestCase):
    @staticmethod
    def result_window(plan):
        window = mock.Mock(spec=gui.StackcopyGUI)
        for name in (
            "activity",
            "running_controls",
            "result_controls",
            "open_btn",
            "phase_var",
            "meta_var",
            "result_body_var",
            "result_body_label",
            "progress",
            "current_file_label",
            "card_followup_note",
            "card_followup_title_var",
            "card_followup_body_var",
            "card_followup_body_label",
            "after_idle",
        ):
            setattr(window, name, mock.Mock())
        window._plan = plan
        window.mode_var = mock.Mock()
        window.mode_var.get.return_value = gui.COPY_MODE
        return window

    def test_card_warning_is_shown_after_success(self):
        window = self.result_window(
            plan_payload(other_files=1, other_files_bytes=100, other_files_trivial=2)
        )

        gui.StackcopyGUI._show_result(
            window,
            "1 file imported",
            "done",
            problems=0,
            allow_open=True,
            success=True,
        )

        window.card_followup_note.grid.assert_called_once_with()
        body = window.card_followup_body_var.set.call_args.args[0]
        self.assertIn("not photos or videos", body)
        self.assertIn("2 tiny camera files are ignored", body)
        window.after_idle.assert_called_once_with(window._scroll_result_into_view)

    def test_card_warning_is_hidden_when_import_did_not_succeed(self):
        window = self.result_window(
            plan_payload(other_files=1, other_files_bytes=100, other_files_trivial=2)
        )

        gui.StackcopyGUI._show_result(
            window,
            "Import did not finish",
            "failed",
            problems=1,
            allow_open=False,
        )

        window.card_followup_note.grid.assert_not_called()
        window.card_followup_note.grid_remove.assert_called_once_with()

    def test_completion_scrolls_to_the_visible_result_area(self):
        window = mock.Mock(spec=gui.StackcopyGUI)
        window.body = mock.Mock()
        window.body._parent_canvas = mock.Mock()

        gui.StackcopyGUI._scroll_result_into_view(window)

        window.body._parent_canvas.yview_moveto.assert_called_once_with(1.0)

    def test_card_warning_is_hidden_for_non_removable_source(self):
        window = self.result_window(plan_payload(source_is_removable=False))

        gui.StackcopyGUI._show_result(
            window,
            "1 file imported",
            "done",
            problems=0,
            allow_open=True,
            success=True,
        )

        window.card_followup_note.grid.assert_not_called()
        window.card_followup_note.grid_remove.assert_called_once_with()


class ConditionalScrollbarTests(unittest.TestCase):
    def test_scrollbar_is_hidden_when_content_fits_and_restored_when_needed(self):
        window = mock.Mock(spec=gui.StackcopyGUI)
        window._body_scrollbar_visible = None
        window.body = mock.Mock()
        window.body._parent_canvas = mock.Mock()
        window.body._scrollbar = mock.Mock()
        window.body._parent_canvas.winfo_height.return_value = 700
        window.body.winfo_reqheight.return_value = 650

        gui.StackcopyGUI._update_body_scrollbar(window)
        window.body._scrollbar.grid_remove.assert_called_once_with()

        window.body.winfo_reqheight.return_value = 750
        gui.StackcopyGUI._update_body_scrollbar(window)
        window.body._scrollbar.grid.assert_called_once_with()


class ProgressParserTests(unittest.TestCase):
    def test_role_and_stack_output_name_survive_spaces(self):
        fields, filename = gui.parse_progress(
            "@@SCPROGRESS phase=move done=31 total=348 role=stack_input "
            "stack_output_name=P8081918%20stacked.jpg file=P8081912.ORF\n"
        )
        self.assertEqual(fields["role"], "stack_input")
        self.assertEqual(fields["stack_output_name"], "P8081918 stacked.jpg")
        self.assertEqual(filename, "P8081912.ORF")

    def test_recorded_progress_sequence_counts_each_lines_own_role(self):
        window = mock.Mock(spec=gui.StackcopyGUI)
        window._total = 0
        window._done = 0
        window._current_role = None
        window._bucket_done = {"stack_output": 0, "stack_input": 0, "other": 0}
        window._degraded = False
        window._plan = plan_payload(
            total=5, stacked_outputs=1, stack_inputs=2, others=2
        )
        window.progress = mock.Mock()
        window.phase_var = mock.Mock()
        window.current_file_var = mock.Mock()

        lines = [
            "@@SCPROGRESS phase=start done=0 total=5",
            "@@SCPROGRESS phase=move done=0 total=5 role=stack_output file=A.JPG",
            "@@SCPROGRESS phase=move done=1 total=5 role=stack_input file=A.ORF",
            "@@SCPROGRESS phase=move done=2 total=5 role=stack_input file=B.ORF",
            "@@SCPROGRESS phase=move done=3 total=5 role=other file=C.JPG",
            "@@SCPROGRESS phase=move done=4 total=5 role=other file=D.MOV",
            "@@SCPROGRESS phase=done done=5 total=5 degraded=0",
        ]
        for line in lines:
            gui.StackcopyGUI._handle_progress(window, line)

        self.assertEqual(
            window._bucket_done,
            {"stack_output": 1, "stack_input": 2, "other": 2},
        )
        self.assertEqual(window._done, 5)
        window.progress.set.assert_called_with(1.0)


class ButtonLabelTests(unittest.TestCase):
    def test_plan_count_and_mode_drive_primary_label(self):
        plan = plan_payload()
        self.assertEqual(
            gui.import_button_label(plan, leave_on_card=False), "Move 348 files"
        )
        self.assertEqual(
            gui.import_button_label(plan, leave_on_card=True), "Copy 348 files"
        )

    def test_no_plan_uses_generic_label(self):
        self.assertEqual(
            gui.import_button_label(None, leave_on_card=False), "Start import"
        )

    def test_preview_label_is_explicitly_non_destructive(self):
        self.assertEqual(
            gui.import_button_label(plan_payload(), leave_on_card=False, preview=True),
            "Preview without moving anything",
        )


class ModeChangeTests(unittest.TestCase):
    def test_mode_change_relabels_cached_plan_without_scheduling_a_scan(self):
        window = mock.Mock(spec=gui.StackcopyGUI)
        window._plan_generation = 7
        window._plan = plan_payload()

        gui.StackcopyGUI._on_mode_changed(window, gui.COPY_MODE)

        window._sync_mode_help.assert_called_once_with()
        window._schedule_save.assert_called_once_with()
        window._refresh_idle_plan.assert_called_once_with()
        window._schedule_plan_scan.assert_not_called()
        self.assertEqual(window._plan_generation, 7)
        self.assertEqual(window._plan, plan_payload())

    def test_card_empty_expectation_is_derived_from_current_mode(self):
        plan = plan_payload()
        self.assertTrue(gui.source_will_be_empty(plan, leave_on_card=False))
        self.assertFalse(gui.source_will_be_empty(plan, leave_on_card=True))


class ImportAnotherTests(unittest.TestCase):
    def test_keeps_remembered_source_and_rescans_it(self):
        window = mock.Mock(spec=gui.StackcopyGUI)
        window.activity = mock.Mock()
        window.actions = mock.Mock()
        window.src_var = mock.Mock()
        window._plan = plan_payload()
        window._plan_generation = 4

        gui.StackcopyGUI._import_another(window)

        window.src_var.set.assert_not_called()
        self.assertIsNone(window._plan)
        self.assertEqual(window._plan_generation, 5)
        window._refresh_idle_plan.assert_called_once_with()
        window._schedule_plan_scan.assert_called_once_with()


class TerminalSummaryTests(unittest.TestCase):
    def test_success_metrics_include_time_bytes_rate_and_failure_state(self):
        self.assertEqual(
            gui.success_metrics(20.0, 10 * 1024 * 1024),
            "20.0 seconds · 10.0 MB · 512.0 KB/s · nothing failed",
        )

    def test_success_metrics_pluralize_reported_problems(self):
        self.assertEqual(gui.success_metrics(1.0, 0, 1), "1.0 seconds · 1 problem")
        self.assertEqual(gui.success_metrics(1.0, 0, 3), "1.0 seconds · 3 problems")

    def test_cli_summary_uses_final_failure_and_import_counts(self):
        parsed = gui.parse_cli_summary(
            "Files safely placed: 348\n  Failures: 0\n"
            "Done. Imported 348 files in 18.4 seconds\n"
        )
        self.assertEqual(parsed, {"problems": 0, "imported": 348})


class PlanRowsComeFromTheHelperTests(unittest.TestCase):
    def test_the_gui_module_has_no_second_copy_of_the_row_wording(self):
        """The row strings must live in one place, or the modes drift apart."""
        source = (ROOT / "stackcopy_gui.py").read_text(encoding="utf-8")
        self.assertEqual(source.count("finished stacked photos - renamed"), 1)
        self.assertEqual(source.count("single shots and videos - names untouched"), 1)


class CanonFieldIntTests(unittest.TestCase):
    """``canon_field_int`` tolerates whatever a photographer types into the field.

    Mirrors ``CanonMethodIndexTests`` in ``tests/test_gui_update_state.py``: valid
    input passes through, everything else falls back to the caller's default
    rather than raising ``ValueError``/``TypeError`` at the moment Import is
    clicked.
    """

    def test_valid_numbers_pass_through_unchanged(self):
        self.assertEqual(gui.canon_field_int("3", 99), 3)
        self.assertEqual(gui.canon_field_int("0", 99), 0)
        self.assertEqual(gui.canon_field_int(" 4 ", 99), 4)

    def test_non_numeric_blank_whitespace_and_none_fall_back_to_the_default(self):
        for bad in ("abc", "", "   ", None):
            with self.subTest(bad=bad):
                self.assertEqual(gui.canon_field_int(bad, 99), 99)


if __name__ == "__main__":
    unittest.main()


class AdvancedButtonLabelTests(unittest.TestCase):
    """The caret must follow the section's state, not a hard-coded default.

    A window restored with Advanced already open used to show the content
    alongside a control still offering to open it, so the first click hid what
    the user was trying to reveal.
    """

    def test_an_open_section_offers_to_close(self):
        self.assertEqual(gui.advanced_button_label(True), "Advanced ▴")

    def test_a_closed_section_offers_to_open(self):
        self.assertEqual(gui.advanced_button_label(False), "Advanced ▾")

    def test_the_two_states_never_share_a_label(self):
        self.assertNotEqual(
            gui.advanced_button_label(True), gui.advanced_button_label(False)
        )


CANON_PLAN = {
    "mode": "canon-import", "stacks": 71, "stacked_outputs": 71,
    "stack_inputs": 1718, "others": 1, "total": 1790,
    "canon_frames_detected": 1719, "unresolved": 0,
}
OLYMPUS_PLAN = {
    "stacks": 3, "stacked_outputs": 3, "stack_inputs": 30,
    "others": 9, "total": 42,
}


class PlanModeTests(unittest.TestCase):
    def test_canon_plan_reports_canon(self):
        self.assertEqual(gui.plan_mode(CANON_PLAN), "canon-import")

    def test_a_plan_without_a_mode_field_is_lightroomimport(self):
        """An older CLI sends no mode; it can only have been lightroomimport."""
        self.assertEqual(gui.plan_mode(OLYMPUS_PLAN), "lightroomimport")

    def test_no_plan_is_lightroomimport(self):
        self.assertEqual(gui.plan_mode(None), "lightroomimport")


class PlanRowLabelTests(unittest.TestCase):
    def test_canon_rows_name_canon_things(self):
        stacked, frames, other = gui.plan_row_labels(CANON_PLAN)
        self.assertIn("71", stacked)
        self.assertIn("1718", frames)
        self.assertIn("DNG", frames)
        self.assertIn("1", other)
        self.assertIn("CR3", other)

    def test_olympus_rows_are_unchanged(self):
        stacked, frames, other = gui.plan_row_labels(OLYMPUS_PLAN)
        self.assertIn("3 finished stacked photos", stacked)
        self.assertIn("frames that fed those stacks", frames)
        self.assertIn("single shots and videos", other)

    def test_the_two_modes_do_not_share_wording(self):
        """A Canon card must not be described as single shots and videos."""
        canon = gui.plan_row_labels(CANON_PLAN)
        olympus = gui.plan_row_labels(OLYMPUS_PLAN)
        self.assertNotEqual(canon[1], olympus[1])
        self.assertNotIn("videos", canon[2])

    def test_olympus_first_row_substitutes_the_real_example_name(self):
        """Pins stackcopy_gui.py:1526-1539's example-substitution behaviour.

        Today's widget code reads ``stacked_output_example`` from the plan
        and splices it into the first row rather than naming the output
        count's own noun twice. A helper that used ``outputs`` in place of
        the example would still pass every other assertion here while
        silently changing what an Olympus user sees.
        """
        plan = dict(OLYMPUS_PLAN)
        plan["stacked_output_example"] = "P8081985 stacked.jpg"
        stacked, _frames, _other = gui.plan_row_labels(plan)
        self.assertIn("P8081985 stacked.jpg", stacked)
        self.assertIn("3 finished stacked photos", stacked)

    def test_olympus_first_row_falls_back_to_the_generic_example(self):
        """Without an explicit example, today's widget shows a stock one."""
        stacked, _frames, _other = gui.plan_row_labels(OLYMPUS_PLAN)
        self.assertIn("a name like P8081885 stacked.jpg", stacked)

    def test_no_plan_uses_the_fully_generic_olympus_wording(self):
        """With no plan at all, today's widget shows the count-free strings."""
        stacked, frames, other = gui.plan_row_labels(None)
        self.assertEqual(
            stacked,
            "Finished stacked photos - renamed with 'stacked' added to the name",
        )
        self.assertEqual(
            frames,
            "Frames that fed those stacks - kept in case you want to "
            "stack the RAWs yourself",
        )
        self.assertEqual(
            other,
            "Single shots and videos - names untouched, dated folders as "
            "Lightroom would make them",
        )


class DetectCanonModeTests(unittest.TestCase):
    def test_cr3_files_tick_the_box_and_say_why(self):
        ticked, reason = gui.detect_canon_mode(CANON_PLAN)
        self.assertTrue(ticked)
        self.assertIn("1719", reason)
        self.assertIn("CR3", reason)

    def test_a_card_without_cr3_does_not_tick(self):
        ticked, reason = gui.detect_canon_mode(OLYMPUS_PLAN)
        self.assertFalse(ticked)
        self.assertEqual(reason, "")

    def test_zero_cr3_reported_explicitly_does_not_tick(self):
        ticked, reason = gui.detect_canon_mode({"canon_frames_detected": 0})
        self.assertFalse(ticked)
        self.assertEqual(reason, "")

    def test_an_explicit_zero_count_degrades_exactly_like_a_missing_key(self):
        """A --lightroomimport scan now always reports the field, even as 0.

        ``detect_canon_mode`` must not distinguish "no CR3s, reported as 0"
        from "no CR3s, field absent" (an older CLI, or --canon-import's own
        payload before it ever computes a count) -- both mean "not Canon".
        """
        explicit_zero = gui.detect_canon_mode({"canon_frames_detected": 0})
        missing_key = gui.detect_canon_mode({})
        self.assertEqual(explicit_zero, missing_key)
        self.assertEqual(explicit_zero, (False, ""))


class CanonImportArgsTests(unittest.TestCase):
    def test_minimal_command_is_positional_only(self):
        args = gui.canon_import_args("SRC", "DST", "SINGLES")
        self.assertEqual(args[:4], ["--canon-import", "SRC", "DST", "--singles-dir"])
        self.assertIn("SINGLES", args)
        for flag in ("--no-dng", "--render-helicon", "--dng-jobs",
                     "--helicon-method", "--dry", "--verbose"):
            self.assertNotIn(flag, args)

    def test_defaults_are_not_passed_explicitly(self):
        """Passing the CLI's own defaults would pin values it may retune."""
        args = gui.canon_import_args(
            "SRC", "DST", "SINGLES", dng_jobs=1, method=2, radius=8, smoothing=4
        )
        for flag in ("--dng-jobs", "--helicon-method",
                     "--helicon-radius", "--helicon-smoothing"):
            self.assertNotIn(flag, args)

    def test_render_defaults_are_not_passed_explicitly(self):
        """The tuning guards only run once ``render=True`` enters the branch.

        Leaving ``render`` at its default in the sibling test never reaches
        the inner ``!=`` guards at all, so it cannot exercise them. This
        drives render=True with every tuning value left at its default to
        prove the nested guards - not just the outer one - suppress them.
        """
        args = gui.canon_import_args(
            "SRC", "DST", "SINGLES", render=True, method=2, radius=8, smoothing=4
        )
        self.assertIn("--render-helicon", args)
        for flag in ("--helicon-method", "--helicon-radius", "--helicon-smoothing"):
            self.assertNotIn(flag, args)

    def test_non_default_tuning_is_passed(self):
        args = gui.canon_import_args(
            "SRC", "DST", "SINGLES", render=True, method=0, radius=6, smoothing=7
        )
        self.assertIn("--render-helicon", args)
        self.assertIn("--helicon-method", args)
        self.assertEqual(args[args.index("--helicon-method") + 1], "0")

    def test_dng_off_maps_to_no_dng(self):
        self.assertIn("--no-dng", gui.canon_import_args("S", "D", "G", dng=False))

    def test_plan_json_and_dry_are_separate_switches(self):
        args = gui.canon_import_args("S", "D", "G", plan_json=True)
        self.assertIn("--plan-json", args)
        self.assertNotIn("--dry", args)

    def test_every_built_command_is_accepted_by_the_cli(self):
        """A GUI that builds an argv the CLI rejects is broken by construction."""
        parser = stackcopy.build_parser()
        for kwargs in (
            {},
            {"dry": True, "verbose": True},
            {"dng": False},
            {"render": True, "method": 1, "radius": 5, "smoothing": 3},
            {"plan_json": True},
            {"dng_jobs": 8},
        ):
            with self.subTest(**kwargs):
                parser.parse_args(gui.canon_import_args("S", "D", "G", **kwargs))

