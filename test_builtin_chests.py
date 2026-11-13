"""Offline tests against supplied chest UI, without desktop input or screenshots."""
from pathlib import Path
from contextlib import ExitStack
import queue
import tempfile
import unittest
from unittest.mock import patch

import numpy as np
from PIL import Image, ImageDraw

import auto_chest
from chest_assets import load_builtin_templates
from chest_detection import detect_chests
from chest_windows import WindowInfo
from fixture_assets import load_fixture as fixture


def centers(detections):
    return sorted(match.center for _, match in detections)


class BuiltinChestTests(unittest.TestCase):
    def assert_original_chests(self, image):
        detections = detect_chests(image)
        self.assertEqual(centers(detections), [(113, 76), (119, 49)])
        for _, match in detections:
            self.assertGreaterEqual(match.score, .94)

    def test_supplied_no_chest_image_has_no_detection(self):
        self.assertEqual(detect_chests(fixture("pet_no_chest.png")), [])

    def test_supplied_ready_image_detects_both_buttons(self):
        self.assert_original_chests(fixture("pet_with_chests.png"))

    def test_supplied_standalone_button_is_recognized_once(self):
        detections = detect_chests(fixture("chest_button.png"))
        self.assertEqual(centers(detections), [(16, 11)])

    def test_pet_artwork_is_not_part_of_the_recognition_template(self):
        original = fixture("pet_with_chests.png")
        replacements = [
            Image.new("RGB", (98, original.height), "black"),
            Image.new("RGB", (98, original.height), "magenta"),
            Image.fromarray(np.random.default_rng(709).integers(
                0, 256, (original.height, 98, 3), dtype=np.uint8)),
        ]
        for replacement in replacements:
            image = original.copy()
            image.paste(replacement, (0, 0))
            self.assert_original_chests(image)

    def test_button_interiors_work_on_different_desktop_backgrounds(self):
        templates = load_builtin_templates()
        expected = [(37, 26), (108, 76)]
        for background in ((255, 255, 255), (12, 12, 12), (30, 120, 180)):
            with self.subTest(background=background):
                image = Image.new("RGB", (150, 110), background)
                image.paste(templates[0][1], (30, 20))
                image.paste(templates[1][1], (100, 70))
                self.assertEqual(centers(detect_chests(image)), expected)

    def test_same_artwork_at_two_positions_detects_two_buttons(self):
        for variant, template in load_builtin_templates():
            with self.subTest(variant=variant):
                image = Image.new("RGB", (150, 100), (18, 27, 36))
                image.paste(template, (20, 10))
                image.paste(template, (95, 62))
                expected = sorted([(20+template.width//2, 10+template.height//2),
                                   (95+template.width//2, 62+template.height//2)])
                self.assertEqual(centers(detect_chests(image)), expected)

    def test_visual_variants_are_not_bound_to_upper_and_lower_positions(self):
        first, second = [template for _, template in load_builtin_templates()[:2]]
        image = Image.new("RGB", (150, 100), (18, 27, 36))
        image.paste(second, (90, 10))
        image.paste(first, (90, 60))
        self.assertEqual(centers(detect_chests(image)), [(97, 66), (98, 16)])

    @staticmethod
    def replace_with_countdown(image, upper=False, lower=False):
        image = image.copy()
        draw = ImageDraw.Draw(image)
        if upper:
            draw.rectangle((104, 38, 140, 61), fill=(255, 226, 179))
            draw.text((106, 43), "29:59", fill=(140, 100, 62))
        if lower:
            draw.rectangle((98, 64, 132, 87), fill=(255, 226, 179))
            draw.text((99, 70), "17:42", fill=(140, 100, 62))
        return image

    def test_upper_countdown_leaves_lower_button_ready(self):
        image = self.replace_with_countdown(fixture("pet_with_chests.png"), upper=True)
        self.assertEqual(centers(detect_chests(image)), [(113, 76)])

    def test_lower_countdown_leaves_upper_button_ready(self):
        image = self.replace_with_countdown(fixture("pet_with_chests.png"), lower=True)
        self.assertEqual(centers(detect_chests(image)), [(119, 49)])

    def test_both_countdowns_are_not_ready_buttons(self):
        image = self.replace_with_countdown(fixture("pet_with_chests.png"), upper=True, lower=True)
        self.assertEqual(detect_chests(image), [])

    def test_loading_requires_no_user_template_directory(self):
        expected = load_builtin_templates()
        with tempfile.TemporaryDirectory() as directory:
            with patch.object(auto_chest, "BASE", Path(directory)):
                loaded = auto_chest.load_templates()
        self.assertEqual([i for i, _ in loaded], [i for i, _ in expected])
        for (_, actual), (_, reference) in zip(loaded, expected):
            np.testing.assert_array_equal(np.asarray(actual), np.asarray(reference))

    def test_old_custom_templates_cannot_replace_builtin_recognition(self):
        expected = load_builtin_templates()
        with tempfile.TemporaryDirectory() as directory:
            old_templates = Path(directory) / "templates"
            old_templates.mkdir()
            Image.new("RGB", (20, 20), "red").save(old_templates / "chest_1.png")
            Image.new("RGB", (20, 20), "blue").save(old_templates / "chest_2.png")
            with patch.object(auto_chest, "BASE", Path(directory)):
                loaded = auto_chest.load_templates()
        self.assertEqual(len(loaded), len(expected))
        for (_, actual), (_, reference) in zip(loaded, expected):
            np.testing.assert_array_equal(np.asarray(actual), np.asarray(reference))


class BuiltinWatcherTests(unittest.TestCase):
    """Exercise the actual watcher and image detector; every OS action is mocked."""

    def setUp(self):
        self.stack = ExitStack()
        self.addCleanup(self.stack.close)
        self.frame = fixture("pet_with_chests.png")
        self.window = WindowInfo(501, 88, "DesktopCatInput", (0, 0, *self.frame.size))
        self.now = 100.
        self.click_events = []
        self.watcher = auto_chest.Watcher(queue.Queue())
        self.watcher.templates = auto_chest.load_templates()
        self.watcher.active.set()
        self.stack.enter_context(patch.object(auto_chest.desktop, "is_desktop_available", return_value=True))
        self.stack.enter_context(patch.object(auto_chest.desktop, "find_pet_windows", side_effect=lambda: [self.window]))
        self.stack.enter_context(patch.object(auto_chest.desktop, "capture_desktop", side_effect=lambda: (self.frame.copy(), (0, 0))))
        self.stack.enter_context(patch.object(auto_chest.desktop, "point_hits_window", return_value=True))
        self.stack.enter_context(patch.object(auto_chest.desktop, "can_observe_disappearance", return_value=True))
        self.stack.enter_context(patch.object(auto_chest.desktop, "window_is_current", return_value=True))
        self.stack.enter_context(patch.object(auto_chest.desktop, "user_idle_seconds", return_value=10.))
        self.stack.enter_context(patch.object(auto_chest.time, "monotonic", side_effect=lambda: self.now))

        def record_click(x, y, window, should_cancel=None):
            self.assertEqual(window, self.window)
            self.assertFalse(should_cancel())
            self.click_events.append((self.now, x, y))
            return True

        self.stack.enter_context(patch.object(auto_chest.desktop, "safe_click", side_effect=record_click))

    def tick(self, count=1, delta=.5):
        for _ in range(count):
            self.now += delta
            self.watcher.tick()

    def clicked_positions(self):
        return [(x, y) for _, x, y in self.click_events]

    def test_two_ready_buttons_are_clicked_independently_after_stable_frames(self):
        self.tick()
        self.assertEqual(self.click_events, [])
        self.tick()
        self.assertEqual(self.clicked_positions(), [(119, 49)])
        self.tick()
        self.assertEqual(self.clicked_positions(), [(119, 49), (113, 76)])
        # The second physical button has no shared five-second cooldown.
        self.assertLess(self.click_events[1][0]-self.click_events[0][0], 5.)
        self.frame = BuiltinChestTests.replace_with_countdown(self.frame, upper=True, lower=True)
        self.tick(12, delta=1.)
        self.assertEqual(len(self.click_events), 2)
        self.assertEqual(self.watcher.confirmed, 2)

    def test_upper_countdown_does_not_prevent_lower_claim(self):
        self.tick(2)
        self.frame = BuiltinChestTests.replace_with_countdown(self.frame, upper=True)
        self.tick()
        self.assertEqual(self.clicked_positions(), [(119, 49), (113, 76)])
        self.tick(3)
        self.assertEqual(len(self.click_events), 2)

    def test_separate_refreshes_are_claimed_without_a_shared_timer(self):
        self.tick(3)
        original = fixture("pet_with_chests.png")
        self.frame = BuiltinChestTests.replace_with_countdown(original, upper=True, lower=True)
        self.tick(2)
        self.assertEqual(len(self.click_events), 2)

        self.now += 1800.
        self.frame = BuiltinChestTests.replace_with_countdown(original, lower=True)
        self.tick(2)
        self.assertEqual(self.clicked_positions()[-1], (119, 49))
        self.assertEqual(len(self.click_events), 3)

        # The other treasure refreshes at a different time, while the first is
        # back in countdown. It must not inherit the first one's cooldown.
        self.frame = BuiltinChestTests.replace_with_countdown(original, upper=True)
        self.tick(2)
        self.assertEqual(self.clicked_positions(), [(119, 49), (113, 76), (119, 49), (113, 76)])
        self.assertLess(self.click_events[3][0]-self.click_events[2][0], 5.)

    def test_same_visual_variant_at_two_locations_has_independent_claims(self):
        template = load_builtin_templates()[0][1]
        self.frame = Image.new("RGB", self.frame.size, (18, 27, 36))
        self.frame.paste(template, (112, 43))
        self.frame.paste(template, (105, 70))
        self.tick(3)
        self.assertEqual(self.clicked_positions(), [(119, 49), (112, 76)])
        self.assertLess(self.click_events[1][0]-self.click_events[0][0], 5.)

    def test_visual_variant_change_at_same_location_does_not_bypass_click_lock(self):
        first, second = [template for _, template in load_builtin_templates()[:2]]
        self.frame = Image.new("RGB", self.frame.size, (18, 27, 36))
        self.frame.paste(first, (50, 30))
        self.tick(2)
        self.assertEqual(self.clicked_positions(), [(57, 36)])
        self.frame = Image.new("RGB", self.frame.size, (18, 27, 36))
        self.frame.paste(second, (49, 30))  # Keep the same click center.
        # A changing visual variant must not allow an immediate duplicate.
        # If it stays ready for >=4s, the result-confirmation layer may retry.
        self.tick(3, delta=1.)
        self.assertEqual(self.clicked_positions(), [(57, 36)])


if __name__ == "__main__":
    unittest.main(verbosity=2)
