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
    "Current session":           "Current",
    "Current week (all models)": "Weekly",
    "Extra usage":               "Extra",
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
        self.root.geometry("+30+1106")
        # self.root.bind("<Control-s>", lambda _: self._save_screenshot())
        
        # Outer frame: sections left, buttons top-right
        outer = tk.Frame(self.root, bg="#121212")
        outer.pack(fill="both", expand=True)

        # Buttons frame — top-right corner
        buttons_frame = tk.Frame(outer, bg="#121212")
        buttons_frame.pack(side="right", anchor="n", padx=(0, 6), pady=(4, 0))

        self.btn_pin = tk.Label(
            buttons_frame, text="⊡", fg="#00FF7F", bg="#121212",
            font=("Segoe UI", 9), cursor="hand2"
        )
        self.btn_pin.pack(side="left", padx=(0, 4))
        self.btn_pin.bind("<Button-1>", lambda _: self._toggle_pin())

        btn_refresh = tk.Label(
            buttons_frame, text="↺", fg="#555555", bg="#121212",
            font=("Segoe UI", 9), cursor="hand2"
        )
        btn_refresh.pack(side="left", padx=(0, 4))
        btn_refresh.bind("<Button-1>", lambda _: threading.Thread(target=self.update_usage, daemon=True).start())
        btn_refresh.bind("<Enter>", lambda _: btn_refresh.config(fg="#00FF7F"))
        btn_refresh.bind("<Leave>", lambda _: btn_refresh.config(fg="#555555"))

        btn_close = tk.Label(
            buttons_frame, text="✕", fg="#555555", bg="#121212",
            font=("Segoe UI", 8, "bold"), cursor="hand2"
        )
        btn_close.pack(side="left")
        btn_close.bind("<Button-1>", lambda _: self._on_close())
        btn_close.bind("<Enter>", lambda _: btn_close.config(fg="#FF4500"))
        btn_close.bind("<Leave>", lambda _: btn_close.config(fg="#555555"))

        # Content area (status or sections)
        self.content_area = tk.Frame(outer, bg="#121212")
        self.content_area.pack(side="left", fill="both", expand=True)

        # Sections frame: 3 columns side by side
        self.sections_frame = tk.Frame(self.content_area, bg="#121212")

        self.col_texts = []
        for i in range(3):
            lpad = 15 if i == 0 else 6
            t = self._make_text(self.sections_frame, height=4, width=26, padx=lpad, pady=6)
            t.pack(side="left")
            self.col_texts.append(t)

        self.sections_frame.pack()

        self.root.bind("<Button-1>", self.start_move)
        self.root.bind("<B1-Motion>", self.do_move)
        self.root.bind("<Button-3>", self.show_menu)
        self.create_menu()

        self.pty = None
        self.buf = []
        self.buf_lock = threading.Lock()
        self.pinned = True
        self.x = self.y = 0

        self.root.after(100, self._set_rounded_corners)
        self.root.after(1000, self._keep_on_top)
        threading.Thread(target=self.refresh_loop, daemon=True).start()

    def _make_text(self, parent, height=4, width=26, padx=10, pady=6):
        t = tk.Text(
            parent, bg="#121212", fg="#d77757", relief="flat",
            font=("Consolas", 9), padx=padx, pady=pady,
            state="disabled", cursor="arrow", wrap="none",
            highlightthickness=0, height=height, width=width
        )
        t.tag_configure("main", font=("Consolas", 10), foreground="#d77757")
        t.tag_configure("dim",  font=("Consolas", 9),  foreground="#d77757")
        t.tag_configure("warn", font=("Consolas", 9),  foreground="#FFA500")
        t.tag_configure("bar",  font=("Consolas", 10), foreground="#00FF7F")
        t.tag_configure("err",  font=("Consolas", 9),  foreground="#FF4500")
        t.bindtags((str(t), str(self.root), 'all'))
        t.bind("<Button-1>", self.start_move)
        t.bind("<B1-Motion>", self.do_move)
        return t

    # def _save_screenshot(self):
    #     import ctypes
    #     import ctypes.wintypes
    #     from PIL import Image

    #     hwnd = ctypes.windll.user32.GetParent(self.root.winfo_id())

    #     # 取得視窗實際大小
    #     rect = ctypes.wintypes.RECT()
    #     ctypes.windll.dwmapi.DwmGetWindowAttribute(
    #         hwnd, 9,  # DWMWA_EXTENDED_FRAME_BOUNDS
    #         ctypes.byref(rect), ctypes.sizeof(rect)
    #     )
    #     w = rect.right - rect.left
    #     h = rect.bottom - rect.top

    #     # 從視窗 DC 直接抓
    #     wdc     = ctypes.windll.user32.GetWindowDC(hwnd)
    #     dc      = ctypes.windll.gdi32.CreateCompatibleDC(wdc)
    #     bmp     = ctypes.windll.gdi32.CreateCompatibleBitmap(wdc, w, h)
    #     ctypes.windll.gdi32.SelectObject(dc, bmp)
    #     ctypes.windll.user32.PrintWindow(hwnd, dc, 2)  # PW_RENDERFULLCONTENT=2

    #     # 把 HBITMAP 轉成 PIL Image
    #     bmp_info = (ctypes.c_int * 4)(40, w, -h, 1 | (32 << 16))
    #     buf = (ctypes.c_char * (w * h * 4))()
    #     ctypes.windll.gdi32.GetDIBits(dc, bmp, 0, h, buf, bmp_info, 0)
    #     img = Image.frombuffer("RGBA", (w, h), buf, "raw", "BGRA", 0, 1)

    #     # 清理
    #     ctypes.windll.gdi32.DeleteObject(bmp)
    #     ctypes.windll.gdi32.DeleteDC(dc)
    #     ctypes.windll.user32.ReleaseDC(hwnd, wdc)

    #     img.save("claude_monitor_demo.png")
    #     print("截圖已儲存")

    def create_menu(self):
        self.menu = tk.Menu(self.root, tearoff=0)
        self.menu.add_command(label="手動刷新", command=lambda: threading.Thread(target=self.update_usage, daemon=True).start())
        self.menu.add_separator()
        self.menu.add_command(label="退出", command=self._on_close)

    def _keep_on_top(self):
        self.root.lift()
        self.root.attributes("-topmost", True)
        self.root.after(1000, self._keep_on_top)

    def _set_rounded_corners(self):
        hwnd = ctypes.windll.user32.GetParent(self.root.winfo_id())
        ctypes.windll.dwmapi.DwmSetWindowAttribute(
            hwnd, 33, ctypes.byref(ctypes.c_int(2)), ctypes.sizeof(ctypes.c_int)
        )

    def show_menu(self, event):
        self.menu.post(event.x_root, event.y_root)

    def _strip(self, text):
        text = _ANSI_RE.sub('', text)
        text = text.replace('\x00', '')
        text = text.replace('\r\n', '\n').replace('\r', '\n')
        return text

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

    def _write_widget(self, widget, segments):
        widget.config(state="normal")
        widget.delete("1.0", "end")
        for text, tag in segments:
            widget.insert("end", text, tag)
        widget.config(state="disabled")

    def _set_status(self, text, tag="dim"):
        def _do():
            for t in self.col_texts:
                self._write_widget(t, [(text, tag)])
        self.root.after(0, _do)

    def _write_data(self, col_segs_list):
        def _do():
            for i, segs in enumerate(col_segs_list):
                self._write_widget(self.col_texts[i], segs)
                if segs:
                    w = max(len(t) for t, _ in segs if t.strip())
                    h = len([t for t, _ in segs if t.endswith("\n")])
                    self.col_texts[i].config(width=max(w, 20), height=max(h, 1))
        self.root.after(0, _do)

    def parse_usage(self, text):
        keys = list(SECTION_LABELS.keys())
        sections = {key: {"pct": None, "resets": None} for key in keys}
        positions = {}
        for key in keys:
            idx = text.find(key)
            if idx == -1:
                idx = text.find(key.replace(" ", ""))
            positions[key] = idx

        for i, key in enumerate(keys):
            idx = positions[key]
            if idx == -1:
                continue
            next_idxs = [positions[k] for k in keys[i+1:] if positions[k] > idx]
            end = min(next_idxs) if next_idxs else idx + 400
            chunk = text[idx:end]
            m = re.search(r'(\d+)%\s*used', chunk)
            if m:
                sections[key]["pct"] = int(m.group(1))
            m2 = re.search(r'Rese\w*[^\n\r]{1,50}', chunk, re.I)
            if m2:
                sections[key]["resets"] = m2.group(0).strip()
        return sections

    def format_bar(self, pct, width=12):
        filled = round(pct / 100 * width)
        return "█" * filled + "░" * (width - filled)

    def _countdown(self, hr, mn):
        now = datetime.now(TAIPEI_TZ)
        dt = now.replace(hour=hr, minute=mn, second=0, microsecond=0)
        if dt <= now:
            dt += timedelta(days=1)
        d = dt - now
        h = int(d.total_seconds() // 3600)
        m = int((d.total_seconds() % 3600) // 60)
        return f"in {h}hr {m:02d}min"

    def _normalize_hour(self, hr, ap):
        if ap == 'pm' and hr != 12: return hr + 12
        if ap == 'am' and hr == 12: return 0
        return hr

    def _format_resets(self, text):
        if not text:
            return text
        m = re.search(r'(\d+):(\d+)\s*(am|pm)', text, re.I)
        if m:
            hr = self._normalize_hour(int(m.group(1)), m.group(3).lower())
            return self._countdown(hr, int(m.group(2)))
        m = re.search(r'([A-Za-z]{3})\s*(\d+)\s*,?\s*(\d+)\s*(am|pm)', text, re.I)
        if m and m.group(1).lower() in _MONTHS:
            hr = self._normalize_hour(int(m.group(3)), m.group(4).lower())
            now = datetime.now(TAIPEI_TZ)
            dt = datetime(now.year, _MONTHS[m.group(1).lower()], int(m.group(2)), hr, 0, tzinfo=TAIPEI_TZ)
            if dt < now:
                dt = datetime(now.year + 1, _MONTHS[m.group(1).lower()], int(m.group(2)), hr, 0, tzinfo=TAIPEI_TZ)
            return f"{dt.strftime('%a')} {dt.strftime('%I:%M%p').lstrip('0').lower()}"
        m = re.search(r'(\d+)\s*(am|pm)', text, re.I)
        if m:
            hr = self._normalize_hour(int(m.group(1)), m.group(2).lower())
            return self._countdown(hr, 0)
        m = re.search(r'([A-Za-z]{3})\s*(\d+)', text, re.I)
        if m and m.group(1).lower() in _MONTHS:
            return f"{m.group(1)} {m.group(2)}"
        return ""

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

    def _close_pty(self):
        if self.pty is not None:
            try:
                self.pty.close()
            except Exception:
                pass
            self.pty = None

    def ensure_pty(self):
        self._close_pty()
        return self.start_pty()

    def update_usage(self):
        self._set_status("抓取中...", "bar")
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
            col_segs_list = []
            for key, label in SECTION_LABELS.items():
                data = sections[key]
                pct = data["pct"]
                resets_raw = data["resets"]
                resets = self._format_resets(resets_raw) if resets_raw else ""
                first = f"{label}  {resets}\n" if resets else f"{label}\n"
                if pct is None:
                    segs = [(first, "main"), ("--\n", "dim")]
                else:
                    bar_tag = "err" if pct >= 90 else "warn" if pct >= 70 else "bar"
                    segs = [(first, bar_tag), (f"{self.format_bar(pct)} {pct}%\n", bar_tag)]
                col_segs_list.append(segs)

            self._write_data(col_segs_list)

        except Exception as e:
            self._close_pty()
            self._set_status(f"抓取失敗: {e}", "err")

    def refresh_loop(self):
        while True:
            self.update_usage()
            time.sleep(300)

    def _toggle_pin(self):
        self.pinned = not self.pinned
        self.btn_pin.config(fg="#00FF7F" if self.pinned else "#555555")

    def start_move(self, event):
        if self.pinned:
            return "break"
        self.x = event.x
        self.y = event.y
        return "break"

    def do_move(self, event):
        if self.pinned:
            return "break"
        x = self.root.winfo_x() + (event.x - self.x)
        y = self.root.winfo_y() + (event.y - self.y)
        self.root.geometry(f"+{x}+{y}")
        return "break"

    def _on_close(self):
        self._close_pty()
        self.root.destroy()

    def run(self):
        self.root.protocol("WM_DELETE_WINDOW", self._on_close)
        self.root.mainloop()


if __name__ == "__main__":
    app = ClaudeHUD()
    app.run()

