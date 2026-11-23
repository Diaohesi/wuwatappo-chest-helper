"""Notification area lifecycle without moving the mouse or starting recognition."""
import os
import queue
import unittest
from unittest.mock import MagicMock, patch

import auto_chest

if os.name == "nt":
    import tkinter as tk
    import chest_tray as tray


@unittest.skipUnless(os.name == "nt", "Windows notification area")
class TrayTests(unittest.TestCase):
    def setUp(self):
        self.app = app = auto_chest.App.__new__(auto_chest.App)
        app.root = MagicMock()
        app.tk = tk
        app.tray = None
        app.closing = app.previewing = app.starting = False
        app.clicker_opening = app.clicker_was_running = False
        app.watcher = auto_chest.Watcher(queue.Queue())
        app.clicker = auto_chest.ClickerProcess()
        app.events = queue.Queue()
        app.key_states = {}
        self.enterContext(patch.object(auto_chest.desktop, "hotkey_down", return_value=False))

    def test_register_before_hiding_and_restore_same_state(self):
        app = self.app
        for running in (True, False):
            with self.subTest(running=running), patch.object(tray, "TrayIcon") as make:
                app.watcher.active.set() if running else app.watcher.active.clear()
                app.root.reset_mock()
                make.side_effect = lambda *_: (app.root.withdraw.assert_not_called(), MagicMock())[1]
                app.hide_to_tray()
                icon = app.tray
                app.hide_to_tray()
                make.assert_called_once()
                app.root.withdraw.assert_called_once()
                self.assertEqual(app.watcher.active.is_set(), running)
                app.events.put(("tray_restore", icon))
                app.events.put(("tray_exit", icon))  # A stale second callback cannot close a restored app.
                app.poll()
                app.root.deiconify.assert_called_once()
                icon.close.assert_called_once()
                self.assertIsNone(app.tray)
                self.assertFalse(app.closing)
                self.assertEqual(app.watcher.active.is_set(), running)

    def test_preview_does_not_create_icon_and_registration_failure_leaves_window_visible(self):
        app = self.app
        with patch.object(tray, "TrayIcon", side_effect=OSError("Explorer unavailable")) as make, \
                patch("tkinter.messagebox.showerror") as error:
            app.previewing = True
            app.hide_to_tray()
            make.assert_not_called()
            app.previewing = False
            app.hide_to_tray()
            error.assert_called_once()
            app.root.withdraw.assert_not_called()
            self.assertIsNone(app.tray)

    def test_right_menu_exit_uses_normal_shutdown(self):
        app = self.app
        app.tray = icon = MagicMock()
        app.events.put(("tray_exit", icon))
        with patch.object(app.clicker, "request_close") as close_clicker:
            app.poll()
        icon.close.assert_called_once()
        close_clicker.assert_called_once()
        self.assertTrue(app.watcher.stop_event.is_set())
        app.root.destroy.assert_called_once()

    def test_native_shell_registration_restart_and_menu_commands(self):
        find = tray._api(tray._user, "FindWindowW", tray.W.HWND, tray.W.LPCWSTR, tray.W.LPCWSTR)
        if not find("Shell_TrayWnd", None):
            self.skipTest("No Explorer notification area in this session")
        root = tk.Tk()
        root.withdraw()
        self.addCleanup(root.destroy)
        root.update_idletasks()
        events = queue.SimpleQueue()
        icon = tray.TrayIcon(root.winfo_id(), events)
        self.addCleanup(icon.close)
        # Forget only our own icon, then deliver the same message Explorer sends after restarting.
        self.assertTrue(tray._notify(tray.NIM_DELETE, tray.C.byref(icon.data)))
        tray._send(icon.hwnd, icon.recreated, 0, 0)
        self.assertTrue(events.empty())
        self.assertTrue(tray._notify(tray.NIM_SETVERSION, tray.C.byref(icon.data)))
        for command, expected in ((0, None), (1, "tray_restore"), (2, "tray_exit")):
            with patch.object(tray, "_track", return_value=command), patch.object(tray, "_foreground"):
                tray._send(icon.hwnd, tray.CALLBACK, -1, tray.ICON_ID << 16 | tray.WM_CONTEXTMENU)
            if expected:
                self.assertEqual(events.get_nowait(), (expected, icon))
            self.assertTrue(events.empty())
        with patch.object(icon, "_add", side_effect=OSError("Explorer unavailable")), self.assertLogs(level="ERROR"):
            tray._send(icon.hwnd, icon.recreated, 0, 0)
        self.assertEqual(events.get_nowait(), ("tray_restore", icon))

    def test_low_integrity_denial_is_optional_only_for_self_test(self):
        with patch.object(tray, "integrity_level", return_value=0x1000), \
                patch.object(tray, "_notify", return_value=False), patch.object(tray, "_filter") as filter_msg:
            with self.assertRaises(tray.RestrictedTrayError):
                tray.TrayIcon(0, queue.SimpleQueue())
            with patch.object(tray, "_api", return_value=lambda *_: 1):
                self.assertEqual(tray.self_test(0), "restricted_process")
            filter_msg.assert_not_called()
