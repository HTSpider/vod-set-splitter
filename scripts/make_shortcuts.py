"""Adds VOD Set Splitter to the Start Menu and the desktop. Run once after setting up the app."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from vod_set_splitter import windows_shell  # noqa: E402

print("Start Menu:", windows_shell.make_shortcut(windows_shell.START_MENU))
print("Desktop:", windows_shell.make_shortcut(windows_shell.DESKTOP))
