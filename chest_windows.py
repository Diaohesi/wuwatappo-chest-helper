"""Small Windows boundary for the WuwaTappo chest helper.

Coordinates are physical virtual-desktop pixels, including negative monitor
coordinates.  Call ``enable_dpi_awareness`` before creating any Tk widgets.
Only visible screen pixels are captured; the helper never brings another app
to the foreground or injects input into a hidden/background window.
"""

from __future__ import annotations

import ctypes
from ctypes import wintypes
from dataclasses import dataclass
import os
from pathlib import Path, PureWindowsPath
import subprocess
import sys
import time
from typing import Callable

from PIL import Image, ImageGrab


@dataclass(frozen=True)
class WindowInfo:
    hwnd: int
    pid: int
    title: str
    rect: tuple[int, int, int, int]


_IS_WINDOWS = os.name == "nt"
_PET_EXE = "wuwatappo.exe"


class CaptureUnavailable(RuntimeError):
    """The desktop API supplied no useful visible pixels; do not infer state."""


class PrivilegeMismatch(RuntimeError):
    """Windows UIPI prevents this helper from controlling the elevated pet."""


_PRIVILEGE_PROBLEM = ("桌宠以管理员权限运行，当前助手权限不足，Windows 会阻止点击。"
                      "请使用管理员启动宝箱助手，并手动确认 Windows 的 UAC 提示。")

if _IS_WINDOWS:
    _user32 = ctypes.WinDLL("user32", use_last_error=True)
    _kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    _advapi32 = ctypes.WinDLL("advapi32", use_last_error=True)
    _shell32 = ctypes.WinDLL("shell32", use_last_error=True)

    _ENUMPROC = ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)
    _user32.EnumWindows.argtypes = [_ENUMPROC, wintypes.LPARAM]
    _user32.EnumWindows.restype = wintypes.BOOL
    _user32.IsWindow.argtypes = [wintypes.HWND]
    _user32.IsWindow.restype = wintypes.BOOL
    _user32.IsWindowVisible.argtypes = [wintypes.HWND]
    _user32.IsWindowVisible.restype = wintypes.BOOL
    _user32.IsIconic.argtypes = [wintypes.HWND]
    _user32.IsIconic.restype = wintypes.BOOL
    _user32.GetWindowRect.argtypes = [wintypes.HWND, ctypes.POINTER(wintypes.RECT)]
    _user32.GetWindowRect.restype = wintypes.BOOL
    _user32.GetWindowThreadProcessId.argtypes = [wintypes.HWND, ctypes.POINTER(wintypes.DWORD)]
    _user32.GetWindowThreadProcessId.restype = wintypes.DWORD
    _user32.GetWindowTextLengthW.argtypes = [wintypes.HWND]
    _user32.GetWindowTextLengthW.restype = ctypes.c_int
    _user32.GetWindowTextW.argtypes = [wintypes.HWND, wintypes.LPWSTR, ctypes.c_int]
    _user32.GetWindowTextW.restype = ctypes.c_int
    _user32.GetClassNameW.argtypes = [wintypes.HWND, wintypes.LPWSTR, ctypes.c_int]
    _user32.GetClassNameW.restype = ctypes.c_int
    _user32.ScreenToClient.argtypes = [wintypes.HWND, ctypes.POINTER(wintypes.POINT)]
    _user32.ScreenToClient.restype = wintypes.BOOL
    _user32.PostMessageW.argtypes = [wintypes.HWND, wintypes.UINT, wintypes.WPARAM, wintypes.LPARAM]
    _user32.PostMessageW.restype = wintypes.BOOL
    _user32.GetSystemMetrics.argtypes = [ctypes.c_int]
    _user32.GetSystemMetrics.restype = ctypes.c_int
    _user32.WindowFromPoint.argtypes = [wintypes.POINT]
    _user32.WindowFromPoint.restype = wintypes.HWND
    _user32.GetAncestor.argtypes = [wintypes.HWND, wintypes.UINT]
    _user32.GetAncestor.restype = wintypes.HWND
    _user32.GetTopWindow.argtypes = [wintypes.HWND]
    _user32.GetTopWindow.restype = wintypes.HWND
    _user32.GetWindow.argtypes = [wintypes.HWND, wintypes.UINT]
    _user32.GetWindow.restype = wintypes.HWND
    _user32.GetCursorPos.argtypes = [ctypes.POINTER(wintypes.POINT)]
    _user32.GetCursorPos.restype = wintypes.BOOL
    _user32.SetCursorPos.argtypes = [ctypes.c_int, ctypes.c_int]
    _user32.SetCursorPos.restype = wintypes.BOOL
    _user32.GetAsyncKeyState.argtypes = [ctypes.c_int]
    _user32.GetAsyncKeyState.restype = wintypes.SHORT
    _user32.OpenInputDesktop.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
    _user32.OpenInputDesktop.restype = wintypes.HANDLE
    _user32.CloseDesktop.argtypes = [wintypes.HANDLE]
    _user32.CloseDesktop.restype = wintypes.BOOL
    _user32.GetUserObjectInformationW.argtypes = [
        wintypes.HANDLE, ctypes.c_int, wintypes.LPVOID, wintypes.DWORD,
        ctypes.POINTER(wintypes.DWORD),
    ]
    _user32.GetUserObjectInformationW.restype = wintypes.BOOL
    _user32.SetWindowPos.argtypes = [
        wintypes.HWND, wintypes.HWND, ctypes.c_int, ctypes.c_int,
        ctypes.c_int, ctypes.c_int, wintypes.UINT,
    ]
    _user32.SetWindowPos.restype = wintypes.BOOL
    _kernel32.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
    _kernel32.OpenProcess.restype = wintypes.HANDLE
    _kernel32.QueryFullProcessImageNameW.argtypes = [
        wintypes.HANDLE, wintypes.DWORD, wintypes.LPWSTR,
        ctypes.POINTER(wintypes.DWORD),
    ]
    _kernel32.QueryFullProcessImageNameW.restype = wintypes.BOOL
    _kernel32.CloseHandle.argtypes = [wintypes.HANDLE]
    _kernel32.CloseHandle.restype = wintypes.BOOL
    _kernel32.GetTickCount.argtypes = []
    _kernel32.GetTickCount.restype = wintypes.DWORD

    _advapi32.OpenProcessToken.argtypes = [wintypes.HANDLE, wintypes.DWORD, ctypes.POINTER(wintypes.HANDLE)]
    _advapi32.OpenProcessToken.restype = wintypes.BOOL
    _advapi32.GetTokenInformation.argtypes = [
        wintypes.HANDLE, ctypes.c_int, wintypes.LPVOID, wintypes.DWORD, ctypes.POINTER(wintypes.DWORD),
    ]
    _advapi32.GetTokenInformation.restype = wintypes.BOOL
    _advapi32.IsValidSid.argtypes = [wintypes.LPVOID]
    _advapi32.IsValidSid.restype = wintypes.BOOL
    _advapi32.GetSidSubAuthorityCount.argtypes = [wintypes.LPVOID]
    _advapi32.GetSidSubAuthorityCount.restype = ctypes.POINTER(ctypes.c_ubyte)
    _advapi32.GetSidSubAuthority.argtypes = [wintypes.LPVOID, wintypes.DWORD]
    _advapi32.GetSidSubAuthority.restype = ctypes.POINTER(wintypes.DWORD)

    class _SID_AND_ATTRIBUTES(ctypes.Structure):
        _fields_ = [("Sid", wintypes.LPVOID), ("Attributes", wintypes.DWORD)]

    class _TOKEN_MANDATORY_LABEL(ctypes.Structure):
        _fields_ = [("Label", _SID_AND_ATTRIBUTES)]

    _shell32.ShellExecuteW.argtypes = [
        wintypes.HWND, wintypes.LPCWSTR, wintypes.LPCWSTR,
        wintypes.LPCWSTR, wintypes.LPCWSTR, ctypes.c_int,
    ]
    _shell32.ShellExecuteW.restype = wintypes.HINSTANCE

    class _LASTINPUTINFO(ctypes.Structure):
        _fields_ = [("cbSize", wintypes.UINT), ("dwTime", wintypes.DWORD)]

    _user32.GetLastInputInfo.argtypes = [ctypes.POINTER(_LASTINPUTINFO)]
    _user32.GetLastInputInfo.restype = wintypes.BOOL

    class _MOUSEINPUT(ctypes.Structure):
        _fields_ = [
            ("dx", wintypes.LONG), ("dy", wintypes.LONG),
            ("mouseData", wintypes.DWORD), ("dwFlags", wintypes.DWORD),
            ("time", wintypes.DWORD), ("dwExtraInfo", ctypes.c_size_t),
        ]

    class _INPUTUNION(ctypes.Union):
        _fields_ = [("mi", _MOUSEINPUT)]

    class _INPUT(ctypes.Structure):
        _anonymous_ = ("u",)
        _fields_ = [("type", wintypes.DWORD), ("u", _INPUTUNION)]

    _user32.SendInput.argtypes = [wintypes.UINT, ctypes.POINTER(_INPUT), ctypes.c_int]
    _user32.SendInput.restype = wintypes.UINT


def _require_windows() -> None:
    if not _IS_WINDOWS:
        raise RuntimeError("This helper requires Windows.")


def integrity_level(pid: int) -> int | None:
    """Read a process token's mandatory integrity RID; return None if unavailable."""
    _require_windows()
    process = _kernel32.OpenProcess(0x1000, False, int(pid))
    if not process:
        return None
    token = wintypes.HANDLE()
    try:
        if not _advapi32.OpenProcessToken(process, 0x0008, ctypes.byref(token)):  # TOKEN_QUERY
            return None
        size = wintypes.DWORD()
        _advapi32.GetTokenInformation(token, 25, None, 0, ctypes.byref(size))  # TokenIntegrityLevel
        if size.value < ctypes.sizeof(_TOKEN_MANDATORY_LABEL):
            return None
        buffer = ctypes.create_string_buffer(size.value)
        if not _advapi32.GetTokenInformation(token, 25, buffer, size.value, ctypes.byref(size)):
            return None
        sid = ctypes.cast(buffer, ctypes.POINTER(_TOKEN_MANDATORY_LABEL)).contents.Label.Sid
        if not sid or not _advapi32.IsValidSid(sid):
            return None
        count = _advapi32.GetSidSubAuthorityCount(sid)
        if not count or count.contents.value == 0:
            return None
        rid = _advapi32.GetSidSubAuthority(sid, count.contents.value-1)
        return int(rid.contents.value) if rid else None
    except (OSError, ValueError):
        return None
    finally:
        if token:
            _kernel32.CloseHandle(token)
        _kernel32.CloseHandle(process)


def input_privilege_problem(windows: list[WindowInfo] | None = None) -> str | None:
    """Explain a known UIPI mismatch without requesting or changing privileges."""
    _require_windows()
    if windows is None:
        windows = find_pet_windows()
    pet_pids = {window.pid for window in windows if _is_pet_pid(window.pid)}
    if not pet_pids:
        return None
    current = integrity_level(os.getpid())
    if current is None:
        return None
    for pid in sorted(pet_pids):
        target = integrity_level(pid)
        if target is not None and target > current:
            return _PRIVILEGE_PROBLEM
    return None


def relaunch_elevated(script: Path | str) -> bool:
    """Request standard Windows UAC; the user must approve it themselves.

    A successful return only means ShellExecute accepted the launch. It never
    approves UAC, edits permissions, or claims that the new window is ready.
    """
    _require_windows()
    try:
        if getattr(sys, "frozen", False):
            # A bundled application must relaunch its EXE, not the temporary
            # extracted .py path or an unrelated pythonw beside the EXE.
            executable = Path(sys.executable).resolve()
            if not executable.is_file():
                return False
            arguments = subprocess.list2cmdline(sys.argv[1:])
            directory = executable.parent
        else:
            script_path = Path(script).resolve()
            if not script_path.is_file():
                return False
            executable = Path(sys.executable)
            pythonw = executable.with_name("pythonw.exe")
            if pythonw.is_file():
                executable = pythonw
            arguments = subprocess.list2cmdline([str(script_path)])
            directory = script_path.parent
        result = _shell32.ShellExecuteW(None, "runas", str(executable), arguments, str(directory), 1)
        code = result if isinstance(result, int) else getattr(result, "value", 0)
        return bool(code and code > 32)
    except (OSError, ValueError):
        return False


def enable_dpi_awareness() -> None:
    """Request per-monitor V2 DPI coordinates, with older Windows fallbacks."""
    _require_windows()
    try:
        fn = _user32.SetProcessDpiAwarenessContext
        fn.argtypes = [ctypes.c_void_p]
        fn.restype = wintypes.BOOL
        if fn(ctypes.c_void_p(-4)):  # DPI_AWARENESS_CONTEXT_PER_MONITOR_AWARE_V2
            return
    except AttributeError:
        pass
    try:
        shcore = ctypes.WinDLL("shcore", use_last_error=True)
        fn = shcore.SetProcessDpiAwareness
        fn.argtypes = [ctypes.c_int]
        fn.restype = ctypes.c_long
        if fn(2) == 0:  # PROCESS_PER_MONITOR_DPI_AWARE
            return
    except (AttributeError, OSError):
        pass
    # Already configured processes may reject a process-wide request.  A
    # thread context still makes this caller's geometry use physical pixels.
    try:
        fn = _user32.SetThreadDpiAwarenessContext
        fn.argtypes = [ctypes.c_void_p]
        fn.restype = ctypes.c_void_p
        if fn(ctypes.c_void_p(-4)):
            return
    except AttributeError:
        pass
    try:
        _user32.SetProcessDPIAware.argtypes = []
        _user32.SetProcessDPIAware.restype = wintypes.BOOL
        _user32.SetProcessDPIAware()
    except AttributeError:
        pass


def _pid(hwnd: int) -> int:
    pid = wintypes.DWORD()
    _user32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
    return int(pid.value)


def _is_pet_pid(pid: int) -> bool:
    if not pid:
        return False
    handle = _kernel32.OpenProcess(0x1000, False, pid)
    if not handle:
        return False
    try:
        size = wintypes.DWORD(32768)
        path = ctypes.create_unicode_buffer(size.value)
        if not _kernel32.QueryFullProcessImageNameW(handle, 0, path, ctypes.byref(size)):
            return False
        return PureWindowsPath(path.value).name.casefold() == _PET_EXE
    finally:
        _kernel32.CloseHandle(handle)


def _window_rect(hwnd: int) -> tuple[int, int, int, int] | None:
    rect = wintypes.RECT()
    if not _user32.GetWindowRect(hwnd, ctypes.byref(rect)):
        return None
    result = (int(rect.left), int(rect.top), int(rect.right), int(rect.bottom))
    if result[2] <= result[0] or result[3] <= result[1]:
        return None
    return result


def find_pet_windows() -> list[WindowInfo]:
    """Find all visible WuwaTappo.exe windows, including unlabelled popups."""
    _require_windows()
    windows: list[WindowInfo] = []
    checked_pids: dict[int, bool] = {}

    @_ENUMPROC
    def callback(hwnd: int, _lparam: int) -> bool:
        if not _user32.IsWindowVisible(hwnd) or _user32.IsIconic(hwnd):
            return True
        pid = _pid(hwnd)
        if pid not in checked_pids:
            checked_pids[pid] = _is_pet_pid(pid)
        if not checked_pids[pid]:
            return True
        rect = _window_rect(hwnd)
        if rect is None:
            return True
        title = ctypes.create_unicode_buffer(_user32.GetWindowTextLengthW(hwnd) + 1)
        _user32.GetWindowTextW(hwnd, title, len(title))
        windows.append(WindowInfo(int(hwnd), pid, title.value, rect))
        return True

    if not _user32.EnumWindows(callback, 0):
        error = ctypes.get_last_error()
        if error:
            raise ctypes.WinError(error)
        raise RuntimeError(
            "Windows could not enumerate this desktop's windows. "
            "Run the helper in the normal unlocked desktop session."
        )
    return windows


def virtual_rect() -> tuple[int, int, int, int]:
    _require_windows()
    left = _user32.GetSystemMetrics(76)  # SM_XVIRTUALSCREEN
    top = _user32.GetSystemMetrics(77)
    return (left, top, left + _user32.GetSystemMetrics(78),
            top + _user32.GetSystemMetrics(79))


def capture_desktop() -> tuple[Image.Image, tuple[int, int]]:
    """Capture visible pixels on all monitors, including layered pet windows."""
    _require_windows()
    if not is_desktop_available():
        raise RuntimeError("The interactive desktop is locked or unavailable.")
    left, top, right, bottom = virtual_rect()
    image = ImageGrab.grab(all_screens=True, include_layered_windows=True).convert("RGB")
    if image.size != (right - left, bottom - top):
        raise CaptureUnavailable("Display geometry changed; retry the screen capture.")
    if all(low == high for low, high in image.getextrema()):
        raise CaptureUnavailable("当前截图只有纯色背景，暂时无法读取桌面画面。")
    return image, (left, top)


def point_belongs_to_pet(x: int, y: int) -> bool:
    _require_windows()
    hwnd = _user32.WindowFromPoint(wintypes.POINT(int(x), int(y)))
    return bool(hwnd and _is_pet_pid(_pid(hwnd)))


def window_is_current(window: WindowInfo) -> bool:
    """Reject stale HWNDs, moved windows, hidden windows, and minimized apps."""
    _require_windows()
    return bool(
        _user32.IsWindow(window.hwnd)
        and _user32.IsWindowVisible(window.hwnd)
        and not _user32.IsIconic(window.hwnd)
        and _pid(window.hwnd) == window.pid
        and _is_pet_pid(window.pid)
        and _window_rect(window.hwnd) == window.rect
    )


def is_desktop_available() -> bool:
    """Only allow the ordinary unlocked input desktop, never a secure one."""
    _require_windows()
    desktop = _user32.OpenInputDesktop(0, False, 0x0001)  # DESKTOP_READOBJECTS
    if not desktop:
        return False
    try:
        name = ctypes.create_unicode_buffer(256)
        needed = wintypes.DWORD()
        if not _user32.GetUserObjectInformationW(
            desktop, 2, name, ctypes.sizeof(name), ctypes.byref(needed)
        ):
            return False
        return name.value.casefold() == "default"
    finally:
        _user32.CloseDesktop(desktop)


def cursor_pos() -> tuple[int, int]:
    _require_windows()
    point = wintypes.POINT()
    if not _user32.GetCursorPos(ctypes.byref(point)):
        raise ctypes.WinError(ctypes.get_last_error())
    return int(point.x), int(point.y)


def user_idle_seconds() -> float:
    _require_windows()
    info = _LASTINPUTINFO(ctypes.sizeof(_LASTINPUTINFO), 0)
    if not _user32.GetLastInputInfo(ctypes.byref(info)):
        return 0.0
    # GetTickCount and dwTime are DWORD and wrap after ~49 days.
    return ((_kernel32.GetTickCount() - info.dwTime) & 0xFFFFFFFF) / 1000.0


def hotkey_down(vk: int) -> bool:
    _require_windows()
    return bool(_user32.GetAsyncKeyState(int(vk)) & 0x8000)


def _point_hits_window(x: int, y: int, window: WindowInfo) -> bool:
    left, top, right, bottom = window.rect
    if not (left <= x < right and top <= y < bottom):
        return False
    hwnd = _user32.WindowFromPoint(wintypes.POINT(x, y))
    if not hwnd or _pid(hwnd) != window.pid:
        return False
    root = _user32.GetAncestor(hwnd, 2)  # GA_ROOT, includes child controls.
    return int(root or hwnd) == window.hwnd


def point_hits_window(x: int, y: int, window: WindowInfo) -> bool:
    """Check a match against the exact live pet HWND that would receive input.

    Multiple WuwaTappo windows can all span the whole desktop.  Their shared
    screenshot rectangles do not establish which window owns a button.  This
    check rejects other pet windows as well as unrelated occluding apps.
    """
    _require_windows()
    return window_is_current(window) and _point_hits_window(int(x), int(y), window)


def _window_z_order() -> list[int] | None:
    """Read one bounded top-level window chain; incomplete queries are unknown."""
    current = _user32.GetTopWindow(None)
    if not current:
        return None
    order, seen = [], set()
    for _ in range(4096):
        hwnd = int(current)
        if hwnd in seen or not _user32.IsWindow(hwnd):
            return None
        seen.add(hwnd)
        order.append(hwnd)
        ctypes.set_last_error(0)
        current = _user32.GetWindow(hwnd, 2)  # GW_HWNDNEXT: the window below.
        if not current:
            return None if ctypes.get_last_error() else order
    return None


def can_observe_disappearance(rect: tuple[int, int, int, int], window: WindowInfo) -> bool:
    """Allow absent pixels where the pet has become transparent after a click.

    This is only absence evidence, never permission to click.  Input may fall
    through to a window below the pet, but an owner above it or a visible
    foreign overlay covering any part of the icon leaves the result unknown.
    """
    _require_windows()
    try:
        left, top, right, bottom = rect
        wl, wt, wr, wb = window.rect
        if not (wl <= left < right <= wr and wt <= top < bottom <= wb):
            return False
        if not is_desktop_available() or not window_is_current(window):
            return False
        owner = _user32.WindowFromPoint(wintypes.POINT((left+right)//2, (top+bottom)//2))
        if not owner:
            return False
        root = _user32.GetAncestor(owner, 2)  # GA_ROOT, not GA_ROOTOWNER.
        if not root:
            return False
        root = int(root)
        order = _window_z_order()
        if order is None or window.hwnd not in order or root not in order:
            return False
        target_index = order.index(window.hwnd)
        if order.index(root) < target_index:
            return False
        for hwnd in order[:target_index]:
            if not _user32.IsWindowVisible(hwnd) or _user32.IsIconic(hwnd):
                continue
            pid = _pid(hwnd)
            if not pid:
                return False
            # The pet has multiple full-desktop transparent layers.  Their
            # rectangles alone cannot establish visual occlusion; an actual
            # point owner above the clicked layer has already been rejected.
            if pid == window.pid:
                continue
            bounds = _window_rect(hwnd)
            if bounds is None:
                return False
            a, b, c, d = bounds
            if left < c and a < right and top < d and b < bottom:
                return False
        return window_is_current(window) and is_desktop_available()
    except (OSError, ValueError):
        return False


def _wait_on_target(seconds: float, x: int, y: int, window: WindowInfo,
                    should_cancel: Callable[[], bool] | None) -> bool:
    deadline = time.monotonic() + seconds
    while True:
        if (should_cancel is not None and should_cancel()) or cursor_pos() != (x, y):
            return False
        if not is_desktop_available() or not window_is_current(window) or not _point_hits_window(x, y, window):
            return False
        remaining = deadline-time.monotonic()
        if remaining <= 0:
            return True
        time.sleep(min(.01, remaining))


def _send_button(flag: int) -> None:
    event = _INPUT()
    event.type = 0
    event.mi.dwFlags = flag
    if _user32.SendInput(1, ctypes.byref(event), ctypes.sizeof(_INPUT)) != 1:
        raise OSError(ctypes.get_last_error(), "SendInput could not send the mouse event")


def _window_class(hwnd: int) -> str:
    text = ctypes.create_unicode_buffer(256)
    _user32.GetClassNameW(hwnd, text, len(text))
    return text.value


def _post_mouse_button(window: WindowInfo, x: int, y: int, flag: int) -> None:
    point = wintypes.POINT(x, y)
    if not _user32.ScreenToClient(window.hwnd, ctypes.byref(point)):
        raise ctypes.WinError(ctypes.get_last_error())
    message = {0: 0x0200, 0x0002: 0x0201, 0x0004: 0x0202}[flag]
    lparam = (point.x & 0xFFFF) | ((point.y & 0xFFFF) << 16)
    if not _user32.PostMessageW(window.hwnd, message, 1 if flag == 0x0002 else 0, lparam):
        raise ctypes.WinError(ctypes.get_last_error())


def safe_click(x: int, y: int, window: WindowInfo,
               should_cancel: Callable[[], bool] | None = None) -> bool:
    """Single-click only a still-current, unobstructed pet window.

    Returns False when any precondition changes.  SendInput failure raises an
    OSError (for example UIPI integrity restrictions); callers should pause.
    Down/up are separated by 100ms so frame-based input polling can see them.
    No foreground activation is used.  The old pointer position is restored
    only if the user has not moved it during the click.
    """
    _require_windows()
    x, y = int(x), int(y)
    if should_cancel is not None and should_cancel():
        return False
    problem = input_privilege_problem([window])
    if problem:
        raise PrivilegeMismatch(problem)
    # Avoid interrupting a drag, or turning this into a modified Ctrl/Alt click.
    if any(hotkey_down(key) for key in (0x01, 0x02, 0x04, 0x10, 0x11, 0x12, 0x5B, 0x5C)):
        return False
    if not is_desktop_available() or not window_is_current(window):
        return False
    if not _point_hits_window(x, y, window):
        return False
    original = cursor_pos()
    # Use ordinary Windows input after the integrity check. Windows performs
    # native hit testing and routes through the pet's overlay/WebView layers.
    send_button = _send_button
    if not _user32.SetCursorPos(x, y):
        return False
    try:
        # Let the app process mouse movement before it sees the button event.
        if not _wait_on_target(.10, x, y, window, should_cancel):
            return False
        # Recheck user-held keys after the hover delay, before pressing down.
        if any(hotkey_down(key) for key in (0x01, 0x02, 0x04, 0x10, 0x11, 0x12, 0x5B, 0x5C)):
            return False
        pressed = False
        try:
            send_button(0x0002)
            pressed = True
            if not _wait_on_target(.10, x, y, window, should_cancel):
                return False
        finally:
            if pressed:
                try:
                    send_button(0x0004)
                except OSError:
                    # One extra release attempt prevents a transient failure
                    # leaving the mouse button down, while preserving the error.
                    send_button(0x0004)
                    raise
        # Do not move away before the application processes mouse-up.
        _wait_on_target(.15, x, y, window, should_cancel)
        return True
    finally:
        if cursor_pos() == (x, y):
            _user32.SetCursorPos(*original)


def place_overlay(hwnd: int, rect: tuple[int, int, int, int]) -> None:
    """Position a temporary Tk capture overlay over the virtual desktop."""
    _require_windows()
    left, top, right, bottom = (int(value) for value in rect)
    if right <= left or bottom <= top:
        raise ValueError("Overlay rectangle must have positive dimensions.")
    root = _user32.GetAncestor(int(hwnd), 2) or int(hwnd)
    if not _user32.SetWindowPos(
        root, wintypes.HWND(-1), left, top, right - left, bottom - top,
        0x0010 | 0x0040,  # SWP_NOACTIVATE | SWP_SHOWWINDOW
    ):
        raise ctypes.WinError(ctypes.get_last_error())
