import sys
import tempfile
import threading
import time
import unittest
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT / "app"), str(ROOT / "tools")]
from ui_helpers import inspect_input, bounded_integer, validate_endpoint, duration_label, initial_model
from translate_from_terms import write_csv, read_input
from llm_stage2 import Stage2Config, Stage2Paused, run_stage2


class InputChecks(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.path = Path(self.temp.name) / "input.csv"

    def test_summary_follows_overwrite_and_skip_preferences(self):
        write_csv(self.path, ["简体中文", "是否需要翻译", "英语"], [
            {"简体中文": "你好", "是否需要翻译": "TRUE", "英语": "Hello"},
            {"简体中文": "再见", "是否需要翻译": "FALSE", "英语": ""},
            {"简体中文": "你好", "是否需要翻译": "TRUE", "英语": ""},
        ])
        self.assertEqual(inspect_input(self.path)[2], {"rows": 3, "targets": 1, "groups": 1, "eligible_rows": 1})
        self.assertEqual(inspect_input(self.path, True, True)[2]["eligible_rows"], 3)

    def test_missing_columns_and_short_rows(self):
        self.path.write_text("简体中文,英语\n你好,\n", encoding="utf-8")
        with self.assertRaisesRegex(ValueError, "是否需要翻译"):
            inspect_input(self.path)
        self.path.write_text("简体中文,是否需要翻译,英语\n你好,TRUE\n", encoding="utf-8")
        self.assertEqual(inspect_input(self.path)[2]["groups"], 1)

    def test_empty_bad_numeric_values(self):
        for value in ("", "x", "1.5", "0", "17"):
            with self.subTest(value=value), self.assertRaises(ValueError):
                bounded_integer(value, "并发", 1, 16)
        self.assertEqual(bounded_integer(" 3 ", "并发", 1, 16), 3)

    def test_endpoint_and_elapsed_time(self):
        for value in ("api.example.com", "https://", "https://a b"):
            with self.assertRaises(ValueError):
                validate_endpoint(value)
        self.assertEqual(validate_endpoint(" https://example.com/v1/chat/completions "), "https://example.com/v1/chat/completions")
        self.assertEqual(duration_label(3661), "01:01:01")

    def test_default_model_migration_is_limited_to_official_old_default(self):
        from llm_stage2 import DEFAULT_MODEL
        self.assertEqual(DEFAULT_MODEL, "deepseek-flash")
        self.assertEqual(initial_model({}), "deepseek-flash")
        self.assertEqual(initial_model({"llm_model": "deepseek-v4-flash"}), "deepseek-flash")
        self.assertEqual(initial_model({"llm_model": "deepseek-v4-flash",
            "llm_endpoint": "https://api.deepseek.com/v1/chat/completions"}), "deepseek-flash")
        self.assertEqual(initial_model({"llm_model": "my-model"}), "my-model")
        self.assertEqual(initial_model({"llm_model": "deepseek-v4-flash",
            "llm_endpoint": "https://custom.example/chat/completions"}), "deepseek-v4-flash")
        self.assertEqual(initial_model({"llm_model": "deepseek-v4-flash",
            "llm_endpoint": "https://api.deepseek.com.example/chat/completions"}), "deepseek-v4-flash")


class PauseChecks(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.path = self.root / "input.csv"
        write_csv(self.path, ["简体中文", "是否需要翻译", "英语"], [
            {"简体中文": f"测试{i}", "是否需要翻译": "TRUE", "英语": ""} for i in range(6)
        ])
        self.stop = threading.Event()
        self.config = Stage2Config(
            input_path=self.path, term_path=None, output_path=self.root / "out.csv",
            report_path=self.root / "report.csv", threads=2, flush_interval=50,
            stop_event=self.stop, use_terms=False,
        )

    def test_pause_saves_inflight_and_resume_does_not_repeat_requests(self):
        started, release = threading.Barrier(3), threading.Event()
        calls, errors = [], []
        def translate(task, config):
            calls.append(task.source)
            if len(calls) <= 2 and not release.is_set():
                started.wait(timeout=5)
                release.wait(timeout=5)
            return {"translations": {"英语": task.source + " translated"}, "needs_manual_review": False}
        def worker():
            try:
                run_stage2(self.config)
            except Exception as exc:
                errors.append(exc)
        with patch("llm_stage2.call_llm", side_effect=translate):
            thread = threading.Thread(target=worker)
            thread.start()
            started.wait(timeout=5)
            self.stop.set()
            release.set()
            thread.join(timeout=5)
            self.assertFalse(thread.is_alive())
            self.assertEqual(len(calls), 2)
            self.assertIsInstance(errors[0], Stage2Paused)
            rows = read_input(self.config.output_path)[1]
            self.assertEqual(sum(bool(row["英语"]) for row in rows), 2)
            self.assertTrue(self.config.report_path.is_file())
            self.stop.clear()
            run_stage2(self.config)
        self.assertEqual(len(calls), 6)
        self.assertEqual(len(set(calls)), 6)
        self.assertTrue(all(row["英语"] for row in read_input(self.config.output_path)[1]))

    def test_pause_before_requests_does_not_call_model(self):
        self.stop.set()
        with patch("llm_stage2.call_llm") as translate, self.assertRaises(Stage2Paused):
            run_stage2(self.config)
        translate.assert_not_called()


class DesktopInteractions(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        import tkinter
        try:
            probe = tkinter.Tk()
            probe.withdraw()
            probe.destroy()
        except tkinter.TclError as exc:
            raise unittest.SkipTest(f"Tk display unavailable: {exc}")

    def setUp(self):
        import qxqy_direct_translator_gui as gui
        self.gui = gui
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.folder = Path(self.temp.name)
        self.settings_patch = patch.object(gui, "CONFIG_PATH", self.folder / "settings.json")
        self.settings_patch.start()
        self.addCleanup(self.settings_patch.stop)
        with patch.object(gui.DirectTranslatorApp, "_load_settings", return_value={}):
            self.app = gui.DirectTranslatorApp()
        self.app.root.withdraw()
        self.addCleanup(self.destroy_app)
        self.input = self.folder / "input.csv"
        write_csv(self.input, ["简体中文", "是否需要翻译", "英语"], [
            {"简体中文": "你好", "是否需要翻译": "TRUE", "英语": ""},
        ])
        self.app.input_path.set(str(self.input))
        self.app.output_dir.set(str(self.folder))
        self.app.llm_api_key.set("test-key")
        self.callback_errors = []
        self.app.root.report_callback_exception = lambda *exc: self.callback_errors.append(exc)

    def destroy_app(self):
        try:
            # Cancel Tcl callbacks to avoid stale scripts across Tk test roots.
            for callback in self.app.root.tk.call("after", "info"):
                self.app.root.after_cancel(callback)
            self.app.root.destroy()
        except self.gui.TclError:
            pass
        self.assertEqual(self.callback_errors, [])

    def start_mocked(self):
        with patch.object(self.gui.threading, "Thread") as thread:
            self.app.run()
        return thread.call_args.kwargs["args"][0]

    def test_missing_key_focuses_settings_without_modal(self):
        self.app.llm_api_key.set("")
        with patch.object(self.gui.messagebox, "showerror") as modal:
            self.app.run()
        modal.assert_not_called()
        self.assertEqual(self.app._step, 0)
        self.assertIn("API Key", self.app.form_feedback.get())
        self.assertFalse(self.app._running)

    def test_first_step_guides_service_before_file(self):
        self.assertEqual(self.app._step, 0)
        self.assertEqual(self.app.run_button["text"], "保存并继续  →")
        self.assertTrue(self.app.step_buttons[2].instate(["disabled"]))
        self.app.llm_api_key.set("")
        self.app.input_path.set("")
        with patch.object(self.gui.threading, "Thread") as worker:
            self.app._primary_action()
            self.app.run()  # Also protect direct/shortcut calls.
        worker.assert_not_called()
        self.assertEqual(self.app._step, 0)
        self.assertIn("API Key", self.app.form_feedback.get())

    def test_next_saves_service_without_request_then_back_preserves_input(self):
        with patch.object(self.gui.threading, "Thread") as worker:
            self.app._primary_action()
        worker.assert_not_called()
        self.assertEqual(self.app._step, 1)
        self.assertTrue(self.app._service_confirmed)
        self.assertTrue((self.folder / "settings.json").is_file())
        self.app._go_step(0)
        self.assertEqual(self.app.input_path.get(), str(self.input))
        self.app.llm_endpoint.set("bad endpoint")
        self.assertFalse(self.app._service_confirmed)
        self.app._go_step(1)
        self.assertEqual(self.app._step, 0)
        self.assertIn("接口地址", self.app.form_feedback.get())

    def test_running_stays_on_results_and_blocks_navigation(self):
        self.start_mocked()
        self.assertEqual(self.app._step, 2)
        self.app._go_step(0)
        self.assertEqual(self.app._step, 2)
        self.assertTrue(self.app.step_buttons[0].instate(["disabled"]))
        self.app._handle_message("paused", None)
        self.app._go_step(0)
        self.assertEqual(self.app._step, 0)
        self.assertTrue(self.app.step_buttons[0].instate(["!disabled"]))

    def test_service_and_preview_cards_align_on_grid(self):
        self.app.root.deiconify()
        self.app.root.update()
        self.assertEqual(self.app.service_card.winfo_rooty(), self.app.service_guide.winfo_rooty())
        self.app._show_step(1)
        self.app.root.update()
        self.assertEqual(self.app.file_card.winfo_rooty(), self.app.preview_tab.winfo_rooty())
        self.assertGreater(self.app.input_entry.winfo_width(), 80)
        self.app.root.withdraw()

    def test_invalid_integer_does_not_start_worker(self):
        self.app.llm_threads.set("bad")
        with patch.object(self.gui.threading, "Thread") as thread:
            self.app.run()
        thread.assert_not_called()
        self.assertIn("整数", self.app.form_feedback.get())
        self.assertTrue(self.app._advanced_open)

    def test_running_locks_fields_and_pause_restores_them(self):
        config = self.start_mocked()
        self.assertTrue(self.app.input_entry.instate(["disabled"]))
        self.assertTrue(self.app.glossary_import_button.instate(["disabled"]))
        self.app.stop()
        self.assertTrue(config.stop_event.is_set())
        self.app._handle_message("paused", None)
        self.assertTrue(self.app.input_entry.instate(["!disabled"]))
        self.assertEqual(self.app.run_button["text"], "继续翻译")

    def test_close_waits_for_saved_pause(self):
        self.start_mocked()
        with patch.object(self.gui.messagebox, "askokcancel", return_value=True), patch.object(self.app, "_finish_close") as close:
            self.app._on_close()
            close.assert_not_called()
            self.assertTrue(self.app._close_requested)
            self.app._handle_message("paused", None)
            close.assert_called_once()

    def test_progress_never_moves_back_for_validation(self):
        self.start_mocked()
        self.app._progress_limit = 90
        self.app._handle_message("stage_progress", ("翻译", 9, 10))
        self.app._handle_message("stage_progress", ("翻译", 8, 10))
        self.assertEqual(float(self.app.progress["value"]), 81)
        self.app._handle_message("validating", None)
        self.assertEqual(float(self.app.progress["value"]), 95)

    def test_preview_and_stale_background_result(self):
        result = inspect_input(self.input)
        self.app._display_preview(result)
        self.assertEqual(len(self.app.input_tree.get_children()), 1)
        self.app._handle_message("preview", (self.app._preview_generation-1, None, "stale error"))
        self.assertNotIn("stale", self.app.input_summary.get())

    def test_log_copy_data_redacts_api_key(self):
        self.app._append_log("a failure containing test-key")
        self.assertNotIn("test-key", self.app.log_text.get("1.0", "end"))

    def test_completion_has_result_buttons_without_modal(self):
        config = self.start_mocked()
        config.output_path.write_text("test", encoding="utf-8")
        config.report_path.write_text("test", encoding="utf-8")
        with patch.object(self.gui.messagebox, "showinfo") as modal:
            self.app._handle_message("done", {"direct": {
                "manual_review": 0, "stage2_filled_cells": 1,
            }})
        modal.assert_not_called()
        self.assertTrue(self.app.result_button.instate(["!disabled"]))
        self.assertTrue(self.app.report_button.instate(["!disabled"]))
        self.assertEqual(self.app.status_title.get(), "翻译完成")

    def test_mapping_has_preview_and_inline_validation(self):
        from glossary_dialog import GlossaryDialog
        dialog = GlossaryDialog(self.app.root, ROOT / "examples/glossary.csv")
        self.assertEqual(len(dialog.preview_tree.get_children()), 2)
        self.assertTrue(dialog.confirm_button.instate(["!disabled"]))
        dialog.variables["英语"].set("简体中文")
        dialog._update_preview()
        self.assertIn("多个语言", dialog.status.get())
        self.assertTrue(dialog.confirm_button.instate(["disabled"]))
        dialog.variables["英语"].set("英语")
        dialog.confirm(ROOT / "examples/glossary.csv")
        self.assertEqual(dialog.result[1], 2)

    def test_compact_window_keeps_primary_actions_visible(self):
        self.app.root.minsize(920, 620)
        self.app.root.geometry("960x680+20+20")
        self.app.root.deiconify()
        self.app.root.update()
        for step in range(3):
            self.app._show_step(step)
            self.app.root.update_idletasks()
            self.assertGreater(self.app.pages[step].winfo_height(), 200)
            def assert_controls_fit(parent):
                for child in parent.winfo_children():
                    if child.winfo_ismapped() and child.winfo_class() in ("TButton", "TMenubutton"):
                        self.assertGreaterEqual(child.winfo_width(), child.winfo_reqwidth(),
                                                child.cget("text"))
                    assert_controls_fit(child)
            assert_controls_fit(self.app.root)
        for widget in (self.app.run_button, self.app.stop_button):
            self.assertTrue(widget.winfo_ismapped())
            self.assertLessEqual(widget.winfo_rooty()+widget.winfo_height(),
                                 self.app.root.winfo_rooty()+self.app.root.winfo_height())
        self.app.root.withdraw()


if __name__ == "__main__":
    unittest.main()
