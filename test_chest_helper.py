"""Offline safety/regression checks. No real screenshot or mouse input is used."""

from contextlib import ExitStack
import queue
import unittest
from unittest.mock import MagicMock, patch

import numpy as np
from PIL import Image

import auto_chest
from chest_matcher import find_match, validate_template
from chest_state import ClickLedger, DetectionGate
from chest_windows import WindowInfo


def patterned_template(seed=77):
    rng = np.random.default_rng(seed)
    return Image.fromarray(rng.integers(40, 170, (12, 16, 3), dtype=np.uint8))


class MatcherTests(unittest.TestCase):
    def setUp(self):
        self.template = patterned_template()

    def test_exact_position_and_center(self):
        screen = Image.new("RGB", (96, 72), (20, 30, 40))
        screen.paste(self.template, (53, 31))
        found = find_match(screen, self.template)
        self.assertIsNotNone(found)
        self.assertEqual((found.x, found.y, found.width, found.height), (53, 31, 16, 12))
        self.assertEqual(found.center, (61, 37))
        self.assertGreater(found.score, .999999)

    def test_wrong_colours_do_not_pass_acceptance_threshold(self):
        pixels = np.asarray(self.template, dtype=np.int16)
        wrong = (pixels + np.array([70, -35, 55])).clip(0, 255).astype(np.uint8)
        screen = Image.new("RGB", (96, 72), (20, 30, 40))
        screen.paste(Image.fromarray(wrong), (53, 31))
        found = find_match(screen, self.template)
        self.assertTrue(found is None or found.score < .90)

    def test_flat_screen_does_not_match(self):
        self.assertIsNone(find_match(Image.new("RGB", (96, 72), "gray"), self.template))

    def test_solid_templates_are_rejected(self):
        for colour in ("gray", "red", "black"):
            with self.subTest(colour=colour), self.assertRaises(ValueError):
                validate_template(Image.new("RGB", (20, 16), colour))

    def test_transparent_template_rejected_but_opaque_alpha_accepted(self):
        alpha = self.template.convert("RGBA")
        validate_template(alpha)
        alpha.putpixel((1, 1), (20, 30, 40, 254))
        with self.assertRaises(ValueError):
            validate_template(alpha)

    def test_smaller_search_region_returns_none(self):
        self.assertIsNone(find_match(Image.new("RGB", (10, 10)), self.template))


class DetectionGateTests(unittest.TestCase):
    def test_two_stable_frames_required_and_large_move_resets_evidence(self):
        gate = DetectionGate()
        key = (1, 2, 3)
        self.assertFalse(gate.observe(key, (20, 20), 10)[0])
        self.assertTrue(gate.observe(key, (21, 20), 12)[0])
        self.assertFalse(gate.observe(key, (50, 20), 14)[0])
        self.assertTrue(gate.observe(key, (50, 20), 16)[0])

    def test_pause_reset_requires_new_pair_of_frames(self):
        gate = DetectionGate()
        key = (1, 2, 3)
        gate.observe(key, (20, 20), 10)
        gate.reset_observations()
        self.assertFalse(gate.observe(key, (20, 20), 20)[0])
        self.assertTrue(gate.observe(key, (20, 20), 22)[0])

    def test_cooldown_is_retained_when_stability_is_reset(self):
        gate = DetectionGate()
        key = (1, 2, 3)
        gate.clicked(key, (20, 20), 10)
        gate.reset_observations()
        self.assertFalse(gate.observe(key, (60, 20), 11)[0])
        self.assertFalse(gate.observe(key, (60, 20), 12)[0])
        self.assertTrue(gate.observe(key, (60, 20), 15)[0])


class ClickLedgerTests(unittest.TestCase):
    def test_overlapping_regions_share_a_lock_across_templates(self):
        ledger = ClickLedger()
        ledger.clicked((2, 3), (10, 10, 20, 20), patterned_template(1))
        self.assertTrue(ledger.blocked((2, 3), (11, 11, 18, 18)))
        ledger.clicked((2, 3), (11, 11, 18, 18), patterned_template(2))
        self.assertEqual(len(ledger.locks), 1)
        self.assertFalse(ledger.blocked((9, 3), (10, 10, 20, 20)))

    def test_a_b_a_keeps_both_original_locations_locked(self):
        ledger = ClickLedger()
        a, b = (10, 10, 20, 20), (60, 10, 20, 20)
        ledger.clicked((2, 3), a, patterned_template())
        ledger.clicked((2, 3), b, patterned_template())
        self.assertTrue(ledger.blocked((2, 3), a))
        self.assertTrue(ledger.blocked((2, 3), b))
        self.assertEqual(len(ledger.locks), 2)

    def test_occlusion_never_counts_as_disappearance(self):
        ledger = ClickLedger()
        box = (10, 10, 20, 20)
        ledger.clicked((2, 3), box, patterned_template())
        lock = ledger.locks[0]
        for _ in range(5):
            self.assertFalse(ledger.observe(lock, None))
        self.assertTrue(ledger.blocked((2, 3), box))

    def test_two_known_missing_frames_rearm_and_unknown_breaks_sequence(self):
        ledger = ClickLedger()
        box = (10, 10, 20, 20)
        ledger.clicked((2, 3), box, patterned_template())
        lock = ledger.locks[0]
        self.assertFalse(ledger.observe(lock, False))
        self.assertFalse(ledger.observe(lock, None))
        self.assertFalse(ledger.observe(lock, False))
        self.assertTrue(ledger.blocked((2, 3), box))
        self.assertTrue(ledger.observe(lock, False))
        self.assertFalse(ledger.blocked((2, 3), box))


class WatcherTests(unittest.TestCase):
    """Real small-image recognition with every Windows operation mocked."""

    def setUp(self):
        self.stack = ExitStack()
        self.addCleanup(self.stack.close)
        self.template = patterned_template()
        self.origin = (0, 0)
        self.rect = (0, 0, 110, 80)
        self.base = WindowInfo(1, 33, "DesktopCatInput", self.rect)
        self.popup = WindowInfo(2, 33, "Toast", self.rect)
        self.windows = [self.base, self.popup]
        self.owner = 2
        self.now = 100.
        self.idle = 10.
        self.frame = self.make_frame((20, 30))
        self.watcher = auto_chest.Watcher(queue.Queue())
        self.watcher.templates = [(1, self.template)]
        self.watcher.active.set()

        self.stack.enter_context(patch.object(auto_chest.desktop, "is_desktop_available", return_value=True))
        self.stack.enter_context(patch.object(auto_chest.desktop, "find_pet_windows", side_effect=lambda: list(self.windows)))
        self.capture = self.stack.enter_context(patch.object(
            auto_chest.desktop, "capture_desktop", side_effect=lambda windows: (self.frame.copy(), self.origin)))
        self.stack.enter_context(patch.object(
            auto_chest.desktop, "point_hits_window", side_effect=lambda x, y, w: w.hwnd == self.owner))
        self.stack.enter_context(patch.object(
            auto_chest.desktop, "can_observe_disappearance", side_effect=lambda rect, w: w.hwnd == self.owner))
        self.stack.enter_context(patch.object(
            auto_chest.desktop, "window_is_current", side_effect=lambda w: w in self.windows))
        self.stack.enter_context(patch.object(
            auto_chest.desktop, "user_idle_seconds", side_effect=lambda: self.idle))
        self.safe_click = self.stack.enter_context(patch.object(auto_chest.desktop, "safe_click", return_value=True))
        self.stack.enter_context(patch.object(auto_chest.time, "monotonic", side_effect=lambda: self.now))

    def make_frame(self, *positions):
        frame = Image.new("RGB", (110, 80), (20, 30, 40))
        for position in positions:
            x, y = position[:2]
            brightness = position[2] if len(position) > 2 else 0
            template = self.template
            if brightness:
                pixels = (np.asarray(template, dtype=np.int16) + brightness).clip(0, 255).astype(np.uint8)
                template = Image.fromarray(pixels)
            frame.paste(template, (x, y))
        return frame

    def tick(self, count=1):
        for _ in range(count):
            # These observation tests run inside the confirmation retry wait.
            # Bounded retries have their own explicit clock-driven tests.
            self.now += .5
            self.watcher.tick()

    def test_same_rect_windows_click_only_actual_owner_and_reuse_match(self):
        with patch.object(auto_chest, "detect_templates", wraps=auto_chest.detect_templates) as matcher:
            self.tick(2)
        self.safe_click.assert_called_once()
        self.assertEqual(self.safe_click.call_args.args, (28, 36, self.popup))
        self.assertTrue(callable(self.safe_click.call_args.kwargs["should_cancel"]))
        # One match in the first frame, one in the second, one fresh verification.
        self.assertEqual(matcher.call_count, 3)

    def test_duplicate_templates_do_not_click_same_button_twice(self):
        self.watcher.templates = [(1, self.template), (2, self.template.copy())]
        self.tick(7)
        self.assertEqual(self.safe_click.call_count, 1)

    def test_hidden_popup_reappears_and_can_be_clicked_again(self):
        self.tick(2)
        self.assertEqual(self.safe_click.call_count, 1)
        self.windows = [self.base]
        self.owner = 1
        self.frame = self.make_frame()
        self.tick(2)
        self.windows = [self.base, self.popup]
        self.owner = 2
        self.frame = self.make_frame((20, 30))
        self.tick()
        self.assertEqual(self.safe_click.call_count, 1)
        self.tick()
        self.assertEqual(self.safe_click.call_count, 1)
        # Hiding a window cannot confirm a claim; the same pending target is
        # retried only after it is known visible for the full retry interval.
        self.now += 4.
        self.tick()
        self.assertEqual(self.safe_click.call_count, 2)

    def test_locked_a_rearms_after_local_absence_while_b_remains_visible(self):
        self.frame = self.make_frame((60, 40, 5))
        self.tick(2)  # B first.
        self.frame = self.make_frame((10, 10), (60, 40, 5))
        self.tick(2)  # A is now the better match.
        self.assertEqual(self.safe_click.call_count, 2)
        self.frame = self.make_frame((60, 40, 5))
        self.tick(2)  # A is locally known to have disappeared; B stays locked.
        self.frame = self.make_frame((10, 10), (60, 40, 5))
        self.tick(2)
        self.assertEqual(self.safe_click.call_count, 3)
        self.assertEqual(self.safe_click.call_args.args[:2], (18, 16))

    def test_pausing_during_fresh_capture_prevents_click(self):
        self.tick()
        count = 0

        def capture(windows):
            nonlocal count
            count += 1
            if count == 2:
                self.watcher.pause()
            return self.frame.copy(), self.origin

        self.capture.side_effect = capture
        self.tick()
        self.safe_click.assert_not_called()

    def test_busy_user_prevents_click(self):
        self.idle = 0.
        self.tick(3)
        self.safe_click.assert_not_called()
        self.idle = 10.
        self.tick()
        self.safe_click.assert_called_once()

    def test_new_user_input_during_verification_prevents_click(self):
        self.tick()
        with patch.object(auto_chest.desktop, "user_idle_seconds", side_effect=[10., 0.]):
            self.tick()
        self.safe_click.assert_not_called()

    def test_locked_desktop_does_not_capture_or_click(self):
        with patch.object(auto_chest.desktop, "is_desktop_available", return_value=False):
            self.tick(2)
        self.capture.assert_not_called()
        self.safe_click.assert_not_called()

    def test_empty_search_backs_off_and_new_icon_restores_fast_confirmation(self):
        self.frame = self.make_frame()
        self.tick(8)
        self.assertEqual(self.watcher.next_interval(), 10.)
        self.safe_click.assert_not_called()
        self.frame = self.make_frame((20, 30))
        self.tick()
        self.assertEqual(self.watcher.next_interval(), self.watcher.interval)
        self.safe_click.assert_not_called()  # Still requires two new frames.
        self.tick()
        self.safe_click.assert_called_once()

    def test_pending_claim_keeps_fast_checks_through_missing_frames(self):
        self.tick(2)
        self.frame = self.make_frame()
        # An occluded pending claim must stay unconfirmed even after backoff.
        self.owner = 0
        self.tick(8)
        self.assertTrue(self.watcher.pending)
        self.assertEqual(self.watcher.next_interval(), self.watcher.interval)
        self.assertEqual(self.watcher.confirmed, 0)

    def test_screen_unavailable_wait_is_interruptible_and_recovers_to_fast_scan(self):
        with patch.object(auto_chest.desktop, "is_desktop_available", return_value=False):
            self.tick()
        self.assertEqual(self.watcher.next_interval(), 15.)
        self.capture.assert_not_called()
        self.tick()
        self.assertEqual(self.watcher.next_interval(), self.watcher.interval)
        self.watcher.close()
        self.assertTrue(self.watcher.stop_event.wait(self.watcher.next_interval()))

    def test_negative_coordinate_crop_and_click(self):
        self.origin = (-110, -80)
        self.rect = (-110, -80, 0, 0)
        self.popup = WindowInfo(2, 33, "Toast", self.rect)
        self.windows = [self.popup]
        crop = auto_chest.crop_window(self.frame, self.origin, self.popup)
        self.assertEqual(crop.size, self.frame.size)
        self.assertEqual(crop.getpixel((20, 30)), self.template.getpixel((0, 0)))
        self.tick(2)
        self.safe_click.assert_called_once()
        self.assertEqual(self.safe_click.call_args.args, (-82, -44, self.popup))
        self.assertTrue(callable(self.safe_click.call_args.kwargs["should_cancel"]))

    def test_partly_offscreen_crop_preserves_screen_alignment(self):
        offscreen = WindowInfo(2, 33, "Toast", (-5, 0, 105, 80))
        crop = auto_chest.crop_window(self.frame, (0, 0), offscreen)
        self.assertEqual(crop.size, (110, 80))
        self.assertEqual(crop.getpixel((5, 0)), self.frame.getpixel((0, 0)))


if __name__ == "__main__":
    unittest.main(verbosity=2)
