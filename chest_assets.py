"""Built-in WuwaTappo chest artwork from the user-supplied UI screenshots.

The tightly cropped chest interiors exclude the pet, transparent window, and
outer desktop background. These are visual variants of the same chest UI,
not identities for the two independently refreshing chest locations.
Four original screenshot variants are retained alongside a native CSS-sized
reference for the scale-aware renderer. No external asset files, network,
or training step are required at runtime.
"""
from __future__ import annotations

import base64
from io import BytesIO

from PIL import Image

# Exact opaque pixels from the supplied legacy and larger Windows 11 UI.
# Retaining both native sizes avoids interpolation and keeps existing scores.
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
    (
        'iVBORw0KGgoAAAANSUhEUgAAABMAAAAPCAIAAACJJmN7AAACDElEQVR42o1STWsTURS9d97LfGQSm1QJiVQoUWqhCIoW60YU'
        'F+Ku6s/wF7j2T/gPXAj+AelCoTs1UJDYNtYSJWlKM2JnMvPem3nvukiIyWTjWd77zrnnnvtw9/WL1pfvUmZABDPwPMcv2efn'
        'iZIZzKNYdO4/2GA3a1Wl8j0A2Lp3/eGjG8FZFARhrpWm+uTkt5Vpkych+L7TvFazbb7arLmejZh/Eo8UrzcqUqbTEmesUa+u'
        'b6wsLfmIuLbesO1CZ783DEIiIiIAQMRS2cXBzqs0/eeWMSyXPNstMMsCACLKtJGJCkcCZnLw3AJ/93aXMQ44tgmAeGXl0q3b'
        'q5VqiXOmVDYY/Gl9OhoOQwJCmNB93+GD02HR8xljU712+LPbPd1+tlVd9o86g48fvkqRmvnko1BYpE0cx8boadUYiiJxeNCP'
        'Inl40E+EytHGW3AAIKPjOHZdj3M2tkwEe3vHvV7Q7wdAiycDIrImc7SO45GUiibySAaSRDLGYOEmxhgpJZ/VkSJRSrmu6/ve'
        '9vO7lUrx+MfZzvuWUno8h4iyLBVCAhmet2F0ksSOgyXfsW1eb1wgIiGEMUbrbOKICAAsztniEkmi2u3uKBKtz50wDJWSWZaS'
        'MUA0pjmujW9ePt3/9ovM/B9E9Dy7UOBSpkqmNJstIuPW5p01/vjJZrN5OYoS+D9YFi5fLDevNv4CBdIjc2/9hEEAAAAASUVO'
        'RK5CYII='
    ),
    (
        'iVBORw0KGgoAAAANSUhEUgAAABQAAAAPCAIAAABr+ngCAAACE0lEQVR42o1STU9TURA9c9/t61OK1CpSbKLEQlyRGD8XujH+'
        'wf4RXbkQ48aQ6IIQeRpjFUVQKAVDKZb7vu67My6K9FE3ntXNnTk5Z84MubDV75tXS2Gn02MWFEBEVxsXm83ZMNw86hucheeR'
        'ArDXPdrdPRxjgkhrdfvu/OKta/PNuqc9IirWnRMNgFm05xUrpEhr7/L0hXp9qqR1c2F2e+fgqG+cYxlKiAAgF7Yik6yubAwG'
        '8QlVpBz4d+7PVypl7SkQiYjN8r39wcf3m8wCERAB0ABKvr42N21MeqocBH5Q1ooIf60qT1Wr5+duzBSn0wDa7e0Xz1fKQVCY'
        'iiYmytfnZh4+uhmc83u94zfL7W73MEktZERWALI0j9MsjpM8Z+fEOXGOB4O4/enn/v5vl7v1L92trV9RlLpRg5wEBhEwp2lK'
        'pEql0lBfBDbncO17lrn1zzujqArQo6dIksTM7Pu+Umr4s/3joLPTs9bJv9Sh7SI/y9IoMnmei4jyVH22eu/BwpWZqeIqRURE'
        'rLV6eA9FPjsXRUYpb6paefxksTIZNBq1Z0/f5rkFwMzW2jy3zKwBKEVEdMaYCLvcOQsIAGtdEifGJCIiwqeBawC1S5Mlv5Rl'
        'Z9YAwBwnL5dWG43pb187xkTseOx+yYWtLLNr7zaWX38wx/F4JgQCCQRjeRHVapPkwhaLCEuaZI4Z/wcCeZ76A20+TWSytCvp'
        'AAAAAElFTkSuQmCC'
    ),
)


def load_builtin_templates() -> list[tuple[int, Image.Image]]:
    """Return fresh opaque RGB template images; indexes identify artwork only."""
    result = []
    for index, encoded in enumerate(_TEMPLATE_PNGS, 1):
        with Image.open(BytesIO(base64.b64decode(encoded))) as image:
            result.append((index, image.convert("RGB")))
    from chest_artwork import render_ready_interior
    native = render_ready_interior().copy()
    native.info["wuwatappo_native"] = True
    result.append((5, native))
    return result
