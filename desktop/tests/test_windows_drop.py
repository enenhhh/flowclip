from __future__ import annotations

import ctypes
import queue
import sys
import unittest
from ctypes import wintypes
from pathlib import Path


SOURCE = Path(__file__).resolve().parents[1] / "src"
sys.path.insert(0, str(SOURCE))

from flowclip import windows_drop  # noqa: E402


class DropPathTests(unittest.TestCase):
    def test_query_drop_paths_preserves_unicode_spaces_and_multiple_files(self) -> None:
        expected = (
            r"C:\中转站\照片 01.png",
            r"D:\FlowClip\very-long-name.txt",
        )

        def query(_hdrop, index, buffer, _size):
            if index == windows_drop.DROP_FILE_COUNT:
                return len(expected)
            value = expected[index]
            if buffer is None:
                return len(value)
            buffer.value = value
            return len(value)

        self.assertEqual(windows_drop._query_drop_paths(123, query), expected)

    def test_query_drop_paths_rejects_untrusted_file_counts(self) -> None:
        def query(_hdrop, _index, _buffer, _size):
            return windows_drop.MAX_FILES_PER_DROP + 1

        with self.assertRaisesRegex(OSError, "数量无效"):
            windows_drop._query_drop_paths(123, query)

    @unittest.skipIf(sys.platform == "win32", "Windows uses the native registration")
    def test_non_windows_registration_is_a_safe_noop(self) -> None:
        class UnusedWidget:
            def update_idletasks(self):
                raise AssertionError("no-op registration must not touch the widget")

            def winfo_id(self):
                raise AssertionError("no-op registration must not touch the widget")

        registration = windows_drop.register_file_drop(UnusedWidget(), lambda _paths: None)
        self.assertFalse(registration.enabled)
        registration.close()
        registration.close()

    @unittest.skipUnless(sys.platform == "win32", "requires a Windows HWND")
    def test_close_restores_the_original_tk_window_procedure(self) -> None:
        import tkinter as tk

        root = tk.Tk()
        root.withdraw()
        registration = None
        try:
            root.update_idletasks()
            child = root.winfo_id()
            hwnd = int(
                windows_drop._user32.GetAncestor(child, windows_drop.GA_ROOT) or child
            )
            original = int(
                windows_drop._user32.GetWindowLongPtrW(
                    hwnd, windows_drop.GWLP_WNDPROC
                )
            )
            registration = windows_drop.register_file_drop(root, lambda _paths: None)
            installed = int(
                windows_drop._user32.GetWindowLongPtrW(
                    hwnd, windows_drop.GWLP_WNDPROC
                )
            )
            self.assertNotEqual(installed, original)

            registration.close()
            restored = int(
                windows_drop._user32.GetWindowLongPtrW(
                    hwnd, windows_drop.GWLP_WNDPROC
                )
            )
            self.assertEqual(restored, original)
        finally:
            if registration is not None:
                registration.close()
            root.destroy()

    @unittest.skipUnless(sys.platform == "win32", "requires a Windows HWND")
    def test_wm_dropfiles_delivers_unicode_paths_to_the_queue(self) -> None:
        import tkinter as tk

        class DropFiles(ctypes.Structure):
            _fields_ = [
                ("pFiles", wintypes.DWORD),
                ("pt", wintypes.POINT),
                ("fNC", wintypes.BOOL),
                ("fWide", wintypes.BOOL),
            ]

        kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
        user32 = ctypes.WinDLL("user32", use_last_error=True)
        kernel32.GlobalAlloc.argtypes = [wintypes.UINT, ctypes.c_size_t]
        kernel32.GlobalAlloc.restype = wintypes.HGLOBAL
        kernel32.GlobalLock.argtypes = [wintypes.HGLOBAL]
        kernel32.GlobalLock.restype = wintypes.LPVOID
        kernel32.GlobalUnlock.argtypes = [wintypes.HGLOBAL]
        kernel32.GlobalUnlock.restype = wintypes.BOOL
        kernel32.GlobalFree.argtypes = [wintypes.HGLOBAL]
        kernel32.GlobalFree.restype = wintypes.HGLOBAL
        user32.SendMessageW.argtypes = [
            wintypes.HWND,
            wintypes.UINT,
            wintypes.WPARAM,
            wintypes.LPARAM,
        ]
        user32.SendMessageW.restype = ctypes.c_ssize_t

        expected = (r"C:\中转站\图片 01.png", r"D:\文件\说明.txt")
        names = ("\0".join(expected) + "\0\0").encode("utf-16-le")
        header = DropFiles(
            pFiles=ctypes.sizeof(DropFiles),
            pt=wintypes.POINT(0, 0),
            fNC=False,
            fWide=True,
        )
        payload = bytes(header) + names
        handle = kernel32.GlobalAlloc(0x0002, len(payload))
        self.assertTrue(handle)
        pointer = kernel32.GlobalLock(handle)
        self.assertTrue(pointer)
        ctypes.memmove(pointer, payload, len(payload))
        kernel32.GlobalUnlock(handle)

        root = tk.Tk()
        root.withdraw()
        registration = None
        delivered: queue.Queue[tuple[str, ...]] = queue.Queue()
        transferred = False
        try:
            registration = windows_drop.register_file_drop(root, delivered.put)
            child = root.winfo_id()
            hwnd = int(
                windows_drop._user32.GetAncestor(child, windows_drop.GA_ROOT) or child
            )
            user32.SendMessageW(hwnd, windows_drop.WM_DROPFILES, handle, 0)
            transferred = True
            self.assertEqual(delivered.get_nowait(), expected)
        finally:
            if not transferred:
                kernel32.GlobalFree(handle)
            if registration is not None:
                registration.close()
            root.destroy()


if __name__ == "__main__":
    unittest.main()
