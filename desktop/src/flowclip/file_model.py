from __future__ import annotations

import re
import unicodedata
import uuid
from dataclasses import dataclass
from typing import Any


DEFAULT_FILE_MAX_BYTES = 10 * 1024 * 1024 * 1024
SHA256_HEX_LENGTH = 64

_MIME_PATTERN = re.compile(
    r"^[A-Za-z0-9][A-Za-z0-9!#$&^_.+-]*/"
    r"[A-Za-z0-9][A-Za-z0-9!#$&^_.+-]*$"
)
_WINDOWS_INVALID_FILENAME_CHARACTERS = frozenset('<>:"/\\|?*')
_WINDOWS_DEVICE_NAMES = frozenset(
    {"CON", "PRN", "AUX", "NUL"}
    | {f"COM{number}" for number in range(1, 10)}
    | {f"LPT{number}" for number in range(1, 10)}
    | {f"COM{number}" for number in "¹²³"}
    | {f"LPT{number}" for number in "¹²³"}
)


class FileProtocolError(ValueError):
    pass


def _required_string(raw: dict[str, Any], name: str, max_chars: int) -> str:
    value = raw.get(name)
    if not isinstance(value, str) or not value or len(value) > max_chars:
        raise FileProtocolError(f"字段 {name} 无效")
    try:
        value.encode("utf-8")
    except UnicodeEncodeError as exc:
        raise FileProtocolError(f"字段 {name} 包含无效 Unicode 字符") from exc
    return value


def _validate_file_id(value: str) -> str:
    try:
        parsed = uuid.UUID(value)
    except (ValueError, AttributeError) as exc:
        raise FileProtocolError("字段 id 必须是规范 UUID") from exc
    canonical = str(parsed)
    if value != canonical:
        raise FileProtocolError("字段 id 必须是规范 UUID")
    return canonical


def _validate_origin(value: str) -> str:
    if any(unicodedata.category(character) == "Cc" for character in value):
        raise FileProtocolError("字段 origin 包含控制字符")
    return value


def normalize_filename(value: object) -> str:
    if not isinstance(value, str):
        raise FileProtocolError("字段 filename 无效")
    filename = unicodedata.normalize("NFC", value)
    if not filename or filename in {".", ".."}:
        raise FileProtocolError("文件名不能为空或使用相对路径名称")
    if len(filename) > 255:
        raise FileProtocolError("文件名不能超过 255 个字符")
    try:
        encoded = filename.encode("utf-8")
    except UnicodeEncodeError as exc:
        raise FileProtocolError("文件名包含无效 Unicode 字符") from exc
    if len(encoded) > 180:
        raise FileProtocolError("文件名编码后不能超过 180 字节")
    if len(filename.encode("utf-16-le")) // 2 > 255:
        raise FileProtocolError("文件名不能超过 255 个 UTF-16 单元")
    if any(character in _WINDOWS_INVALID_FILENAME_CHARACTERS for character in filename):
        raise FileProtocolError("文件名包含路径分隔符或系统保留字符")
    if any(unicodedata.category(character) == "Cc" for character in filename):
        raise FileProtocolError("文件名包含控制字符")
    if filename.endswith((" ", ".")):
        raise FileProtocolError("文件名不能以空格或点结尾")
    device_stem = filename.split(".", 1)[0].upper()
    if device_stem in _WINDOWS_DEVICE_NAMES:
        raise FileProtocolError("文件名使用了系统保留设备名")
    return filename


def normalize_mime(value: object) -> str:
    if not isinstance(value, str) or not value or len(value) > 128:
        raise FileProtocolError("字段 mime 无效")
    try:
        value.encode("ascii")
    except UnicodeEncodeError as exc:
        raise FileProtocolError("MIME 类型必须是 ASCII type/subtype") from exc
    if not _MIME_PATTERN.fullmatch(value):
        raise FileProtocolError("MIME 类型必须是 ASCII type/subtype")
    return value.lower()


def _validate_non_negative_integer(value: object, name: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        raise FileProtocolError(f"字段 {name} 必须是非负整数")
    return value


def validate_sha256(value: object) -> str:
    if not isinstance(value, str):
        raise FileProtocolError("字段 sha256 无效")
    normalized = value.lower()
    if len(normalized) != SHA256_HEX_LENGTH or any(
        character not in "0123456789abcdef" for character in normalized
    ):
        raise FileProtocolError("字段 sha256 无效")
    return normalized


@dataclass(frozen=True, slots=True)
class FileMetadata:
    file_id: str
    origin: str
    filename: str
    mime: str
    size: int
    created_at: int

    @classmethod
    def from_dict(
        cls,
        raw: Any,
        max_bytes: int = DEFAULT_FILE_MAX_BYTES,
    ) -> "FileMetadata":
        if not isinstance(raw, dict):
            raise FileProtocolError("请求内容必须是 JSON 对象")
        if isinstance(max_bytes, bool) or not isinstance(max_bytes, int) or max_bytes < 0:
            raise ValueError("max_bytes 必须是非负整数")

        file_id = _validate_file_id(_required_string(raw, "id", 36))
        origin = _validate_origin(_required_string(raw, "origin", 128))
        filename = normalize_filename(raw.get("filename"))
        mime = normalize_mime(raw.get("mime"))
        size = _validate_non_negative_integer(raw.get("size"), "size")
        if size > max_bytes:
            raise FileProtocolError("文件超过服务器单文件大小限制")
        created_at = _validate_non_negative_integer(raw.get("createdAt"), "createdAt")
        return cls(file_id, origin, filename, mime, size, created_at)

    def to_dict(self) -> dict[str, object]:
        return {
            "id": self.file_id,
            "origin": self.origin,
            "filename": self.filename,
            "mime": self.mime,
            "size": self.size,
            "createdAt": self.created_at,
        }


@dataclass(frozen=True, slots=True)
class FileRecord:
    metadata: FileMetadata
    status: str
    sha256: str | None = None

    def __post_init__(self) -> None:
        if self.status not in {"pending", "ready"}:
            raise FileProtocolError("文件状态无效")
        if self.status == "pending" and self.sha256 is not None:
            raise FileProtocolError("待上传文件不能包含 sha256")
        if self.status == "ready":
            object.__setattr__(self, "sha256", validate_sha256(self.sha256))

    @property
    def file_id(self) -> str:
        return self.metadata.file_id

    @property
    def size(self) -> int:
        return self.metadata.size

    def to_dict(self) -> dict[str, object]:
        payload = self.metadata.to_dict()
        payload["status"] = self.status
        if self.sha256 is not None:
            payload["sha256"] = self.sha256
        return payload

    @classmethod
    def from_dict(
        cls,
        raw: Any,
        max_bytes: int = DEFAULT_FILE_MAX_BYTES,
    ) -> "FileRecord":
        if not isinstance(raw, dict):
            raise FileProtocolError("文件记录必须是 JSON 对象")
        metadata = FileMetadata.from_dict(raw, max_bytes)
        status = raw.get("status")
        if not isinstance(status, str):
            raise FileProtocolError("文件状态无效")
        sha256 = raw.get("sha256")
        return cls(metadata, status, sha256 if isinstance(sha256, str) else None)
