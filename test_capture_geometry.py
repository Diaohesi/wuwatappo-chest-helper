"""Screen intersection checks; every screenshot and input operation is mocked."""
from contextlib import ExitStack
import queue
import unittest
from unittest.mock import patch

from PIL import Image

import auto_chest
from chest_assets import load_builtin_templates
from chest_matcher import Match
from chest_windows import WindowInfo


class RawCaptureGeometryTests(unittest.TestCase):
    def setUp(self):
        self.image = Image.new("RGB", (160, 100), (30, 45, 60))
        self.window = WindowInfo(51, 72, "DesktopCatInput", (-8, -8, 168, 108))

    def test_partial_window_crop_keeps_original_window_coordinates(self):
        self.image.putpixel((0, 0), (1, 2, 3))
        self.image.putpixel((159, 99), (4, 5, 6))
        crop = auto_chest.crop_window(self.image, (0, 0), self.window)
        self.assertIsNotNone(crop)
        self.assertEqual(crop.size, (176, 116))
        self.assertEqual(crop.getpixel((8, 8)), (1, 2, 3))
        self.assertEqual(crop.getpixel((167, 107)), (4, 5, 6))
        self.assertEqual(crop.getpixel((7, 7)), (0, 0, 0))
        self.assertEqual(crop.getpixel((168, 108)), (0, 0, 0))

    def test_completely_offscreen_windows_have_no_crop(self):
        for rect in ((-40, 10, 0, 50), (160, 10, 200, 50),
                     (10, -40, 50, 0), (10, 100, 50, 140)):
            with self.subTest(rect=rect):
                window = WindowInfo(51, 72, "DesktopCatInput", rect)
                self.assertIsNone(auto_chest.crop_window(self.image, (0, 0), window))

    def test_invalid_or_excessive_window_dimensions_are_rejected(self):
        for rect in ((0, 0, 0, 100), (0, 0, 160, -1),
                     (-8, -8, 281, 108), (-8, -8, 168, 221)):
            with self.subTest(rect=rect):
                window = WindowInfo(51, 72, "DesktopCatInput", rect)
                self.assertIsNone(auto_chest.crop_window(self.image, (0, 0), window))

    def test_negative_monitor_origin_preserves_the_same_local_pixel_alignment(self):
        window = WindowInfo(51, 72, "DesktopCatInput", (-168, -108, 8, 8))
        crop = auto_chest.crop_window(self.image, (-160, -100), window)
        self.assertEqual(crop.size, (176, 116))
        self.assertEqual(crop.getpixel((8, 8)), self.image.getpixel((0, 0)))
        self.assertEqual(crop.getpixel((7, 7)), (0, 0, 0))

    def test_match_box_may_touch_real_capture_edges(self):
        for x, y in ((8, 8), (153, 96)):
            with self.subTest(position=(x, y)):
                match = Match(x, y, 15, 12, 1.)
                self.assertTrue(auto_chest.match_is_captured(match, self.window, (0, 0), self.image))

    def test_one_uncaptured_pixel_invalidates_match_despite_visible_center(self):
        for x, y in ((7, 30), (154, 30), (30, 7), (30, 97)):
            with self.subTest(position=(x, y)):
                match = Match(x, y, 15, 12, 1.)
                self.assertFalse(auto_chest.match_is_captured(match, self.window, (0, 0), self.image))

    def test_match_box_check_uses_physical_coordinates_with_negative_origin(self):
        window = WindowInfo(51, 72, "DesktopCatInput", (-168, -108, 8, 8))
        self.assertTrue(auto_chest.match_is_captured(Match(8, 8, 15, 12, 1.), window, (-160, -100), self.image))
        self.assertFalse(auto_chest.match_is_captured(Match(7, 8, 15, 12, 1.), window, (-160, -100), self.image))


class WatcherCaptureGeometryTests(unittest.TestCase):
    def setUp(self):
        self.stack = ExitStack()
        self.addCleanup(self.stack.close)
        self.now = 100.
        self.window = WindowInfo(52, 72, "DesktopCatInput", (-8, -8, 168, 108))
        self.render = WindowInfo(51, 72, "Wuwa Tappo", self.window.rect)
        self.origin = (0, 0)
        self.frame = self.make_frame((20, 20))
        self.capture_hook = None
        self.sent = []
        self.watcher = auto_chest.Watcher(queue.Queue())
        self.watcher.mode = "borderless"
        self.watcher.templates = load_builtin_templates()
        self.watcher.active.set()
        self.stack.enter_context(patch.object(auto_chest.time, "monotonic", side_effect=lambda: self.now))
        self.stack.enter_context(patch.object(auto_chest.desktop, "is_desktop_available", return_value=True))
        self.stack.enter_context(patch.object(auto_chest.desktop, "find_pet_windows", side_effect=lambda: [self.window]))
        self.stack.enter_context(patch.object(auto_chest.desktop, "input_privilege_problem", return_value=None))
        self.stack.enter_context(patch.object(auto_chest.desktop, "window_is_current", return_value=True))
        self.stack.enter_context(patch.object(auto_chest.desktop, "point_hits_window", return_value=True))
        self.stack.enter_context(patch.object(auto_chest.desktop, "can_observe_disappearance", return_value=True))
        self.stack.enter_context(patch.object(auto_chest.desktop, "user_idle_seconds", return_value=10.))
        self.stack.enter_context(patch.object(auto_chest.bg, "find_background_pairs", side_effect=lambda _: [(self.render, self.window)]))
        self.capture = self.stack.enter_context(patch.object(auto_chest.desktop, "capture_desktop", side_effect=self.capture_frame))
        self.click = self.stack.enter_context(patch.object(auto_chest.bg, "background_click", side_effect=self.record_click))
        self.physical = self.stack.enter_context(patch.object(auto_chest.desktop, "safe_click", side_effect=self.record_click))
        self.native_capture = self.stack.enter_context(patch.object(auto_chest.bg, "BackgroundCapture", side_effect=AssertionError("Unexpected native capture")))

    def make_frame(self, *positions):
        image = Image.new("RGB", (160, 100), (30, 45, 60))
        template = load_builtin_templates()[0][1]
        for position in positions:
            image.paste(template, position)
        return image

    def capture_frame(self, windows):
        if self.capture_hook:
            self.capture_hook(self.capture.call_count)
        return self.frame.copy(), self.origin

    def record_click(self, x, y, window, should_cancel=None, before_press=None):
        self.assertEqual(window, self.window)
        self.assertIsNotNone(should_cancel)
        self.assertFalse(should_cancel())
        if before_press is not None:
            self.assertTrue(before_press())
        self.sent.append((x, y))
        return True

    def tick(self, count=1):
        for _ in range(count):
            self.now += 1.
            self.watcher.tick()

    def initial_click(self):
        self.tick()
        self.assertEqual(self.sent, [])
        self.tick()
        self.assertEqual(self.sent, [(27, 26)])
        self.assertEqual(self.watcher.confirmed, 0)

    def test_visible_icon_in_window_with_offscreen_frame_margins_is_clicked(self):
        self.initial_click()
        self.assertEqual(self.watcher.pending[0].lock.box, (28, 28, 15, 12))
        self.physical.assert_not_called()
        self.native_capture.assert_not_called()

    def test_visible_compatibility_mode_keeps_the_same_aligned_screen_coordinates(self):
        self.watcher.mode = "visible"
        self.initial_click()
        self.physical.assert_called_once()
        self.click.assert_not_called()

    def test_negative_screen_origin_is_not_applied_twice(self):
        self.origin = (-160, -100)
        self.window = WindowInfo(52, 72, "DesktopCatInput", (-168, -108, 8, 8))
        self.render = WindowInfo(51, 72, "Wuwa Tappo", self.window.rect)
        self.tick(count=2)
        self.assertEqual(self.sent, [(-133, -74)])

    def test_partially_offscreen_real_icon_is_not_clicked(self):
        self.frame = self.make_frame((-6, 22))
        self.tick(count=3)
        self.assertEqual(self.sent, [])

    def test_monitor_gap_cannot_confirm_or_retry_pending_claim(self):
        from chest_displays import Monitor
        self.initial_click()
        self.frame = self.make_frame()
        self.frame.info['monitor_layout'] = (Monitor('left',(0,0,24,100),True,96), Monitor('right',(28,0,160,100),False,144))
        self.tick(count=3)
        self.assertEqual(self.sent, [(27, 26)])
        self.assertEqual(self.watcher.confirmed, 0)
        self.assertEqual(self.watcher.pending[0].lock.missing_frames, 0)

    def test_changing_monitors_reobserves_resized_slot_without_pausing(self):
        from chest_displays import Monitor
        layout = (Monitor('left',(-200,0,0,100),False,144), Monitor('primary',(0,0,160,100),True,96))
        self.frame.info['monitor_layout'] = layout
        self.initial_click()
        self.origin = (-200, 0)
        self.window = WindowInfo(52,72,'DesktopCatInput',(-208,-8,-32,108))
        self.render = WindowInfo(51,72,'Wuwa Tappo', self.window.rect)
        self.frame = self.make_frame()
        from chest_artwork import render_ready_interior
        self.frame.paste(render_ready_interior(32,25), (80,50))
        self.frame.info['monitor_layout'] = layout
        self.tick()
        self.assertEqual(self.sent, [(27,26)])
        self.tick()
        self.assertEqual(len(self.sent), 2)
        self.assertTrue(-110 <= self.sent[-1][0] <= -100)
        self.assertTrue(self.watcher.active.is_set())
        self.assertEqual(self.watcher.confirmed, 0)

    def test_high_scoring_match_in_padding_cannot_be_clicked(self):
        # Its center is visible at screen x=1, so center hit testing alone
        # cannot establish that all of its template pixels were captured.
        match = Match(2, 30, 15, 12, 1.)
        with patch.object(auto_chest, "detect_templates", return_value=[(1, match)]):
            self.tick(count=3)
        self.assertEqual(self.sent, [])
        self.click.assert_not_called()
        self.assertEqual(self.watcher.pending, [])

    def test_fresh_verification_rejects_match_when_capture_origin_excludes_its_edge(self):
        def move_capture_at_verification(number):
            if number == 3:
                self.origin = (25, 0)
        def high_scoring_match(image, *_):
            if image.size == (176, 116):
                return [(1, Match(28, 28, 15, 12, 1.))]
            return [(1, Match(6, 6, 15, 12, 1.))]
        self.capture_hook = move_capture_at_verification
        with patch.object(auto_chest, "detect_templates", side_effect=high_scoring_match):
            self.tick(count=2)
        self.assertEqual(self.capture.call_count, 3)
        self.click.assert_not_called()
        self.assertEqual(self.sent, [])

    def test_pending_button_moving_outside_capture_cannot_confirm_disappearance(self):
        self.initial_click()
        self.frame = self.make_frame()
        self.origin = (25, 0)
        self.tick(count=3)
        self.assertEqual(self.watcher.confirmed, 0)
        self.assertEqual(self.watcher.pending[0].lock.missing_frames, 0)
        self.assertEqual(self.sent, [(27, 26)])

    def test_pending_confirmation_needs_entire_roi_even_if_old_button_box_fits(self):
        self.initial_click()
        self.frame = self.make_frame()
        self.origin = (18, 0)
        lock = self.watcher.pending[0].lock
        self.assertTrue(auto_chest.match_is_captured(Match(*lock.box, 1.), self.window, self.origin, self.frame))
        self.tick(count=3)
        self.assertEqual(self.watcher.confirmed, 0)
        self.assertEqual(lock.missing_frames, 0)
        self.assertIsNone(self.watcher.pending[0].visible_since)

    def test_fully_captured_disappearance_still_confirms_after_two_observations(self):
        self.initial_click()
        self.frame = self.make_frame()
        self.tick()
        self.assertEqual(self.watcher.confirmed, 0)
        self.tick()
        self.assertEqual(self.watcher.confirmed, 1)
        self.assertEqual(self.watcher.pending, [])

    def test_scan_once_reports_correct_coordinates_for_partially_offscreen_window(self):
        results = auto_chest.scan_once(self.watcher.templates, .94)
        self.assertEqual([(result["x"], result["y"]) for result in results], [(27, 26)])
        self.assertEqual(self.sent, [])

    def test_scan_once_filters_high_scoring_matches_from_padding(self):
        with patch.object(auto_chest, "detect_templates", return_value=[(1, Match(2, 30, 15, 12, 1.))]):
            results = auto_chest.scan_once(self.watcher.templates, .94)
        self.assertEqual(results, [])
        self.assertEqual(self.sent, [])


if __name__ == "__main__":
    unittest.main()
