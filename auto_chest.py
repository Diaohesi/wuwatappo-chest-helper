"""WuwaTappo chest recognition with background and visible input modes."""
from __future__ import annotations

import argparse
from dataclasses import dataclass, replace
import json
import logging
from logging.handlers import RotatingFileHandler
import os
from pathlib import Path
import queue
import sys
import tempfile
import threading
import time

from chest_assets import load_builtin_templates
from chest_detection import detect_chests, detect_templates
from chest_state import DetectionGate, ClickLedger, ClickLock
import chest_windows as desktop
import chest_background as bg
from ui_assets import load_background

SOURCE_BASE = Path(__file__).resolve().parent
# Frozen modules live in _internal. Persist user settings beside the EXE,
# falling back to a per-user folder when that directory is read-only.
BASE = Path(sys.executable).resolve().parent if getattr(sys, "frozen", False) else SOURCE_BASE
if getattr(sys, "frozen", False):
    try:
        with tempfile.TemporaryFile(dir=BASE):
            pass
    except OSError:
        BASE = Path(os.environ.get("LOCALAPPDATA", str(Path.home()))) / "WuwaTappoChestHelper"
        BASE.mkdir(parents=True, exist_ok=True)
CONFIG = BASE / "config.json"
DEFAULTS = {"threshold": 0.94, "interval": 2.0, "mode": "borderless"}
MODE_NAMES = {"borderless": "无黄框领取（不占鼠标）", "background": "遮挡后台（可能有黄框）",
              "visible": "可见点击（兼容模式）"}
MODE_DESCRIPTIONS = {
    "borderless": "无捕获黄框，不移动鼠标、不抢焦点；宝箱须在桌面可见，完全被遮挡时等待。",
    "background": "可在普通全屏遮挡下领取，不移动鼠标；Windows 10 会显示系统黄色捕获框，其他系统视版本和授权而定。",
    "visible": "宝箱须可见；键鼠空闲 1.5 秒后短暂移动真实鼠标点击，适合定向消息不兼容时使用。",
}
APP_VERSION = "1.8.2"
CLAIM_RETRY_WAIT = 4.0
CLAIM_MAX_ATTEMPTS = 3


@dataclass
class PendingClaim:
    """A sent input is pending until visible pixels prove the icon disappeared."""
    lock: ClickLock
    attempts: int
    last_attempt: float
    visible_since: float | None
    window_rect: tuple[int, int, int, int]
    frame_digest: str | None = None
    render_signature: tuple | None = None

    def retry_due(self, now):
        return (self.visible_since is not None and now-self.visible_since >= CLAIM_RETRY_WAIT
                and now-self.last_attempt >= CLAIM_RETRY_WAIT)


def same_button_box(a, b):
    x, y, w, h = a
    left, top, width, height = b
    overlap = max(0, min(x+w, left+width)-max(x, left)) * max(0, min(y+h, top+height)-max(y, top))
    return overlap >= .5 * min(w*h, width*height)


def read_config():
    try:
        data = json.loads(CONFIG.read_text(encoding="utf-8"))
        mode = data.get("mode")
        if mode not in MODE_NAMES or (mode == "background" and data.get("settings_version") != 2):
            mode = "borderless"
        return {"threshold": max(.90, min(.99, float(data.get("threshold", .94)))),
                "interval": max(1., min(30., float(data.get("interval", 2.)))),
                "mode": mode}
    except (OSError, ValueError, TypeError):
        return DEFAULTS.copy()


def load_templates():
    """Always use the bundled chest artwork, independent of user files/skins."""
    return load_builtin_templates()


def crop_window(image, origin, window):
    left, top, right, bottom = window.rect
    x, y = left-origin[0], top-origin[1]
    width, height = right-left, bottom-top
    # Invisible frame margins may extend beyond a monitor. Keep coordinates
    # relative to the original window; padded pixels are never click/absence
    # evidence (every relevant region is checked against the actual capture).
    if (width <= 0 or height <= 0 or width > image.width+128 or height > image.height+128
            or x >= image.width or y >= image.height or x+width <= 0 or y+height <= 0):
        return None
    return image.crop((x, y, x+width, y+height))


def rect_is_captured(rect, origin, image):
    left, top, right, bottom = rect
    return (origin[0] <= left < right <= origin[0]+image.width
            and origin[1] <= top < bottom <= origin[1]+image.height)


def match_is_captured(match, window, origin, image):
    left, top = window.rect[0]+match.x, window.rect[1]+match.y
    return rect_is_captured((left, top, left+match.width, top+match.height), origin, image)


class Watcher:
    def __init__(self, events):
        self.events = events
        self.stop_event = threading.Event()
        self.wake_event = threading.Event()
        self.active = threading.Event()
        self.thread = None
        self.templates = []
        self.gate = DetectionGate()
        self.ledger = ClickLedger()
        self.threshold = .94
        self.interval = 2.
        self.clicks = 0
        self.confirmed = 0
        self.pending: list[PendingClaim] = []
        self.next_track = 0
        self.empty_rounds = 0
        self.waiting_for_screen = False
        self.mode = "visible"
        self.background_capture = None

    def post(self, kind, text):
        self.events.put((kind, text))
        if kind in ("log", "error"):
            logging.log(logging.WARNING if kind == "error" else logging.INFO, text)

    def start(self, templates, threshold, interval, mode="visible"):
        # The GUI only calls configure after the old thread has finished.
        if mode not in MODE_NAMES:
            raise ValueError("Unknown input mode")
        if self.mode != mode:
            # An explicit mode change starts new observations without treating
            # disappearance from a different capture source as confirmation.
            self.gate = DetectionGate()
            self.ledger = ClickLedger()
            self.pending.clear()
        self.mode = mode
        self.templates = templates
        self.threshold, self.interval = threshold, interval
        self.gate.reset_observations()
        self.reset_pending_attempts()
        self.empty_rounds = 0
        self.waiting_for_screen = False
        self.stop_event.clear()
        self.wake_event.clear()
        self.active.set()
        self.thread = threading.Thread(target=self.run, daemon=True, name="chest-watcher")
        self.thread.start()

    def pause(self):
        self.active.clear()
        self.wake_event.set()

    def close(self):
        self.active.clear()
        self.stop_event.set()
        self.wake_event.set()

    def reset_pending_attempts(self):
        """An explicit manual start/continue grants one fresh bounded retry budget."""
        now = time.monotonic()
        for claim in self.pending:
            claim.attempts = 0
            claim.last_attempt = now
            claim.visible_since = None
            claim.lock.missing_frames = 0

    def pending_at(self, window_key, box):
        return next((claim for claim in self.pending
                     if claim.lock.window_key == window_key and same_button_box(claim.lock.box, box)), None)

    def unknown_claims(self):
        for claim in self.pending:
            claim.visible_since = None
            self.ledger.observe(claim.lock, None)

    def observe_claim(self, lock, present, now):
        claim = next((p for p in self.pending if p.lock is lock), None)
        if claim is not None:
            if present is not True:
                claim.visible_since = None
            elif claim.visible_since is None:
                claim.visible_since = now
        if self.ledger.observe(lock, present):
            if claim is not None:
                self.pending.remove(claim)
                self.confirmed += 1
                self.post("confirmed", str(self.confirmed))
                self.post("log", "已确认宝箱图案消失：原位置连续两帧未出现图案，等待再次刷新。")
            return
        if (claim is not None and present is True and claim.retry_due(now)
                and claim.attempts >= CLAIM_MAX_ATTEMPTS):
            self.active.clear()
            self.post("error", "同一宝箱已尝试 3 次，图案仍在，已暂停领取。检查后可按 F8 继续重试。")

    def run(self):
        try:
            desktop.enable_dpi_awareness()
            while not self.stop_event.is_set():
                self.wake_event.clear()
                if not self.active.is_set():
                    self.gate.reset_observations()
                    self.close_background_capture()
                    self.wake_event.wait(.5)
                    continue
                self.tick()
                self.wake_event.wait(self.next_interval())
        except desktop.PrivilegeMismatch as exc:
            self.post("privilege", str(exc))
            self.active.clear()
        except Exception:
            logging.exception("Monitoring stopped")
            self.post("error", "识别已暂停，详情见 logs/auto_chest.log。")
            self.active.clear()
        finally:
            self.close_background_capture()

    def close_background_capture(self):
        if self.background_capture is not None:
            self.background_capture.close()
            self.background_capture = None

    def next_interval(self):
        """Back off empty scans while keeping claims and newly seen icons responsive."""
        if self.waiting_for_screen:
            return max(self.interval, 15.)
        if self.pending or self.empty_rounds < 3:
            return self.interval
        return max(self.interval, min(10., self.interval * 2**min(self.empty_rounds-2, 4)))

    def tick(self):
        if self.mode == "background":
            try:
                return self.tick_background()
            except desktop.CaptureUnavailable:
                self.waiting_for_screen = True
                self.gate.reset_observations()
                self.unknown_claims()
                self.close_background_capture()
                self.post("status", "后台窗口刚刚变化，等待重新连接。")
                return
        if not self.active.is_set() or self.stop_event.is_set():
            return
        self.waiting_for_screen = False
        if not desktop.is_desktop_available():
            self.waiting_for_screen = True
            self.gate.reset_observations()
            self.unknown_claims()
            self.post("status", "等待解锁桌面……")
            return
        windows = desktop.find_pet_windows()
        if not windows:
            self.waiting_for_screen = True
            for key in list(self.gate.states):
                self.gate.observe(key, None, time.monotonic())
            self.unknown_claims()
            self.post("status", "等待 WuwaTappo 出现（请勿最小化）……")
            return
        problem = desktop.input_privilege_problem(windows)
        if problem:
            self.active.clear()
            self.post("privilege", problem)
            return
        if self.mode == "borderless":
            # Desktop pixels avoid WGC's system capture border. Direct input
            # still goes only to the verified native layer of the same pet.
            windows = [input_window for _, input_window in bg.find_background_pairs(windows)]
            if not windows:
                self.waiting_for_screen = True
                self.gate.reset_observations()
                self.unknown_claims()
                self.post("status", "等待桌宠的输入窗口；无黄框模式需要宝箱在桌面可见。")
                return
        try:
            image, origin = desktop.capture_desktop()
        except desktop.CaptureUnavailable:
            self.waiting_for_screen = True
            self.gate.reset_observations()
            self.unknown_claims()
            self.post("status", "暂时无法读取桌面画面，正在等待恢复；不会点击或确认领取。")
            return
        crops, matches = {}, {}
        windows_by_key = {(w.hwnd, w.pid): w for w in windows}
        now = time.monotonic()
        # Check each clicked location itself. A different best match or an
        # occluding window must never count as evidence this icon disappeared.
        for lock in list(self.ledger.locks):
            window = windows_by_key.get(lock.window_key)
            if window is None:
                # A hidden/minimized window is unknown, not a successful claim.
                self.observe_claim(lock, None, now)
                continue
            if window.rect not in crops:
                crops[window.rect] = crop_window(image, origin, window)
            crop = crops[window.rect]
            x, y, width, height = lock.box
            visible = desktop.point_hits_window(window.rect[0]+x+width//2, window.rect[1]+y+height//2, window)
            if crop is None or not desktop.window_is_current(window):
                self.observe_claim(lock, None, now)
                continue
            # Either built-in visual variant can prove that this icon remains.
            # The template number is not the identity of a timer/button.
            left, top = max(0, x-4), max(0, y-4)
            patch = crop.crop((left, top, min(crop.width, x+width+4), min(crop.height, y+height+4)))
            checks = detect_templates(patch, self.templates, self.threshold)
            present = any(max(abs(m.center[0]+left-x-width//2),
                              abs(m.center[1]+top-y-height//2)) <= 6 for _, m in checks)
            claim = next((p for p in self.pending if p.lock is lock), None)
            if present:
                # Only exact input ownership can establish a still-ready icon
                # or allow a retry. Re-anchor after a move only when seen again.
                if visible and claim is not None:
                    claim.window_rect = window.rect
                self.observe_claim(lock, True if visible else None, now)
            else:
                # Removing a layered icon may expose an underlying window to
                # hit testing. That is valid absence evidence, unlike a window
                # above the pet covering this location or a moved pet window.
                region = (window.rect[0]+left, window.rect[1]+top,
                          window.rect[0]+min(crop.width, x+width+4),
                          window.rect[1]+min(crop.height, y+height+4))
                unchanged = claim is None or claim.window_rect == window.rect
                known = (unchanged and rect_is_captured(region, origin, image)
                         and desktop.can_observe_disappearance(region, window))
                self.observe_claim(lock, False if known else None, now)
        if not self.active.is_set():
            return

        seen, ready_targets = set(), []
        for window in windows:
            if window.rect not in crops:
                crops[window.rect] = crop_window(image, origin, window)
            crop = crops[window.rect]
            if window.rect not in matches:
                matches[window.rect] = detect_templates(crop, self.templates, self.threshold) if crop is not None else []
            for index, match in matches[window.rect]:
                if self.stop_event.is_set() or not self.active.is_set():
                    return
                if not match_is_captured(match, window, origin, image):
                    continue
                position = match.center
                if not desktop.point_hits_window(window.rect[0]+position[0], window.rect[1]+position[1], window):
                    continue
                # Associate observations by position, shared across artwork variants.
                # Each distinct button gets its own stable frames and cooldown.
                nearby = [key for key, state in self.gate.states.items()
                          if key[1:] == (window.hwnd, window.pid) and key not in seen
                          and self.gate._near(state.position, position)]
                if nearby:
                    key = min(nearby, key=lambda k: sum(abs(a-b) for a, b in zip(self.gate.states[k].position, position)))
                else:
                    self.next_track += 1
                    key = (self.next_track, window.hwnd, window.pid)
                seen.add(key)
                ready, _ = self.gate.observe(key, position, time.monotonic())
                box = (match.x, match.y, match.width, match.height)
                claim = self.pending_at((window.hwnd, window.pid), box)
                if claim is not None:
                    ready = (self.gate.states[key].stable_frames >= self.gate.required
                             and claim.retry_due(time.monotonic()) and claim.attempts < CLAIM_MAX_ATTEMPTS)
                if ready and (claim is not None or not self.ledger.blocked((window.hwnd, window.pid), box)):
                    ready_targets.append((window, index, match, key, claim))
        for key in set(self.gate.states)-seen:
            self.gate.observe(key, None, time.monotonic())
        self.empty_rounds = 0 if seen else self.empty_rounds+1
        if ready_targets:
            if self.mode == "visible" and desktop.user_idle_seconds() < 1.5:
                self.post("status", "宝箱已识别，等鼠标和键盘空闲 1.5 秒后点击……")
                return
            # New ready buttons remain independent while an earlier one awaits
            # confirmation; retries never monopolize both treasure slots.
            window, index, match, key, claim = min(ready_targets, key=lambda target: target[4] is not None)
            if not desktop.window_is_current(window):
                self.gate.reset_observations()
                return
            try:
                fresh, fresh_origin = desktop.capture_desktop()
            except desktop.CaptureUnavailable:
                self.gate.reset_observations()
                self.unknown_claims()
                self.post("status", "点击复核时无法读取桌面画面，本次跳过。")
                return
            fresh_crop = crop_window(fresh, fresh_origin, window)
            # Recheck this specific button, so another ready chest cannot steal
            # the best-match result and make this button impossible to click.
            left, top = max(0, match.x-6), max(0, match.y-6)
            checks = []
            if fresh_crop is not None:
                patch = fresh_crop.crop((left, top, min(fresh_crop.width, match.x+match.width+6),
                                         min(fresh_crop.height, match.y+match.height+6)))
                checks = [(variant, replace(m, x=m.x+left, y=m.y+top))
                          for variant, m in detect_templates(patch, self.templates, self.threshold)]
            checks = [(variant, m) for variant, m in checks
                      if max(abs(m.center[0]-match.center[0]), abs(m.center[1]-match.center[1])) <= 6
                      and match_is_captured(m, window, fresh_origin, fresh)]
            if not checks:
                self.gate.reset_observations()
                return
            index, verify = max(checks, key=lambda item: item[1].score)
            if (not self.active.is_set() or self.stop_event.is_set()
                    or (self.mode == "visible" and desktop.user_idle_seconds() < 1.5)):
                return
            x, y = window.rect[0]+verify.center[0], window.rect[1]+verify.center[1]
            if self.mode == "borderless":
                def cancelled():
                    return (self.stop_event.is_set() or not self.active.is_set()
                            or not desktop.is_desktop_available()
                            or not desktop.window_is_current(window))
                visible = lambda: desktop.point_hits_window(x, y, window)
                clicked = False if cancelled() or not visible() else bg.background_click(
                    x, y, window, should_cancel=cancelled, before_press=visible)
            else:
                clicked = desktop.safe_click(x, y, window, should_cancel=lambda: self.stop_event.is_set() or not self.active.is_set())
            if clicked:
                sent_at = time.monotonic()
                self.gate.clicked(key, verify.center, sent_at)
                if claim is None:
                    self.ledger.clicked((window.hwnd, window.pid),
                                        (verify.x, verify.y, verify.width, verify.height), dict(self.templates)[index])
                    lock = next(lock for lock in self.ledger.locks
                                if lock.window_key == (window.hwnd, window.pid)
                                and same_button_box(lock.box, (verify.x, verify.y, verify.width, verify.height)))
                    claim = PendingClaim(lock, 0, sent_at, sent_at, window.rect)
                    self.pending.append(claim)
                claim.attempts += 1
                claim.last_attempt = claim.visible_since = sent_at
                claim.window_rect = window.rect
                claim.lock.missing_frames = 0
                self.clicks += 1
                self.post("count", str(self.clicks))
                self.post("log", f"已发出宝箱点击（第 {claim.attempts}/3 次），相似度 {verify.score:.3f}；等待确认图案消失。")
            else:
                self.post("status", "宝箱位置被遮挡或窗口已移动，本次跳过。")
            return
        if self.pending:
            self.post("status", f"有 {len(self.pending)} 个已发点击等待确认；图案仍在时最多尝试 3 次。")
        else:
            self.post("status", f"正在等待宝箱各自刷新；当前检查间隔 {self.next_interval():g} 秒。")

    @staticmethod
    def background_signature(frame):
        origin = getattr(frame, "origin", None)
        if origin is None:
            origin = bg.client_origin(frame.render)
        return (frame.render.hwnd, frame.render.pid, frame.render.rect,
                frame.input.rect, origin)

    def tick_background(self):
        """Observe render pixels and address only the paired native input layer."""
        if not self.active.is_set() or self.stop_event.is_set():
            return
        self.waiting_for_screen = False
        if not desktop.is_desktop_available():
            self.waiting_for_screen = True
            self.gate.reset_observations()
            self.unknown_claims()
            self.close_background_capture()
            self.post("status", "等待解锁桌面；后台领取已暂停……")
            return
        windows = desktop.find_pet_windows()
        problem = desktop.input_privilege_problem(windows)
        if problem:
            self.active.clear()
            self.close_background_capture()
            self.post("privilege", problem)
            return
        pairs = bg.find_background_pairs(windows)
        if not pairs:
            self.waiting_for_screen = True
            self.gate.reset_observations()
            self.unknown_claims()
            self.close_background_capture()
            self.post("status", "等待桌宠的后台窗口；请保持桌宠运行且未最小化……")
            return
        if self.background_capture is None:
            self.background_capture = bg.BackgroundCapture()
        cancelled = lambda: self.stop_event.is_set() or not self.active.is_set()
        frames = {}
        for render, input_window in pairs:
            try:
                frame = self.background_capture.grab(render, input_window, should_cancel=cancelled)
                if cancelled():
                    return
                if not desktop.window_is_current(frame.render) or not desktop.window_is_current(frame.input):
                    continue
                frames[(input_window.hwnd, input_window.pid)] = frame
            except desktop.CaptureUnavailable:
                self.waiting_for_screen = True
                self.gate.reset_observations()
                self.unknown_claims()
                self.close_background_capture()
                self.post("status", "后台画面暂不可用，等待重连；当前保持后台模式。")
                return
        now = time.monotonic()
        for lock in list(self.ledger.locks):
            frame = frames.get(lock.window_key)
            claim = next((p for p in self.pending if p.lock is lock), None)
            if frame is None:
                self.observe_claim(lock, None, now)
                continue
            crop = frame.image
            x, y, width, height = lock.box
            if not (0 <= x < x+width <= crop.width and 0 <= y < y+height <= crop.height):
                self.observe_claim(lock, None, now)
                continue
            left, top = max(0, x-4), max(0, y-4)
            patch = crop.crop((left, top, min(crop.width, x+width+4), min(crop.height, y+height+4)))
            present = any(max(abs(m.center[0]+left-x-width//2),
                              abs(m.center[1]+top-y-height//2)) <= 6
                          for _, m in detect_templates(patch, self.templates, self.threshold))
            signature = self.background_signature(frame)
            if present:
                if claim is not None:
                    claim.window_rect = frame.input.rect
                    claim.render_signature = signature
                self.observe_claim(lock, True, now)
            else:
                # Identical/cached frames, a new window origin, or a different
                # capture source cannot advance a disappearance streak.
                known = (claim is not None and signature == claim.render_signature
                         and frame.changed and frame.digest != claim.frame_digest)
                self.observe_claim(lock, False if known else None, now)
        if cancelled():
            return
        seen, ready_targets = set(), []
        for window_key, frame in frames.items():
            for index, match in detect_templates(frame.image, self.templates, self.threshold):
                if cancelled():
                    return
                position = match.center
                nearby = [key for key, state in self.gate.states.items()
                          if key[1:] == window_key and key not in seen
                          and self.gate._near(state.position, position)]
                if nearby:
                    key = min(nearby, key=lambda k: sum(abs(a-b) for a, b in zip(self.gate.states[k].position, position)))
                else:
                    self.next_track += 1
                    key = (self.next_track, *window_key)
                seen.add(key)
                ready, _ = self.gate.observe(key, position, now)
                box = (match.x, match.y, match.width, match.height)
                claim = self.pending_at(window_key, box)
                if claim is not None:
                    ready = (self.gate.states[key].stable_frames >= self.gate.required
                             and claim.retry_due(now) and claim.attempts < CLAIM_MAX_ATTEMPTS)
                if ready and (claim is not None or not self.ledger.blocked(window_key, box)):
                    ready_targets.append((frame, index, match, key, claim))
        for key in set(self.gate.states)-seen:
            self.gate.observe(key, None, now)
        self.empty_rounds = 0 if seen else self.empty_rounds+1
        if not ready_targets:
            self.post("status", f"后台领取中；等待图案刷新或确认，检查间隔 {self.next_interval():g} 秒。")
            return
        selected, index, match, key, claim = min(ready_targets, key=lambda t: t[4] is not None)
        try:
            fresh = self.background_capture.grab(selected.render, selected.input, should_cancel=cancelled)
        except desktop.CaptureUnavailable:
            self.waiting_for_screen = True
            self.gate.reset_observations()
            self.unknown_claims()
            self.close_background_capture()
            self.post("status", "后台点击复核失败，等待重新连接。")
            return
        if (cancelled() or not desktop.window_is_current(fresh.render)
                or not desktop.window_is_current(fresh.input)
                or self.background_signature(fresh) != self.background_signature(selected)):
            self.gate.reset_observations()
            return
        left, top = max(0, match.x-6), max(0, match.y-6)
        patch = fresh.image.crop((left, top, min(fresh.image.width, match.x+match.width+6),
                                  min(fresh.image.height, match.y+match.height+6)))
        checks = [(variant, replace(m, x=m.x+left, y=m.y+top))
                  for variant, m in detect_templates(patch, self.templates, self.threshold)]
        checks = [(variant, m) for variant, m in checks
                  if max(abs(m.center[0]-match.center[0]), abs(m.center[1]-match.center[1])) <= 6]
        if not checks or cancelled():
            self.gate.reset_observations()
            return
        index, verify = max(checks, key=lambda item: item[1].score)
        signature = self.background_signature(fresh)
        origin = bg.client_origin(fresh.render)
        if origin != signature[-1]:
            self.gate.reset_observations()
            return
        x, y = origin[0]+verify.center[0], origin[1]+verify.center[1]
        if bg.background_click(x, y, fresh.input, should_cancel=cancelled):
            sent_at = time.monotonic()
            self.gate.clicked(key, verify.center, sent_at)
            window_key = (fresh.input.hwnd, fresh.input.pid)
            if claim is None:
                box = (verify.x, verify.y, verify.width, verify.height)
                self.ledger.clicked(window_key, box, dict(self.templates)[index])
                lock = next(lock for lock in self.ledger.locks
                            if lock.window_key == window_key and same_button_box(lock.box, box))
                claim = PendingClaim(lock, 0, sent_at, sent_at, fresh.input.rect)
                self.pending.append(claim)
            claim.attempts += 1
            claim.last_attempt = claim.visible_since = sent_at
            claim.window_rect = fresh.input.rect
            claim.frame_digest = fresh.digest
            claim.render_signature = signature
            claim.lock.missing_frames = 0
            self.clicks += 1
            self.post("count", str(self.clicks))
            self.post("log", f"已发送后台宝箱点击（第 {claim.attempts}/3 次）；等待新画面确认图案消失。")
        else:
            self.post("status", "后台窗口状态已变化，本次未点击。")


class App:
    def __init__(self):
        import tkinter as tk
        from tkinter import ttk
        self.tk, self.ttk = tk, ttk
        self.root = tk.Tk()
        self.root.title(f"WuwaTappo 宝箱自动领取 {APP_VERSION}")
        self.root.geometry("1080x880")
        self.root.minsize(980, 860)
        self.root.configure(background="#f3f2f4")
        self.background_source = load_background()
        self.background_job = None
        self.background_photo = None
        self.events = queue.Queue()
        self.watcher = Watcher(self.events)
        self.key_states = {}
        self.previewing = False
        self.starting = False
        self.closing = False
        self.settings = read_config()
        self.threshold = tk.StringVar(value=str(self.settings["threshold"]))
        self.interval = tk.StringVar(value=str(self.settings["interval"]))
        self.mode = tk.StringVar(value=MODE_NAMES[self.settings["mode"]])
        self.mode_help = tk.StringVar(value=MODE_DESCRIPTIONS[self.settings["mode"]])
        self.status = tk.StringVar(value="宝箱识别已内置，点击“开始领取”即可。")
        self.count = tk.StringVar(value="已发出点击：0；确认图案消失：0")

        from PIL import ImageTk, ImageOps
        self.window_icon = ImageTk.PhotoImage(ImageOps.fit(self.background_source, (64, 64), centering=(.5, .25)))
        self.root.iconphoto(True, self.window_icon)
        style = ttk.Style(self.root)
        style.theme_use("clam")
        card = "#fcfcfd"
        style.configure("Chest.TFrame", background=card)
        style.configure("Chest.TLabel", background=card, foreground="#2c2933", font=("Microsoft YaHei UI", 10))
        style.configure("Chest.TButton", padding=(10, 9), font=("Microsoft YaHei UI", 10))
        style.configure("Primary.Chest.TButton", background="#963b50", foreground="white")
        style.map("Primary.Chest.TButton", background=[("active", "#ac4b63"), ("pressed", "#7e3042")])
        self.canvas = tk.Canvas(self.root, highlightthickness=0, borderwidth=0)
        self.canvas.pack(fill="both", expand=True)
        self.background_item = self.canvas.create_image(0, 0, anchor="nw")
        frame = tk.Frame(self.canvas, background=card, padx=18, pady=18)
        self.controls_item = self.canvas.create_window(0, 0, anchor="nw", window=frame)
        self.canvas.bind("<Configure>", self.queue_background)
        ttk.Label(frame, text="WuwaTappo 宝箱助手", style="Chest.TLabel", font=("Microsoft YaHei UI", 17, "bold")).pack(anchor="w")
        ttk.Label(frame, text=f"自动领取  /  {APP_VERSION}", style="Chest.TLabel", foreground="#963b50").pack(anchor="w", pady=(3, 10))
        ttk.Label(frame, text="图案已内置；无宝箱时自动降低检查频率。", style="Chest.TLabel", wraplength=350).pack(anchor="w", pady=(0, 10))
        steps = ("① 点击“开始领取”，等待金色宝箱出现。\n"
                 "② 点击后图案消失，两个宝箱各自刷新、各自领取。\n"
                 "③ 按需选择运行模式；保持解锁，F8 暂停，F12 退出。")
        ttk.Label(frame, text=steps, style="Chest.TLabel", justify="left", wraplength=350).pack(anchor="w", pady=(0, 12))
        self.admin_row = ttk.Frame(frame, style="Chest.TFrame")
        self.admin_row.pack(fill="x", pady=(0, 4))
        self.admin_button = ttk.Button(self.admin_row, text="以管理员身份重新启动", style="Chest.TButton", command=self.restart_admin)
        options = ttk.Frame(frame, style="Chest.TFrame")
        options.pack(fill="x", pady=5)
        ttk.Label(options, text="相似度阈值", style="Chest.TLabel").grid(row=0, column=0, sticky="w", pady=4)
        ttk.Spinbox(options, from_=.90, to=.99, increment=.01, textvariable=self.threshold, width=8).grid(row=0, column=1, sticky="e", padx=8, pady=4)
        ttk.Label(options, text="活跃检查间隔（秒）", style="Chest.TLabel").grid(row=1, column=0, sticky="w", pady=4)
        ttk.Spinbox(options, from_=1, to=30, increment=1, textvariable=self.interval, width=8).grid(row=1, column=1, sticky="e", padx=8, pady=4)
        options.columnconfigure(0, weight=1)
        mode_row = ttk.Frame(frame, style="Chest.TFrame")
        mode_row.pack(fill="x", pady=(4, 2))
        ttk.Label(mode_row, text="运行模式", style="Chest.TLabel").pack(side="left")
        self.mode_choice = ttk.Combobox(mode_row, textvariable=self.mode, values=list(MODE_NAMES.values()),
                                       state="readonly", width=23)
        self.mode_choice.pack(side="right")
        self.mode.trace_add("write", self.update_mode_help)
        ttk.Label(frame, textvariable=self.mode_help, style="Chest.TLabel", wraplength=350,
                  foreground="#5f425c", justify="left").pack(anchor="w", pady=(4, 6))
        buttons = ttk.Frame(frame, style="Chest.TFrame")
        buttons.pack(fill="x", pady=(10, 4))
        self.start_button = ttk.Button(buttons, text="开始领取  /  F8", style="Primary.Chest.TButton", command=self.toggle)
        self.start_button.pack(side="left", fill="x", expand=True)
        ttk.Button(buttons, text="暂停", style="Chest.TButton", command=self.pause).pack(side="left", padx=(8, 0))
        secondary = ttk.Frame(frame, style="Chest.TFrame")
        secondary.pack(fill="x", pady=(4, 8))
        ttk.Button(secondary, text="仅识别一次", style="Chest.TButton", command=self.preview).pack(side="left", fill="x", expand=True)
        ttk.Button(secondary, text="退出  /  F12", style="Chest.TButton", command=self.close).pack(side="left", padx=(8, 0))
        ttk.Label(frame, textvariable=self.status, style="Chest.TLabel", wraplength=350, foreground="#5f425c").pack(anchor="w", pady=5)
        ttk.Label(frame, textvariable=self.count, style="Chest.TLabel", font=("Microsoft YaHei UI", 9)).pack(anchor="w", pady=5)
        self.log = tk.Text(frame, height=7, wrap="word", state="disabled", font=("Microsoft YaHei UI", 9),
                           background="#f2f0f4", foreground="#47414f", relief="flat", padx=10, pady=8)
        self.log.pack(fill="both", expand=True, pady=(8, 0))
        self.root.protocol("WM_DELETE_WINDOW", self.close)
        self.root.after(150, self.poll)
        logging.info("宝箱助手 %s 已启动，使用内置图案；当前保持暂停。", APP_VERSION)
        problem = desktop.input_privilege_problem()
        if problem:
            self.show_privilege_problem(problem)

    def queue_background(self, event):
        if self.background_job is not None:
            self.root.after_cancel(self.background_job)
        self.background_job = self.root.after(60, self.paint_background)

    def paint_background(self):
        from PIL import Image, ImageDraw, ImageFilter, ImageOps, ImageTk
        self.background_job = None
        width, height = self.canvas.winfo_width(), self.canvas.winfo_height()
        if width < 2 or height < 2:
            return
        card_width, margin = 408, 24
        card_left = width-card_width-margin
        # A soft full-window background plus the complete portrait keeps the
        # supplied character and caption visible beside the readable controls.
        backdrop = ImageOps.fit(self.background_source, (width, height)).filter(ImageFilter.GaussianBlur(24))
        backdrop = Image.blend(backdrop, Image.new("RGB", (width, height), "white"), .88)
        portrait = ImageOps.contain(self.background_source, (card_left+12, height), Image.Resampling.LANCZOS)
        backdrop.paste(portrait, ((card_left+12-portrait.width)//2, (height-portrait.height)//2))
        draw = ImageDraw.Draw(backdrop)
        draw.rounded_rectangle((card_left+3, margin+4, width-margin+3, height-margin+4), radius=20, fill="#dedbe2")
        draw.rounded_rectangle((card_left, margin, width-margin, height-margin), radius=20, fill="#fcfcfd")
        self.background_photo = ImageTk.PhotoImage(backdrop)
        self.canvas.itemconfigure(self.background_item, image=self.background_photo)
        self.canvas.coords(self.controls_item, card_left+8, margin+8)
        self.canvas.itemconfigure(self.controls_item, width=card_width-16, height=height-2*margin-16)

    def append_log(self, text):
        self.log.configure(state="normal")
        self.log.insert("end", time.strftime("%H:%M:%S ")+text+"\n")
        if int(self.log.index("end-1c").split(".")[0]) > 150:
            self.log.delete("1.0", "30.0")
        self.log.see("end")
        self.log.configure(state="disabled")

    def update_mode_help(self, *_):
        mode = next((key for key, label in MODE_NAMES.items() if label == self.mode.get()), None)
        self.mode_help.set(MODE_DESCRIPTIONS.get(mode, "请选择运行模式。"))

    def get_settings(self):
        threshold, interval = float(self.threshold.get()), float(self.interval.get())
        if not .90 <= threshold <= .99 or not 1 <= interval <= 30:
            raise ValueError("相似度应为 0.90～0.99，检查间隔应为 1～30 秒。")
        mode = next((key for key, label in MODE_NAMES.items() if label == self.mode.get()), None)
        if mode is None:
            raise ValueError("请选择有效的运行模式。")
        CONFIG.write_text(json.dumps({"threshold": threshold, "interval": interval, "mode": mode,
                                      "settings_version": 2}, indent=2), encoding="utf-8")
        return threshold, interval, mode

    def toggle(self):
        from tkinter import messagebox
        if self.previewing or self.starting:
            return
        if self.watcher.active.is_set():
            self.pause()
            return
        try:
            problem = desktop.input_privilege_problem()
            if problem:
                self.show_privilege_problem(problem)
                return
            templates = load_templates()
            threshold, interval, mode = self.get_settings()
            self.watcher.close()
            self.starting = True
            self.mode_choice.configure(state="disabled")
            self.status.set("正在启动识别……")
            self.root.after(50, lambda: self.finish_start(templates, threshold, interval, mode))
        except Exception as exc:
            self.mode_choice.configure(state="readonly")
            messagebox.showerror("无法启动", str(exc), parent=self.root)

    def show_privilege_problem(self, problem):
        self.pause()
        self.status.set(problem)
        self.admin_button.pack(side="left")
        self.append_log(problem)
        logging.warning("权限不足：%s", problem)

    def restart_admin(self):
        """Request the standard Windows consent prompt; the user approves it."""
        self.pause()
        if desktop.relaunch_elevated(BASE / "auto_chest.py"):
            self.close()
        else:
            self.status.set("管理员启动未完成。当前仍保持暂停，可再次点击管理员启动。")

    def finish_start(self, templates, threshold, interval, mode):
        if not self.starting:
            return
        if self.watcher.thread and self.watcher.thread.is_alive():
            self.root.after(100, lambda: self.finish_start(templates, threshold, interval, mode))
            return
        self.starting = False
        self.watcher.start(templates, threshold, interval, mode)
        self.append_log({"borderless": "已开始无黄框领取：宝箱可见时定向点击，不占鼠标；完全遮挡时等待。",
                         "background": "已开始遮挡后台：窗口取图和定向消息，不占鼠标；系统可能显示黄色捕获框。",
                         "visible": "已开始可见点击：连续两帧命中、点击前复核，鼠标键盘空闲后点击。"}[mode])
        self.start_button.configure(text="暂停领取  /  F8")
        self.status.set("正在等待宝箱出现……")

    def pause(self):
        self.starting = False
        self.watcher.pause()
        self.mode_choice.configure(state="readonly")
        self.start_button.configure(text="继续领取  /  F8")
        self.status.set("已暂停。F8 继续，F12 退出。")

    def preview(self):
        from tkinter import messagebox
        if self.previewing:
            return
        self.pause()
        try:
            templates = load_templates()
            threshold, _, mode = self.get_settings()
        except Exception as exc:
            messagebox.showerror("无法识别", str(exc), parent=self.root)
            return
        self.previewing = True
        self.root.withdraw()

        def worker():
            try:
                desktop.enable_dpi_awareness()
                from chest_diagnostics import collect_diagnostics, summarize_diagnostics
                report = collect_diagnostics(templates, threshold, mode, crop_window)
                report["helper_version"] = APP_VERSION
                destination = BASE / "diagnostics" / "latest.json"
                destination.parent.mkdir(parents=True, exist_ok=True)
                destination.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
                self.events.put(("preview", summarize_diagnostics(report)+f"\n诊断文件：{destination}"))
            except Exception as exc:
                self.events.put(("preview", f"识别失败：{exc}"))

        self.root.after(350, lambda: threading.Thread(target=worker, daemon=True).start())

    def poll(self):
        from tkinter import messagebox
        if self.closing:
            return
        try:
            while True:
                kind, text = self.events.get_nowait()
                if kind == "status" and self.watcher.active.is_set():
                    self.status.set(text)
                elif kind in ("count", "confirmed"):
                    self.count.set(f"已发出点击：{self.watcher.clicks}；确认图案消失：{self.watcher.confirmed}")
                elif kind == "log":
                    self.append_log(text)
                elif kind == "error":
                    self.pause()
                    self.status.set(text)
                    self.append_log(text)
                elif kind == "privilege":
                    self.show_privilege_problem(text)
                elif kind == "preview":
                    self.previewing = False
                    self.root.deiconify()
                    self.append_log("仅识别，没有点击。"+text.replace("\n", "；"))
                    messagebox.showinfo("识别结果（未点击）", text, parent=self.root)
        except queue.Empty:
            pass
        for vk in (0x77, 0x7B):  # F8 / F12
            down = desktop.hotkey_down(vk)
            if down and not self.key_states.get(vk, False):
                if vk == 0x7B:
                    self.close()
                    return
                if not self.previewing:
                    self.toggle()
            self.key_states[vk] = down
        self.root.after(150, self.poll)

    def close(self):
        if self.closing:
            return
        self.closing = True
        self.starting = False
        self.watcher.close()
        self.root.withdraw()
        self.finish_close()

    def finish_close(self):
        # Let a pending message release and capture-process cleanup finish.
        if self.watcher.thread and self.watcher.thread.is_alive():
            self.root.after(30, self.finish_close)
        else:
            self.root.destroy()

    def run(self):
        self.root.mainloop()


def scan_background_once(templates, threshold):
    if not desktop.is_desktop_available():
        raise RuntimeError("桌面已锁定或不是可交互桌面。")
    pairs = bg.find_background_pairs(desktop.find_pet_windows())
    result = []
    manager = bg.BackgroundCapture()
    try:
        for render, input_window in pairs:
            frame = manager.grab(render, input_window)
            origin = bg.client_origin(frame.render)
            for index, match in detect_templates(frame.image, templates, threshold):
                result.append({"variant": index, "window": render.title, "score": round(match.score, 4),
                               "x": origin[0]+match.center[0], "y": origin[1]+match.center[1],
                               "recognized": True, "mode": "background"})
    finally:
        manager.close()
    return result


def scan_once(templates, threshold):
    if not desktop.is_desktop_available():
        raise RuntimeError("桌面已锁定或不是可交互桌面。")
    windows = desktop.find_pet_windows()
    if not windows:
        return []
    image, origin = desktop.capture_desktop()
    result, cache = [], {}
    for window in windows:
        crop = crop_window(image, origin, window)
        if crop is None:
            continue
        if window.rect not in cache:
            cache[window.rect] = detect_templates(crop, templates, threshold)
        for index, match in cache[window.rect]:
            if not match_is_captured(match, window, origin, image):
                continue
            x, y = window.rect[0]+match.center[0], window.rect[1]+match.center[1]
            if desktop.point_hits_window(x, y, window):
                result.append({"variant": index, "window": window.title, "score": round(match.score, 4),
                               "x": x, "y": y, "recognized": True, "visible": True})
    return result


def setup_logging():
    (BASE / "logs").mkdir(exist_ok=True)
    handler = RotatingFileHandler(BASE / "logs" / "auto_chest.log", maxBytes=512_000, backupCount=2, encoding="utf-8")
    logging.basicConfig(level=logging.INFO, handlers=[handler], format="%(asctime)s %(levelname)s %(message)s")


def runtime_self_test(destination):
    """Verify packaged images, native dependencies and Tk; never send input."""
    report = {"ok": False, "version": APP_VERSION, "frozen": bool(getattr(sys, "frozen", False))}
    root = None
    try:
        import tkinter as tk
        from PIL import Image, ImageTk
        background = load_background()
        templates = load_templates()
        cell_width = max(template.width for _, template in templates) + 30
        cell_height = max(template.height for _, template in templates) + 30
        screen = Image.new("RGB", (cell_width * len(templates), cell_height), (30, 45, 60))
        for position, (_, template) in enumerate(templates):
            screen.paste(template, (position * cell_width + 15, 15))
        detections = detect_templates(screen, templates, .94)
        if (len(detections) != len(templates)
                or {variant for variant, _ in detections} != {variant for variant, _ in templates}):
            raise RuntimeError("Built-in recognition self-test failed")
        root = tk.Tk()
        root.withdraw()
        photo = ImageTk.PhotoImage(background, master=root)
        if (photo.width(), photo.height()) != (708, 807):
            raise RuntimeError("Embedded background has unexpected dimensions")
        root.update_idletasks()
        report.update(ok=True, background=list(background.size), detected_chests=len(detections),
                      template_variants=len(templates),
                      tk=str(root.tk.call("info", "patchlevel")), default_mode=DEFAULTS["mode"],
                      modes=list(MODE_NAMES), mode_descriptions=MODE_DESCRIPTIONS)
    except Exception as exc:
        report["error"] = f"{type(exc).__name__}: {exc}"
    finally:
        if root is not None:
            root.destroy()
    destination = Path(destination)
    destination.parent.mkdir(parents=True, exist_ok=True)
    destination.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    return 0 if report["ok"] else 1


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--check", action="store_true", help="Check runtime and WuwaTappo windows; never clicks")
    parser.add_argument("--scan-once", action="store_true", help="Recognize once using built-in chest icons; never clicks")
    parser.add_argument("--self-test", type=Path, metavar="JSON", help="Verify packaged dependencies without clicking; write report to JSON")
    parser.add_argument("--background-self-test", type=Path, metavar="JSON", help="Verify isolated background SDK worker without input")
    parser.add_argument("--diagnose", type=Path, metavar="JSON", help="Write read-only recognition diagnostics without input or saved screenshots")
    parser.add_argument("--mode", choices=tuple(MODE_NAMES), help="Capture mode for --scan-once")
    args = parser.parse_args()
    desktop.enable_dpi_awareness()
    if args.self_test is not None:
        raise SystemExit(runtime_self_test(args.self_test))
    if args.background_self_test is not None:
        report = {"ok": False, "version": APP_VERSION, "frozen": bool(getattr(sys, "frozen", False))}
        try:
            report["backend"] = bg.runtime_self_test(timeout=15)
            if not report["backend"].get("ok"):
                raise RuntimeError(str(report["backend"]))
            report["ok"] = True
        except Exception as exc:
            report["error"] = f"{type(exc).__name__}: {exc}"
        args.background_self_test.parent.mkdir(parents=True, exist_ok=True)
        args.background_self_test.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
        raise SystemExit(0 if report["ok"] else 1)
    if args.diagnose is not None:
        from chest_diagnostics import collect_diagnostics
        report = collect_diagnostics(load_templates(), read_config()["threshold"],
                                     args.mode or read_config()["mode"], crop_window)
        report["helper_version"] = APP_VERSION
        args.diagnose.parent.mkdir(parents=True, exist_ok=True)
        args.diagnose.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
        return
    if args.check:
        print(json.dumps({"version": APP_VERSION, "python": sys.version.split()[0], "desktop_available": desktop.is_desktop_available(),
                          "input_privilege_problem": desktop.input_privilege_problem(),
                          "virtual_screen": desktop.virtual_rect(),
                          "windows": [vars(w) for w in desktop.find_pet_windows()],
                          "recognition": "built-in", "templates": [{"variant": i, "size": list(t.size)}
                                                                    for i, t in load_templates()]}, ensure_ascii=False, indent=2))
        return
    if args.scan_once:
        templates = load_templates()
        scan = scan_background_once if args.mode == "background" else scan_once
        print(json.dumps(scan(templates, read_config()["threshold"]), ensure_ascii=False, indent=2))
        return
    setup_logging()
    App().run()


if __name__ == "__main__":
    import multiprocessing
    multiprocessing.freeze_support()
    main()
