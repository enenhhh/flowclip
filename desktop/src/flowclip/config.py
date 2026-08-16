from __future__ import annotations

import ipaddress
import json
import secrets
import socket
import uuid
from dataclasses import asdict, dataclass, fields
from pathlib import Path
from urllib.parse import urlsplit

from .platform_integration import (
    ensure_private_directory,
    legacy_config_directories,
    secure_private_file,
    set_run_at_startup,
    user_config_directory,
    write_private_file,
)


APP_NAME = "FlowClip"
TOKEN_MIN_LENGTH = 16
TOKEN_MAX_LENGTH = 512

_TRUSTED_HTTP_NETWORKS = tuple(
    ipaddress.ip_network(value)
    for value in (
        "10.0.0.0/8",
        "100.64.0.0/10",
        "127.0.0.0/8",
        "169.254.0.0/16",
        "172.16.0.0/12",
        "192.168.0.0/16",
        "::1/128",
        "fc00::/7",
        "fe80::/10",
    )
)


class InvalidSharedTokenError(ValueError):
    pass


def validate_shared_token(token: object) -> None:
    if not isinstance(token, str) or not TOKEN_MIN_LENGTH <= len(token) <= TOKEN_MAX_LENGTH:
        raise InvalidSharedTokenError("共享密钥应为 16 到 512 个可见 ASCII 字符")
    if any(not 0x21 <= ord(character) <= 0x7E for character in token):
        raise InvalidSharedTokenError(
            "共享密钥只能使用可见 ASCII 字符，不能包含空格、中文或控制字符"
        )


def config_directory() -> Path:
    return user_config_directory(APP_NAME)


def config_path() -> Path:
    return config_directory() / "config.json"


def _legacy_config_paths(canonical: Path) -> tuple[Path, ...]:
    return tuple(
        directory / "config.json"
        for directory in legacy_config_directories(
            APP_NAME, canonical=canonical.parent
        )
    )


@dataclass(slots=True)
class AppConfig:
    device_id: str
    device_name: str
    server_url: str
    token: str
    auto_sync: bool
    poll_interval: float
    max_size_mb: int
    file_max_size_mb: int
    file_storage_limit_mb: int
    file_store_directory: str
    download_directory: str
    server_enabled: bool
    listen_host: str
    listen_port: int
    start_minimized: bool
    run_at_startup: bool
    require_public_https: bool

    @classmethod
    def defaults(cls) -> "AppConfig":
        downloads = Path.home() / "Downloads"
        return cls(
            device_id=str(uuid.uuid4()),
            device_name=socket.gethostname()[:64] or APP_NAME,
            server_url="http://127.0.0.1:8765",
            token=secrets.token_urlsafe(32),
            auto_sync=True,
            poll_interval=1.5,
            max_size_mb=20,
            file_max_size_mb=2048,
            file_storage_limit_mb=8192,
            file_store_directory=str(downloads / "FlowClip Server"),
            download_directory=str(downloads / "FlowClip"),
            server_enabled=False,
            listen_host="0.0.0.0",
            listen_port=8765,
            start_minimized=False,
            run_at_startup=False,
            require_public_https=True,
        )

    @property
    def max_bytes(self) -> int:
        return self.max_size_mb * 1024 * 1024

    @property
    def file_max_bytes(self) -> int:
        return self.file_max_size_mb * 1024 * 1024

    @property
    def file_storage_limit_bytes(self) -> int:
        return self.file_storage_limit_mb * 1024 * 1024

    @property
    def effective_server_url(self) -> str:
        if self.server_enabled:
            return f"http://127.0.0.1:{self.listen_port}"
        return self.server_url.rstrip("/")

    def validate(self) -> None:
        validate_shared_token(self.token)
        self._validate_non_token_fields()

    def _validate_non_token_fields(self) -> None:
        if not self.device_id or len(self.device_id) > 128:
            raise ValueError("设备 ID 无效")
        if not self.device_name.strip() or len(self.device_name) > 64:
            raise ValueError("设备名称应为 1 到 64 个字符")
        if not 0.5 <= self.poll_interval <= 60:
            raise ValueError("同步间隔应为 0.5 到 60 秒")
        if not 1 <= self.max_size_mb <= 100:
            raise ValueError("单条内容上限应为 1 到 100 MB")
        if not 1 <= self.file_max_size_mb <= 10_240:
            raise ValueError("单文件上限应为 1 到 10240 MB")
        if not self.file_max_size_mb <= self.file_storage_limit_mb <= 51_200:
            raise ValueError("文件存储总量应不小于单文件上限，且不超过 51200 MB")
        for label, raw_path in (
            ("服务端文件目录", self.file_store_directory),
            ("下载目录", self.download_directory),
        ):
            if not isinstance(raw_path, str) or not raw_path.strip():
                raise ValueError(f"{label}不能为空")
            if not Path(raw_path).expanduser().is_absolute():
                raise ValueError(f"{label}必须使用绝对路径")
        if not 1 <= self.listen_port <= 65535:
            raise ValueError("端口应为 1 到 65535")
        if not self.listen_host.strip():
            raise ValueError("监听地址不能为空")
        try:
            parsed = urlsplit(self.server_url)
            hostname = parsed.hostname
            port = parsed.port
        except ValueError as exc:
            raise ValueError("服务器地址格式无效") from exc
        scheme = parsed.scheme.lower()
        if scheme not in {"http", "https"} or not parsed.netloc or not hostname:
            raise ValueError("服务器地址必须是 http:// 或 https:// URL")
        if port is None or not 1 <= port <= 65535:
            raise ValueError("服务器地址必须包含有效端口")
        if (
            parsed.username is not None
            or parsed.password is not None
            or parsed.path not in {"", "/"}
            or parsed.query
            or parsed.fragment
        ):
            raise ValueError("服务器地址不能包含账号、路径、查询或片段")
        if scheme == "http" and self.require_public_https:
            try:
                address = ipaddress.ip_address(hostname)
            except ValueError as exc:
                raise ValueError("公网或域名服务器必须使用 HTTPS") from exc
            if not any(address in network for network in _TRUSTED_HTTP_NETWORKS):
                raise ValueError("公网或域名服务器必须使用 HTTPS")


def load_config() -> AppConfig:
    defaults = AppConfig.defaults()
    canonical = config_path()
    path = canonical
    if not canonical.exists():
        path = next(
            (
                candidate
                for candidate in _legacy_config_paths(canonical)
                if candidate.exists()
            ),
            canonical,
        )
    if not path.exists():
        save_config(defaults)
        return defaults
    ensure_private_directory(path.parent)
    secure_private_file(path)
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
        allowed = {field.name for field in fields(AppConfig)}
        merged = asdict(defaults)
        merged.update({key: value for key, value in raw.items() if key in allowed})
        config = AppConfig(**merged)
        try:
            config.validate()
        except InvalidSharedTokenError:
            # Keep an old non-ASCII token visible so the user can replace it without
            # losing unrelated server, port, or startup settings.
            config._validate_non_token_fields()
        if path != canonical:
            _write_config(config, validate=False)
        return config
    except (ValueError, TypeError, json.JSONDecodeError):
        backup = path.with_suffix(".invalid.json")
        try:
            path.replace(backup)
        except OSError:
            pass
        save_config(defaults)
        return defaults


def save_config(config: AppConfig) -> None:
    _write_config(config, validate=True)


def _write_config(config: AppConfig, *, validate: bool) -> None:
    if validate:
        config.validate()
    directory = config_directory()
    ensure_private_directory(directory)
    path = config_path()
    encoded = (
        json.dumps(asdict(config), ensure_ascii=False, indent=2) + "\n"
    ).encode("utf-8")
    write_private_file(path, encoded)
