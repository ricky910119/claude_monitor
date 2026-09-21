"""Claude + Codex quota HUD. Both providers stay visible and refresh independently."""
import argparse
import ctypes
from ctypes import wintypes
from datetime import datetime
import json
import logging
from logging.handlers import RotatingFileHandler
import os
from pathlib import Path
import queue
import sys
import threading
import tkinter as tk

from usage_sources import UsageError, fetch_claude, fetch_codex

BASE_DIR = Path(__file__).resolve().parent
CLAUDE_PATH = os.environ.get("CLAUDE_MONITOR_CLAUDE_PATH", str(Path.home() / ".local/bin/claude.exe"))
CODEX_PATH = os.environ.get("CLAUDE_MONITOR_CODEX_PATH", str(Path.home() / "AppData/Local/Programs/OpenAI/Codex/bin/codex.exe"))
CODEX_CWD = str(BASE_DIR)
REFRESH_SECONDS = 180
COLORS = {
    "background": "#232731",
    "border": "#3D4657",
    "claude": "#DEB39B",
    "codex": "#96BAA5",
    "text": "#E8DFCF",
    "waiting": "#B0BBD0",
    "stale": "#8C95A6",
    "success": "#84AB8D",
    "initial": "#747E8F",
    "loading": "#D9BD7E",
    "error": "#D48080",
    "button": "#9EABC0",
    "button_active_bg": "#384354",
    "button_active_fg": "#F0F4FA",
    "tooltip_bg": "#2D323E",
    "tooltip_text": "#E8DFCF",
    "usage_low": "#A8C2B5",
    "usage_medium": "#DDBE8F",
    "usage_high": "#D99393",
}
BG = COLORS["background"]
GREEN = COLORS["success"]


def usage_color(used_percent):
    if used_percent > 80:
        return COLORS["usage_high"]
    if used_percent > 40:
        return COLORS["usage_medium"]
    return COLORS["usage_low"]


def make_logger():
    logger = logging.getLogger("quota_monitor")
    if not logger.handlers:
        handler = RotatingFileHandler(BASE_DIR / "monitor_diagnostics.log", maxBytes=100_000,
                                      backupCount=1, encoding="utf-8")
        handler.setFormatter(logging.Formatter("%(asctime)s %(message)s"))
        logger.addHandler(handler)
        logger.setLevel(logging.INFO)
    return logger


def monitor_work_area():
    """Use DISPLAY2's work area; fall back to the primary after unplugging."""
    class MonitorInfo(ctypes.Structure):
        _fields_ = [("cbSize", wintypes.DWORD), ("rcMonitor", wintypes.RECT),
                    ("rcWork", wintypes.RECT), ("dwFlags", wintypes.DWORD),
                    ("szDevice", wintypes.WCHAR * 32)]
    found = []
    ctypes.windll.user32.GetMonitorInfoW.argtypes = [wintypes.HANDLE, ctypes.c_void_p]
    ctypes.windll.user32.GetMonitorInfoW.restype = wintypes.BOOL
    callback_type = ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HANDLE, wintypes.HDC,
                                      ctypes.POINTER(wintypes.RECT), wintypes.LPARAM)
    def collect(handle, dc, rect, data):
        info = MonitorInfo()
        info.cbSize = ctypes.sizeof(info)
        if ctypes.windll.user32.GetMonitorInfoW(handle, ctypes.byref(info)):
            found.append((info.szDevice, bool(info.dwFlags & 1),
                          (info.rcWork.left, info.rcWork.top, info.rcWork.right, info.rcWork.bottom)))
        return True
    callback = callback_type(collect)
    ctypes.windll.user32.EnumDisplayMonitors(None, None, callback, 0)
    if not found:
        return (0, 0, 1920, 1032)
    return next((r for name, primary, r in found if name.endswith("DISPLAY2")),
                next((r for name, primary, r in found if primary), found[0][2]))


class ClaudeHUD:
    def __init__(self, auto_refresh=True):
        self.root = tk.Tk()
        self.root.title("Claude + Codex Usage")
        self.root.overrideredirect(True)
        self.root.attributes("-topmost", True)
        self.root.attributes("-alpha", 0.97)
        self.root.configure(bg=BG)
        self.stop = threading.Event()
        self.events = queue.Queue()
        self.busy = {name: False for name in ("Claude", "Codex")}
        self.last_success = {}
        self.workers = []
        self.pinned = True
        self.dragged = False
        self.drag_origin = None
        self.logger = make_logger()
        self.widgets = {}
        self.status = {}
        self.details = {agent: "百分比代表已使用額度；等待首次更新。" for agent in self.busy}
        self.tooltip = None
        self.tooltip_job = None
        outer = tk.Frame(self.root, bg=BG, padx=12, pady=7,
                         highlightbackground=COLORS["border"], highlightthickness=1)
        outer.pack(fill="both", expand=True)
        for agent in ("Claude", "Codex"):
            if agent == "Codex":
                tk.Frame(outer, bg=COLORS["border"], width=1, height=16).pack(side="left", padx=14)
            frame = tk.Frame(outer, bg=BG)
            frame.pack(side="left")
            brand = tk.Label(frame, text=agent, bg=BG,
                             fg=COLORS[agent.lower()],
                             font=("Segoe UI", 10, "bold"))
            brand.pack(side="left", padx=(0, 7))
            dot = tk.Label(frame, text="·", bg=BG, fg=COLORS["initial"], font=("Segoe UI", 11, "bold"))
            dot.pack(side="left", padx=(0, 5))
            self.status[agent] = dot
            widget = tk.Frame(frame, bg=BG)
            widget.pack(side="left")
            self.widgets[agent] = widget
            self._write(agent, [("等待更新", COLORS["waiting"])])
            for target in (frame, brand, dot, widget):
                target.bind("<Enter>", lambda event, a=agent: self._queue_tooltip(a))
                target.bind("<Leave>", lambda event: self._hide_tooltip())
        tk.Frame(outer, bg=COLORS["border"], width=1, height=16).pack(side="left", padx=(14, 8))
        self.pin_button = tk.Button(outer, text="固定", command=self._toggle_pin)
        self.pin_button.pack(side="left")
        controls = [self.pin_button]
        for text, command in (("↻", self.refresh_all), ("×", self._on_close)):
            button = tk.Button(outer, text=text, command=command)
            button.pack(side="left")
            controls.append(button)
        for button in controls:
            button.config(bg=BG, fg=COLORS["button"], activebackground=COLORS["button_active_bg"],
                          activeforeground=COLORS["button_active_fg"], relief="flat", bd=0,
                          font=("Segoe UI", 9, "bold"), padx=6, pady=0, cursor="hand2")
        self.root.bind("<ButtonPress-1>", self.start_move)
        self.root.bind("<B1-Motion>", self.do_move)
        self.root.bind("<Button-3>", self.show_menu)
        self.menu = tk.Menu(self.root, tearoff=0, font=("Segoe UI", 9, "bold"))
        self.menu.add_command(label="更新兩者", command=self.refresh_all)
        self.menu.add_command(label="回到螢幕 2 左下角", command=self.reset_position)
        self.menu.add_separator()
        self.menu.add_command(label="退出", command=self._on_close)
        self.root.protocol("WM_DELETE_WINDOW", self._on_close)
        self.root.after(100, self._position)
        self.root.after(120, self._rounded_corners)
        self.root.after(100, self._poll)
        if auto_refresh:
            self.root.after(150, self._scheduled_refresh)

    def _position(self):
        if self.stop.is_set() or self.dragged:
            return
        self.root.update_idletasks()
        left, top, right, bottom = monitor_work_area()
        self._move(left + 30, max(top + 10, bottom - self.root.winfo_height() - 30))

    def _move(self, x, y):
        self._move_window(self.root, x, y)

    @staticmethod
    def _move_window(window, x, y):
        user32 = ctypes.windll.user32
        user32.GetParent.argtypes = [wintypes.HWND]
        user32.GetParent.restype = wintypes.HWND
        user32.SetWindowPos.argtypes = [wintypes.HWND, wintypes.HWND, ctypes.c_int,
                                       ctypes.c_int, ctypes.c_int, ctypes.c_int, ctypes.c_uint]
        user32.SetWindowPos(user32.GetParent(window.winfo_id()), None, int(x), int(y), 0, 0, 0x0015)

    def _rounded_corners(self):
        try:
            hwnd = ctypes.windll.user32.GetParent(self.root.winfo_id())
            ctypes.windll.dwmapi.DwmSetWindowAttribute(wintypes.HWND(hwnd), 33,
                ctypes.byref(ctypes.c_int(2)), ctypes.sizeof(ctypes.c_int))
        except (AttributeError, OSError):
            pass

    def _queue_tooltip(self, agent):
        self._hide_tooltip()
        self.tooltip_job = self.root.after(350, lambda: self._show_tooltip(agent))

    def _show_tooltip(self, agent):
        self.tooltip_job = None
        self.tooltip = tk.Toplevel(self.root)
        self.tooltip.overrideredirect(True)
        self.tooltip.attributes("-topmost", True)
        tk.Label(self.tooltip, text=f"{agent} · 已使用額度\n{self.details[agent]}",
                 justify="left", bg=COLORS["tooltip_bg"], fg=COLORS["tooltip_text"], font=("Segoe UI", 9, "bold"),
                 padx=12, pady=9, wraplength=520).pack()
        self.tooltip.update_idletasks()
        left, top, right, bottom = monitor_work_area()
        x = max(left + 10, min(self.root.winfo_x(), right - self.tooltip.winfo_width() - 10))
        y = max(top + 10, self.root.winfo_y() - self.tooltip.winfo_height() - 8)
        self._move_window(self.tooltip, x, y)

    def _hide_tooltip(self):
        if self.tooltip_job is not None:
            self.root.after_cancel(self.tooltip_job)
            self.tooltip_job = None
        if self.tooltip is not None:
            self.tooltip.destroy()
            self.tooltip = None

    def reset_position(self):
        self.dragged = False
        self._position()

    def _toggle_pin(self):
        self.pinned = not self.pinned
        self.pin_button.config(text="固定" if self.pinned else "拖曳",
                               fg=COLORS["button"] if self.pinned else GREEN)

    def start_move(self, event):
        if not self.pinned and not isinstance(event.widget, tk.Button):
            self.drag_origin = (event.x_root, event.y_root, self.root.winfo_x(), self.root.winfo_y())

    def do_move(self, event):
        if not self.pinned and self.drag_origin:
            x, y, wx, wy = self.drag_origin
            self.dragged = True
            self._move(wx + event.x_root - x, wy + event.y_root - y)

    def show_menu(self, event):
        self.menu.tk_popup(event.x_root, event.y_root)

    def _write(self, agent, segments):
        container = self.widgets[agent]
        for child in container.winfo_children():
            child.destroy()
        for text, color in segments:
            label = tk.Label(container, text=text, bg=BG, fg=color,
                             font=("Segoe UI", 10, "bold"), padx=0, pady=0, bd=0)
            label.pack(side="left")
            label.bind("<Enter>", lambda event, a=agent: self._queue_tooltip(a))
            label.bind("<Leave>", lambda event: self._hide_tooltip())
        self.root.after_idle(self._position)

    def refresh_all(self):
        if self.stop.is_set():
            return
        for delay, (agent, path, fetch) in enumerate(
                (("Claude", CLAUDE_PATH, fetch_claude),
                 ("Codex", CODEX_PATH, fetch_codex))):
            if self.busy[agent]:
                continue
            self.busy[agent] = True
            self.status[agent].config(text="·", fg=COLORS["loading"])
            if agent not in self.last_success:
                self._write(agent, [("讀取中…", COLORS["waiting"])])
            # Starting two interactive CLIs at the same instant increases loader,
            # ConPTY, and network pressure. Reserve both slots now, launch Codex
            # five seconds after Claude, and keep same-provider overlap blocked.
            self.root.after(delay * 5000, self._start_fetch, agent, path, fetch)

    def _start_fetch(self, agent, path, fetch):
        if self.stop.is_set():
            self.busy[agent] = False
            return
        worker = threading.Thread(target=self._fetch, args=(agent, path, fetch), daemon=True)
        self.workers = [w for w in self.workers if w.is_alive()]
        self.workers.append(worker)
        worker.start()

    def _fetch(self, agent, path, fetch):
        try:
            self.events.put((agent, fetch(path, CODEX_CWD, self.stop), None))
        except UsageError as exc:
            self.events.put((agent, None, str(exc)))
        except Exception as exc:
            self.events.put((agent, None, f"抓取失敗：{type(exc).__name__}"))

    def _last_text(self, agent):
        return f" · 上次成功 {self.last_success[agent]}" if agent in self.last_success else ""

    def _poll(self):
        if self.stop.is_set():
            return
        while True:
            try:
                agent, data, error = self.events.get_nowait()
            except queue.Empty:
                break
            self.busy[agent] = False
            if error:
                self.status[agent].config(text="!", fg=COLORS["error"])
                if agent not in self.last_success:
                    self._write(agent, [("待確認", COLORS["waiting"])])
                else:
                    for label in self.widgets[agent].winfo_children():
                        label.config(fg=COLORS["stale"])
                previous = self.details[agent].split("\n上次結果：")[-1]
                self.details[agent] = error + self._last_text(agent) + "\n上次結果：" + previous
                self.logger.info("%s %s", agent, error)
            else:
                self.last_success[agent] = datetime.now().strftime("%H:%M:%S")
                self.status[agent].config(text="·", fg=GREEN)
                summaries = []
                details = ["更新成功 " + self.last_success[agent]]
                for quota in data:
                    if summaries:
                        summaries.append(("   ", COLORS["text"]))
                    label = {"本次額度": "本次", "每週（全部模型）": "每週", "每週額度": "每週",
                             "用量點數": "點數", "額外用量": "額外"}.get(quota.label, quota.label)
                    label = label.replace(" 小時額度", "h").replace(" 分鐘額度", "m")
                    if label.startswith("Current week ("):
                        label = label[len("Current week ("):].rstrip(")")
                    if quota.used is None:
                        summaries.append((f"{label} {'關' if quota.note == '未啟用' else '—'}", COLORS["text"]))
                        details.append(f"{quota.label}：{quota.note}")
                        continue
                    summaries.append((f"{label} ", COLORS["text"]))
                    summaries.append((f"{quota.used:g}%", usage_color(quota.used)))
                    details.append(f"{quota.label}：已用 {quota.used:g}%")
                    if quota.resets:
                        details.append("  " + quota.resets)
                self.details[agent] = "\n".join(details)
                self._write(agent, summaries)
                values = ", ".join(
                    f"{quota.label}={quota.used:g}%" if quota.used is not None
                    else f"{quota.label}=n/a"
                    for quota in data)
                self.logger.info("%s success quota_windows=%d values=%s", agent, len(data), values)
        self.root.after(100, self._poll)

    def _scheduled_refresh(self):
        self.refresh_all()
        if not self.stop.is_set():
            self.root.after(REFRESH_SECONDS * 1000, self._scheduled_refresh)

    def _on_close(self):
        if self.stop.is_set():
            return
        self.stop.set()
        self._hide_tooltip()
        self.root.withdraw()
        self._finish_close()

    def _finish_close(self):
        if any(w.is_alive() for w in self.workers):
            self.root.after(100, self._finish_close)
        else:
            self.root.destroy()

    def run(self):
        from monitor_dock import install_dock
        install_dock(self)
        self.root.mainloop()


def diagnose(agent):
    stop = threading.Event()
    failed = False
    for name, path, fetch in (("claude", CLAUDE_PATH, fetch_claude), ("codex", CODEX_PATH, fetch_codex)):
        if agent not in (name, "all"):
            continue
        try:
            quotas = fetch(path, CODEX_CWD, stop)
            print(json.dumps({"agent": name, "ok": True, "quotas": [vars(q) for q in quotas]}, ensure_ascii=True))
        except Exception as exc:
            failed = True
            detail = str(exc) if isinstance(exc, UsageError) else type(exc).__name__
            print(json.dumps({"agent": name, "ok": False, "error": detail}, ensure_ascii=True))
    return int(failed)


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--diagnose", choices=("claude", "codex", "all"))
    args = parser.parse_args()
    if args.diagnose:
        sys.exit(diagnose(args.diagnose))
    try:
        ClaudeHUD().run()
    except Exception as exc:
        make_logger().error("GUI startup failed: %s", type(exc).__name__)
        ctypes.windll.user32.MessageBoxW(None, f"啟動失敗：{type(exc).__name__}\n請使用 python.exe 啟動查看錯誤。", "Usage Monitor", 0x10)
        raise
