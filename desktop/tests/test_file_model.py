from __future__ import annotations

import sys
import unittest
import uuid
from pathlib import Path


SOURCE = Path(__file__).resolve().parents[1] / "src"
sys.path.insert(0, str(SOURCE))

from flowclip.file_model import (  # noqa: E402
    FileMetadata,
    FileProtocolError,
    FileRecord,
)


def metadata_dict(**changes: object) -> dict[str, object]:
    raw: dict[str, object] = {
        "id": str(uuid.uuid4()),
        "origin": "设备一",
        "filename": "示例.txt",
        "mime": "text/plain",
        "size": 12,
        "createdAt": 123456,
    }
    raw.update(changes)
    return raw


class FileMetadataTests(unittest.TestCase):
    def test_chinese_filename_is_preserved_and_normalized_to_nfc(self) -> None:
        metadata = FileMetadata.from_dict(
            metadata_dict(filename="中文-e\u0301.txt"), 1024
        )
        self.assertEqual(metadata.filename, "中文-é.txt")
        self.assertEqual(metadata.to_dict()["filename"], "中文-é.txt")

    def test_path_control_reserved_and_device_names_are_rejected(self) -> None:
        invalid = (
            "",
            ".",
            "..",
            "a/b.txt",
            "a\\b.txt",
            "a\0b.txt",
            "bad:name.txt",
            "trailing. ",
            "trailing.",
            "CON",
            "con.txt",
            "LPT9.log",
            "COM1.anything",
            "COM¹.txt",
            "LPT².log",
            "😀" * 64 + ".txt",
        )
        for filename in invalid:
            with self.subTest(filename=filename):
                with self.assertRaises(FileProtocolError):
                    FileMetadata.from_dict(metadata_dict(filename=filename), 1024)

    def test_invalid_unicode_is_rejected_before_json_or_disk_io(self) -> None:
        for field in ("origin", "filename"):
            with self.subTest(field=field):
                with self.assertRaisesRegex(FileProtocolError, "Unicode"):
                    FileMetadata.from_dict(
                        metadata_dict(**{field: "bad\ud800value"}), 1024
                    )

    def test_portable_filename_utf8_limit_is_180_bytes(self) -> None:
        accepted = "a" * 176 + ".txt"
        self.assertEqual(
            FileMetadata.from_dict(metadata_dict(filename=accepted), 1024).filename,
            accepted,
        )
        with self.assertRaisesRegex(FileProtocolError, "180 字节"):
            FileMetadata.from_dict(
                metadata_dict(filename="a" * 177 + ".txt"), 1024
            )

    def test_uuid_must_be_canonical(self) -> None:
        canonical = str(uuid.uuid4())
        for value in (canonical.upper(), canonical.replace("-", ""), "not-a-uuid"):
            with self.subTest(value=value):
                with self.assertRaisesRegex(FileProtocolError, "规范 UUID"):
                    FileMetadata.from_dict(metadata_dict(id=value), 1024)

    def test_size_and_timestamp_require_real_non_negative_integers(self) -> None:
        for field, value in (
            ("size", True),
            ("size", -1),
            ("size", 1.0),
            ("createdAt", False),
            ("createdAt", -1),
            ("createdAt", "1"),
        ):
            with self.subTest(field=field, value=value):
                with self.assertRaisesRegex(FileProtocolError, "非负整数"):
                    FileMetadata.from_dict(metadata_dict(**{field: value}), 1024)

        self.assertEqual(FileMetadata.from_dict(metadata_dict(size=0), 0).size, 0)
        with self.assertRaisesRegex(FileProtocolError, "大小限制"):
            FileMetadata.from_dict(metadata_dict(size=2), 1)

    def test_mime_is_strict_ascii_type_subtype(self) -> None:
        accepted = {
            "IMAGE/PNG": "image/png",
            "application/octet-stream": "application/octet-stream",
            "image/svg+xml": "image/svg+xml",
        }
        for value, expected in accepted.items():
            with self.subTest(value=value):
                self.assertEqual(
                    FileMetadata.from_dict(metadata_dict(mime=value), 1024).mime,
                    expected,
                )

        for value in (
            "text/plain; charset=utf-8",
            "text/",
            "/plain",
            "text//plain",
            "文本/plain",
            "text/plain\n",
        ):
            with self.subTest(value=value):
                with self.assertRaisesRegex(FileProtocolError, "MIME"):
                    FileMetadata.from_dict(metadata_dict(mime=value), 1024)

    def test_ready_record_requires_valid_sha256(self) -> None:
        metadata = FileMetadata.from_dict(metadata_dict(), 1024)
        pending = FileRecord(metadata, "pending")
        self.assertNotIn("sha256", pending.to_dict())
        ready = FileRecord(metadata, "ready", "A" * 64)
        self.assertEqual(ready.to_dict()["sha256"], "a" * 64)
        with self.assertRaisesRegex(FileProtocolError, "sha256"):
            FileRecord(metadata, "ready", "bad")


if __name__ == "__main__":
    unittest.main()
