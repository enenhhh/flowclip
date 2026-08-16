from __future__ import annotations

import base64
import hashlib
import time
import uuid
from dataclasses import dataclass
from typing import Any


SUPPORTED_KINDS = {"text", "image"}
_MIME_TOKEN_CHARS = frozenset(
    "abcdefghijklmnopqrstuvwxyz0123456789!#$&^_.+-"
)
_MIME_TOKEN_START_CHARS = frozenset("abcdefghijklmnopqrstuvwxyz0123456789")


class ProtocolError(ValueError):
    pass


def _is_mime_token(value: str, *, require_alnum_start: bool = False) -> bool:
    if not value or any(character not in _MIME_TOKEN_CHARS for character in value):
        return False
    return not require_alnum_start or value[0] in _MIME_TOKEN_START_CHARS


def _validate_mime(mime: str, kind: str) -> None:
    sections = mime.split(";")
    media_type = sections[0]
    if media_type != media_type.strip() or media_type.count("/") != 1:
        raise ProtocolError("MIME 类型格式无效")

    major, subtype = media_type.split("/", 1)
    if not _is_mime_token(major, require_alnum_start=True) or not _is_mime_token(
        subtype, require_alnum_start=True
    ):
        raise ProtocolError("MIME 类型格式无效")
    if major != kind:
        label = "文本" if kind == "text" else "图片"
        raise ProtocolError(f"{label}内容的 MIME 类型无效")

    for raw_parameter in sections[1:]:
        parameter = raw_parameter.strip()
        if parameter.count("=") != 1:
            raise ProtocolError("MIME 参数无效")
        name, value = parameter.split("=", 1)
        if not _is_mime_token(name) or not _is_mime_token(value):
            raise ProtocolError("MIME 参数无效")


@dataclass(frozen=True, slots=True)
class ClipItem:
    item_id: str
    origin: str
    kind: str
    mime: str
    filename: str
    data: bytes
    created_at: int

    @classmethod
    def create(
        cls,
        *,
        origin: str,
        kind: str,
        mime: str,
        data: bytes,
        filename: str = "",
    ) -> "ClipItem":
        return cls(
            item_id=str(uuid.uuid4()),
            origin=origin,
            kind=kind,
            mime=mime,
            filename=filename,
            data=data,
            created_at=int(time.time() * 1000),
        )

    @property
    def checksum(self) -> str:
        return hashlib.sha256(self.data).hexdigest()

    @property
    def signature(self) -> str:
        digest = hashlib.sha256()
        digest.update(self.kind.encode("utf-8"))
        digest.update(b"\0")
        digest.update(self.mime.encode("utf-8"))
        digest.update(b"\0")
        digest.update(self.data)
        return digest.hexdigest()

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.item_id,
            "origin": self.origin,
            "kind": self.kind,
            "mime": self.mime,
            "filename": self.filename,
            "data": base64.b64encode(self.data).decode("ascii"),
            "sha256": self.checksum,
            "createdAt": self.created_at,
        }

    @classmethod
    def from_dict(cls, raw: Any, max_bytes: int) -> "ClipItem":
        if not isinstance(raw, dict):
            raise ProtocolError("请求内容必须是 JSON 对象")

        def required_string(name: str, max_chars: int) -> str:
            value = raw.get(name)
            if not isinstance(value, str) or not value or len(value) > max_chars:
                raise ProtocolError(f"字段 {name} 无效")
            return value

        item_id = required_string("id", 128)
        origin = required_string("origin", 128)
        kind = required_string("kind", 16)
        if kind not in SUPPORTED_KINDS:
            raise ProtocolError("不支持的剪贴板类型")
        mime = required_string("mime", 128).lower()
        try:
            mime.encode("ascii")
        except UnicodeEncodeError as exc:
            raise ProtocolError("MIME 类型必须是 ASCII") from exc
        if any(ord(character) < 0x20 or ord(character) == 0x7F for character in mime):
            raise ProtocolError("MIME 类型包含无效字符")
        _validate_mime(mime, kind)

        filename = raw.get("filename", "")
        if not isinstance(filename, str) or len(filename) > 255:
            raise ProtocolError("字段 filename 无效")
        encoded = required_string("data", ((max_bytes + 2) // 3) * 4)
        try:
            data = base64.b64decode(encoded, validate=True)
        except (ValueError, TypeError) as exc:
            raise ProtocolError("data 不是有效的 Base64") from exc
        if base64.b64encode(data).decode("ascii") != encoded:
            raise ProtocolError("data 必须是规范的带填充 Base64")
        if len(data) > max_bytes:
            raise ProtocolError("内容超过服务器大小限制")
        if kind == "text":
            try:
                text = data.decode("utf-8")
            except UnicodeDecodeError as exc:
                raise ProtocolError("文本不是有效的 UTF-8") from exc
            if "\0" in text:
                raise ProtocolError("文本不能包含 NUL 字符")

        checksum = required_string("sha256", 64).lower()
        if len(checksum) != 64 or not all(c in "0123456789abcdef" for c in checksum):
            raise ProtocolError("sha256 字段无效")
        actual = hashlib.sha256(data).hexdigest()
        if actual != checksum:
            raise ProtocolError("内容校验失败")

        created_at = raw.get("createdAt")
        if not isinstance(created_at, int) or created_at < 0:
            raise ProtocolError("字段 createdAt 无效")
        return cls(item_id, origin, kind, mime, filename, data, created_at)
