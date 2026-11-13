"""Verify the cheaper search preserves the original FFT score and detections."""
import unittest
from unittest.mock import patch

import numpy as np
from PIL import Image

import chest_matcher
from chest_assets import load_builtin_templates
from chest_detection import detect_templates


class SparseMatcherTests(unittest.TestCase):
    def compare_fft(self, image, template, threshold=.94, scales=(1.0,)):
        actual = chest_matcher.find_matches(image, template, scales, threshold)
        # Zero sparse capacity forces the pre-existing FFT formula whenever
        # there are candidate locations, including the deliberately inserted
        # near-threshold patches below.
        with patch.object(chest_matcher, "_SPARSE_MAX_CANDIDATES", 0):
            reference = chest_matcher.find_matches(image, template, scales, threshold)
        actual = sorted(actual, key=lambda m: (m.y, m.x, m.scale))
        reference = sorted(reference, key=lambda m: (m.y, m.x, m.scale))
        self.assertEqual([(m.x, m.y, m.width, m.height, m.scale) for m in actual],
                         [(m.x, m.y, m.width, m.height, m.scale) for m in reference])
        np.testing.assert_allclose([m.score for m in actual], [m.score for m in reference],
                                   atol=1e-10, rtol=1e-10)
        return actual

    def test_colour_bound_keeps_noisy_and_brightness_shifted_chests(self):
        rng = np.random.default_rng(992)
        for _, template in load_builtin_templates():
            pixels = np.asarray(template, dtype=np.int16)
            image = Image.fromarray(rng.integers(0, 90, (100, 160, 3), dtype=np.uint8))
            for x, delta, noise in ((10, 0, 0), (50, 5, 3), (95, 16, 8)):
                variant = pixels + delta + rng.integers(-noise, noise+1, pixels.shape)
                image.paste(Image.fromarray(variant.clip(0, 255).astype(np.uint8)), (x, 35))
            for threshold in (.90, .94, .99, 1.0):
                with self.subTest(template=template.size, threshold=threshold):
                    found = self.compare_fft(image, template, threshold)
                    if threshold < 1.0:
                        self.assertIn((10, 35), [(m.x, m.y) for m in found])

    def test_scaled_templates_keep_exact_coordinates(self):
        template = load_builtin_templates()[0][1]
        image = Image.new("RGB", (140, 100), (24, 29, 38))
        large = template.resize((30, 24), Image.Resampling.LANCZOS)
        image.paste(large, (77, 48))
        found = self.compare_fft(image, template, scales=(1.0, 2.0))
        self.assertEqual([(m.x, m.y, m.scale) for m in found], [(77, 48, 2.0)])

    def test_dense_similar_colours_preserve_matches(self):
        template = load_builtin_templates()[0][1]
        mean = tuple(np.asarray(template).mean(axis=(0, 1)).round().astype(int))
        image = Image.new("RGB", (160, 120), mean)
        image.paste(template, (31, 38))
        image.paste(template, (119, 81))
        found = self.compare_fft(image, template)
        self.assertEqual([(m.x, m.y) for m in found], [(31, 38), (119, 81)])

    def test_shared_frame_search_matches_separate_searches(self):
        templates = load_builtin_templates()
        image = Image.new("RGB", (180, 130), (18, 27, 36))
        image.paste(templates[0][1], (21, 34))
        image.paste(templates[1][1], (119, 84))
        together = chest_matcher.find_matches_many(image, [t for _, t in templates])
        separate = [chest_matcher.find_matches(image, t) for _, t in templates]
        self.assertEqual(together, separate)
        self.assertEqual([m.center for _, m in detect_templates(image, templates)], [(28, 40), (127, 90)])

    def test_unmatched_frame_does_not_allocate_fft_arrays(self):
        image = Image.new("RGB", (320, 240), (0, 0, 30))
        with patch.object(np.fft, "rfft2", side_effect=AssertionError("Unexpected FFT")):
            self.assertEqual(detect_templates(image, load_builtin_templates()), [])


if __name__ == "__main__":
    unittest.main()
