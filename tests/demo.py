"""Opens the app on a video with its own data folder, runs steps, and saves a screenshot after each.

    python tests/demo.py <video> <folder for pictures> "<javascript step>" ["<step>" ...]

Your real projects and settings are never touched: the demo works on a copy of the settings.
"""
import ctypes
import json
import os
import shutil
import sys
import tempfile
import time
from ctypes import wintypes
from pathlib import Path

real = Path(os.environ["APPDATA"]) / "VOD Set Splitter"
fake = Path(tempfile.mkdtemp())
(fake / "VOD Set Splitter").mkdir()
if (real / "settings.json").exists():
    shutil.copy(real / "settings.json", fake / "VOD Set Splitter" / "settings.json")
os.environ["APPDATA"] = str(fake)
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

import webview  # noqa: E402
from PIL import Image  # noqa: E402

from vod_set_splitter import desktop, windows_shell  # noqa: E402


def grab(hwnd):
    user32, gdi32 = ctypes.windll.user32, ctypes.windll.gdi32
    user32.GetDC.restype = wintypes.HDC
    gdi32.CreateCompatibleDC.restype = wintypes.HDC
    gdi32.CreateCompatibleDC.argtypes = [wintypes.HDC]
    gdi32.CreateCompatibleBitmap.restype = wintypes.HBITMAP
    gdi32.CreateCompatibleBitmap.argtypes = [wintypes.HDC, ctypes.c_int, ctypes.c_int]
    gdi32.SelectObject.argtypes = [wintypes.HDC, wintypes.HGDIOBJ]
    gdi32.GetBitmapBits.argtypes = [wintypes.HBITMAP, ctypes.c_long, ctypes.c_void_p]
    user32.PrintWindow.argtypes = [wintypes.HWND, wintypes.HDC, wintypes.UINT]
    gdi32.DeleteObject.argtypes = [wintypes.HGDIOBJ]
    gdi32.DeleteDC.argtypes = [wintypes.HDC]
    user32.ReleaseDC.argtypes = [wintypes.HWND, wintypes.HDC]
    rect = wintypes.RECT()
    user32.GetClientRect(hwnd, ctypes.byref(rect))
    w, h = rect.right, rect.bottom
    screen = user32.GetDC(hwnd)
    memory = gdi32.CreateCompatibleDC(screen)
    bitmap = gdi32.CreateCompatibleBitmap(screen, w, h)
    gdi32.SelectObject(memory, bitmap)
    user32.PrintWindow(hwnd, memory, 3)  # client area, full content
    buffer = ctypes.create_string_buffer(w * h * 4)
    gdi32.GetBitmapBits(bitmap, w * h * 4, buffer)
    image = Image.frombuffer("RGBA", (w, h), buffer, "raw", "BGRA", 0, 1).convert("RGB")
    gdi32.DeleteObject(bitmap); gdi32.DeleteDC(memory); user32.ReleaseDC(hwnd, screen)
    return image


def run(window, api, video, folder, steps):
    time.sleep(2.5)
    if video:
        window.evaluate_js(f"api().open_video({json.dumps(video)}).then(opened)")
        time.sleep(3)
    hwnd = windows_shell.find_window(desktop.APP_TITLE)
    for n, step in enumerate(["0"] + steps):
        wait = 3.0
        if step.startswith("WAIT"):
            wait = float(step.split()[1])
        else:
            window.evaluate_js(step)
        time.sleep(wait)
        hwnd = windows_shell.find_window(desktop.APP_TITLE) or hwnd
        if ctypes.windll.user32.IsIconic(hwnd):
            ctypes.windll.user32.ShowWindow(hwnd, 4)  # restore without taking focus
            ctypes.windll.user32.SetWindowPos(hwnd, 0, -4000, 40, 0, 0, 0x0001 | 0x0010)  # keep it off screen
            time.sleep(1)
        try:
            grab(hwnd).save(Path(folder) / f"shot{n}.png")
        except ValueError:
            print(f"shot{n}: window not drawable (hwnd {hwnd}, minimised: {ctypes.windll.user32.IsIconic(hwnd)})")
        log = window.evaluate_js("JSON.stringify({sets: sets.length, sel, toasts: document.getElementById('toasts').innerText, t: player.currentTime, err: player.error && player.error.code})")
        print(f"shot{n}:", log, flush=True)
    window.destroy()


if __name__ == "__main__":
    video, folder, steps = (sys.argv[1] or None), sys.argv[2], sys.argv[3:]
    windows_shell.use_own_taskbar_identity()
    api = desktop.Api()
    # Off screen and without taking focus, so a test run never gets in your way.
    window = webview.create_window(desktop.APP_TITLE, str(desktop.UI), js_api=api, width=1480, height=920, x=-4000, y=40,
                                   focus=False, background_color="#121417")
    api._window = window
    webview.start(run, (window, api, video, folder, steps))
    shutil.rmtree(fake, ignore_errors=True)
