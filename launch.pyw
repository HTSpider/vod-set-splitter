"""Starts VOD Set Splitter without a console window, from wherever it's launched."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent / "src"))

from vod_set_splitter.desktop import main

main()
