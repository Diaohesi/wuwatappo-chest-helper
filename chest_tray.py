"""Windows notification-area icon. All native messages use Tk's existing UI thread."""

import ctypes as C
from ctypes import wintypes as W
import logging
import os
import queue

from chest_windows import integrity_level


class _IconData(C.Structure):
    _fields_ = [("size", W.DWORD), ("hwnd", W.HWND), ("id", W.UINT), ("flags", W.UINT),
                ("callback", W.UINT), ("icon", W.HICON), ("tip", W.WCHAR * 128),
                ("state", W.DWORD), ("mask", W.DWORD), ("info", W.WCHAR * 256),
                ("version", W.UINT), ("title", W.WCHAR * 64), ("info_flags", W.DWORD),
                ("guid", C.c_byte * 16), ("balloon_icon", W.HICON)]


def _api(dll, name, result, *args):
    fn = getattr(dll, name)
    fn.restype, fn.argtypes = result, args
    return fn


_user = C.WinDLL("user32", use_last_error=True)
_com = C.WinDLL("comctl32", use_last_error=True)
_proc = C.WINFUNCTYPE(W.LPARAM, W.HWND, W.UINT, W.WPARAM, W.LPARAM, W.WPARAM, W.WPARAM)
_create = _api(_user, "CreateWindowExW", W.HWND, W.DWORD, W.LPCWSTR, W.LPCWSTR, W.DWORD,
               C.c_int, C.c_int, C.c_int, C.c_int, W.HWND, W.HMENU, W.HINSTANCE, C.c_void_p)
_destroy = _api(_user, "DestroyWindow", W.BOOL, W.HWND)
_register = _api(_user, "RegisterWindowMessageW", W.UINT, W.LPCWSTR)
_filter = _api(_user, "ChangeWindowMessageFilterEx", W.BOOL, W.HWND, W.UINT, W.DWORD, C.c_void_p)
_subclass = _api(_com, "SetWindowSubclass", W.BOOL, W.HWND, _proc, W.WPARAM, W.WPARAM)
_unsubclass = _api(_com, "RemoveWindowSubclass", W.BOOL, W.HWND, _proc, W.WPARAM)
_default = _api(_com, "DefSubclassProc", W.LPARAM, W.HWND, W.UINT, W.WPARAM, W.LPARAM)
_notify = _api(C.WinDLL("shell32"), "Shell_NotifyIconW", W.BOOL, W.DWORD, C.POINTER(_IconData))
_ancestor = _api(_user, "GetAncestor", W.HWND, W.HWND, W.UINT)
_send = _api(_user, "SendMessageW", W.LPARAM, W.HWND, W.UINT, W.WPARAM, W.LPARAM)
_load_icon = _api(_user, "LoadIconW", W.HICON, W.HINSTANCE, C.c_void_p)
_menu = _api(_user, "CreatePopupMenu", W.HMENU)
_append = _api(_user, "AppendMenuW", W.BOOL, W.HMENU, W.UINT, W.WPARAM, W.LPCWSTR)
_track = _api(_user, "TrackPopupMenu", W.UINT, W.HMENU, W.UINT, C.c_int, C.c_int, C.c_int, W.HWND, C.c_void_p)
_destroy_menu = _api(_user, "DestroyMenu", W.BOOL, W.HMENU)
_foreground = _api(_user, "SetForegroundWindow", W.BOOL, W.HWND)
_post = _api(_user, "PostMessageW", W.BOOL, W.HWND, W.UINT, W.WPARAM, W.LPARAM)
_cursor = _api(_user, "GetCursorPos", W.BOOL, C.POINTER(W.POINT))

CALLBACK, ICON_ID = 0x8001, 1
NIM_ADD, NIM_DELETE, NIM_SETVERSION = 0, 2, 4
WM_CONTEXTMENU, NIN_SELECT, NIN_KEYSELECT = 0x7B, 0x400, 0x401


class RestrictedTrayError(PermissionError):
    """Explorer refused registration from a low-integrity process."""


class TrayIcon:
    def __init__(self, tk_hwnd, events):
        self.events = events
        self.integrity = integrity_level(os.getpid())
        self.recreated = _register("TaskbarCreated")
        self.handler = _proc(self._message)
        # A hidden top-level (not message-only) window receives Explorer's restart broadcast.
        self.hwnd = _create(0x80, "STATIC", None, 0x80000000, 0, 0, 0, 0, None, None, None, None)
        if not self.hwnd:
            raise C.WinError(C.get_last_error())
        self.data = _IconData(size=C.sizeof(_IconData), hwnd=self.hwnd, id=ICON_ID,
                              flags=1 | 2 | 4 | 0x80, callback=CALLBACK, version=4,
                              tip="WuwaTappo 宝箱助手")
        try:
            parent = _ancestor(tk_hwnd, 2)  # GA_ROOT; borrow the existing Tk icon.
            self.data.icon = (_send(parent, 0x7F, 2, 0) or _send(parent, 0x7F, 1, 0)
                              or _load_icon(None, 32512))
            if not self.recreated or not _subclass(self.hwnd, self.handler, 1, 0):
                raise C.WinError(C.get_last_error())
            if self.integrity is None or self.integrity > 0x2000:
                # Only higher-integrity windows need to allow messages from normal Explorer.
                for msg in (self.recreated, CALLBACK):
                    if not _filter(self.hwnd, msg, 1, None):
                        raise C.WinError(C.get_last_error())
            self._add()
        except Exception:
            self.close()
            raise

    def _add(self):
        if not (_notify(NIM_ADD, C.byref(self.data)) and _notify(NIM_SETVERSION, C.byref(self.data))):
            if self.integrity is not None and self.integrity < 0x2000:
                raise RestrictedTrayError("Windows 限制了当前进程访问系统托盘，主窗口将保持显示。")
            raise OSError("Windows 通知区域暂不可用。")

    def _message(self, hwnd, msg, wp, lp, _id, _ref):
        try:
            if msg == self.recreated:
                self._add()
                return 0
            if msg == CALLBACK and (lp >> 16) & 0xffff == ICON_ID:
                event = lp & 0xffff
                if event in (0x202, NIN_SELECT, NIN_KEYSELECT):  # WM_LBUTTONUP / keyboard selection
                    self.events.put(("tray_restore", self))
                elif event == WM_CONTEXTMENU:
                    self._popup(C.c_short(wp).value, C.c_short(wp >> 16).value)
                return 0
        except Exception:
            logging.exception("托盘暂不可用，恢复主窗口")
            self.events.put(("tray_restore", self))
            return 0
        return _default(hwnd, msg, wp, lp)

    def _popup(self, x, y):
        menu = _menu()
        if not menu:
            raise C.WinError(C.get_last_error())
        try:
            if not (_append(menu, 0, 1, "显示主窗口") and _append(menu, 0, 2, "退出助手")):
                raise C.WinError(C.get_last_error())
            if (x, y) == (-1, -1):
                point = W.POINT()
                _cursor(C.byref(point))
                x, y = point.x, point.y
            _foreground(self.hwnd)
            command = _track(menu, 0x100 | 0x80 | 2, x, y, 0, self.hwnd, None)
            _post(self.hwnd, 0, 0, 0)  # Required to dismiss a notification-area menu reliably.
            if command in (1, 2):
                self.events.put(("tray_restore" if command == 1 else "tray_exit", self))
        finally:
            _destroy_menu(menu)

    def close(self):
        if self.hwnd:
            _notify(NIM_DELETE, C.byref(self.data))
            _unsubclass(self.hwnd, self.handler, 1)
            _destroy(self.hwnd)
            self.hwnd = None
            self.handler = None


def self_test(tk_hwnd):
    # Build runners can have a usable Tk desktop without a notification area.
    find = _api(_user, "FindWindowW", W.HWND, W.LPCWSTR, W.LPCWSTR)
    if not find("Shell_TrayWnd", None):
        return "shell_unavailable"
    events = queue.SimpleQueue()
    try:
        icon = TrayIcon(tk_hwnd, events)
    except RestrictedTrayError:
        return "restricted_process"
    try:
        _send(icon.hwnd, CALLBACK, 0, ICON_ID << 16 | NIN_KEYSELECT)
        if events.get_nowait() != ("tray_restore", icon):
            raise RuntimeError("Tray callback self-test failed")
        return "registered_and_received_callback"
    finally:
        icon.close()
