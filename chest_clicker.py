"""Launch the user's bundled MouseClickTool without enabling its clicking."""
from __future__ import annotations

import ctypes
from ctypes import wintypes
import hashlib
import logging
import os
from pathlib import Path
import subprocess
import time
from clicker_job import ProcessGroup


CLICKER_VERSION = "3.3"
CLICKER_SHA256 = "e4b07ea0a1cebc18e825c0ad3bc9bfdadbcb1b5b30e26cf05e00f5409da6bf60"
CLICKER_RELATIVE_PATH = Path("bundled_tools") / "MouseClickTool.exe"


def bundled_clicker_path(root=None):
    # __file__ is inside _internal in the folder-based frozen distribution.
    return (Path(root) if root is not None else Path(__file__).resolve().parent) / CLICKER_RELATIVE_PATH


def verify_clicker(root=None):
    path = bundled_clicker_path(root)
    try:
        with path.open("rb") as stream:
            digest = hashlib.file_digest(stream, "sha256").hexdigest()
    except OSError as exc:
        raise RuntimeError("内置连点器文件缺失或不可读，请完整解压新版程序包。") from exc
    if digest != CLICKER_SHA256:
        raise RuntimeError("内置连点器文件校验失败，请重新解压完整程序包。")
    return {"version": CLICKER_VERSION, "sha256": digest, "bytes": path.stat().st_size,
            "verified": True, "launched": False}


def _request_window_close(pid):
    """Ask only windows of the exact process started here to close normally."""
    user32 = ctypes.WinDLL("user32", use_last_error=True)
    callback_type = ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)
    user32.GetWindowThreadProcessId.argtypes = [wintypes.HWND, ctypes.POINTER(wintypes.DWORD)]
    user32.PostMessageW.argtypes = [wintypes.HWND, wintypes.UINT, wintypes.WPARAM, wintypes.LPARAM]
    user32.PostMessageW.restype = wintypes.BOOL
    user32.EnumWindows.argtypes = [callback_type, wintypes.LPARAM]
    user32.EnumWindows.restype = wintypes.BOOL

    @callback_type
    def visit(hwnd, _):
        owner = wintypes.DWORD()
        user32.GetWindowThreadProcessId(hwnd, ctypes.byref(owner))
        if owner.value == pid:
            user32.PostMessageW(hwnd, 0x0010, 0, 0)  # WM_CLOSE
        return True

    user32.EnumWindows(visit, 0)


def _release_mouse_buttons():
    # The original tool supports press-and-hold, but its FormClosing handler
    # does not send mouse-up. Release only after every owned process exits.
    from chest_windows import _send_button
    for flag in (0x0004, 0x0010, 0x0040):  # LEFTUP, RIGHTUP, MIDDLEUP
        _send_button(flag)


class ClickerProcess:
    """Own one launched process; never enumerate or stop unrelated clickers."""
    def __init__(self, root=None):
        self.root = root
        self.process = None
        self.group = None
        self.close_requested_at = None

    @property
    def running(self):
        if self.process is not None:
            self.process.poll()  # Reap the original wrapper even after restart.
        return bool(self.group and self.group.pids())

    def launch(self, data_directory):
        if self.running:
            return False
        if os.name != "nt":
            raise RuntimeError("内置连点器仅支持 Windows。")
        verify_clicker(self.root)
        if self.group is not None:
            self.group.close()
        working = Path(data_directory) / "clicker"
        working.mkdir(parents=True, exist_ok=True)
        # List arguments preserve spaces/Unicode; no shell or temporary EXE.
        self.group = ProcessGroup()
        self.process = None
        try:
            self.process = subprocess.Popen([str(bundled_clicker_path(self.root))],
                                            cwd=working, close_fds=True)
            self.group.assign(self.process)
        except Exception:
            if self.process is not None and self.process.poll() is None:
                self.process.terminate()
                self.process.wait(timeout=5)
            self.group.close()
            self.group = None
            raise
        self.close_requested_at = None
        return True

    def request_close(self):
        if not self.running or self.close_requested_at is not None:
            return
        self.close_requested_at = time.monotonic()
        try:
            for pid in self.group.pids():
                _request_window_close(pid)
        except OSError:
            # poll() retains responsibility for terminating an unresponsive
            # owned process; GUI callbacks remain nonblocking.
            pass

    def poll(self):
        if (self.running and self.close_requested_at is not None
                and time.monotonic()-self.close_requested_at >= 2.):
            try:
                self.group.terminate()
            except ProcessLookupError:
                pass
        active = self.running
        if not active and self.group is not None:
            self.group.close()
            self.group = None
            try:
                _release_mouse_buttons()
            except OSError:
                logging.warning("连点器已关闭，当前桌面无法补发鼠标抬起事件。", exc_info=True)
        return active
