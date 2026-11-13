"""Deterministic diagnostics tests: no real screenshots, windows or input."""
from contextlib import ExitStack
import json
from types import SimpleNamespace
import unittest
from unittest.mock import MagicMock, patch

from PIL import Image

from chest_assets import load_builtin_templates
import chest_diagnostics as diagnostics
from chest_matcher import Match
from chest_windows import WindowInfo


class DiagnosticsTests(unittest.TestCase):
    def setUp(self):
        self.stack = ExitStack()
        self.addCleanup(self.stack.close)
        self.templates = load_builtin_templates()
        self.render = WindowInfo(101, 77, "Wuwa Tappo", (0, 0, 160, 100))
        self.input = WindowInfo(102, 77, "DesktopCatInput", self.render.rect)
        self.windows = [self.render, self.input]
        self.pairs = [(self.render, self.input)]
        self.origin = (0, 0)
        self.image = Image.new("RGB", (160, 100), (30, 45, 60))
        self.image.paste(self.templates[0][1], (20, 20))
        self.owner = 102
        self.roots = {}
        self.user32 = MagicMock()
        self.user32.GetThreadDpiAwarenessContext.return_value = -4
        self.user32.GetAwarenessFromDpiAwarenessContext.return_value = 2
        self.user32.GetDpiForWindow.return_value = 144
        self.user32.WindowFromPoint.side_effect = lambda _point: self.owner
        self.user32.GetAncestor.side_effect = lambda hwnd, _flag: self.roots.get(hwnd, hwnd)
        self.stack.enter_context(patch.object(diagnostics.desktop, "_user32", self.user32))
        self.stack.enter_context(patch.object(diagnostics.desktop, "_window_class", side_effect=lambda hwnd: "DesktopCatInput" if hwnd == 102 else "Tauri Window"))
        self.stack.enter_context(patch.object(diagnostics.desktop, "_pid", side_effect=lambda hwnd: 77 if hwnd in (101, 102, 901) else 88))
        self.stack.enter_context(patch.object(diagnostics.desktop, "is_desktop_available", return_value=True))
        self.stack.enter_context(patch.object(diagnostics.desktop, "virtual_rect", return_value=(0, 0, 160, 100)))
        self.stack.enter_context(patch.object(diagnostics.desktop, "find_pet_windows", side_effect=lambda: self.windows))
        self.stack.enter_context(patch.object(diagnostics.bg, "find_background_pairs", side_effect=lambda _: self.pairs))
        self.privilege = self.stack.enter_context(patch.object(diagnostics.desktop, "input_privilege_problem", return_value=None))
        self.capture = self.stack.enter_context(patch.object(diagnostics.desktop, "capture_desktop", side_effect=lambda: (self.image.copy(), self.origin)))
        self.hit = self.stack.enter_context(patch.object(diagnostics.desktop, "point_hits_window", side_effect=lambda x, y, window: self.roots.get(self.owner, self.owner) == window.hwnd))
        self.background = self.stack.enter_context(patch.object(diagnostics.bg, "BackgroundCapture"))
        self.manager = self.background.return_value
        self.manager.grab.side_effect = lambda *_: SimpleNamespace(image=self.image.copy(), origin=self.origin)
        self.crop = MagicMock(side_effect=self.crop_image)
        self.forbidden = []
        for owner, name in ((diagnostics.desktop, "safe_click"), (diagnostics.bg, "background_click"),
                            (diagnostics.desktop, "relaunch_elevated"), (diagnostics.desktop, "enable_dpi_awareness")):
            self.forbidden.append(self.stack.enter_context(patch.object(owner, name, side_effect=AssertionError(name))))
        self.save = self.stack.enter_context(patch.object(Image.Image, "save", side_effect=AssertionError("Screenshot saved")))
        self.addCleanup(self.assert_read_only)

    def assert_read_only(self):
        for forbidden in self.forbidden:
            forbidden.assert_not_called()
        self.save.assert_not_called()
        for name in ("SendInput", "PostMessageW", "SendMessageTimeoutW", "SetCursorPos", "SetForegroundWindow", "SetWindowPos", "BlockInput"):
            getattr(self.user32, name).assert_not_called()

    def crop_image(self, image, origin, window):
        x, y = window.rect[0]-origin[0], window.rect[1]-origin[1]
        return image.crop((x, y, x+window.rect[2]-window.rect[0], y+window.rect[3]-window.rect[1]))

    def collect(self, mode="borderless"):
        report = diagnostics.collect_diagnostics(self.templates, .94, mode, self.crop)
        self.assertFalse(report["input_sent"])
        self.assertFalse(report["screenshots_saved"])
        json.dumps(report, ensure_ascii=False)
        return report

    def test_recognized_report_has_window_dpi_owner_and_rect_cache(self):
        with patch.object(diagnostics, "detect_templates", wraps=diagnostics.detect_templates) as detect:
            report = self.collect()
        self.assertEqual(report["reason"], "recognized")
        self.assertEqual(report["stats"]["recognized_count"], 1)
        self.assertEqual(report["stats"]["raw_candidate_count"], 2)
        self.assertEqual(report["stats"]["unique_pattern_count"], 1)
        self.assertEqual(report["stats"]["searched_rect_count"], 1)
        self.capture.assert_called_once()
        self.crop.assert_called_once()
        detect.assert_called_once()
        self.background.assert_not_called()
        self.assertEqual(report["windows"][1]["dpi"], 144)
        accepted = next(c for c in report["candidates"] if c["accepted"])
        self.assertEqual(accepted["center"], [27, 26])
        self.assertTrue(accepted["hit_test_passed"])
        self.assertEqual(accepted["owner"]["root_hwnd"], 102)
        self.assertIn("未发送点击", diagnostics.summarize_diagnostics(report))

    def test_no_windows_is_explicit_and_captures_nothing(self):
        self.windows, self.pairs = [], []
        report = self.collect()
        self.assertEqual(report["reason"], "no_windows")
        self.capture.assert_not_called()
        self.background.assert_not_called()

    def test_background_missing_pair_never_falls_back_to_desktop_capture(self):
        self.pairs = []
        report = self.collect("background")
        self.assertEqual(report["reason"], "no_pairs")
        self.capture.assert_not_called()
        self.background.assert_not_called()

    def test_borderless_missing_pair_retains_raw_patterns_but_accepts_none(self):
        self.pairs = []
        report = self.collect()
        self.assertEqual(report["reason"], "no_pairs")
        self.assertEqual(report["stats"]["recognized_count"], 0)
        self.assertEqual(report["stats"]["raw_candidate_count"], 2)

    def test_visible_mode_can_recognize_without_a_background_pair(self):
        self.pairs = []
        report = self.collect("visible")
        self.assertEqual(report["reason"], "recognized")
        self.background.assert_not_called()

    def test_pattern_rejected_by_real_owner_keeps_both_pattern_and_owner_evidence(self):
        self.owner = 999
        report = self.collect()
        self.assertEqual(report["reason"], "hit_test_rejected")
        self.assertEqual(report["stats"]["hit_test_rejected_count"], 1)
        candidate = next(c for c in report["candidates"] if c["window_hwnd"] == 102)
        self.assertGreater(candidate["score"], .99)
        self.assertEqual(candidate["owner"]["pid"], 88)
        self.assertEqual(candidate["owner"]["root_hwnd"], 999)
        self.assertFalse(candidate["accepted"])

    def test_child_owner_reports_separate_root_and_pid(self):
        self.owner, self.roots[901] = 901, 102
        report = self.collect()
        candidate = next(c for c in report["candidates"] if c["accepted"])
        self.assertEqual(candidate["owner"]["hwnd"], 901)
        self.assertEqual(candidate["owner"]["root_hwnd"], 102)
        self.assertEqual(candidate["owner"]["pid"], 77)

    def test_empty_pattern_has_bounded_fixed_size_best_score_diagnostics(self):
        with patch.object(diagnostics, "detect_templates", return_value=[]), \
                patch.object(diagnostics, "find_match", return_value=Match(20, 20, 15, 12, .73)) as best:
            report = self.collect()
        self.assertEqual(report["reason"], "no_pattern")
        self.assertEqual(best.call_count, len(self.templates))
        self.assertTrue(all(call.kwargs["scales"] == (1.0,) for call in best.call_args_list))
        self.assertTrue(all(not c["accepted"] for c in report["best_matches"]))
        self.assertIn("0.730", diagnostics.summarize_diagnostics(report))

    def test_real_screen_range_rejects_padded_matches_and_partial_boxes(self):
        self.render = WindowInfo(101, 77, "Wuwa Tappo", (-10, 0, 160, 100))
        self.input = WindowInfo(102, 77, "DesktopCatInput", self.render.rect)
        self.windows, self.pairs = [self.render, self.input], [(self.render, self.input)]
        # The first box crosses the left real capture edge; the second crosses
        # the right edge even though its center remains within the screenshot.
        matches = [(1, Match(4, 20, 15, 12, 1.)), (1, Match(160, 20, 15, 12, 1.)),
                   (1, Match(30, 20, 15, 12, 1.))]
        with patch.object(diagnostics, "detect_templates", return_value=matches):
            report = self.collect()
        self.assertEqual(report["stats"]["outside_capture_count"], 4)
        self.assertEqual(report["stats"]["recognized_count"], 1)
        self.assertTrue(all(c["rejection"] == "outside_capture" for c in report["candidates"] if not c["in_capture"]))
        self.assertTrue(all(0 <= call.args[0] < 150 for call in self.hit.call_args_list))

    def test_large_image_skips_low_score_fft_but_still_runs_normal_detection(self):
        self.image = Image.new("RGB", (3200, 1000), (30, 45, 60))
        self.render = WindowInfo(101, 77, "Wuwa Tappo", (0, 0, 3200, 1000))
        self.input = WindowInfo(102, 77, "DesktopCatInput", self.render.rect)
        self.windows, self.pairs = [self.render, self.input], [(self.render, self.input)]
        with patch.object(diagnostics, "detect_templates", return_value=[]) as detect, \
                patch.object(diagnostics, "find_match", side_effect=AssertionError("Large low-score FFT")) as best:
            report = self.collect()
        self.assertEqual(report["reason"], "no_pattern")
        detect.assert_called_once()
        best.assert_not_called()
        self.assertEqual(report["stats"]["best_score_skipped_rect_count"], 1)
        self.assertIn("限制内存", diagnostics.summarize_diagnostics(report))

    def test_one_window_crop_failure_preserves_another_window_result(self):
        self.crop.side_effect = lambda image, origin, window: (_ for _ in ()).throw(RuntimeError("crop failed")) if window.hwnd == 101 else image.copy()
        report = self.collect()
        self.assertEqual(report["reason"], "recognized")
        self.assertEqual(report["windows"][0]["image_status"], "error")
        self.assertEqual(report["windows"][1]["image_status"], "ok")
        self.assertEqual(report["stats"]["recognized_count"], 1)

    def test_capture_failure_is_separate_and_redacts_local_paths(self):
        self.capture.side_effect = OSError(r"missing C:\Users\private-name\secret\capture.dll")
        report = self.collect()
        self.assertEqual(report["reason"], "capture_error")
        self.assertEqual(report["stats"]["capture_error_count"], 1)
        self.assertNotIn("private-name", json.dumps(report))

    def test_locked_desktop_captures_nothing(self):
        with patch.object(diagnostics.desktop, "is_desktop_available", return_value=False):
            report = self.collect()
        self.assertEqual(report["reason"], "capture_error")
        self.capture.assert_not_called()
        self.background.assert_not_called()

    def test_privilege_and_metadata_failures_are_read_only_and_do_not_hide_matches(self):
        self.privilege.return_value = "权限不足"
        self.user32.GetDpiForWindow.side_effect = OSError("DPI unavailable")
        report = self.collect()
        self.assertEqual(report["reason"], "recognized")
        self.assertEqual(report["privilege_problem"], "权限不足")
        self.assertEqual(report["stats"]["window_error_count"], 2)
        self.assertIn("权限不匹配", diagnostics.summarize_diagnostics(report))

    def test_background_capture_uses_client_origin_and_ignores_coverage_for_recognition(self):
        self.owner = 999
        self.origin = (-160, -100)
        report = self.collect("background")
        self.assertEqual(report["reason"], "recognized")
        self.capture.assert_not_called()
        self.manager.close.assert_called_once()
        candidate = report["candidates"][0]
        self.assertEqual(candidate["center"], [-133, -74])
        self.assertFalse(candidate["hit_test_passed"])
        self.assertTrue(candidate["accepted"])

    def test_background_error_still_closes_worker_and_never_uses_gdi(self):
        self.manager.grab.side_effect = RuntimeError("capture timeout")
        report = self.collect("background")
        self.assertEqual(report["reason"], "capture_error")
        self.manager.close.assert_called_once()
        self.capture.assert_not_called()

    def test_background_one_failed_pair_preserves_later_success(self):
        render2 = WindowInfo(201, 78, "Wuwa Tappo", (200, 0, 360, 100))
        input2 = WindowInfo(202, 78, "DesktopCatInput", render2.rect)
        self.windows += [render2, input2]
        self.pairs += [(render2, input2)]
        self.manager.grab.side_effect = [RuntimeError("first capture failed"), SimpleNamespace(image=self.image.copy(), origin=(200, 0))]
        report = self.collect("background")
        self.assertEqual(report["reason"], "recognized")
        self.assertEqual(report["stats"]["capture_error_count"], 1)
        self.assertEqual(report["stats"]["recognized_count"], 1)
        self.manager.close.assert_called_once()


if __name__ == "__main__":
    unittest.main()
