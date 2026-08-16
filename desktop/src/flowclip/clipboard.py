from __future__ import annotations

import io
import sys
import time
from dataclasses import dataclass
from typing import Protocol

from PIL import Image, UnidentifiedImageError

from .model import ClipItem


MAX_IMAGE_PIXELS = 32_000_000
MAX_CLIPBOARD_BYTES = 100 * 1024 * 1024


class ClipboardError(RuntimeError):
    pass


class ClipboardBackend(Protocol):
    def sequence_number(self) -> int:
        ...

    def capture(self, origin: str, max_bytes: int) -> ClipItem | None:
        ...

    def apply(self, item: ClipItem, expected_sequence: int | None = None) -> int:
        ...


@dataclass(frozen=True, slots=True)
class ClipboardContent:
    kind: str
    mime: str
    data: bytes


def validate_image_dimensions(image: Image.Image) -> None:
    width, height = image.size
    if width <= 0 or height <= 0 or width * height > MAX_IMAGE_PIXELS:
        raise ClipboardError(
            f"图片尺寸 {width}x{height} 超过 {MAX_IMAGE_PIXELS:,} 像素限制"
        )


def normalize_image(data: bytes, max_bytes: int) -> bytes:
    try:
        with Image.open(io.BytesIO(data)) as image:
            validate_image_dimensions(image)
            image.load()
            converted = image.convert("RGBA") if image.mode != "RGBA" else image
            output = io.BytesIO()
            converted.save(output, format="PNG", optimize=True)
    except (OSError, UnidentifiedImageError, Image.DecompressionBombError) as exc:
        raise ClipboardError("剪贴板图片无法解码") from exc
    encoded = output.getvalue()
    ensure_size(encoded, max_bytes, "图片")
    return encoded


def image_to_png(image: Image.Image, max_bytes: int) -> bytes:
    validate_image_dimensions(image)
    output = io.BytesIO()
    converted = image.convert("RGBA") if image.mode != "RGBA" else image
    converted.save(output, format="PNG", optimize=True)
    encoded = output.getvalue()
    ensure_size(encoded, max_bytes, "图片")
    return encoded


def ensure_size(data: bytes, max_bytes: int, label: str) -> None:
    if len(data) > max_bytes:
        raise ClipboardError(
            f"{label}为 {len(data) / 1024 / 1024:.1f} MB，超过同步上限"
        )


def content_to_item(
    content: ClipboardContent | None, origin: str, max_bytes: int
) -> ClipItem | None:
    if content is None:
        return None
    if content.kind == "text":
        ensure_size(content.data, max_bytes, "文本")
        try:
            text = content.data.decode("utf-8")
        except UnicodeDecodeError as exc:
            raise ClipboardError("剪贴板文本不是有效的 UTF-8") from exc
        if "\0" in text:
            raise ClipboardError("文本不能包含 NUL 字符")
        return ClipItem.create(
            origin=origin,
            kind="text",
            mime="text/plain; charset=utf-8",
            data=content.data,
        )
    if content.kind == "image":
        data = normalize_image(content.data, max_bytes)
        return ClipItem.create(
            origin=origin,
            kind="image",
            mime="image/png",
            filename=time.strftime("FlowClip_%Y%m%d_%H%M%S.png"),
            data=data,
        )
    raise ClipboardError("不支持的剪贴板类型")


def create_clipboard(owner_hwnd: int | None = None) -> ClipboardBackend:
    if sys.platform == "win32":
        from .clipboard_windows import WindowsClipboard

        return WindowsClipboard(owner_hwnd)
    if sys.platform == "darwin":
        from .clipboard_macos import MacClipboard

        return MacClipboard()
    if sys.platform.startswith("linux"):
        from .clipboard_linux import LinuxClipboard

        return LinuxClipboard()
    raise ClipboardError(f"当前平台暂不受支持：{sys.platform}")
