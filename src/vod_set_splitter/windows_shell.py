"""Makes VOD Set Splitter a proper Windows app: its own taskbar button, icon and Start Menu shortcut.

A pinned window normally reopens only the program behind it, which here is Python on its own.
These properties give Windows the app's real name, icon and launch command instead.
"""
import ctypes
import os
import subprocess
import sys
import time
from ctypes import wintypes
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
APP_ID = "VodSetSplitter.App"
APP_NAME = "VOD Set Splitter"
ICON_FILE = ROOT / "assets" / "icon.ico"
PYTHONW = Path(sys.executable).with_name("pythonw.exe")
LAUNCHER = ROOT / "launch.pyw"
RELAUNCH_COMMAND = f'"{PYTHONW}" "{LAUNCHER}"'
START_MENU = Path(os.environ.get("APPDATA", "")) / "Microsoft" / "Windows" / "Start Menu" / "Programs" / f"{APP_NAME}.lnk"

VT_LPWSTR = 31
GPS_READWRITE = 2
SET_VALUE, GET_VALUE, COMMIT, RELEASE = 6, 5, 7, 2


class GUID(ctypes.Structure):
    _fields_ = [("a", wintypes.DWORD), ("b", wintypes.WORD), ("c", wintypes.WORD), ("d", ctypes.c_ubyte * 8)]


class PROPERTYKEY(ctypes.Structure):
    _fields_ = [("fmtid", GUID), ("pid", wintypes.DWORD)]


class PROPVARIANT(ctypes.Structure):
    _fields_ = [("vt", wintypes.WORD), ("r1", wintypes.WORD), ("r2", wintypes.WORD), ("r3", wintypes.WORD),
                ("value", ctypes.c_void_p), ("extra", ctypes.c_void_p)]


def _guid(a, b, c, *d):
    return GUID(a, b, c, (ctypes.c_ubyte * 8)(*d))


IID_PROPERTY_STORE = _guid(0x886D8EEB, 0x8CF2, 0x4446, 0x8D, 0x02, 0xCD, 0xBA, 0x1D, 0xBD, 0xCF, 0x99)
APP_MODEL = _guid(0x9F4C2855, 0x9F79, 0x4B39, 0xA8, 0xD0, 0xE1, 0xD4, 0x2D, 0xE1, 0xD5, 0xF3)
KEY_RELAUNCH_COMMAND = PROPERTYKEY(APP_MODEL, 2)
KEY_RELAUNCH_ICON = PROPERTYKEY(APP_MODEL, 3)
KEY_RELAUNCH_NAME = PROPERTYKEY(APP_MODEL, 4)
KEY_ID = PROPERTYKEY(APP_MODEL, 5)


def _method(store, index, *argtypes):
    table = ctypes.cast(store, ctypes.POINTER(ctypes.POINTER(ctypes.c_void_p))).contents
    return ctypes.WINFUNCTYPE(ctypes.c_long, ctypes.c_void_p, *argtypes)(table[index])


def _set(store, key, text):
    value = ctypes.create_unicode_buffer(text)
    variant = PROPVARIANT(vt=VT_LPWSTR, value=ctypes.cast(value, ctypes.c_void_p))
    return _method(store, SET_VALUE, ctypes.POINTER(PROPERTYKEY), ctypes.POINTER(PROPVARIANT))(
        store, ctypes.byref(key), ctypes.byref(variant))


def _get(store, key):
    variant = PROPVARIANT()
    if _method(store, GET_VALUE, ctypes.POINTER(PROPERTYKEY), ctypes.POINTER(PROPVARIANT))(
            store, ctypes.byref(key), ctypes.byref(variant)) != 0 or variant.vt != VT_LPWSTR:
        return None
    return ctypes.wstring_at(variant.value)


def _release(store):
    table = ctypes.cast(store, ctypes.POINTER(ctypes.POINTER(ctypes.c_void_p))).contents
    ctypes.WINFUNCTYPE(ctypes.c_ulong, ctypes.c_void_p)(table[RELEASE])(store)


def set_window_identity(hwnd):
    """Makes a pin of this window reopen the app, with its own name and icon."""
    ctypes.windll.ole32.CoInitialize(None)
    function = ctypes.windll.shell32.SHGetPropertyStoreForWindow
    function.argtypes = [wintypes.HWND, ctypes.POINTER(GUID), ctypes.POINTER(ctypes.c_void_p)]
    store = ctypes.c_void_p()
    if function(hwnd, ctypes.byref(IID_PROPERTY_STORE), ctypes.byref(store)) != 0:
        return False
    results = [
        _set(store, KEY_ID, APP_ID),
        _set(store, KEY_RELAUNCH_COMMAND, RELAUNCH_COMMAND),
        _set(store, KEY_RELAUNCH_NAME, APP_NAME),
        _set(store, KEY_RELAUNCH_ICON, f"{ICON_FILE},0"),
    ]
    same = _get(store, KEY_RELAUNCH_COMMAND) == RELAUNCH_COMMAND and _get(store, KEY_ID) == APP_ID
    _release(store)
    return all(result == 0 for result in results) and same


def set_shortcut_identity(path):
    """Gives a shortcut the same identity, so it shares a taskbar button with the running app."""
    ctypes.windll.ole32.CoInitialize(None)
    function = ctypes.windll.shell32.SHGetPropertyStoreFromParsingName
    function.argtypes = [wintypes.LPCWSTR, ctypes.c_void_p, ctypes.c_int, ctypes.POINTER(GUID), ctypes.POINTER(ctypes.c_void_p)]
    store = ctypes.c_void_p()
    if function(str(path), None, GPS_READWRITE, ctypes.byref(IID_PROPERTY_STORE), ctypes.byref(store)) != 0:
        return False
    saved = _set(store, KEY_ID, APP_ID) == 0 and _method(store, COMMIT)(store) == 0
    _release(store)
    return saved


def use_own_taskbar_identity():
    """Without this, Windows groups the app under Python in the taskbar. Call before any window."""
    ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID(APP_ID)


def find_window(title):
    user32 = ctypes.windll.user32
    found = []

    @ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)
    def check(hwnd, _):
        length = user32.GetWindowTextLengthW(hwnd)
        text = ctypes.create_unicode_buffer(length + 1)
        user32.GetWindowTextW(hwnd, text, length + 1)
        if user32.IsWindowVisible(hwnd) and text.value == title:
            found.append(hwnd)
        return True

    user32.EnumWindows(check, 0)
    return found[0] if found else None


def set_window_icon(title):
    """Gives the title bar and taskbar the app's icon, and makes a pin reopen the app."""
    user32 = ctypes.windll.user32
    user32.LoadImageW.restype = wintypes.HANDLE
    user32.LoadImageW.argtypes = [wintypes.HINSTANCE, wintypes.LPCWSTR, wintypes.UINT, ctypes.c_int, ctypes.c_int, wintypes.UINT]
    user32.SendMessageW.restype = ctypes.c_ssize_t
    user32.SendMessageW.argtypes = [wintypes.HWND, wintypes.UINT, ctypes.c_size_t, ctypes.c_ssize_t]
    for _ in range(40):
        hwnd = find_window(title)
        if hwnd:
            break
        time.sleep(0.25)
    else:
        return False
    for which, size in ((0, 16), (1, 32)):  # ICON_SMALL, ICON_BIG
        icon = user32.LoadImageW(None, str(ICON_FILE), 1, size, size, 0x10)  # IMAGE_ICON, LR_LOADFROMFILE
        if icon:
            user32.SendMessageW(hwnd, 0x80, which, icon)  # WM_SETICON
    return set_window_identity(hwnd)


def _desktop() -> Path:
    """The desktop Windows really shows (it can be inside OneDrive)."""
    buffer = ctypes.create_unicode_buffer(260)
    if ctypes.windll.shell32.SHGetFolderPathW(None, 0x10, None, 0, buffer) == 0:  # CSIDL_DESKTOPDIRECTORY
        return Path(buffer.value)
    return Path(os.environ.get("USERPROFILE", "")) / "Desktop"


DESKTOP = _desktop() / f"{APP_NAME}.lnk"


def make_shortcut(link: Path) -> bool:
    """A shortcut with the app's icon and taskbar identity, so it can be pinned and shares the running app's button."""
    link.parent.mkdir(parents=True, exist_ok=True)
    script = ("$s = (New-Object -ComObject WScript.Shell).CreateShortcut($env:LINK); $s.TargetPath = $env:TARGET; "
              "$s.Arguments = $env:ARGS; $s.WorkingDirectory = $env:WORKDIR; $s.IconLocation = $env:ICON; "
              "$s.Description = 'Find, check and export the sets in a tournament VOD'; $s.Save()")
    env = {**os.environ, "LINK": str(link), "TARGET": str(PYTHONW), "ARGS": f'"{LAUNCHER}"', "WORKDIR": str(ROOT), "ICON": f"{ICON_FILE},0"}
    subprocess.run(["powershell", "-NoProfile", "-Command", script], env=env, capture_output=True, timeout=30, creationflags=0x08000000)
    return link.exists() and set_shortcut_identity(link)


def make_start_menu_shortcut():
    """The Start Menu entry, kept pointing at this folder (made again if the app moves)."""
    if START_MENU.exists() and read_shortcut_target(START_MENU) == str(PYTHONW):
        return True
    return make_shortcut(START_MENU)


def read_shortcut_target(link: Path) -> str:
    script = "(New-Object -ComObject WScript.Shell).CreateShortcut($env:LINK).TargetPath"
    result = subprocess.run(["powershell", "-NoProfile", "-Command", script], env={**os.environ, "LINK": str(link)},
                            capture_output=True, text=True, timeout=30, creationflags=0x08000000)
    return result.stdout.strip()
