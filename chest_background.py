"""Window capture and message input for the tested WuwaTappo overlay.

The MaaFw capture engine runs only in a separate process while background
mode is active. This module never moves the real pointer, changes foreground
focus, moves another window, or falls back to physical input.
"""
from __future__ import annotations

import ctypes
from ctypes import wintypes
from dataclasses import dataclass
import hashlib
import logging
import multiprocessing
from pathlib import Path
import sys
import threading
import time
from typing import Callable

from PIL import Image

import chest_windows as desktop


SDK_VERSION = "5.16.0"


@dataclass
class BackgroundFrame:
    image: Image.Image
    render: desktop.WindowInfo
    input: desktop.WindowInfo
    digest: str
    changed: bool
    origin: tuple[int, int] | None = None
    monitor: desktop.displays.Monitor | None = None


if desktop._IS_WINDOWS:
    _user32 = desktop._user32
    _user32.GetForegroundWindow.argtypes = []
    _user32.GetForegroundWindow.restype = wintypes.HWND
    _user32.ClientToScreen.argtypes = [wintypes.HWND, ctypes.POINTER(wintypes.POINT)]
    _user32.ClientToScreen.restype = wintypes.BOOL
    _user32.GetClientRect.argtypes = [wintypes.HWND, ctypes.POINTER(wintypes.RECT)]
    _user32.GetClientRect.restype = wintypes.BOOL
    _user32.SendMessageTimeoutW.argtypes = [
        wintypes.HWND, wintypes.UINT, wintypes.WPARAM, wintypes.LPARAM,
        wintypes.UINT, wintypes.UINT, ctypes.POINTER(ctypes.c_size_t),
    ]
    _user32.SendMessageTimeoutW.restype = ctypes.c_ssize_t


def client_origin(window: desktop.WindowInfo) -> tuple[int, int]:
    """Return physical screen coordinates of this window's client origin."""
    desktop._require_windows()
    point = wintypes.POINT(0, 0)
    if not _user32.ClientToScreen(window.hwnd, ctypes.byref(point)):
        raise desktop.CaptureUnavailable("无法读取桌宠窗口坐标，等待窗口恢复。")
    return int(point.x), int(point.y)


def _client_size(window: desktop.WindowInfo) -> tuple[int, int]:
    rect = wintypes.RECT()
    if not _user32.GetClientRect(window.hwnd, ctypes.byref(rect)):
        raise desktop.CaptureUnavailable("无法读取桌宠窗口大小。")
    size = int(rect.right-rect.left), int(rect.bottom-rect.top)
    if min(size) <= 0:
        raise desktop.CaptureUnavailable("桌宠窗口当前没有可读取的画面。")
    return size


def _same_identity(window: desktop.WindowInfo) -> bool:
    return bool(_user32.IsWindow(window.hwnd)
                and desktop._pid(window.hwnd) == window.pid
                and desktop._is_pet_pid(window.pid)
                and desktop._window_class(window.hwnd) == "DesktopCatInput")


def _input_is_current(window: desktop.WindowInfo) -> bool:
    return bool(desktop.window_is_current(window)
                and desktop._window_class(window.hwnd) == "DesktopCatInput")


def _pair_is_current(render, input_window) -> bool:
    return bool(render.pid == input_window.pid
                and render.title == "Wuwa Tappo"
                and desktop.window_is_current(render)
                and _input_is_current(input_window))


def find_background_pairs(windows: list[desktop.WindowInfo]):
    """Pair unique render/input layers in each verified pet process.

    Render and native input windows can have identical desktop rectangles;
    geometry alone is never used to identify either layer.
    """
    desktop._require_windows()
    pairs = []
    for pid in sorted({window.pid for window in windows}):
        renders = [window for window in windows
                   if window.pid == pid and window.title == "Wuwa Tappo"]
        inputs = [window for window in windows
                  if window.pid == pid and window.title == "DesktopCatInput"
                  and desktop._window_class(window.hwnd) == "DesktopCatInput"]
        if len(inputs) != 1:
            continue  # Multiple native input layers in one process remain ambiguous.
        target = inputs[0]
        if len(renders) > 1:
            # Some pets create a render window per display. Select only an unambiguous
            # window that contains the input layer; do not pick an arbitrary screen.
            renders = [r for r in renders if desktop.displays.intersection(r.rect, target.rect) == target.rect]
        if len(renders) == 1 and _pair_is_current(renders[0], target):
            pairs.append((renders[0], target))
    return pairs


def _wait_job(job, timeout=7.0):
    deadline = time.monotonic()+timeout
    while not job.done:
        if time.monotonic() >= deadline:
            raise TimeoutError("后台窗口截图超时。")
        time.sleep(.02)
    if not job.succeeded:
        raise RuntimeError("后台窗口截图连接失败。")


def _capture_worker(connection, render):
    """Capture-only spawn target; the parent can end any native call safely."""
    try:
        desktop.enable_dpi_awareness()
        if not desktop.is_desktop_available() or not desktop.window_is_current(render):
            raise RuntimeError("桌面锁定或桌宠窗口已变化。")
        # No SDK, native model or GPU capture is loaded in the normal GUI.
        from maa.controller import Win32Controller
        from maa.define import MaaWin32InputMethodEnum as Input
        from maa.define import MaaWin32ScreencapMethodEnum as Capture

        controller = Win32Controller(
            render.hwnd, screencap_method=Capture.FramePool,
            mouse_method=Input.Null, keyboard_method=Input.Null,
        )
        if not controller.set_screenshot_use_raw_size(True):
            raise RuntimeError("无法使用后台截图的原始像素大小。")
        _wait_job(controller.post_connection())
        while True:
            command = connection.recv()
            if command == "close":
                return
            if command != "grab":
                raise RuntimeError("Unknown capture command")
            if not desktop.is_desktop_available() or not desktop.window_is_current(render):
                raise RuntimeError("桌面锁定或桌宠窗口已变化。")
            expected_size = _client_size(render)
            _wait_job(controller.post_screencap())
            bgr = controller.cached_image
            if bgr is None or bgr.ndim != 3 or bgr.shape[2] != 3:
                raise RuntimeError("后台截图没有提供有效像素。")
            image = Image.fromarray(bgr[:, :, ::-1].copy())
            if image.size != expected_size or not desktop.window_is_current(render):
                raise RuntimeError("桌宠窗口大小已变化。")
            if all(low == high for low, high in image.getextrema()):
                raise RuntimeError("后台截图只有纯色，无法判断宝箱状态。")
            connection.send({"ok": True, "size": image.size, "rgb": image.tobytes()})
            del image, bgr
    except (EOFError, BrokenPipeError):
        pass
    except BaseException as exc:
        try:
            connection.send({"ok": False, "error": f"{type(exc).__name__}: {exc}"})
        except (EOFError, BrokenPipeError, OSError):
            pass
    finally:
        connection.close()
        # A native capture destructor can wait indefinitely. The process owns
        # no input state; its parent always joins/terminates it with a bound.


def _runtime_self_test_worker(connection):
    """Load the packaged SDK libraries without creating a controller/window."""
    report = {"ok": False, "expected_sdk_version": SDK_VERSION}
    try:
        desktop._require_windows()
        from importlib.metadata import version

        report["sdk_version"] = version("MaaFw").lstrip("v")
        if report["sdk_version"] != SDK_VERSION:
            raise RuntimeError(f"需要 MaaFw {SDK_VERSION}，当前为 {report['sdk_version']}。")
        from maa.library import Library

        # Importing maa only configures paths. Explicitly load the three DLLs
        # required by this backend; do not connect to any external window.
        Library.framework()
        Library.toolkit()
        binary_dir = Path(Library.framework_libpath).resolve().parent
        win32_library = ctypes.WinDLL(
            str(binary_dir / "MaaWin32ControlUnit.dll"),
            winmode=0x00000100 | 0x00001000,
        )
        report["framework_version"] = Library.version().lstrip("v")
        if report["framework_version"] != SDK_VERSION:
            raise RuntimeError("MaaFramework DLL 版本与已固定的运行库版本不一致。")
        report["win32_ready"] = bool(win32_library)
        report["ok"] = report["win32_ready"]
    except BaseException as exc:
        report["error"] = f"{type(exc).__name__}: {exc}"
    try:
        connection.send(report)
    except (EOFError, BrokenPipeError, OSError):
        pass
    finally:
        connection.close()


def runtime_self_test(timeout=15.0) -> dict:
    """Check bundled native dependencies in a bounded, capture-free process."""
    process = parent = child = None
    try:
        if not 0 < float(timeout) <= 60:
            raise ValueError("自检超时必须介于 0 到 60 秒之间。")
        context = multiprocessing.get_context("spawn")
        parent, child = context.Pipe(duplex=False)
        process = context.Process(target=_runtime_self_test_worker, args=(child,),
                                  name="chest-background-self-test", daemon=True)
        process.start()
        child.close()
        if not parent.poll(float(timeout)):
            return {"ok": False, "error": "后台运行库自检超时。"}
        report = parent.recv()
        if not isinstance(report, dict) or not isinstance(report.get("ok"), bool):
            return {"ok": False, "error": "后台运行库自检返回无效结果。"}
        return report
    except Exception as exc:
        return {"ok": False, "error": f"{type(exc).__name__}: {exc}"}
    finally:
        for connection in (parent, child):
            if connection is not None:
                connection.close()
        if process is not None:
            # A native DLL teardown can hang even after it sent a result.
            # Always bound cleanup, just as with the capture-only process.
            try:
                if process.is_alive():
                    process.terminate()
                process.join(timeout=.5)
                if process.is_alive():
                    process.kill()
                    process.join(timeout=.5)
                if not process.is_alive():
                    process.close()
            except (AssertionError, ValueError):
                # Process.start may have failed before an OS handle existed.
                pass


class BackgroundCapture:
    """One capture-only process, reused for the same render/input identity."""
    def __init__(self, timeout=12.0, poll_interval=.05):
        self.timeout = timeout
        self.poll_interval = poll_interval
        self._mutex = threading.RLock()
        self._process = self._connection = self._key = self._digest = None

    def _close_locked(self):
        process, connection = self._process, self._connection
        self._process = self._connection = self._key = self._digest = None
        if connection is not None:
            try:
                connection.close()
            except OSError:
                pass
        if process is not None:
            if process.is_alive():
                process.terminate()
            process.join(timeout=.5)
            if process.is_alive():
                process.kill()
                process.join(timeout=.5)
            if not process.is_alive():
                process.close()

    def close(self):
        """Release capture/GPU resources, including while the GUI is paused."""
        with self._mutex:
            self._close_locked()

    def _start_locked(self, render, input_window):
        context = multiprocessing.get_context("spawn")
        parent, child = context.Pipe(duplex=True)
        process = context.Process(target=_capture_worker, args=(child, render),
                                  name="chest-background-capture", daemon=True)
        try:
            process.start()
        except BaseException:
            parent.close()
            child.close()
            raise
        child.close()
        self._connection, self._process = parent, process

    def grab(self, render, input_window, should_cancel: Callable[[], bool] | None = None):
        """Read one unscaled frame or raise CaptureUnavailable and reconnect later.

        ``changed`` means pixels differ from the previous frame in this capture
        session. First frames and repeated cached/static images are False;
        callers must not use those as fresh disappearance evidence.
        """
        desktop._require_windows()
        key = (render.hwnd, render.pid, render.rect,
               input_window.hwnd, input_window.pid, input_window.rect)
        try:
            if (should_cancel is not None and should_cancel()) or not desktop.is_desktop_available():
                raise desktop.CaptureUnavailable("等待解锁或后台识别已暂停。")
            if not _pair_is_current(render, input_window):
                raise desktop.CaptureUnavailable("桌宠后台窗口已变化，正在等待重新连接。")
            origin, size = client_origin(render), _client_size(render)
            monitor = desktop.displays.owning_display(input_window.rect, desktop.displays.monitors())
            key += (monitor,)
            with self._mutex:
                if self._key != key or self._process is None or not self._process.is_alive():
                    self._close_locked()
                    self._start_locked(render, input_window)
                    self._key = key
                process, connection = self._process, self._connection
                connection.send("grab")
            deadline = time.monotonic()+self.timeout
            while not connection.poll(min(self.poll_interval, max(0., deadline-time.monotonic()))):
                if (should_cancel is not None and should_cancel()) or not desktop.is_desktop_available():
                    raise desktop.CaptureUnavailable("等待解锁或后台识别已暂停。")
                if not process.is_alive() or time.monotonic() >= deadline:
                    raise desktop.CaptureUnavailable("后台截图暂时不可用，正在等待重新连接。")
            payload = connection.recv()
            if not payload.get("ok"):
                raise desktop.CaptureUnavailable(payload.get("error", "后台截图失败。"))
            if (should_cancel is not None and should_cancel()) or not desktop.is_desktop_available():
                raise desktop.CaptureUnavailable("等待解锁或后台识别已暂停。")
            if (not _pair_is_current(render, input_window)
                    or origin != client_origin(render) or size != _client_size(render)
                    or monitor != desktop.displays.owning_display(input_window.rect, desktop.displays.monitors())
                    or tuple(payload["size"]) != size or len(payload["rgb"]) != size[0]*size[1]*3):
                raise desktop.CaptureUnavailable("桌宠窗口位置或大小已变化，已丢弃本次截图。")
            image = Image.frombytes("RGB", size, payload["rgb"])
            if all(low == high for low, high in image.getextrema()):
                raise desktop.CaptureUnavailable("后台截图只有纯色，无法判断宝箱状态。")
            digest = hashlib.sha256(payload["rgb"]).hexdigest()
            with self._mutex:
                if self._connection is not connection:
                    raise desktop.CaptureUnavailable("后台截图已暂停。")
                changed = self._digest is not None and self._digest != digest
                self._digest = digest
            return BackgroundFrame(image, render, input_window, digest, changed, origin, monitor)
        except desktop.CaptureUnavailable:
            self.close()
            raise
        except Exception as exc:
            self.close()
            raise desktop.CaptureUnavailable(f"后台截图连接失败：{exc}") from exc


def _send_message(window, code, wparam=0, lparam=0):
    if not _same_identity(window):
        raise OSError("桌宠输入窗口已退出或被替换。")
    result = ctypes.c_size_t()
    ctypes.set_last_error(0)
    if not _user32.SendMessageTimeoutW(window.hwnd, code, wparam, lparam,
                                      0x0002, 600, ctypes.byref(result)):
        raise OSError(ctypes.get_last_error(), f"后台窗口消息 0x{code:x} 失败或超时。")


def _wait_input(seconds, window, should_cancel):
    deadline = time.monotonic()+seconds
    while True:
        if ((should_cancel is not None and should_cancel())
                or not desktop.is_desktop_available() or not _input_is_current(window)):
            return False
        remaining = deadline-time.monotonic()
        if remaining <= 0:
            return True
        time.sleep(min(.01, remaining))


def background_click(x: int, y: int, input_window: desktop.WindowInfo,
                     should_cancel: Callable[[], bool] | None = None,
                     before_press: Callable[[], bool] | None = None) -> bool:
    """Send one tested native-overlay click without using physical input.

    Coordinates are physical SCREEN pixels. A held button is released even
    after a timed-out DOWN (whose delivery is unknown), cancellation or lock.
    Cleanup only addresses the original HWND/PID and never restores a cursor
    or changes the foreground window. ``before_press`` runs once after hover,
    before DOWN; it can reject a newly covered target without treating the
    target's disappearance after a successful DOWN as cancellation.
    """
    desktop._require_windows()
    window = input_window
    if ((should_cancel is not None and should_cancel())
            or not desktop.is_desktop_available() or not _input_is_current(window)):
        return False
    problem = desktop.input_privilege_problem([window])
    if problem:
        raise desktop.PrivilegeMismatch(problem)
    point = wintypes.POINT(int(x), int(y))
    if not _user32.ScreenToClient(window.hwnd, ctypes.byref(point)):
        raise ctypes.WinError(ctypes.get_last_error())
    width, height = _client_size(window)
    if not (0 <= point.x < width and 0 <= point.y < height
            and point.x <= 32767 and point.y <= 32767):
        return False
    lparam = (point.x & 0xffff) | ((point.y & 0xffff) << 16)
    foreground = _user32.GetForegroundWindow()
    activated = desktop._pid(foreground) != window.pid
    attempted_down = False
    try:
        if activated:
            _send_message(window, 0x0006, 1)  # Synthetic WA_ACTIVE, never foreground activation.
        _send_message(window, 0x0200, 0, lparam)
        if not _wait_input(.10, window, should_cancel):
            return False
        if before_press is not None and not before_press():
            return False
        # The final observation may take time; pause/lock still cancels input.
        if not _wait_input(0., window, should_cancel):
            return False
        attempted_down = True
        _send_message(window, 0x0201, 1, lparam)
        if not _wait_input(.12, window, should_cancel):
            return False
        return True
    finally:
        active_error = sys.exc_info()[1]
        try:
            if attempted_down and _same_identity(window):
                try:
                    _send_message(window, 0x0202, 0, lparam)
                except OSError as release_error:
                    # One bounded release retry; preserve a prior DOWN error.
                    try:
                        _send_message(window, 0x0202, 0, lparam)
                    except OSError as second_error:
                        if active_error is None:
                            raise
                        active_error.add_note(f"后台松开消息也失败：{second_error}")
                        logging.warning("Background mouse release failed: %s", release_error)
        finally:
            if _same_identity(window):
                try:
                    # Avoid deactivating a pet the user has now foregrounded.
                    if desktop._pid(_user32.GetForegroundWindow()) != window.pid:
                        _send_message(window, 0x02a3)
                        if activated:
                            _send_message(window, 0x0006, 0)
                except OSError:
                    logging.warning("Could not clear synthetic pet hover", exc_info=True)
