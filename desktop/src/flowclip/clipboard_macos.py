from __future__ import annotations

from collections.abc import Callable
from typing import Any

from .clipboard import (
    MAX_CLIPBOARD_BYTES,
    ClipboardContent,
    ClipboardError,
    content_to_item,
    normalize_image,
)
from .model import ClipItem


class MacClipboard:
    STRING_TYPE = "public.utf8-plain-text"
    PNG_TYPE = "public.png"
    TIFF_TYPE = "public.tiff"

    def __init__(
        self,
        *,
        pasteboard: Any | None = None,
        data_factory: Callable[[bytes], Any] | None = None,
    ) -> None:
        if pasteboard is None or data_factory is None:
            try:
                from AppKit import NSPasteboard
                from Foundation import NSData
            except ImportError as exc:
                raise ClipboardError(
                    "macOS 剪贴板组件缺失，请重新安装完整的 FlowClip 应用"
                ) from exc
            pasteboard = pasteboard or NSPasteboard.generalPasteboard()
            data_factory = data_factory or (
                lambda value: NSData.dataWithBytes_length_(value, len(value))
            )
        self._pasteboard = pasteboard
        self._data_factory = data_factory

    def sequence_number(self) -> int:
        return int(self._pasteboard.changeCount())

    def _read_current(self) -> ClipboardContent | None:
        image_type = self._pasteboard.availableTypeFromArray_(
            [self.PNG_TYPE, self.TIFF_TYPE]
        )
        if image_type is not None:
            value = self._pasteboard.dataForType_(image_type)
            if value is not None:
                raw = bytes(value)
                if len(raw) > MAX_CLIPBOARD_BYTES:
                    raise ClipboardError("剪贴板内容超过 100 MB 安全读取上限")
                return ClipboardContent("image", str(image_type), raw)
        text = self._pasteboard.stringForType_(self.STRING_TYPE)
        if text is None:
            return None
        encoded = str(text).encode("utf-8")
        if len(encoded) > MAX_CLIPBOARD_BYTES:
            raise ClipboardError("剪贴板内容超过 100 MB 安全读取上限")
        return ClipboardContent("text", "text/plain; charset=utf-8", encoded)

    def capture(self, origin: str, max_bytes: int) -> ClipItem | None:
        return content_to_item(self._read_current(), origin, max_bytes)

    def apply(self, item: ClipItem, expected_sequence: int | None = None) -> int:
        current = self.sequence_number()
        if expected_sequence is not None and current != expected_sequence:
            raise ClipboardError("本机剪贴板已变化，未覆盖新内容")
        self._pasteboard.clearContents()
        if item.kind == "text":
            try:
                text = item.data.decode("utf-8")
            except UnicodeDecodeError as exc:
                raise ClipboardError("远端文本不是有效的 UTF-8") from exc
            if "\0" in text:
                raise ClipboardError("远端文本包含 NUL 字符")
            written = self._pasteboard.setString_forType_(text, self.STRING_TYPE)
        elif item.kind == "image":
            data = normalize_image(item.data, MAX_CLIPBOARD_BYTES)
            written = self._pasteboard.setData_forType_(
                self._data_factory(data), self.PNG_TYPE
            )
        else:
            raise ClipboardError("不支持的远端剪贴板类型")
        if not written:
            raise ClipboardError("无法写入 macOS 剪贴板")
        return self.sequence_number()
