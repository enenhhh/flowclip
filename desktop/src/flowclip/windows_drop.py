from __future__ import annotations

import ctypes
import sys
from ctypes import wintypes
from typing import Callable, Protocol


WM_DROPFILES = 0x0233
GWLP_WNDPROC = -4
GA_ROOT = 2
DROP_FILE_COUNT = 0xFFFFFFFF
MAX_FILES_PER_DROP = 4096

DropCallback = Callable[[tuple[str, ...]], None]


class DropWidget(Protocol):
    def update_idletasks(self) -> None: ...

    def winfo_id(self) -> int: ...


class FileDropRegistration(Protocol):
    @property
    def enabled(self) -> bool: ...

    def close(self) -> None: ...


def _query_drop_paths(
    hdrop: int,
    query_file: Callable[[int, int, object, int], int],
) -> tuple[str, ...]:
    count = int(query_file(hdrop, DROP_FILE_COUNT, None, 0))
    if count < 0 or count > MAX_FILES_PER_DROP:
        raise OSError("一次拖入的文件数量无效")

    paths: list[str] = []
    for index in range(count):
        length = int(query_file(hdrop, index, None, 0))
        if length <= 0:
            continue
        buffer = ctypes.create_unicode_buffer(length + 1)
        copied = int(query_file(hdrop, index, buffer, len(buffer)))
        if copied <= 0 or copied > length:
            continue
        paths.append(buffer.value)
    return tuple(paths)


class _NoOpFileDrop:
    @property
    def enabled(self) -> bool:
        return False

    def close(self) -> None:
        return


if sys.platform == "win32":
    _LONG_PTR = ctypes.c_ssize_t
    _WNDPROC = ctypes.WINFUNCTYPE(
        _LONG_PTR,
        wintypes.HWND,
        wintypes.UINT,
        wintypes.WPARAM,
        wintypes.LPARAM,
    )

    _user32 = ctypes.WinDLL("user32", use_last_error=True)
    _shell32 = ctypes.WinDLL("shell32", use_last_error=True)

    _user32.GetAncestor.argtypes = [wintypes.HWND, wintypes.UINT]
    _user32.GetAncestor.restype = wintypes.HWND
    _user32.IsWindow.argtypes = [wintypes.HWND]
    _user32.IsWindow.restype = wintypes.BOOL
    _user32.GetWindowLongPtrW.argtypes = [wintypes.HWND, ctypes.c_int]
    _user32.GetWindowLongPtrW.restype = _LONG_PTR
    _user32.SetWindowLongPtrW.argtypes = [
        wintypes.HWND,
        ctypes.c_int,
        _LONG_PTR,
    ]
    _user32.SetWindowLongPtrW.restype = _LONG_PTR
    _user32.CallWindowProcW.argtypes = [
        ctypes.c_void_p,
        wintypes.HWND,
        wintypes.UINT,
        wintypes.WPARAM,
        wintypes.LPARAM,
    ]
    _user32.CallWindowProcW.restype = _LONG_PTR

    _shell32.DragAcceptFiles.argtypes = [wintypes.HWND, wintypes.BOOL]
    _shell32.DragAcceptFiles.restype = None
    _shell32.DragQueryFileW.argtypes = [
        wintypes.HANDLE,
        wintypes.UINT,
        wintypes.LPWSTR,
        wintypes.UINT,
    ]
    _shell32.DragQueryFileW.restype = wintypes.UINT
    _shell32.DragFinish.argtypes = [wintypes.HANDLE]
    _shell32.DragFinish.restype = None


    class _WindowsFileDrop:
        def __init__(self, widget: DropWidget, callback: DropCallback) -> None:
            widget.update_idletasks()
            widget_hwnd = int(widget.winfo_id())
            root_hwnd = _user32.GetAncestor(wintypes.HWND(widget_hwnd), GA_ROOT)
            self._hwnd = int(root_hwnd or widget_hwnd)
            if not _user32.IsWindow(wintypes.HWND(self._hwnd)):
                raise OSError("无法获取拖放窗口句柄")

            self._callback = callback
            self._old_wndproc = 0
            self._closed = False
            self._wndproc = _WNDPROC(self._window_proc)
            self._wndproc_address = int(
                ctypes.cast(self._wndproc, ctypes.c_void_p).value or 0
            )
            if not self._wndproc_address:
                raise OSError("无法创建拖放窗口过程")

            ctypes.set_last_error(0)
            previous = _user32.SetWindowLongPtrW(
                wintypes.HWND(self._hwnd),
                GWLP_WNDPROC,
                self._wndproc_address,
            )
            error = ctypes.get_last_error()
            if not previous and error:
                raise ctypes.WinError(error)
            self._old_wndproc = int(previous)
            _shell32.DragAcceptFiles(wintypes.HWND(self._hwnd), True)

        @property
        def enabled(self) -> bool:
            return not self._closed

        def _window_proc(
            self,
            hwnd: int,
            message: int,
            wparam: int,
            lparam: int,
        ) -> int:
            if message == WM_DROPFILES:
                try:
                    paths = _query_drop_paths(int(wparam), _shell32.DragQueryFileW)
                    if paths and not self._closed:
                        self._callback(paths)
                except BaseException:
                    # Exceptions must never escape a native window-procedure callback.
                    pass
                finally:
                    _shell32.DragFinish(wintypes.HANDLE(wparam))
                return 0

            return int(
                _user32.CallWindowProcW(
                    ctypes.c_void_p(self._old_wndproc),
                    wintypes.HWND(hwnd),
                    message,
                    wparam,
                    lparam,
                )
            )

        def close(self) -> None:
            if self._closed:
                return
            self._closed = True
            _shell32.DragAcceptFiles(wintypes.HWND(self._hwnd), False)
            if not _user32.IsWindow(wintypes.HWND(self._hwnd)):
                return

            current = int(
                _user32.GetWindowLongPtrW(
                    wintypes.HWND(self._hwnd), GWLP_WNDPROC
                )
            )
            if current != self._wndproc_address:
                raise OSError("窗口过程已被其他组件替换，无法安全恢复拖放处理")

            ctypes.set_last_error(0)
            previous = _user32.SetWindowLongPtrW(
                wintypes.HWND(self._hwnd),
                GWLP_WNDPROC,
                self._old_wndproc,
            )
            error = ctypes.get_last_error()
            if not previous and error:
                raise ctypes.WinError(error)


def register_file_drop(
    widget: DropWidget,
    callback: DropCallback,
) -> FileDropRegistration:
    if sys.platform != "win32":
        return _NoOpFileDrop()
    return _WindowsFileDrop(widget, callback)
