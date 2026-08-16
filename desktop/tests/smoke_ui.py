from __future__ import annotations

import socket
import sys
import time
import os
from pathlib import Path

from PIL import ImageGrab


SOURCE = Path(__file__).resolve().parents[1] / "src"
sys.path.insert(0, str(SOURCE))

from flowclip.clipboard_windows import WindowsClipboard  # noqa: E402
from flowclip.config import AppConfig  # noqa: E402
from flowclip.ui import FlowClipWindow  # noqa: E402


def free_port() -> int:
    with socket.socket() as probe:
        probe.bind(("127.0.0.1", 0))
        return int(probe.getsockname()[1])


def main() -> None:
    import tkinter as tk

    config = AppConfig.defaults()
    config.auto_sync = False
    config.server_enabled = True
    config.listen_host = "127.0.0.1"
    config.listen_port = free_port()
    config.server_url = f"http://127.0.0.1:{config.listen_port}"
    config.token = "smoke-test-token-long-enough"
    cache = Path(os.environ.get("FLOWCLIP_SMOKE_CACHE", r"D:\DevCache"))
    config.file_store_directory = str(cache / "flowclip-ui-smoke" / "server")
    config.download_directory = str(cache / "flowclip-ui-smoke" / "downloads")

    clipboard = WindowsClipboard()
    print("Clipboard sequence:", clipboard.sequence_number())
    try:
        current = clipboard.capture(config.device_id, config.max_bytes)
        print("Clipboard kind:", current.kind if current else "unsupported-or-empty")
    except Exception as exception:
        print("Clipboard capture skipped:", exception)

    root = tk.Tk()
    window = FlowClipWindow(root, config)
    root.update_idletasks()
    root.update()
    left = root.winfo_rootx()
    top = root.winfo_rooty()
    width = root.winfo_width()
    height = root.winfo_height()
    output = Path(__file__).resolve().parents[2] / "dist" / "desktop-ui-smoke.png"
    ImageGrab.grab(bbox=(left, top, left + width, top + height)).save(output)
    print("Window:", width, "x", height)
    print("Screenshot:", output)

    window._show_file_window()
    deadline = time.monotonic() + 5
    while (
        window.file_window is not None
        and window.file_window.active
        and time.monotonic() < deadline
    ):
        root.update()
        time.sleep(0.05)
    root.update_idletasks()
    root.update()
    assert window.file_window is not None
    file_window = window.file_window.window
    file_left = file_window.winfo_rootx()
    file_top = file_window.winfo_rooty()
    file_width = file_window.winfo_width()
    file_height = file_window.winfo_height()
    file_output = output.with_name("file-ui-smoke.png")
    ImageGrab.grab(
        bbox=(
            file_left,
            file_top,
            file_left + file_width,
            file_top + file_height,
        )
    ).save(file_output)
    print("File window:", file_width, "x", file_height)
    print("File screenshot:", file_output)
    window.quit()


if __name__ == "__main__":
    main()
