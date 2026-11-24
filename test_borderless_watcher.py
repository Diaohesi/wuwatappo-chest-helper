"""Visible-screen recognition with directed input and no physical mouse use."""
from contextlib import ExitStack, redirect_stdout
import io
import json
from pathlib import Path
import queue
import tempfile
import unittest
from unittest.mock import patch

from PIL import Image

import auto_chest
from chest_assets import load_builtin_templates
from chest_windows import WindowInfo


class BorderlessWatcherTests(unittest.TestCase):
    def setUp(self):
        self.stack = ExitStack()
        self.addCleanup(self.stack.close)
        self.now = 100.
        self.events = queue.Queue()
        self.watcher = auto_chest.Watcher(self.events)
        self.watcher.mode = "borderless"
        self.watcher.templates = load_builtin_templates()
        self.watcher.active.set()
        self.render = WindowInfo(51, 72, "Wuwa Tappo", (0, 0, 160, 100))
        self.input = WindowInfo(52, 72, "DesktopCatInput", (0, 0, 160, 100))
        self.windows = [self.render, self.input]
        self.desktop_available = True
        self.current = True
        self.occluded = False
        self.clickthrough = False
        self.screen_origin = (0, 0)
        self.frame = self.make_frame((20, 20, 1))
        self.capture_hook = None
        self.hover_hook = None
        self.press_hook = None
        self.sent = []

        self.stack.enter_context(patch.object(auto_chest.time, "monotonic", side_effect=lambda: self.now))
        self.stack.enter_context(patch.object(auto_chest.desktop, "is_desktop_available", side_effect=lambda: self.desktop_available))
        self.stack.enter_context(patch.object(auto_chest.desktop, "find_pet_windows", side_effect=lambda: list(self.windows)))
        self.stack.enter_context(patch.object(auto_chest.desktop, "input_privilege_problem", return_value=None))
        self.stack.enter_context(patch.object(auto_chest.desktop, "window_is_current", side_effect=lambda w: self.current and w in self.windows))
        self.stack.enter_context(patch.object(auto_chest.desktop, "point_hits_window", side_effect=lambda *_: not (self.occluded or self.clickthrough)))
        self.stack.enter_context(patch.object(auto_chest.desktop, "can_observe_disappearance", side_effect=lambda *_: not self.occluded))
        self.pairs = self.stack.enter_context(patch.object(auto_chest.bg, "find_background_pairs", side_effect=lambda _: [(self.render, self.input)] if self.windows else []))
        self.capture = self.stack.enter_context(patch.object(auto_chest.desktop, "capture_desktop", side_effect=self.capture_frame))
        self.click = self.stack.enter_context(patch.object(auto_chest.bg, "background_click", side_effect=self.directed_click))

        # An actively typing user must not delay this mode or enter a physical
        # mouse path. MaaFw's capture process is unnecessary for visible pixels.
        self.forbidden = []
        for owner, name in ((auto_chest.desktop, "safe_click"),
                            (auto_chest.desktop, "user_idle_seconds"),
                            (auto_chest.bg, "BackgroundCapture")):
            self.forbidden.append(self.stack.enter_context(patch.object(owner, name,
                side_effect=AssertionError(f"Forbidden borderless path: {name}"))))

    def make_frame(self, *icons):
        image = Image.new("RGB", (160, 100), (30, 45, 60))
        templates = dict(load_builtin_templates())
        for x, y, variant in icons:
            image.paste(templates[variant], (x, y))
        return image

    def capture_frame(self, windows):
        if self.capture_hook:
            self.capture_hook(self.capture.call_count)
        return self.frame.copy(), self.screen_origin

    def directed_click(self, x, y, window, should_cancel=None, before_press=None):
        self.assertEqual(window, self.input)
        self.assertIsNotNone(should_cancel)
        self.assertIsNotNone(before_press)
        if should_cancel():
            return False
        # Visibility is checked after hover, immediately before DOWN. General
        # cancellation remains valid during the following hold/release period.
        if self.hover_hook:
            self.hover_hook()
        if should_cancel() or not before_press():
            return False
        if self.press_hook:
            self.press_hook()
        if should_cancel():
            return False
        self.sent.append((self.now, x, y))
        return True

    def tick(self, delta=1., count=1):
        for _ in range(count):
            self.now += delta
            self.watcher.tick()

    def initial_click(self):
        self.tick(count=2)
        self.assertEqual([(x, y) for _, x, y in self.sent], [(27, 26)])
        self.assertEqual(self.watcher.confirmed, 0)

    def test_busy_user_clicks_only_paired_input_without_physical_mouse_or_maafw(self):
        self.initial_click()
        self.assertEqual(self.watcher.pending[0].lock.window_key, (52, 72))
        self.pairs.assert_called()
        for operation in self.forbidden:
            operation.assert_not_called()

    def test_two_real_chest_images_are_clicked_and_confirmed_independently(self):
        self.frame = self.make_frame((20, 20, 1), (90, 60, 1))
        self.initial_click()
        self.tick(.5)
        self.assertEqual([(x, y) for _, x, y in self.sent], [(27, 26), (97, 66)])
        self.assertEqual(len(self.watcher.pending), 2)
        self.frame = self.make_frame((90, 60, 1))
        self.tick(.5, count=2)
        self.assertEqual(self.watcher.confirmed, 1)
        self.assertEqual(len(self.watcher.pending), 1)
        self.frame = self.make_frame()
        self.clickthrough = True
        self.tick(.5, count=2)
        self.assertEqual(self.watcher.confirmed, 2)
        self.assertEqual(self.watcher.pending, [])
        self.assertEqual(len(self.sent), 2)

    def test_initial_occlusion_blocks_chest_shaped_pixels(self):
        self.occluded = True
        self.tick(count=4)
        self.click.assert_not_called()
        self.assertEqual(self.sent, [])
        self.assertEqual(self.watcher.clicks, 0)

    def test_occlusion_at_fresh_verification_prevents_directed_input(self):
        self.tick()
        self.capture_hook = lambda number: setattr(self, "occluded", True) if number == 3 else None
        self.tick()
        self.assertEqual(self.capture.call_count, 3)
        self.click.assert_not_called()
        self.assertEqual(self.sent, [])

    def test_occlusion_after_hover_cancels_before_button_down(self):
        self.hover_hook = lambda: setattr(self, "occluded", True)
        self.tick(count=2)
        self.click.assert_called_once()
        self.assertEqual(self.sent, [])
        self.assertEqual(self.watcher.clicks, 0)
        self.assertEqual(self.watcher.pending, [])

    def test_pause_after_hover_cancels_before_button_down(self):
        self.hover_hook = self.watcher.pause
        self.tick(count=2)
        self.click.assert_called_once()
        self.assertEqual(self.sent, [])
        self.assertEqual(self.watcher.pending, [])

    def test_chest_becoming_clickthrough_on_down_still_registers_sent_input(self):
        self.press_hook = lambda: setattr(self, "clickthrough", True)
        self.initial_click()
        self.assertEqual(self.watcher.clicks, 1)
        self.assertEqual(len(self.watcher.pending), 1)
        self.frame = self.make_frame()
        self.tick(count=2)
        self.assertEqual(self.watcher.confirmed, 1)
        self.assertEqual(len(self.sent), 1)

    def test_window_move_during_fresh_capture_prevents_click(self):
        self.tick()
        def move_at_verification(number):
            if number == 3:
                self.input = WindowInfo(52, 72, "DesktopCatInput", (5, 0, 165, 100))
                self.windows = [self.render, self.input]
        self.capture_hook = move_at_verification
        self.tick()
        self.click.assert_not_called()
        self.assertEqual(self.sent, [])

    def test_moved_window_cannot_confirm_disappearance_at_new_location(self):
        self.initial_click()
        self.frame = self.make_frame()
        self.input = WindowInfo(52, 72, "DesktopCatInput", (5, 0, 160, 100))
        self.windows = [self.render, self.input]
        self.tick(count=3)
        self.assertEqual(self.watcher.confirmed, 0)
        self.assertEqual(len(self.sent), 1)
        self.assertEqual(self.watcher.pending[0].lock.missing_frames, 0)

    def test_obstruction_breaks_disappearance_evidence_and_retry_budget_is_retained(self):
        self.initial_click()
        self.frame = self.make_frame()
        self.clickthrough = True
        self.tick()
        self.occluded = True
        self.tick(20., count=3)
        self.assertEqual(self.watcher.confirmed, 0)
        self.assertEqual(self.watcher.pending[0].attempts, 1)
        self.assertEqual(len(self.sent), 1)
        self.occluded = False
        self.tick()
        self.assertEqual(self.watcher.confirmed, 0)
        self.tick()
        self.assertEqual(self.watcher.confirmed, 1)

    def test_missing_capture_does_not_confirm_or_retry(self):
        self.initial_click()
        self.frame = self.make_frame()
        with patch.object(auto_chest.desktop, "capture_desktop", side_effect=auto_chest.desktop.CaptureUnavailable("lost desktop")):
            self.tick(10., count=3)
        self.assertEqual(self.watcher.confirmed, 0)
        self.assertEqual(len(self.sent), 1)
        self.assertTrue(self.watcher.active.is_set())
        self.assertIsNone(self.watcher.pending[0].visible_since)

    def test_lock_stops_capture_and_resumes_with_two_new_missing_observations(self):
        self.initial_click()
        self.frame = self.make_frame()
        self.tick()
        count = self.capture.call_count
        self.desktop_available = False
        self.tick(30., count=3)
        self.assertEqual(self.capture.call_count, count)
        self.assertEqual(self.watcher.confirmed, 0)
        self.assertEqual(len(self.sent), 1)
        self.desktop_available = True
        self.tick()
        self.assertEqual(self.watcher.confirmed, 0)
        self.tick()
        self.assertEqual(self.watcher.confirmed, 1)

    def test_privilege_mismatch_pauses_before_capture(self):
        with patch.object(auto_chest.desktop, "input_privilege_problem", return_value="权限不足"):
            self.tick()
        self.capture.assert_not_called()
        self.click.assert_not_called()
        self.assertFalse(self.watcher.active.is_set())
        self.assertTrue(any(kind == "privilege" for kind, _ in list(self.events.queue)))

    def test_no_verified_pair_never_falls_back_to_another_pet_window(self):
        with patch.object(auto_chest.bg, "find_background_pairs", return_value=[]):
            self.tick(count=3)
        self.capture.assert_not_called()
        self.click.assert_not_called()
        self.assertEqual(self.sent, [])

    def test_failed_attempts_continue_without_falling_back_to_physical_input(self):
        self.initial_click()
        self.tick(2.1, count=6)
        self.assertEqual(len(self.sent), 3)
        self.assertTrue(self.watcher.active.is_set())
        self.tick(6.)
        self.assertEqual(len(self.sent), 4)
        self.assertEqual(self.watcher.confirmed, 0)
        for operation in self.forbidden:
            operation.assert_not_called()

    def test_negative_monitor_origin_is_used_for_directed_click_coordinates(self):
        self.render = WindowInfo(51, 72, "Wuwa Tappo", (-160, -100, 0, 0))
        self.input = WindowInfo(52, 72, "DesktopCatInput", (-160, -100, 0, 0))
        self.windows = [self.render, self.input]
        self.screen_origin = (-160, -100)
        self.tick(count=2)
        self.assertEqual([(x, y) for _, x, y in self.sent], [(-133, -74)])


class BorderlessConfigurationTests(unittest.TestCase):
    def read_settings(self, settings):
        with tempfile.TemporaryDirectory() as directory:
            config = Path(directory) / "config.json"
            if settings is not None:
                config.write_text(json.dumps(settings), encoding="utf-8")
            with patch.object(auto_chest, "CONFIG", config):
                return auto_chest.read_config()

    def test_defaults_and_new_settings_use_borderless_mode(self):
        self.assertEqual(auto_chest.DEFAULTS["mode"], "borderless")
        self.assertEqual(self.read_settings(None)["mode"], "borderless")

    def test_legacy_background_default_migrates_without_changing_controls(self):
        settings = self.read_settings({"mode": "background", "threshold": .96, "interval": 3.})
        self.assertEqual(settings["mode"], "borderless")
        self.assertEqual(settings["threshold"], .96)
        self.assertEqual(settings["interval"], 3.)

    def test_legacy_explicit_visible_or_borderless_choice_is_preserved(self):
        for mode in ("visible", "borderless"):
            with self.subTest(mode=mode):
                self.assertEqual(self.read_settings({"mode": mode})["mode"], mode)

    def test_current_settings_version_preserves_every_valid_manual_choice(self):
        for mode in ("background", "borderless", "visible"):
            with self.subTest(mode=mode):
                self.assertEqual(self.read_settings({"settings_version": 2, "mode": mode})["mode"], mode)

    def test_missing_or_invalid_mode_defaults_to_borderless(self):
        for settings in ({}, {"mode": "unknown"}, {"mode": None}):
            with self.subTest(settings=settings):
                self.assertEqual(self.read_settings(settings)["mode"], "borderless")

    def test_mode_names_and_cli_help_expose_all_three_modes(self):
        self.assertEqual(set(auto_chest.MODE_NAMES), {"background", "borderless", "visible"})
        self.assertEqual(len(set(auto_chest.MODE_NAMES.values())), 3)
        self.assertEqual(set(auto_chest.MODE_DESCRIPTIONS), set(auto_chest.MODE_NAMES))
        self.assertTrue(all(description.strip() for description in auto_chest.MODE_DESCRIPTIONS.values()))
        output = io.StringIO()
        with patch.object(auto_chest.sys, "argv", ["auto_chest.py", "--help"]), redirect_stdout(output):
            with self.assertRaises(SystemExit) as exit_status:
                auto_chest.main()
        self.assertEqual(exit_status.exception.code, 0)
        for mode in ("background", "borderless", "visible"):
            self.assertIn(mode, output.getvalue())


if __name__ == "__main__":
    unittest.main()
