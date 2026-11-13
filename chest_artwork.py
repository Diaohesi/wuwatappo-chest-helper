"""Original ready-button artwork bundled for skin-independent scale matching.

The three PNG layers and their CSS geometry come from WuwaTappo 1.0.4.
No installed pet resources or external image files are read at runtime.
"""
from functools import lru_cache
from io import BytesIO
import base64
from PIL import Image

BUTTON_SIZE = (62, 44)
GLYPH_BOX = (14, 11, 42, 32)
INTERIOR_BOX = (12, 9, 44, 34)

_IMAGES = {
    'bubble': (
        'iVBORw0KGgoAAAANSUhEUgAAAFQAAAA8CAYAAAGPy61gAAAAGXRFWHRTb2Z0d2FyZQBBZG9iZSBJbWFnZVJlYWR5ccllPAAAAyRp'
        'VFh0WE1MOmNvbS5hZG9iZS54bXAAAAAAADw/eHBhY2tldCBiZWdpbj0i77u/IiBpZD0iVzVNME1wQ2VoaUh6cmVTek5UY3prYzlk'
        'Ij8+IDx4OnhtcG1ldGEgeG1sbnM6eD0iYWRvYmU6bnM6bWV0YS8iIHg6eG1wdGs9IkFkb2JlIFhNUCBDb3JlIDcuMS1jMDAwIDc5'
        'LmNjZjg0ZTAsIDIwMjIvMDYvMjItMjI6MTM6MjYgICAgICAgICI+IDxyZGY6UkRGIHhtbG5zOnJkZj0iaHR0cDovL3d3dy53My5v'
        'cmcvMTk5OS8wMi8yMi1yZGYtc3ludGF4LW5zIyI+IDxyZGY6RGVzY3JpcHRpb24gcmRmOmFib3V0PSIiIHhtbG5zOnhtcE1NPSJo'
        'dHRwOi8vbnMuYWRvYmUuY29tL3hhcC8xLjAvbW0vIiB4bWxuczpzdFJlZj0iaHR0cDovL25zLmFkb2JlLmNvbS94YXAvMS4wL3NU'
        'eXBlL1Jlc291cmNlUmVmIyIgeG1sbnM6eG1wPSJodHRwOi8vbnMuYWRvYmUuY29tL3hhcC8xLjAvIiB4bXBNTTpEb2N1bWVudElE'
        'PSJ4bXAuZGlkOkVCNzhBNzQxOTU3NzExRjE5Q0IyQTIyQzc1MjE3MDQ2IiB4bXBNTTpJbnN0YW5jZUlEPSJ4bXAuaWlkOkVCNzhB'
        'NzQwOTU3NzExRjE5Q0IyQTIyQzc1MjE3MDQ2IiB4bXA6Q3JlYXRvclRvb2w9IkFkb2JlIFBob3Rvc2hvcCAyMi41IChXaW5kb3dz'
        'KSI+IDx4bXBNTTpEZXJpdmVkRnJvbSBzdFJlZjppbnN0YW5jZUlEPSJ4bXAuaWlkOjkxREVBQzk0NUZFQjExRjFCMjY4OEI1NjZG'
        'RkFENjlDIiBzdFJlZjpkb2N1bWVudElEPSJ4bXAuZGlkOjkxREVBQzk1NUZFQjExRjFCMjY4OEI1NjZGRkFENjlDIi8+IDwvcmRm'
        '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'
        'gg=='
    ),
    'base': (
        'iVBORw0KGgoAAAANSUhEUgAAACQAAAAbCAYAAAFjKx12AAAAGXRFWHRTb2Z0d2FyZQBBZG9iZSBJbWFnZVJlYWR5ccllPAAAAyRp'
        'VFh0WE1MOmNvbS5hZG9iZS54bXAAAAAAADw/eHBhY2tldCBiZWdpbj0i77u/IiBpZD0iVzVNME1wQ2VoaUh6cmVTek5UY3prYzlk'
        'Ij8+IDx4OnhtcG1ldGEgeG1sbnM6eD0iYWRvYmU6bnM6bWV0YS8iIHg6eG1wdGs9IkFkb2JlIFhNUCBDb3JlIDcuMS1jMDAwIDc5'
        'LmNjZjg0ZTAsIDIwMjIvMDYvMjItMjI6MTM6MjYgICAgICAgICI+IDxyZGY6UkRGIHhtbG5zOnJkZj0iaHR0cDovL3d3dy53My5v'
        'cmcvMTk5OS8wMi8yMi1yZGYtc3ludGF4LW5zIyI+IDxyZGY6RGVzY3JpcHRpb24gcmRmOmFib3V0PSIiIHhtbG5zOnhtcD0iaHR0'
        'cDovL25zLmFkb2JlLmNvbS94YXAvMS4wLyIgeG1sbnM6eG1wTU09Imh0dHA6Ly9ucy5hZG9iZS5jb20veGFwLzEuMC9tbS8iIHht'
        'bG5zOnN0UmVmPSJodHRwOi8vbnMuYWRvYmUuY29tL3hhcC8xLjAvc1R5cGUvUmVzb3VyY2VSZWYjIiB4bXA6Q3JlYXRvclRvb2w9'
        'IkFkb2JlIFBob3Rvc2hvcCAyMi41IChXaW5kb3dzKSIgeG1wTU06SW5zdGFuY2VJRD0ieG1wLmlpZDowQzgzNDBDNTk1NzgxMUYx'
        'QTgyOUNDQzRGMTMxMTAwMiIgeG1wTU06RG9jdW1lbnRJRD0ieG1wLmRpZDowQzgzNDBDNjk1NzgxMUYxQTgyOUNDQzRGMTMxMTAw'
        'MiI+IDx4bXBNTTpEZXJpdmVkRnJvbSBzdFJlZjppbnN0YW5jZUlEPSJ4bXAuaWlkOjBDODM0MEMzOTU3ODExRjFBODI5Q0NDNEYx'
        'MzExMDAyIiBzdFJlZjpkb2N1bWVudElEPSJ4bXAuZGlkOjBDODM0MEM0OTU3ODExRjFBODI5Q0NDNEYxMzExMDAyIi8+IDwvcmRm'
        'OkRlc2NyaXB0aW9uPiA8L3JkZjpSREY+IDwveDp4bXBtZXRhPiA8P3hwYWNrZXQgZW5kPSJyIj8+7cs/PQAAAztJREFUeNpi/P//'
        'PwM6YGLAAlgaAkz+8/MLoAoyMDI6fvz4YT+yIEAAMaKbyUTQLBYQATQHLgAQQIzY3EXQmKJFexn74533A/U64DQaYv7fQIb/zO9/'
        '/vzJ8OPHdwaAACLKOiYGIgBRiliagsz+8/LyoTi8L84ZxQ1M//79Y/j8+RNcAF0B3DqQQpAPv3//DjYJJgkSA2EWZB2/fv1kqPc3'
        'fo8eJAABRFQQUC0EiAqlhkCTCfx8AvnoEoyMDAdAMVi44ADcD/0JTgn//zHOx+6i/wz5kAD6hiIBSif//zG/749zqoeFMC5D4GkJ'
        'Eoi/wBgEQPHIxMQENZDRAWjIA1wGgMIYFKUgg0BOR8lNyHHduPGsI4iuDzBiZPiP20XguK73NUpgYMKtiAA4ALIMIIAGX/QPY4NY'
        '2sNt/nNwcGCTSwDm4IWIVO0gACyn1iMXaIhcwDiB6efPHww4Ym5BX6wzvFwGpXJshkATZQHYa58+fcSVysAagUUrwTTGglz4IGcN'
        '5FKMmKTGhJ41vnz5jBFWQJMW4DLgx48fmKUfCPz9+xcsAXIZGzu3YuWKA6AMuxDosgSYGlCR+u3bN6DaP5heQwcgxT++fFEAMh9g'
        'rW1whRFWwPx/P7B8xigdMMLnP0MCEzDnAgtDYGmIG+Ax5P8HYB3jUL/p7EKAAKNa7h90WW3UQfQCjJACywVYVPxTIKyc7UDhgh0P'
        'SLGgP8HVAJh7DQir/PcA2DQ4wAjMRuBUzcPDy8DMzEyKXQ/+/vubWLrkAErO6olxDgCWGfMJZU2CUQYqyr5+/UKKXgVmJub9kBCA'
        'hYaHAtAx68l1DDjKYCGEDFhZWRk4OblA9QsxZnz4z/B/IshvwHZKPcUVIzbB379/A/FHsIPY2TmAmB2fGQKMQH8BCzeKHAIr//EW'
        '1aBCE9R7AGGw61lYwY5jYWGByU8oXryvEFlPb6xTP9AjBYQcADIb1DsBNdeRC2cWUnzx589vMEbq9jSiqwE5sCPCtgBkCQiDfA6i'
        'QTUfUb0iAvUPAW8y328MMI6HcesDTByAafI+qKEOClVQ8+b3719gTxBZRR0Ap9qGAKN6YGO8YSALRKCDG5o2nWsEANsWjlZ4L1To'
        'AAAAAElFTkSuQmCC'
    ),
    'lid': (
        'iVBORw0KGgoAAAANSUhEUgAAACQAAAAbCAYAAAFjKx12AAAAGXRFWHRTb2Z0d2FyZQBBZG9iZSBJbWFnZVJlYWR5ccllPAAAAyRp'
        'VFh0WE1MOmNvbS5hZG9iZS54bXAAAAAAADw/eHBhY2tldCBiZWdpbj0i77u/IiBpZD0iVzVNME1wQ2VoaUh6cmVTek5UY3prYzlk'
        'Ij8+IDx4OnhtcG1ldGEgeG1sbnM6eD0iYWRvYmU6bnM6bWV0YS8iIHg6eG1wdGs9IkFkb2JlIFhNUCBDb3JlIDcuMS1jMDAwIDc5'
        'LmNjZjg0ZTAsIDIwMjIvMDYvMjItMjI6MTM6MjYgICAgICAgICI+IDxyZGY6UkRGIHhtbG5zOnJkZj0iaHR0cDovL3d3dy53My5v'
        'cmcvMTk5OS8wMi8yMi1yZGYtc3ludGF4LW5zIyI+IDxyZGY6RGVzY3JpcHRpb24gcmRmOmFib3V0PSIiIHhtbG5zOnhtcD0iaHR0'
        'cDovL25zLmFkb2JlLmNvbS94YXAvMS4wLyIgeG1sbnM6eG1wTU09Imh0dHA6Ly9ucy5hZG9iZS5jb20veGFwLzEuMC9tbS8iIHht'
        'bG5zOnN0UmVmPSJodHRwOi8vbnMuYWRvYmUuY29tL3hhcC8xLjAvc1R5cGUvUmVzb3VyY2VSZWYjIiB4bXA6Q3JlYXRvclRvb2w9'
        'IkFkb2JlIFBob3Rvc2hvcCAyMi41IChXaW5kb3dzKSIgeG1wTU06SW5zdGFuY2VJRD0ieG1wLmlpZDoxMkY1RDkxMzk1NzgxMUYx'
        'QjU3QkQ5MDQ1RDU5NjJFNyIgeG1wTU06RG9jdW1lbnRJRD0ieG1wLmRpZDoxMkY1RDkxNDk1NzgxMUYxQjU3QkQ5MDQ1RDU5NjJF'
        'NyI+IDx4bXBNTTpEZXJpdmVkRnJvbSBzdFJlZjppbnN0YW5jZUlEPSJ4bXAuaWlkOjEyRjVEOTExOTU3ODExRjFCNTdCRDkwNDVE'
        'NTk2MkU3IiBzdFJlZjpkb2N1bWVudElEPSJ4bXAuZGlkOjEyRjVEOTEyOTU3ODExRjFCNTdCRDkwNDVENTk2MkU3Ii8+IDwvcmRm'
        'OkRlc2NyaXB0aW9uPiA8L3JkZjpSREY+IDwveDp4bXBtZXRhPiA8P3hwYWNrZXQgZW5kPSJyIj8+GV3QVgAAAXNJREFUeNpiZACC'
        'vjjn/wxQwMjIcIDx/384Hw6YGLAArIIAAcQINO89kBaAC6Cbx0TQHIAAYkR2EwgULdpL2FyinYgOAAKIsT/BxeH/v//7cSkAWc+E'
        'rgAkiMwHuZl6bqKzIoAAYuyNdUpgZGScj0sB0GMLihfvS0SPGQzbGBkY4/HYEwAyBB58/xkO4FRJTFBSzf8j3CCAAAIn5P545/3A'
        'MHcg04wEYIwuZAKlD3yGEIp2IFjQHePgQMBr/x+ADVu81xGfKmYm5vW4DQK6omjRPkWkFJ6Ix8IPowly1KDhaRBAgIFzf2+cSwMw'
        'Z9aTb8z/C5DqmVGBbBMY/jcWL9rXwEhhUQQ06Z9D0eL9B0HM7hgXB2Ym3JU7Ea46wESJY4ANhwaYY0CgdMmeA8BasoGC+HJgbI9w'
        'UGBjZapnJCK4gQXXgU9f/k1s3HDgAzHmg81mYYlnZPzvQESUPfj1+18j1UrHQZc7Rh006qBRB406iEwAAAq0l3CbCfNXAAAAAElF'
        'TkSuQmCC'
    ),
}

@lru_cache(maxsize=1)
def _artwork():
    return {key: Image.open(BytesIO(base64.b64decode(value))).convert("RGBA")
            for key, value in _IMAGES.items()}


def load_ready_artwork():
    """Return independent RGBA copies of the original bubble/base/lid layers."""
    return {key: image.copy() for key, image in _artwork().items()}


@lru_cache(maxsize=1)
def _composited_button():
    result = _artwork()["bubble"].resize((248, 176), Image.Resampling.BICUBIC)
    for key in ("base", "lid"):
        layer = _artwork()[key].resize((112, 84), Image.Resampling.BICUBIC)
        result.alpha_composite(layer, (56, 44))
    return result


@lru_cache(maxsize=384)
def render_ready_interior(width=32, height=25, method="bicubic", phase_x=0., phase_y=0.):
    """Rasterize the opaque interior at a physical size and subpixel phase.

    This uses source layers at their CSS coordinates rather than enlarging a
    previously captured low-resolution screenshot. The returned cache entry
    is read-only; callers that modify images must take a copy first.
    """
    resample = {"bilinear": Image.Resampling.BILINEAR,
                "bicubic": Image.Resampling.BICUBIC}[method]
    scale = width/32
    left, top = 48-phase_x/scale*4, 36-phase_y/scale*4
    box = (left, top, left+width/scale*4, top+height/scale*4)
    result = _composited_button().resize((width, height), resample=resample, box=box)
    # The comparison rectangle stays wholly inside the opaque speech bubble.
    if result.getchannel("A").getextrema()[0] < 254:
        raise ValueError("Ready-button interior must remain opaque")
    return result.convert("RGB")
