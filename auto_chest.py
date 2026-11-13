"""WuwaTappo visible-screen image recognition. Run without arguments for the GUI."""
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
DEFAULTS = {"threshold": 0.94, "interval": 2.0}
APP_VERSION = "1.5"
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
        return {"threshold": max(.90, min(.99, float(data.get("threshold", .94)))),
                "interval": max(1., min(30., float(data.get("interval", 2.))))}
    except (OSError, ValueError, TypeError):
        return DEFAULTS.copy()


def load_templates():
    """Always use the bundled chest artwork, independent of user files/skins."""
    return load_builtin_templates()


def crop_window(image, origin, window):
    left, top, right, bottom = window.rect
    x, y = left-origin[0], top-origin[1]
    # Partly off-screen windows are skipped rather than padding with invented pixels.
    if x < 0 or y < 0 or x+right-left > image.width or y+bottom-top > image.height:
        return None
    return image.crop((x, y, x+right-left, y+bottom-top))


class Watcher:
    def __init__(self, events):
        self.events = events
        self.stop_event = threading.Event()
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

    def post(self, kind, text):
        self.events.put((kind, text))
        if kind in ("log", "error"):
            logging.log(logging.WARNING if kind == "error" else logging.INFO, text)

    def start(self, templates, threshold, interval):
        # The GUI only calls configure after the old thread has finished.
        self.templates = templates
        self.threshold, self.interval = threshold, interval
        self.gate.reset_observations()
        self.reset_pending_attempts()
        self.stop_event.clear()
        self.active.set()
        self.thread = threading.Thread(target=self.run, daemon=True, name="chest-watcher")
        self.thread.start()

    def pause(self):
        self.active.clear()

    def close(self):
        self.active.clear()
        self.stop_event.set()

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
            while not self.stop_event.is_set():
                if not self.active.is_set():
                    self.gate.reset_observations()
                    self.stop_event.wait(.15)
                    continue
                self.tick()
                self.stop_event.wait(self.interval)
        except desktop.PrivilegeMismatch as exc:
            self.post("privilege", str(exc))
            self.active.clear()
        except Exception:
            logging.exception("Monitoring stopped")
            self.post("error", "识别已暂停，详情见 logs/auto_chest.log。")
            self.active.clear()

    def tick(self):
        if not self.active.is_set() or self.stop_event.is_set():
            return
        if not desktop.is_desktop_available():
            self.gate.reset_observations()
            self.unknown_claims()
            self.post("status", "等待解锁桌面……")
            return
        windows = desktop.find_pet_windows()
        if not windows:
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
        try:
            image, origin = desktop.capture_desktop()
        except desktop.CaptureUnavailable:
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
                known = unchanged and desktop.can_observe_disappearance(region, window)
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
        if ready_targets:
            if desktop.user_idle_seconds() < 1.5:
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
                      if max(abs(m.center[0]-match.center[0]), abs(m.center[1]-match.center[1])) <= 6]
            if not checks:
                self.gate.reset_observations()
                return
            index, verify = max(checks, key=lambda item: item[1].score)
            if (not self.active.is_set() or self.stop_event.is_set()
                    or desktop.user_idle_seconds() < 1.5):
                return
            x, y = window.rect[0]+verify.center[0], window.rect[1]+verify.center[1]
            if desktop.safe_click(x, y, window, should_cancel=lambda: self.stop_event.is_set() or not self.active.is_set()):
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
            self.post("status", f"正在等待宝箱各自刷新；每轮间隔 {self.interval:g} 秒。")


class App:
    def __init__(self):
        import tkinter as tk
        from tkinter import ttk
        self.tk, self.ttk = tk, ttk
        self.root = tk.Tk()
        self.root.title(f"WuwaTappo 宝箱自动领取 {APP_VERSION}")
        self.root.geometry("1080x800")
        self.root.minsize(980, 760)
        self.root.configure(background="#f3f2f4")
        self.background_source = load_background()
        self.background_job = None
        self.background_photo = None
        self.events = queue.Queue()
        self.watcher = Watcher(self.events)
        self.key_states = {}
        self.previewing = False
        self.starting = False
        self.settings = read_config()
        self.threshold = tk.StringVar(value=str(self.settings["threshold"]))
        self.interval = tk.StringVar(value=str(self.settings["interval"]))
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
        ttk.Label(frame, text="宝箱图案已内置，更换桌宠也能直接使用。", style="Chest.TLabel", wraplength=350).pack(anchor="w", pady=(0, 10))
        steps = ("① 点击“开始领取”，等待金色宝箱出现。\n"
                 "② 点击后图案消失，两个宝箱各自刷新、各自领取。\n"
                 "③ 保持桌面解锁、按钮可见；F8 暂停，F12 退出。")
        ttk.Label(frame, text=steps, style="Chest.TLabel", justify="left", wraplength=350).pack(anchor="w", pady=(0, 12))
        self.admin_row = ttk.Frame(frame, style="Chest.TFrame")
        self.admin_row.pack(fill="x", pady=(0, 4))
        self.admin_button = ttk.Button(self.admin_row, text="以管理员身份重新启动", style="Chest.TButton", command=self.restart_admin)
        options = ttk.Frame(frame, style="Chest.TFrame")
        options.pack(fill="x", pady=5)
        ttk.Label(options, text="相似度阈值", style="Chest.TLabel").grid(row=0, column=0, sticky="w", pady=4)
        ttk.Spinbox(options, from_=.90, to=.99, increment=.01, textvariable=self.threshold, width=8).grid(row=0, column=1, sticky="e", padx=8, pady=4)
        ttk.Label(options, text="检查间隔（秒）", style="Chest.TLabel").grid(row=1, column=0, sticky="w", pady=4)
        ttk.Spinbox(options, from_=1, to=30, increment=1, textvariable=self.interval, width=8).grid(row=1, column=1, sticky="e", padx=8, pady=4)
        options.columnconfigure(0, weight=1)
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
        self.root.after(100, self.poll)
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

    def get_settings(self):
        threshold, interval = float(self.threshold.get()), float(self.interval.get())
        if not .90 <= threshold <= .99 or not 1 <= interval <= 30:
            raise ValueError("相似度应为 0.90～0.99，检查间隔应为 1～30 秒。")
        CONFIG.write_text(json.dumps({"threshold": threshold, "interval": interval}, indent=2), encoding="utf-8")
        return threshold, interval

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
            threshold, interval = self.get_settings()
            self.watcher.close()
            self.starting = True
            self.status.set("正在启动识别……")
            self.root.after(50, lambda: self.finish_start(templates, threshold, interval))
        except Exception as exc:
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

    def finish_start(self, templates, threshold, interval):
        if not self.starting:
            return
        if self.watcher.thread and self.watcher.thread.is_alive():
            self.root.after(100, lambda: self.finish_start(templates, threshold, interval))
            return
        self.starting = False
        self.watcher.start(templates, threshold, interval)
        self.append_log("已开始：连续两帧命中、点击前复核，鼠标键盘空闲后点击。")
        self.start_button.configure(text="暂停领取  /  F8")
        self.status.set("正在等待宝箱出现……")

    def pause(self):
        self.starting = False
        self.watcher.pause()
        self.start_button.configure(text="继续领取  /  F8")
        self.status.set("已暂停。F8 继续，F12 退出。")

    def preview(self):
        from tkinter import messagebox
        if self.previewing:
            return
        self.pause()
        try:
            templates = load_templates()
            threshold, _ = self.get_settings()
        except Exception as exc:
            messagebox.showerror("无法识别", str(exc), parent=self.root)
            return
        self.previewing = True
        self.root.withdraw()

        def worker():
            try:
                results = scan_once(templates, threshold)
                lines = [f"宝箱 {i}：相似度 {r['score']:.3f}，位置（{r['x']}，{r['y']}）"
                         for i, r in enumerate(results, 1)]
                self.events.put(("preview", "\n".join(lines) if lines else "当前未发现可领取的宝箱，可能正在倒计时或被遮挡。"))
            except Exception as exc:
                self.events.put(("preview", f"识别失败：{exc}"))

        self.root.after(350, lambda: threading.Thread(target=worker, daemon=True).start())

    def poll(self):
        from tkinter import messagebox
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
        self.root.after(100, self.poll)

    def close(self):
        self.starting = False
        self.watcher.close()
        self.root.destroy()

    def run(self):
        self.root.mainloop()


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
        screen = Image.new("RGB", (160, 100), (30, 45, 60))
        for (_, template), position in zip(templates, ((20, 20), (90, 60))):
            screen.paste(template, position)
        detections = detect_templates(screen, templates, .94)
        if len(detections) != 2:
            raise RuntimeError("Built-in recognition self-test failed")
        root = tk.Tk()
        root.withdraw()
        photo = ImageTk.PhotoImage(background, master=root)
        if (photo.width(), photo.height()) != (708, 807):
            raise RuntimeError("Embedded background has unexpected dimensions")
        root.update_idletasks()
        report.update(ok=True, background=list(background.size), detected_chests=len(detections),
                      tk=str(root.tk.call("info", "patchlevel")))
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
    args = parser.parse_args()
    desktop.enable_dpi_awareness()
    if args.self_test is not None:
        raise SystemExit(runtime_self_test(args.self_test))
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
        print(json.dumps(scan_once(templates, read_config()["threshold"]), ensure_ascii=False, indent=2))
        return
    setup_logging()
    App().run()


if __name__ == "__main__":
    main()
