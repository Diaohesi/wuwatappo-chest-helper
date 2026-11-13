"""Built-in WuwaTappo chest artwork from the user-supplied UI screenshots.

The tightly cropped chest interiors exclude the pet, transparent window, and
outer desktop background. These are visual variants of the same chest UI,
not identities for the two independently refreshing chest locations.
No screenshots, external asset files, network, or training step are required
at runtime. Original supplied images are retained as offline test fixtures.
"""
from __future__ import annotations

import base64
from io import BytesIO

from PIL import Image

# The supplied screenshots have fixed-size UI. Physical display/UI scaling
# can be handled by the caller using chest_matcher's explicit scales argument.
BUILTIN_SCALES = (1.0,)

_TEMPLATE_PNGS = (
    (
        'iVBORw0KGgoAAAANSUhEUgAAAA8AAAAMCAIAAAAyIHCzAAABgElEQVR42n1PTUtbURCdud58GW2JEZEoZmEWKrRV'
        'Ny5EhFJc9HfV/yR1KdgiVluoIDa0RX0ak7wkN8n7vjPTxTMxq57dnDlnzhl0jg/Pz+rG+AACAACAiEohMY8IAIB8'
        'IfvmXVV/Prp03T5MoFx+/f7D2+8Xf+p1R+TF0Te+Hg4DpdToKkwXcquri5Xlsul5bsv0Bz6PDL4f6d29jTBIxurS'
        '3MxKdQEAamuV2VeF5lOP6FmtM0o7d+1gpAaAbFan6QgYBLHjuDbhdDVdzOmLb9e5fF7rTEo9OO3Hh+7Bx+0fl7/P'
        'z35FUTKujgjKEoVhRGRTyhI3Gp2b6/vbv60wjCe/FIGp/fUlESYiAFRKIaK13H4yPeNZSxNSYeap/fWldCCyIgwA'
        'tVplZ3ctl8103AERi4i1NkniOI40IqZxIhLHMROVyjMr1YXhMLA28TxPhFlEmAFAV5bnnbvWONESff3ys9noNFvd'
        'btdM9i6VZtE5/nR6cmUGHvwXxWJhc6v2D0nh6RbdbW5eAAAAAElFTkSuQmCC'
    ),
    (
        'iVBORw0KGgoAAAANSUhEUgAAABAAAAAMCAIAAADkharWAAABiklEQVR42mVRW2sTYRA932Wzm93NRitqaVArNFKr'
        '6EuRSsH/IPjb8nOEekEkYEIDTQM2kbRKwUSTbWaT5rv5kBg2el5mmDkzc2aGqVat17389KE9GKTOOfzF4737gV9o'
        'NL4ihyjyOYD+t5/DdbZfkM+ebx8c7gZ+IV9AdC0BlJJiVCpqbQAwh6Qc7b/YuX33hhD89ZuXH9+3h4PUYtmOqVZt'
        'PKJ0RMsJjAWBV74Z+wUJxpTW6YiIZrDLIRLAl3qn2exxLhahOA6q1cr+waMw9Bv17ulJ//doslIlAWTTGRGFYSiE'
        'BDCZzI6Pe5V7t5IkrH/uaGPya8iFsdYSUbEYep4HwBh79LbFOPuHDYCvPOdcltF0mjlnOWebWxs71YoQawSllASQ'
        'uyfm87nW+uH21uGrvXI5SlM6bfedc8YYrZUxRgKI41BKsTjrQt7w17jTPi8lwfnFZZZR/kUSwJOnD35cDM6631eJ'
        '8Xjy7qgpOL+6mmL1AYY7mxtMtWrWuoxm13O1Ju5/MOZ54g+8QMzvlf6lqwAAAABJRU5ErkJggg=='
    ),
)


def load_builtin_templates() -> list[tuple[int, Image.Image]]:
    """Return fresh opaque RGB template images; indexes identify artwork only."""
    result = []
    for index, encoded in enumerate(_TEMPLATE_PNGS, 1):
        with Image.open(BytesIO(base64.b64decode(encoded))) as image:
            result.append((index, image.convert("RGB")))
    return result
