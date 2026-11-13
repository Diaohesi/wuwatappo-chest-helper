"""Structural performance regressions; no timing limits, capture, or input.

The false-colour scene targets the proposal filter's expensive negative case.
Real screenshot holdouts check that rejecting those proposals does not stop
the scan before later ready buttons. The envelope tests independently compute
the original RGB/NCC score; they test the prefilter, not renderer accuracy.
"""
from __future__ import annotations

import math
import unittest
from unittest.mock import patch

import numpy as np
from PIL import Image, ImageDraw

from chest_artwork import render_ready_interior
from chest_assets import load_builtin_templates
from chest_detection import detect_chests, detect_templates
import chest_matcher
import chest_scaling
from fixture_assets import load_fixture


def brown_gold_decoys(size=(320, 200)):
    """Sixty plausible colour components, with none of the chest structure."""
    image = Image.new("RGB", size, (254, 210, 145))
    draw = ImageDraw.Draw(image)
    for top in range(5, 180, 30):
        for left in range(5, 300, 30):
            draw.rectangle((left, top, left+13, top+9), fill=(132, 87, 62))
            draw.rectangle((left+5, top+3, left+7, top+6), fill=(254, 210, 145))
    return image


def independent_rgb_score(actual, reference):
    """Direct score at one aligned patch, without a matcher or prefilter."""
    a = np.asarray(actual, dtype=np.float64)
    b = np.asarray(reference, dtype=np.float64)
    ac = a-a.mean(axis=(0, 1), keepdims=True)
    bc = b-b.mean(axis=(0, 1), keepdims=True)
    ncc = float(np.sum(ac*bc)/math.sqrt(float(np.sum(ac*ac)*np.sum(bc*bc))))
    mse = float(np.mean((a-b)**2))
    return max(0., min(1., ncc))*math.exp(-mse/(2*45.**2))


class ScaledPerformanceTests(unittest.TestCase):
    def test_sixty_colour_decoys_skip_most_native_phase_searches(self):
        image = brown_gold_decoys()
        self.assertEqual(len(chest_scaling.colour_regions(image)), 60)
        templates = load_builtin_templates()
        fixed_ids = {id(template) for _, template in templates}
        original_search = chest_scaling.find_matches_many
        original_envelope = chest_scaling._envelope_allows
        rejections = []
        native_searches = []

        def inspect_search(frame, references, *args, **kwargs):
            # A necessary-condition prefilter may allow a few difficult
            # negatives. Count costly native checks rather than requiring
            # every negative to be rejected by an approximate structure.
            if references and not all(id(reference) in fixed_ids for reference in references):
                native_searches.append(len(references))
            return original_search(frame, references, *args, **kwargs)

        def inspect_envelope(*args, **kwargs):
            allowed = original_envelope(*args, **kwargs)
            rejections.append(not allowed)
            return allowed

        with patch.object(chest_scaling, "find_matches_many", side_effect=inspect_search), \
                patch.object(chest_scaling, "_envelope_allows", side_effect=inspect_envelope):
            self.assertEqual(detect_templates(image, templates, .96), [])
        self.assertTrue(rejections, "The negative case did not exercise the prefilter")
        self.assertGreaterEqual(sum(rejections)/len(rejections), .75,
                                "Too many false sizes reached native phase matching")
        # This hard scene used to score 16 size/height banks (512 native
        # references) per component. Keep at least a fourfold reduction in
        # expensive RGB/NCC work without imposing a machine timing limit.
        self.assertLessEqual(sum(native_searches), 60*128)

    def test_real_chests_after_sixty_decoys_are_still_found(self):
        screenshot = load_fixture("native_ready_chests.png")
        offset = (0, 200)
        image = brown_gold_decoys((max(320, screenshot.width), 200+screenshot.height))
        image.paste(screenshot, offset)
        regions = chest_scaling.colour_regions(image)
        self.assertGreater(len(regions), 60)
        self.assertTrue(all(top < offset[1] for _, top, _, _ in regions[:60]))
        self.assertTrue(any(top >= offset[1] for _, top, _, _ in regions[60:]))
        found = detect_chests(image, .96)
        expected = [(197, 295), (187, 343)]
        self.assertEqual(len(found), 2, [(i, m.center, m.score) for i, m in found])
        for _, match in found:
            center = min(expected, key=lambda value: math.dist(value, match.center))
            self.assertLessEqual(math.dist(center, match.center), 2.)
            self.assertGreaterEqual(match.score, .96)
            expected.remove(center)

    def test_local_sparse_budget_does_not_change_default_small_template_path(self):
        template = load_builtin_templates()[0][1]
        mean = tuple(int(value) for value in np.asarray(template).mean(axis=(0, 1)).round())
        image = Image.new("RGB", (320, 200), (20, 24, 28))
        ImageDraw.Draw(image).rectangle((20, 20, 62, 62), fill=mean)
        image.paste(template, (180, 90))

        # There are over 183 colour candidates but under the established
        # 4096 cap. The original small-template path must remain sparse.
        with patch.object(np.fft, "rfft2", side_effect=AssertionError("Default path used FFT")):
            before = chest_matcher.find_matches(image, template, threshold=.96)
            default_many = chest_matcher.find_matches_many(image, [template], threshold=.96)[0]
        self.assertEqual([(m.x, m.y) for m in before], [(180, 90)])
        self.assertEqual(before, default_many)

        original_fft = np.fft.rfft2
        with patch.object(np.fft, "rfft2", wraps=original_fft) as local_fft:
            local = chest_matcher.find_matches_many(
                image, [template], threshold=.96, sparse_pixel_budget=32_768)[0]
        self.assertGreater(local_fft.call_count, 0, "The explicit local budget was not exercised")
        self.assertEqual([(m.x, m.y) for m in local], [(180, 90)])
        np.testing.assert_allclose([m.score for m in before], [m.score for m in local], atol=1e-10)

        # One local call must not leak its budget into a subsequent frame.
        with patch.object(np.fft, "rfft2", side_effect=AssertionError("Local budget leaked")):
            self.assertEqual(chest_matcher.find_matches(image, template, threshold=.96), before)

    def test_four_k_negative_frame_never_prepares_template_fft_arrays(self):
        image = Image.new("RGB", (3840, 2160), (18, 26, 42))
        with patch.object(np.fft, "rfft2", side_effect=AssertionError("4K FFT allocated")), \
                patch.object(chest_matcher, "_PreparedImage",
                             side_effect=AssertionError("Full frame prepared for matching")):
            self.assertEqual(detect_chests(image, .96), [])

    def test_envelope_keeps_scoring_positive_noisy_phases_at_saved_thresholds(self):
        rng = np.random.default_rng(194)
        noisy_positives = 0
        for width, height in ((16, 13), (37, 29), (64, 50)):
            for method in ("bilinear", "bicubic"):
                for phase_x, phase_y in ((0., 0.), (.25, .75), (.5, .25), (.75, .5)):
                    reference = render_ready_interior(width, height, method, phase_x, phase_y)
                    for threshold, brightness in ((.94, 12), (.96, 8)):
                        pixels = np.asarray(reference, dtype=np.int16)
                        pixels = pixels+brightness+rng.integers(-2, 3, pixels.shape)
                        actual = Image.fromarray(pixels.clip(0, 255).astype(np.uint8))
                        score = independent_rgb_score(actual, reference)
                        with self.subTest(size=(width, height), method=method,
                                          phase=(phase_x, phase_y), threshold=threshold):
                            self.assertGreaterEqual(score, threshold)
                            self.assertLess(score, .999)
                            frame = Image.new("RGB", (width+9, height+7), (16, 23, 31))
                            frame.paste(actual, (4, 3))
                            prepared = chest_matcher._PreparedImage(frame)
                            self.assertTrue(chest_scaling._envelope_allows(
                                prepared, width, height, threshold),
                                "The prefilter discarded a passing RGB/NCC patch")
                            noisy_positives += 1
        self.assertEqual(noisy_positives, 48)


if __name__ == "__main__":
    unittest.main()
