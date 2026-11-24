"""Physical display layout and bounded Windows desktop reads."""
import ctypes as C
from ctypes import wintypes as W
from dataclasses import dataclass
import os

from PIL import Image


@dataclass(frozen=True)
class Monitor:
    device: str
    rect: tuple
    primary: bool
    dpi: int | None = None


def intersection(a, b):
    l, t, r, d = max(a[0], b[0]), max(a[1], b[1]), min(a[2], b[2]), min(a[3], b[3])
    return (l, t, r, d) if l < r and t < d else None


def bounds(rects):
    return min(r[0] for r in rects), min(r[1] for r in rects), max(r[2] for r in rects), max(r[3] for r in rects)


def covered(rect, screens):
    """Subtract real monitor intersections, including icons straddling a shared edge."""
    pending = [rect]
    for screen in screens:
        remaining = []
        for box in pending:
            clip = intersection(box, screen)
            if clip is None:
                remaining.append(box)
                continue
            l, t, r, b = box
            x, y, u, v = clip
            remaining.extend(part for part in ((l,t,r,y), (l,v,r,b), (l,y,x,v), (u,y,r,v))
                             if part[0] < part[2] and part[1] < part[3])
        if not remaining:
            return True
        pending = remaining
    return False


def owning_display(rect, layout):
    def area(m):
        i = intersection(rect, m.rect)
        return (i[2]-i[0]) * (i[3]-i[1]) if i else 0
    return max((m for m in layout if intersection(rect, m.rect)), key=area, default=None)


if os.name == 'nt':
    def bind(dll, name, result, *args):
        fn = getattr(dll, name)
        fn.restype, fn.argtypes = result, args
        return fn

    user = C.WinDLL('user32', use_last_error=True)
    gdi = C.WinDLL('gdi32', use_last_error=True)
    shcore = C.WinDLL('shcore', use_last_error=True)

    class Info(C.Structure):
        _fields_ = [('size', W.DWORD), ('screen', W.RECT), ('work', W.RECT), ('flags', W.DWORD), ('device', W.WCHAR * 32)]

    callback = C.WINFUNCTYPE(W.BOOL, W.HANDLE, W.HDC, C.POINTER(W.RECT), W.LPARAM)
    enum = bind(user, 'EnumDisplayMonitors', W.BOOL, W.HDC, C.c_void_p, callback, W.LPARAM)
    info = bind(user, 'GetMonitorInfoW', W.BOOL, W.HANDLE, C.POINTER(Info))
    dpi = bind(shcore, 'GetDpiForMonitor', C.c_long, W.HANDLE, C.c_int, C.POINTER(W.UINT), C.POINTER(W.UINT))
    get_dc = bind(user, 'GetDC', W.HDC, W.HWND)
    release = bind(user, 'ReleaseDC', C.c_int, W.HWND, W.HDC)
    memory_dc = bind(gdi, 'CreateCompatibleDC', W.HDC, W.HDC)
    delete_dc = bind(gdi, 'DeleteDC', W.BOOL, W.HDC)
    select = bind(gdi, 'SelectObject', W.HANDLE, W.HDC, W.HANDLE)
    delete = bind(gdi, 'DeleteObject', W.BOOL, W.HANDLE)
    dib = bind(gdi, 'CreateDIBSection', W.HANDLE, W.HDC, C.c_void_p, W.UINT, C.POINTER(C.c_void_p), W.HANDLE, W.DWORD)
    blit = bind(gdi, 'BitBlt', W.BOOL, W.HDC, C.c_int, C.c_int, C.c_int, C.c_int, W.HDC, C.c_int, C.c_int, W.DWORD)
    flush = bind(gdi, 'GdiFlush', W.BOOL)


def monitors():
    found = []

    @callback
    def visit(handle, _dc, _rect, _data):
        detail = Info(size=C.sizeof(Info))
        if not info(handle, C.byref(detail)):
            return False
        r = detail.screen
        x, y = W.UINT(), W.UINT()
        effective = x.value if dpi(handle, 0, C.byref(x), C.byref(y)) == 0 else None
        found.append(Monitor(detail.device, (r.left, r.top, r.right, r.bottom), bool(detail.flags & 1), effective))
        return True

    if not enum(None, None, visit, 0) or not found:
        raise OSError('无法读取显示器布局。')
    return tuple(sorted(found, key=lambda m: (m.device, m.rect)))


def grab_region(rect):
    """One physical-pixel BitBlt, including layered windows, without allocating all screens."""
    import struct
    l, t, r, b = rect
    w, h = r-l, b-t
    source = get_dc(None)
    target = bitmap = previous = None
    try:
        if not source or not (target := memory_dc(source)):
            raise C.WinError(C.get_last_error())
        header = struct.pack('<IiiHHIIiiII', 40, w, -h, 1, 32, 0, 0, 0, 0, 0, 0)
        pixels = C.c_void_p()
        bitmap = dib(source, header, 0, C.byref(pixels), None, 0)
        if not bitmap or not pixels or not (previous := select(target, bitmap)):
            raise C.WinError(C.get_last_error())
        if not blit(target, 0, 0, w, h, source, l, t, 0x00CC0020 | 0x40000000) or not flush():
            raise C.WinError(C.get_last_error())
        return Image.frombytes('RGB', (w, h), C.string_at(pixels, w*h*4), 'raw', 'BGRX')
    finally:
        if previous:
            select(target, previous)
        if bitmap:
            delete(bitmap)
        if target:
            delete_dc(target)
        if source:
            release(None, source)
