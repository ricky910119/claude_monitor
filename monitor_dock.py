"""Fixed, non-activating HUD overlay on DISPLAY2's bottom taskbar."""
import ctypes
from ctypes import wintypes


def work_area():
    """Keep the working area's horizontal bounds, but include the bottom taskbar."""
    class MonitorInfo(ctypes.Structure):
        _fields_ = [("size", wintypes.DWORD), ("monitor", wintypes.RECT),
                    ("work", wintypes.RECT), ("flags", wintypes.DWORD),
                    ("device", wintypes.WCHAR * 32)]
    user32 = ctypes.windll.user32
    user32.GetMonitorInfoW.argtypes = [wintypes.HANDLE, ctypes.c_void_p]
    user32.GetMonitorInfoW.restype = wintypes.BOOL
    monitors = []
    callback_type = ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HANDLE,
                                      wintypes.HDC, ctypes.POINTER(wintypes.RECT), wintypes.LPARAM)
    def collect(handle, dc, rect, data):
        info = MonitorInfo()
        info.size = ctypes.sizeof(info)
        if user32.GetMonitorInfoW(handle, ctypes.byref(info)):
            monitors.append((info.device, info.flags & 1,
                             (info.work.left, info.work.top, info.work.right, info.monitor.bottom)))
        return True
    callback = callback_type(collect)
    user32.EnumDisplayMonitors(None, None, callback, 0)
    if not monitors:
        return None
    return next((rect for name, primary, rect in monitors if name.endswith("DISPLAY2")),
                next((rect for name, primary, rect in monitors if primary), monitors[0][2]))


def install_dock(app):
    """Install once; stop the watchdog when the window is closing/destroyed."""
    if getattr(app, "_dock_installed", False):
        return
    app._dock_installed = True
    root = app.root
    user32 = ctypes.windll.user32
    user32.GetParent.argtypes = [wintypes.HWND]
    user32.GetParent.restype = wintypes.HWND
    user32.SetWindowPos.argtypes = [wintypes.HWND, wintypes.HWND, ctypes.c_int,
                                   ctypes.c_int, ctypes.c_int, ctypes.c_int, ctypes.c_uint]

    def sync():
        area = work_area()
        if area is None or not root.winfo_exists() or root.state() == "withdrawn":
            return
        app.pinned = True
        app.dragged = False
        left, top, right, bottom = area
        x = max(left, min(left + 8, right - root.winfo_width()))
        y = max(top, bottom - root.winfo_height())
        hwnd = user32.GetParent(root.winfo_id())
        # HWND_TOPMOST; NOSIZE | NOACTIVATE. Do not use NOZORDER here.
        user32.SetWindowPos(hwnd, wintypes.HWND(-1), x, y, 0, 0, 0x0011)

    def tick():
        stop = getattr(app, "stop", None)
        if stop is not None and stop.is_set():
            return
        if not root.winfo_exists():
            return
        sync()
        root.after(500, tick)

    app._position = sync
    app.reset_position = sync
    app._toggle_pin = sync
    app.pinned = True
    button = getattr(app, "pin_button", None)
    if button is not None:
        button.config(command=sync)
    root.after_idle(tick)
