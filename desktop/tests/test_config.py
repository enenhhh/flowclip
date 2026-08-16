from __future__ import annotations

import sys
import json
import os
import tempfile
import unittest
from dataclasses import replace
from pathlib import Path
from unittest.mock import patch


SOURCE = Path(__file__).resolve().parents[1] / "src"
sys.path.insert(0, str(SOURCE))

from flowclip.config import AppConfig, load_config  # noqa: E402


class ConfigValidationTests(unittest.TestCase):
    def validate_url(self, server_url: str) -> None:
        replace(AppConfig.defaults(), server_url=server_url).validate()

    def test_server_url_requires_explicit_valid_port(self) -> None:
        for server_url in (
            "http://127.0.0.1",
            "https://clip.example.com",
            "http://127.0.0.1:0",
            "http://127.0.0.1:65536",
            "http://127.0.0.1:not-a-port",
        ):
            with self.subTest(server_url=server_url):
                with self.assertRaisesRegex(ValueError, "端口|格式"):
                    self.validate_url(server_url)

    def test_disabling_public_https_does_not_relax_port_validation(self) -> None:
        for server_url in (
            "http://8.8.8.8",
            "http://clip.example.com",
            "http://8.8.8.8:0",
            "http://clip.example.com:65536",
        ):
            with self.subTest(server_url=server_url):
                with self.assertRaisesRegex(ValueError, "端口|格式"):
                    replace(
                        AppConfig.defaults(),
                        server_url=server_url,
                        require_public_https=False,
                    ).validate()

    def test_http_accepts_only_trusted_literal_networks(self) -> None:
        for host in (
            "127.0.0.1",
            "10.0.0.2",
            "172.16.0.2",
            "192.168.1.20",
            "169.254.1.2",
            "100.64.0.1",
            "100.127.255.254",
            "[::1]",
            "[fd00::2]",
            "[fe80::2]",
        ):
            with self.subTest(host=host):
                self.validate_url(f"http://{host}:8765")

    def test_http_rejects_public_addresses_and_hostnames(self) -> None:
        for host in ("8.8.8.8", "100.128.0.1", "clip.local"):
            with self.subTest(host=host):
                with self.assertRaisesRegex(ValueError, "HTTPS"):
                    self.validate_url(f"http://{host}:8765")

    def test_https_accepts_public_host_with_explicit_port(self) -> None:
        self.validate_url("https://clip.example.com:443")

    def test_public_http_can_be_enabled_explicitly(self) -> None:
        for host in ("8.8.8.8", "clip.example.com"):
            with self.subTest(host=host):
                replace(
                    AppConfig.defaults(),
                    server_url=f"http://{host}:8765",
                    require_public_https=False,
                ).validate()

    def test_shared_token_requires_visible_ascii(self) -> None:
        for token in (
            "短密钥",
            "中文密钥不能用于HTTP头1234",
            "token with spaces is invalid",
            "valid-token-12345\n",
            "a" * 513,
        ):
            with self.subTest(token=token):
                with self.assertRaisesRegex(ValueError, "ASCII|16 到 512"):
                    replace(AppConfig.defaults(), token=token).validate()

        replace(AppConfig.defaults(), token="visible-ASCII_1234").validate()

    def test_load_preserves_other_settings_when_old_token_is_non_ascii(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            flowclip_dir = Path(directory) / "FlowClip"
            flowclip_dir.mkdir()
            raw = {
                "device_id": "kept-device-id",
                "device_name": "Kept device",
                "server_url": "http://127.0.0.1:9988",
                "token": "这是旧的中文共享密钥123456",
                "auto_sync": True,
                "poll_interval": 2.5,
                "max_size_mb": 20,
                "server_enabled": True,
                "listen_host": "0.0.0.0",
                "listen_port": 9988,
                "start_minimized": False,
                "run_at_startup": False,
            }
            (flowclip_dir / "config.json").write_text(
                json.dumps(raw, ensure_ascii=False), encoding="utf-8"
            )

            with patch.dict(os.environ, {"APPDATA": directory}):
                loaded = load_config()

            self.assertEqual(loaded.token, raw["token"])
            self.assertEqual(loaded.listen_port, 9988)
            self.assertTrue(loaded.server_enabled)
            self.assertTrue(loaded.require_public_https)
            self.assertFalse((flowclip_dir / "config.invalid.json").exists())

    def test_server_url_rejects_credentials_and_extra_components(self) -> None:
        for server_url in (
            "https://user@clip.example.com:443",
            "https://clip.example.com:443/api",
            "https://clip.example.com:443/?after=1",
            "https://clip.example.com:443/#fragment",
        ):
            with self.subTest(server_url=server_url):
                with self.assertRaisesRegex(ValueError, "不能包含"):
                    self.validate_url(server_url)

    def test_file_limits_are_independent_from_clipboard_limit(self) -> None:
        config = replace(
            AppConfig.defaults(),
            max_size_mb=8,
            file_max_size_mb=2048,
            file_storage_limit_mb=8192,
        )
        config.validate()
        self.assertEqual(config.max_bytes, 8 * 1024 * 1024)
        self.assertEqual(config.file_max_bytes, 2048 * 1024 * 1024)
        self.assertEqual(config.file_storage_limit_bytes, 8192 * 1024 * 1024)

    def test_file_storage_limit_must_cover_one_file(self) -> None:
        with self.assertRaisesRegex(ValueError, "不小于单文件"):
            replace(
                AppConfig.defaults(),
                file_max_size_mb=2048,
                file_storage_limit_mb=1024,
            ).validate()

    def test_file_directories_must_be_absolute(self) -> None:
        for field in ("file_store_directory", "download_directory"):
            with self.subTest(field=field):
                with self.assertRaisesRegex(ValueError, "绝对路径"):
                    replace(AppConfig.defaults(), **{field: "relative/path"}).validate()


if __name__ == "__main__":
    unittest.main()
