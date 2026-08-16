from __future__ import annotations

import hashlib
import os
import shutil
import subprocess
import threading
import time
from collections.abc import Callable, Mapping

from .clipboard import (
    MAX_CLIPBOARD_BYTES,
    ClipboardContent,
    ClipboardError,
    content_to_item,
    normalize_image,
)
from .model import ClipItem


CommandRunner = Callable[[list[str], bytes | None, int], bytes]
_IMAGE_TYPES = ("image/png", "image/jpeg", "image/webp", "image/bmp")
_TEXT_TYPES = (
    "text/plain; charset=utf-8",
    "text/plain;charset=utf-8",
    "text/plain;charset=UTF-8",
    "UTF8_STRING",
    "text/plain",
)
_CACHE_SECONDS = 0.05


def _run_command(arguments: list[str], input_data: bytes | None, limit: int) -> bytes:
    try:
        completed = subprocess.run(
            arguments,
            input=input_data,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            timeout=8,
            check=False,
        )
    except FileNotFoundError as exc:
        raise ClipboardError(f"找不到剪贴板工具：{arguments[0]}") from exc
    except subprocess.TimeoutExpired as exc:
        raise ClipboardError(f"剪贴板工具响应超时：{arguments[0]}") from exc
    if completed.returncode != 0:
        detail = completed.stderr.decode("utf-8", errors="replace").strip()
        detail = detail[:240] if detail else f"退出码 {completed.returncode}"
        raise ClipboardError(f"剪贴板工具执行失败：{detail}")
    if len(completed.stdout) > limit:
        raise ClipboardError("剪贴板内容超过 100 MB 安全读取上限")
    return completed.stdout


class LinuxClipboard:
    def __init__(
        self,
        *,
        environment: Mapping[str, str] | None = None,
        which: Callable[[str], str | None] = shutil.which,
        runner: CommandRunner = _run_command,
    ) -> None:
        env = os.environ if environment is None else environment
        has_wayland = bool(env.get("WAYLAND_DISPLAY"))
        has_x11 = bool(env.get("DISPLAY"))
        if has_wayland and which("wl-copy") and which("wl-paste"):
            self.mode = "wayland"
        elif has_x11 and which("xclip"):
            self.mode = "x11"
        else:
            raise ClipboardError(
                "Linux 剪贴板不可用：Wayland 请安装 wl-clipboard，"
                "X11 请安装 xclip，并确认图形会话环境变量可用"
            )
        self._runner = runner
        self._lock = threading.RLock()
        self._sequence = 0
        self._fingerprint: bytes | None = None
        self._initialized = False
        self._cached: ClipboardContent | None = None
        self._cached_at = 0.0

    def _run(
        self, arguments: list[str], input_data: bytes | None = None, limit: int = 64 * 1024
    ) -> bytes:
        return self._runner(arguments, input_data, limit)

    def _available_types(self) -> set[str]:
        try:
            if self.mode == "wayland":
                raw = self._run(["wl-paste", "--list-types"])
            else:
                raw = self._run(
                    ["xclip", "-selection", "clipboard", "-target", "TARGETS", "-out"]
                )
        except ClipboardError:
            return set()
        decoded = raw.decode("ascii", errors="ignore")
        if self.mode == "wayland":
            return {line.strip() for line in decoded.splitlines() if line.strip()}
        return set(decoded.split())

    @staticmethod
    def _select_type(available: set[str], candidates: tuple[str, ...]) -> str | None:
        for candidate in candidates:
            if candidate in available:
                return candidate
        lowered = {value.lower(): value for value in available}
        for candidate in candidates:
            found = lowered.get(candidate.lower())
            if found is not None:
                return found
        return None

    def _read_type(self, mime: str) -> bytes:
        if self.mode == "wayland":
            return self._run(
                ["wl-paste", "--no-newline", "--type", mime],
                limit=MAX_CLIPBOARD_BYTES,
            )
        return self._run(
            ["xclip", "-selection", "clipboard", "-target", mime, "-out"],
            limit=MAX_CLIPBOARD_BYTES,
        )

    def _read_current(self) -> ClipboardContent | None:
        available = self._available_types()
        image_type = self._select_type(available, _IMAGE_TYPES)
        text_type = self._select_type(available, _TEXT_TYPES)
        try:
            if image_type is not None:
                return ClipboardContent("image", image_type.lower(), self._read_type(image_type))
            if text_type is not None:
                data = self._read_type(text_type)
                data.decode("utf-8")
                return ClipboardContent("text", "text/plain; charset=utf-8", data)
        except (ClipboardError, UnicodeDecodeError):
            return None
        return None

    @staticmethod
    def _content_fingerprint(content: ClipboardContent | None) -> bytes:
        digest = hashlib.sha256()
        if content is None:
            digest.update(b"empty")
        else:
            digest.update(content.kind.encode("ascii"))
            digest.update(b"\0")
            digest.update(content.mime.encode("ascii"))
            digest.update(b"\0")
            digest.update(content.data)
        return digest.digest()

    def _refresh(self, *, force: bool = False) -> ClipboardContent | None:
        now = time.monotonic()
        if not force and now - self._cached_at < _CACHE_SECONDS:
            return self._cached
        content = self._read_current()
        fingerprint = self._content_fingerprint(content)
        if self._initialized and fingerprint != self._fingerprint:
            self._sequence += 1
        self._initialized = True
        self._fingerprint = fingerprint
        self._cached = content
        self._cached_at = now
        return content

    def sequence_number(self) -> int:
        with self._lock:
            self._refresh()
            return self._sequence

    def capture(self, origin: str, max_bytes: int) -> ClipItem | None:
        with self._lock:
            return content_to_item(self._refresh(), origin, max_bytes)

    def _write(self, content: ClipboardContent) -> None:
        wire_mime = (
            "text/plain;charset=utf-8" if content.kind == "text" else content.mime
        )
        if self.mode == "wayland":
            self._run(
                ["wl-copy", "--type", wire_mime],
                input_data=content.data,
            )
        else:
            self._run(
                ["xclip", "-selection", "clipboard", "-target", wire_mime, "-in"],
                input_data=content.data,
            )

    def apply(self, item: ClipItem, expected_sequence: int | None = None) -> int:
        with self._lock:
            self._refresh(force=True)
            if expected_sequence is not None and expected_sequence != self._sequence:
                raise ClipboardError("本机剪贴板已变化，未覆盖新内容")
            if item.kind == "text":
                try:
                    text = item.data.decode("utf-8")
                except UnicodeDecodeError as exc:
                    raise ClipboardError("远端文本不是有效的 UTF-8") from exc
                if "\0" in text:
                    raise ClipboardError("远端文本包含 NUL 字符")
                desired = ClipboardContent(
                    "text", "text/plain; charset=utf-8", item.data
                )
            elif item.kind == "image":
                desired = ClipboardContent(
                    "image",
                    "image/png",
                    normalize_image(item.data, MAX_CLIPBOARD_BYTES),
                )
            else:
                raise ClipboardError("不支持的远端剪贴板类型")
            self._write(desired)
            expected_fingerprint = self._content_fingerprint(desired)
            for _attempt in range(10):
                current = self._refresh(force=True)
                if self._content_fingerprint(current) == expected_fingerprint:
                    return self._sequence
                time.sleep(0.05)
            raise ClipboardError("写入后无法确认 Linux 剪贴板内容")
