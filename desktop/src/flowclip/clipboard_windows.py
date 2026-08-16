from __future__ import annotations

import ctypes
import io
import os
import time
from ctypes import wintypes
from pathlib import Path

from PIL import Image, ImageGrab, UnidentifiedImageError

from .clipboard import ClipboardError, MAX_IMAGE_PIXELS
from .model import ClipItem


CF_UNICODETEXT = 13
CF_DIB = 8
GMEM_MOVEABLE = 0x0002
user32 = ctypes.WinDLL("user32", use_last_error=True)
kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)

user32.OpenClipboard.argtypes = [wintypes.HWND]
user32.OpenClipboard.restype = wintypes.BOOL
user32.CloseClipboard.argtypes = []
user32.CloseClipboard.restype = wintypes.BOOL
user32.EmptyClipboard.argtypes = []
user32.EmptyClipboard.restype = wintypes.BOOL
user32.IsClipboardFormatAvailable.argtypes = [wintypes.UINT]
user32.IsClipboardFormatAvailable.restype = wintypes.BOOL
user32.GetClipboardData.argtypes = [wintypes.UINT]
user32.GetClipboardData.restype = wintypes.HANDLE
user32.SetClipboardData.argtypes = [wintypes.UINT, wintypes.HANDLE]
user32.SetClipboardData.restype = wintypes.HANDLE
user32.GetClipboardSequenceNumber.argtypes = []
user32.GetClipboardSequenceNumber.restype = wintypes.DWORD
user32.IsWindow.argtypes = [wintypes.HWND]
user32.IsWindow.restype = wintypes.BOOL

kernel32.GlobalAlloc.argtypes = [wintypes.UINT, ctypes.c_size_t]
kernel32.GlobalAlloc.restype = wintypes.HGLOBAL
kernel32.GlobalLock.argtypes = [wintypes.HGLOBAL]
kernel32.GlobalLock.restype = wintypes.LPVOID
kernel32.GlobalUnlock.argtypes = [wintypes.HGLOBAL]
kernel32.GlobalUnlock.restype = wintypes.BOOL
kernel32.GlobalFree.argtypes = [wintypes.HGLOBAL]
kernel32.GlobalFree.restype = wintypes.HGLOBAL


class WindowsClipboard:
    def __init__(self, owner_hwnd: int | None = None) -> None:
        if os.name != "nt":
            raise RuntimeError("WindowsClipboard 只能在 Windows 上使用")
        self._owner_hwnd = owner_hwnd

    @staticmethod
    def sequence_number() -> int:
        return int(user32.GetClipboardSequenceNumber())

    @staticmethod
    def _open(owner_hwnd: int | None = None) -> None:
        for _attempt in range(10):
            if user32.OpenClipboard(owner_hwnd):
                return
            time.sleep(0.025)
        raise ClipboardError("剪贴板正被其他程序占用")

    def capture(self, origin: str, max_bytes: int) -> ClipItem | None:
        image = self._capture_image()
        if image is not None:
            with image:
                output = io.BytesIO()
                safe_image = image.convert("RGBA") if image.mode != "RGBA" else image
                safe_image.save(output, format="PNG", optimize=True)
                data = output.getvalue()
            if len(data) > max_bytes:
                raise ClipboardError(
                    f"图片压缩后为 {len(data) / 1024 / 1024:.1f} MB，超过同步上限"
                )
            filename = time.strftime("FlowClip_%Y%m%d_%H%M%S.png")
            return ClipItem.create(
                origin=origin,
                kind="image",
                mime="image/png",
                filename=filename,
                data=data,
            )

        text = self._capture_text()
        if text is None:
            return None
        if "\0" in text:
            raise ClipboardError("文本不能包含 NUL 字符")
        data = text.encode("utf-8")
        if len(data) > max_bytes:
            raise ClipboardError(
                f"文本为 {len(data) / 1024 / 1024:.1f} MB，超过同步上限"
            )
        return ClipItem.create(
            origin=origin,
            kind="text",
            mime="text/plain; charset=utf-8",
            data=data,
        )

    @staticmethod
    def _validate_image_dimensions(image: Image.Image) -> None:
        width, height = image.size
        if width <= 0 or height <= 0 or width * height > MAX_IMAGE_PIXELS:
            raise ClipboardError(
                f"图片尺寸 {width}x{height} 超过 {MAX_IMAGE_PIXELS:,} 像素限制"
            )

    def _capture_image(self) -> Image.Image | None:
        try:
            value = ImageGrab.grabclipboard()
        except (OSError, RuntimeError):
            return None
        if isinstance(value, Image.Image):
            self._validate_image_dimensions(value)
            return value.copy()
        if isinstance(value, list):
            for name in value:
                path = Path(name)
                if not path.is_file():
                    continue
                try:
                    with Image.open(path) as candidate:
                        self._validate_image_dimensions(candidate)
                        candidate.verify()
                    with Image.open(path) as candidate:
                        self._validate_image_dimensions(candidate)
                        return candidate.copy()
                except (OSError, UnidentifiedImageError, Image.DecompressionBombError):
                    continue
        return None

    def _capture_text(self) -> str | None:
        if not user32.IsClipboardFormatAvailable(CF_UNICODETEXT):
            return None
        self._open()
        try:
            handle = user32.GetClipboardData(CF_UNICODETEXT)
            if not handle:
                return None
            pointer = kernel32.GlobalLock(handle)
            if not pointer:
                return None
            try:
                return ctypes.wstring_at(pointer)
            finally:
                kernel32.GlobalUnlock(handle)
        finally:
            user32.CloseClipboard()

    def apply(self, item: ClipItem, expected_sequence: int | None = None) -> int:
        if item.kind == "text":
            text = item.data.decode("utf-8")
            if "\0" in text:
                raise ClipboardError("远端文本包含 NUL 字符")
            return self._set_text(text, expected_sequence)
        if item.kind == "image":
            return self._set_image(item.data, expected_sequence)
        raise ClipboardError("不支持的远端剪贴板类型")

    def _write_owner(self) -> int:
        if not self._owner_hwnd or not user32.IsWindow(self._owner_hwnd):
            raise ClipboardError("剪贴板写入窗口句柄无效")
        return self._owner_hwnd

    def _set_text(self, text: str, expected_sequence: int | None) -> int:
        encoded = (text + "\0").encode("utf-16-le")
        handle = kernel32.GlobalAlloc(GMEM_MOVEABLE, len(encoded))
        if not handle:
            raise ClipboardError("无法分配剪贴板内存")
        transferred = False
        try:
            pointer = kernel32.GlobalLock(handle)
            if not pointer:
                raise ClipboardError("无法锁定剪贴板内存")
            try:
                ctypes.memmove(pointer, encoded, len(encoded))
            finally:
                kernel32.GlobalUnlock(handle)
            self._open(self._write_owner())
            try:
                if (
                    expected_sequence is not None
                    and self.sequence_number() != expected_sequence
                ):
                    raise ClipboardError("本机剪贴板已变化，未覆盖新内容")
                if not user32.EmptyClipboard():
                    raise ClipboardError("无法清空剪贴板")
                if not user32.SetClipboardData(CF_UNICODETEXT, handle):
                    raise ClipboardError("无法写入剪贴板")
                transferred = True
                written_sequence = self.sequence_number()
            finally:
                user32.CloseClipboard()
        finally:
            if not transferred:
                kernel32.GlobalFree(handle)
        return written_sequence

    def _set_image(self, data: bytes, expected_sequence: int | None) -> int:
        try:
            with Image.open(io.BytesIO(data)) as image:
                self._validate_image_dimensions(image)
                image.load()
                converted = image.convert("RGB")
                output = io.BytesIO()
                converted.save(output, format="BMP")
                dib = output.getvalue()[14:]
        except (OSError, UnidentifiedImageError, Image.DecompressionBombError) as exc:
            raise ClipboardError("收到的图片无法解码") from exc

        handle = kernel32.GlobalAlloc(GMEM_MOVEABLE, len(dib))
        if not handle:
            raise ClipboardError("无法分配剪贴板内存")
        transferred = False
        try:
            pointer = kernel32.GlobalLock(handle)
            if not pointer:
                raise ClipboardError("无法锁定剪贴板内存")
            try:
                ctypes.memmove(pointer, dib, len(dib))
            finally:
                kernel32.GlobalUnlock(handle)
            self._open(self._write_owner())
            try:
                if (
                    expected_sequence is not None
                    and self.sequence_number() != expected_sequence
                ):
                    raise ClipboardError("本机剪贴板已变化，未覆盖新内容")
                if not user32.EmptyClipboard():
                    raise ClipboardError("无法清空剪贴板")
                if not user32.SetClipboardData(CF_DIB, handle):
                    raise ClipboardError("无法写入图片剪贴板")
                transferred = True
                written_sequence = self.sequence_number()
            finally:
                user32.CloseClipboard()
        finally:
            if not transferred:
                kernel32.GlobalFree(handle)
        return written_sequence
