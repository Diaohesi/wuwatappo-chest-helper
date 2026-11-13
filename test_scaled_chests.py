"""Independent CSS-scene scale regressions; no desktop capture or input.

Scenes use the app's original three transparent artwork layers and CSS sizes,
not recognition templates. We compose a whole pet/button/desktop scene at 4x
CSS resolution, then resample the entire image with a separate renderer.
This exercises surrounding pixels, fractional phases and alpha compositing.
Synthetic rendering is supplemented by the original screenshot holdouts; it
does not prove every WebView/GPU renderer produces identical pixels.
"""
from __future__ import annotations

from dataclasses import dataclass
import math
import unittest
from unittest.mock import patch

from PIL import Image, ImageDraw, ImageOps

from chest_artwork import load_ready_artwork
from chest_detection import detect_chests
import chest_matcher
from fixture_assets import load_fixture


PET_SCALES = (.5, .625, .75, 1., 1.25, 1.5, 1.75, 2.)
DPIS = (1., 1.25, 1.5, 1.75, 2.)
RESAMPLERS = (Image.Resampling.BILINEAR, Image.Resampling.BICUBIC)
BACKGROUNDS = ((18, 26, 42), (250, 248, 242), (35, 115, 155))
SCENE_SIZE = (320, 220)
SUPERSAMPLE = 4
CSS_BUTTON_SIZE = (62, 44)
CSS_GLYPH_SIZE = (28, 21)
CSS_GLYPH_ORIGIN = (14, 11)
CSS_GLYPH_CENTER = (28, 21.5)
BUTTON_ORIGINS = ((230, 44), (207, 112))


@dataclass
class RenderedScene:
    image: Image.Image
    centers: list[tuple[float, float]]


def _button_layer(artwork, state):
    """Independent CSS composition; no production template renderer is used."""
    width, height = (value * SUPERSAMPLE for value in CSS_BUTTON_SIZE)
    button = artwork["bubble"].convert("RGBA").resize(
        (width, height), Image.Resampling.BICUBIC)
    if state in ("ready", "gray"):
        size = tuple(value * SUPERSAMPLE for value in CSS_GLYPH_SIZE)
        origin = tuple(value * SUPERSAMPLE for value in CSS_GLYPH_ORIGIN)
        # Both images have CSS background-size:contain; their 36:27 ratio is
        # exactly the 28:21 pseudo-element ratio. Preserve transparent gaps:
        # parts of the gold drawing come from the bubble behind these layers.
        for key in ("base", "lid"):
            button.alpha_composite(artwork[key].convert("RGBA").resize(
                size, Image.Resampling.BICUBIC), origin)
    elif state == "countdown":
        label = Image.new("RGBA", (42, 18), (0, 0, 0, 0))
        ImageDraw.Draw(label).text((0, 0), "29:59", fill=(130, 88, 46, 255))
        button.alpha_composite(label.resize((42*SUPERSAMPLE, 18*SUPERSAMPLE),
                                            Image.Resampling.BICUBIC),
                               (15*SUPERSAMPLE, 13*SUPERSAMPLE))
    elif state != "empty":
        raise ValueError(state)
    if state == "gray":
        alpha = button.getchannel("A")
        button = ImageOps.grayscale(button).convert("RGBA")
        button.putalpha(alpha)
    return button


def render_scene(pet_scale=1., dpi=1., *, resampling=Image.Resampling.BILINEAR,
                 phase=(.25, .75), background=BACKGROUNDS[0],
                 states=("ready", "ready"), occlude=False):
    """Scale the complete scene after native-resource alpha composition.

    The recognizer is never told the scale. DPI and pet scale are independent
    inputs even where products happen to be equal. Pixel centers are computed
    from CSS geometry, not from a detector's selected template or scale.
    """
    artwork = load_ready_artwork()
    frame = Image.new("RGBA", tuple(value * SUPERSAMPLE for value in SCENE_SIZE),
                      (*background, 255))
    # An actual no-chest pet/desktop screenshot supplies independent context.
    pet = load_fixture("pet_no_chest.png").convert("RGBA")
    frame.alpha_composite(pet.resize(tuple(value * SUPERSAMPLE for value in pet.size),
                                    Image.Resampling.BICUBIC),
                          (25*SUPERSAMPLE, 49*SUPERSAMPLE))
    draw = ImageDraw.Draw(frame)
    draw.rectangle((155*SUPERSAMPLE, 14*SUPERSAMPLE, 315*SUPERSAMPLE, 30*SUPERSAMPLE),
                   fill=(64, 72, 94, 255))
    draw.text((161*SUPERSAMPLE, 18*SUPERSAMPLE), "Desktop context", fill="white")
    origins = []
    for (left, top), state in zip(BUTTON_ORIGINS, states):
        x = round((left+phase[0])*SUPERSAMPLE)
        y = round((top+phase[1])*SUPERSAMPLE)
        frame.alpha_composite(_button_layer(artwork, state), (x, y))
        if state == "ready":
            origins.append((x/SUPERSAMPLE, y/SUPERSAMPLE))
    if occlude:
        draw.rectangle((195*SUPERSAMPLE, 33*SUPERSAMPLE, 319*SUPERSAMPLE, 170*SUPERSAMPLE),
                       fill=(78, 84, 93, 255))
        origins = []
    effective_scale = float(pet_scale) * float(dpi)
    output_size = tuple(round(value * effective_scale) for value in SCENE_SIZE)
    # Render RGB first so resampling includes the real composed surroundings,
    # instead of independently resizing a ready template and pasting it later.
    image = frame.convert("RGB").resize(output_size, resampling)
    sx, sy = image.width/SCENE_SIZE[0], image.height/SCENE_SIZE[1]
    centers = [((left+CSS_GLYPH_CENTER[0])*sx, (top+CSS_GLYPH_CENTER[1])*sy)
               for left, top in origins]
    return RenderedScene(image, centers)


class ScaledSceneDetectionTests(unittest.TestCase):
    def assert_scene(self, scene, threshold=.94):
        found = detect_chests(scene.image, threshold)
        self.assertEqual(len(found), len(scene.centers),
                         [(variant, match.center, round(match.score, 5)) for variant, match in found])
        remaining = list(scene.centers)
        for variant, match in found:
            self.assertGreaterEqual(match.score, threshold)
            expected = min(remaining, key=lambda center: math.dist(center, match.center))
            self.assertLessEqual(abs(match.center[0]-expected[0]), 2.,
                                 (variant, match, expected))
            self.assertLessEqual(abs(match.center[1]-expected[1]), 2.,
                                 (variant, match, expected))
            remaining.remove(expected)
        self.assertEqual(remaining, [])

    def test_native_resources_have_independent_css_source_dimensions(self):
        artwork = load_ready_artwork()
        self.assertEqual(set(artwork), {"bubble", "base", "lid"})
        self.assertEqual(artwork["bubble"].size, (84, 60))
        for name in ("base", "lid"):
            self.assertEqual(artwork[name].size, (36, 27))
            self.assertEqual(artwork[name].mode, "RGBA")
            self.assertLess(artwork[name].getchannel("A").getextrema()[0], 255)

    def test_complete_scene_at_all_pet_scales_and_independent_dpi(self):
        for scale in PET_SCALES:
            for dpi in DPIS:
                for resampling in RESAMPLERS:
                    with self.subTest(pet_scale=scale, dpi=dpi, interpolation=resampling.name):
                        self.assert_scene(render_scene(scale, dpi, resampling=resampling))

    def test_saved_point_96_threshold_fractional_phases_and_backgrounds(self):
        cases = ((.5, 1., (.75, .25)), (.75, 1.25, (.25, .75)),
                 (1.25, 1.5, (.5, .25)), (2., 2., (.75, .75)))
        for background in BACKGROUNDS:
            for scale, dpi, phase in cases:
                with self.subTest(background=background, pet_scale=scale, dpi=dpi, phase=phase):
                    self.assert_scene(render_scene(scale, dpi, phase=phase,
                                                  background=background,
                                                  resampling=Image.Resampling.BICUBIC), .96)

    def test_every_integer_pet_setting_from_50_to_200_at_saved_threshold(self):
        for percent in range(50, 201):
            with self.subTest(pet_percent=percent):
                scene = render_scene(percent/100, states=("ready", "empty"),
                                     resampling=Image.Resampling.BICUBIC)
                self.assert_scene(scene, .96)

    def test_each_scaled_chest_can_be_ready_independently(self):
        for scale in (.5, 1., 2.):
            for states in (("ready", "countdown"), ("empty", "ready")):
                with self.subTest(scale=scale, states=states):
                    self.assert_scene(render_scene(scale, states=states))

    def test_countdown_empty_gray_and_occluded_scenes_are_not_ready(self):
        for scale in (.5, 1., 1.5, 2.):
            for state in ("countdown", "empty", "gray"):
                for resampling in RESAMPLERS:
                    with self.subTest(scale=scale, state=state, interpolation=resampling.name):
                        self.assert_scene(render_scene(scale, states=(state, state),
                                                      resampling=resampling))
            with self.subTest(scale=scale, occluded=True):
                self.assert_scene(render_scene(scale, occlude=True))

    def test_supplied_real_screenshots_remain_positive_holdouts(self):
        expected = {"pet_with_chests.png": ((119, 49), (113, 76)),
                    "win11_chests.png": ((154, 51), (146, 85)),
                    "native_ready_chests.png": ((197, 95), (187, 143))}
        for name, centers in expected.items():
            with self.subTest(fixture=name):
                self.assert_scene(RenderedScene(load_fixture(name), list(centers)), .96)

    def test_supplied_1k_50_percent_and_200_percent_nonready_screenshots(self):
        for name in ("pet_50_no_chest.png", "pet_200_countdowns.png"):
            with self.subTest(fixture=name):
                self.assert_scene(RenderedScene(load_fixture(name), []))

    def test_supplied_no_chest_screenshot_remains_negative_when_entirely_scaled(self):
        source = load_fixture("pet_no_chest.png")
        for scale in PET_SCALES:
            for resampling in RESAMPLERS:
                with self.subTest(scale=scale, interpolation=resampling.name):
                    image = source.resize(tuple(round(value*scale) for value in source.size), resampling)
                    self.assert_scene(RenderedScene(image, []))

    def test_desktop_resolution_does_not_scale_ui_or_scan_every_scale_over_full_screen(self):
        scene = render_scene(.75, 1.25, resampling=Image.Resampling.BICUBIC)
        offset = (37, 61)
        for resolution in ((1280, 720), (1920, 1080), (2560, 1440), (3840, 2160)):
            desktop = Image.new("RGB", resolution, BACKGROUNDS[2])
            desktop.paste(scene.image, offset)
            expected = [(x+offset[0], y+offset[1]) for x, y in scene.centers]
            full_searches = []
            original = chest_matcher._find_candidates

            def inspect_search(image, template, *args, **kwargs):
                if image.width*image.height >= resolution[0]*resolution[1]*.9:
                    scales = kwargs.get("scales", args[0] if args else (1.,))
                    full_searches.append(tuple(scales))
                return original(image, template, *args, **kwargs)

            with self.subTest(resolution=resolution), patch.object(chest_matcher, "_find_candidates", inspect_search):
                self.assert_scene(RenderedScene(desktop, expected))
                # Preserve a bounded number of optional native-size legacy
                # searches, but forbid whole-monitor NCC for dozens of scales.
                self.assertLessEqual(len(full_searches), 5, full_searches)
                self.assertTrue(all(len(scales) == 1 for scales in full_searches), full_searches)


if __name__ == "__main__":
    unittest.main()
