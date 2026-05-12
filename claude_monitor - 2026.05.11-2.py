import tkinter as tk
import threading
import time
import re
import ctypes
import winpty
from datetime import datetime, timezone, timedelta

TAIPEI_TZ = timezone(timedelta(hours=8))
_MONTHS = {'jan':1,'feb':2,'mar':3,'apr':4,'may':5,'jun':6,
           'jul':7,'aug':8,'sep':9,'oct':10,'nov':11,'dec':12}

CLAUDE_PATH = r'C:\Users\Soon666\AppData\Roaming\npm\node_modules\@anthropic-ai\claude-code\bin\claude.exe'
SECTION_LABELS = {
    "Current session":           "Current session",
    "Current week (all models)": "Weekly limits",
    "Extra usage":               "Extra usage",
}
_ANSI_RE = re.compile(r'\x1B(?:[@-Z\\-_]|\[[0-?]*[ -/]*[@-~])')


class ClaudeHUD:
    def __init__(self):
        self.root = tk.Tk()
        self.root.overrideredirect(True)
        self.root.attributes("-topmost", True)
        self.root.attributes("-alpha", 0.75)
        self.root.configure(bg='#121212')
        self.root.config(highlightbackground="#333333", highlightthickness=1)
        self.root.geometry("+50+50")

        title_bar = tk.Frame(self.root, bg="#121212")
        title_bar.pack(fill="x", padx=10, pady=(5, 0))

        title = tk.Label(
            title_bar, text="CLAUDE STATUS", fg="#d77757", bg="#121212",
            font=("Segoe UI", 7, "bold")
        )
        title.pack(side="left")

        btn_close = tk.Label(
            title_bar, text="✕", fg="#555555", bg="#121212",
            font=("Segoe UI", 8, "bold"), cursor="hand2"
        )
        btn_close.pack(side="right", padx=(4, 0))
        btn_close.bind("<Button-1>", lambda _: self._on_close())
        btn_close.bind("<Enter>", lambda _: btn_close.config(fg="#FF4500"))
        btn_close.bind("<Leave>", lambda _: btn_close.config(fg="#555555"))

        btn_refresh = tk.Label(
            title_bar, text="↺", fg="#555555", bg="#121212",
            font=("Segoe UI", 9), cursor="hand2"
        )
        btn_refresh.pack(side="right", padx=(4, 0))
        btn_refresh.bind("<Button-1>", lambda _: threading.Thread(target=self.update_usage, daemon=True).start())
        btn_refresh.bind("<Enter>", lambda _: btn_refresh.config(fg="#00FF7F"))
        btn_refresh.bind("<Leave>", lambda _: btn_refresh.config(fg="#555555"))

        title_bar.bind("<Button-1>", self.start_move)
        title_bar.bind("<B1-Motion>", self.do_move)
        title_bar.bind("<Button-3>", self.show_menu)
        title.bind("<Button-1>", self.start_move)
        title.bind("<B1-Motion>", self.do_move)
        title.bind("<Button-3>", self.show_menu)

        self.content_text = tk.Text(
            self.root, bg="#121212", fg="#00FF7F", relief="flat",
            font=("Consolas", 9), padx=15, pady=10,
            state="disabled", cursor="arrow", wrap="none",
            highlightthickness=0
        )
        self.content_text.tag_configure("main", font=("Consolas", 10), foreground="#d77757")
        self.content_text.tag_configure("sub",  font=("Consolas", 8),  foreground="#d77757")
        self.content_text.tag_configure("dim",  font=("Consolas", 9),  foreground="#d77757")
        self.content_text.tag_configure("warn", font=("Consolas", 9),  foreground="#FFA500")
        self.content_text.tag_configure("accent", font=("Consolas", 9), foreground="#d77757")
        self.content_text.tag_configure("bar",    font=("Consolas", 10), foreground="#00FF7F")
        self.content_text.tag_configure("err",  font=("Consolas", 9),  foreground="#FF4500")
        self.content_text.config(height=10, width=44)
        self.content_text.pack(anchor="w")
        self.content_text.bind("<Button-1>", self.start_move)
        self.content_text.bind("<B1-Motion>", self.do_move)
        self.root.bind("<Button-1>", self.start_move)
        self.root.bind("<B1-Motion>", self.do_move)
        self.root.bind("<Button-3>", self.show_menu)
        self.create_menu()

        self.pty = None
        self.buf = []
        self.buf_lock = threading.Lock()
        self.x = self.y = 0

        self.root.after(100, self._set_rounded_corners)
        threading.Thread(target=self.refresh_loop, daemon=True).start()

    def create_menu(self):
        self.menu = tk.Menu(self.root, tearoff=0)
        self.menu.add_command(label="手動刷新", command=lambda: threading.Thread(target=self.update_usage, daemon=True).start())
        self.menu.add_separator()
        self.menu.add_command(label="退出", command=self._on_close)

    def _set_rounded_corners(self):
        hwnd = ctypes.windll.user32.GetParent(self.root.winfo_id())
        ctypes.windll.dwmapi.DwmSetWindowAttribute(
            hwnd, 33, ctypes.byref(ctypes.c_int(2)), ctypes.sizeof(ctypes.c_int)
        )

    def show_menu(self, event):
        self.menu.post(event.x_root, event.y_root)

    def _strip(self, text):
        return _ANSI_RE.sub('', text)

    def _buf_snapshot(self, start=0):
        with self.buf_lock:
            snapshot = self.buf[start:]
        return self._strip("".join(snapshot))

    def _wait_for(self, predicate, timeout=15):
        deadline = time.time() + timeout
        while time.time() < deadline:
            if predicate():
                return True
            time.sleep(0.05)
        return False

    def _write_text(self, segments):
        def _do():
            self.content_text.config(state="normal")
            self.content_text.delete("1.0", "end")
            for text, tag in segments:
                self.content_text.insert("end", text, tag)
            lines = int(self.content_text.index("end-1c").split(".")[0])
            width = max(len(t) for t, _ in segments if t.strip())
            self.content_text.config(state="disabled", height=max(lines, 10), width=max(width, 44))
        self.root.after(0, _do)

    def _set_status(self, text, tag="dim"):
        self._write_text([(text, tag)])

    def parse_usage(self, text):
        sections = {key: {"pct": None, "resets": None, "spent": None} for key in SECTION_LABELS}
        for key in sections:
            idx = text.find(key)
            if idx == -1:
                idx = text.find(key.replace(" ", ""))
            if idx == -1:
                continue
            chunk = text[idx:idx+300]
            m = re.search(r'(\d+)%\s*used', chunk)
            if m:
                sections[key]["pct"] = int(m.group(1))
            m2 = re.search(r'Resets[^\n\r]{1,40}', chunk)
            if m2:
                sections[key]["resets"] = m2.group(0).strip()
            m3 = re.search(r'\$[\d.]+\s*/\s*\$[\d.]+\s+spent', chunk)
            if m3:
                sections[key]["spent"] = m3.group(0)
        return sections

    def format_bar(self, pct, width=20):
        filled = round(pct / 100 * width)
        return "█" * filled + "░" * (width - filled)

    def _format_resets(self, text):
        if not text:
            return text
        m = re.search(r'Resets\s*([A-Za-z]{3})\s*(\d+)\s*,?\s*(\d+)\s*(am|pm)', text, re.I)
        if m:
            mon, day, hr, ap = m.group(1), int(m.group(2)), int(m.group(3)), m.group(4).lower()
            if ap == 'pm' and hr != 12: hr += 12
            elif ap == 'am' and hr == 12: hr = 0
            now = datetime.now(TAIPEI_TZ)
            dt = datetime(now.year, _MONTHS[mon.lower()], day, hr, 0, tzinfo=TAIPEI_TZ)
            if dt < now:
                dt = datetime(now.year + 1, _MONTHS[mon.lower()], day, hr, 0, tzinfo=TAIPEI_TZ)
            return f"Resets {dt.strftime('%a')} {dt.strftime('%I:%M%p').lstrip('0').lower()} (Asia/Taipei)"
        m = re.search(r'Resets\s*(\d+)\s*(am|pm)', text, re.I)
        if m:
            hr, ap = int(m.group(1)), m.group(2).lower()
            if ap == 'pm' and hr != 12: hr += 12
            elif ap == 'am' and hr == 12: hr = 0
            now = datetime.now(TAIPEI_TZ)
            dt = now.replace(hour=hr, minute=0, second=0, microsecond=0)
            if dt <= now:
                dt += timedelta(days=1)
            d = dt - now
            h, mn = int(d.total_seconds() // 3600), int((d.total_seconds() % 3600) // 60)
            return f"Resets in {h}hr {mn:02d}min (Asia/Taipei)"
        return text

    def start_pty(self):
        with self.buf_lock:
            self.buf = []
        self.pty = winpty.PtyProcess.spawn(CLAUDE_PATH, dimensions=(24, 80))

        def reader():
            while True:
                try:
                    chunk = self.pty.read(4096)
                    if chunk:
                        with self.buf_lock:
                            self.buf.append(chunk)
                except Exception:
                    break

        threading.Thread(target=reader, daemon=True).start()
        return self._wait_for(lambda: ">\xa0" in self._buf_snapshot(), timeout=15)

    def ensure_pty(self):
        if self.pty is not None:
            try:
                self.pty.close()
            except Exception:
                pass
            self.pty = None
        return self.start_pty()

    def update_usage(self):
        self._set_status("  抓取中...\n\n       ▐▛███▜▌\n      ▝▜█████▛▘\n        ▘▘ ▝▝", "accent")
        try:
            if not self.ensure_pty():
                raise Exception("PTY 啟動失敗")

            with self.buf_lock:
                start_idx = len(self.buf)

            self.pty.write("/usage\r")
            self._wait_for(lambda: "spent" in self._buf_snapshot(start_idx))

            time.sleep(0.2)
            self.pty.write("\x1b")
            time.sleep(0.3)

            new = self._buf_snapshot(start_idx)
            with self.buf_lock:
                self.buf = self.buf[start_idx:]

            sections = self.parse_usage(new)
            current_time = time.strftime("%H:%M:%S")

            pad = max(len(l) for l in SECTION_LABELS.values())
            segs = [
                (f"CLAUDE USAGE  {current_time}\n", "dim"),
                ("─" * 28 + "\n", "dim"),
            ]
            for i, (key, label) in enumerate(SECTION_LABELS.items()):
                if i > 0:
                    segs.append(("\n", "dim"))
                data = sections[key]
                pct = data["pct"]
                if pct is None:
                    segs.append((f"{label:<{pad}}  --\n", "main"))
                    continue
                bar_tag = "err" if pct >= 90 else "warn" if pct >= 70 else "bar"
                segs.append((f"{label:<{pad}}  ", "main"))
                segs.append((f"{self.format_bar(pct)} {pct}%\n", bar_tag))
                if data.get("spent"):
                    segs.append((f"  {data['spent']}\n", "sub"))
                if data.get("resets"):
                    segs.append((f"  {self._format_resets(data['resets'])}\n", "sub"))

            self._write_text(segs)

        except Exception as e:
            if self.pty is not None:
                try:
                    self.pty.close()
                except Exception:
                    pass
                self.pty = None
            self._set_status(f"抓取失敗: {e}", "err")

    def refresh_loop(self):
        while True:
            self.update_usage()
            time.sleep(30)

    def start_move(self, event):
        self.x = event.x
        self.y = event.y
        return "break"

    def do_move(self, event):
        x = self.root.winfo_x() + (event.x - self.x)
        y = self.root.winfo_y() + (event.y - self.y)
        self.root.geometry(f"+{x}+{y}")
        return "break"

    def _on_close(self):
        if self.pty is not None:
            try:
                self.pty.close()
            except Exception:
                pass
        self.root.destroy()

    def run(self):
        self.root.protocol("WM_DELETE_WINDOW", self._on_close)
        self.root.mainloop()


if __name__ == "__main__":
    app = ClaudeHUD()
    app.run()
