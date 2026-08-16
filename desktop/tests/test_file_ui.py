from __future__ import annotations

import sys
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace


SOURCE = Path(__file__).resolve().parents[1] / "src"
sys.path.insert(0, str(SOURCE))

from flowclip.client import ConnectionFailure  # noqa: E402
from flowclip.file_client import FileTransferCancelled  # noqa: E402
from flowclip.file_ui import (  # noqa: E402
    FileTransferWindow,
    format_byte_count,
    format_transfer_rate,
    inspect_upload_path,
)


class FileUiFormattingTests(unittest.TestCase):
    def test_byte_counts_are_compact_and_stable(self) -> None:
        self.assertEqual(format_byte_count(0), "0 B")
        self.assertEqual(format_byte_count(1024), "1.0 KB")
        self.assertEqual(format_byte_count(1536), "1.5 KB")
        self.assertEqual(format_byte_count(2 * 1024 * 1024), "2.0 MB")

    def test_transfer_rate_handles_idle_and_active_states(self) -> None:
        self.assertEqual(format_transfer_rate(0), "--")
        self.assertEqual(format_transfer_rate(1024 * 1024), "1.0 MB/s")


class FakeUploadClient:
    def __init__(self, after_upload=None) -> None:
        self.uploaded: list[Path] = []
        self.after_upload = after_upload
        self.list_calls = 0

    def upload_file(self, path, *, origin, progress, cancelled):
        selected = Path(path)
        if not selected.exists():
            raise ConnectionFailure("文件在上传前消失")
        size = selected.stat().st_size
        progress(0, size)
        if cancelled():
            raise FileTransferCancelled("文件传输已取消")
        progress(size, size)
        self.uploaded.append(selected)
        if self.after_upload is not None:
            self.after_upload(selected)
        return object()

    def list_files(self):
        self.list_calls += 1
        return []


def upload_operation(paths, client, max_bytes=1024):
    window = object.__new__(FileTransferWindow)
    window._worker = None
    window._config_provider = lambda: SimpleNamespace(
        file_max_bytes=max_bytes,
        device_id="desktop-test",
    )
    window._client = lambda _config=None: client
    window._set_status = lambda _message, _level="muted": None
    captured = {}

    def start_job(status, operation, *, cancellable=True):
        captured["status"] = status
        captured["operation"] = operation
        captured["cancellable"] = cancellable
        return True

    window._start_job = start_job
    assert window.upload_paths(paths)
    return captured["operation"]


class FileUiUploadTests(unittest.TestCase):
    def test_inspect_upload_path_rejects_directories_and_oversized_files(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            directory = Path(temporary)
            source = directory / "large.bin"
            source.write_bytes(b"12345")
            with self.assertRaisesRegex(ValueError, "不支持文件夹"):
                inspect_upload_path(directory, 10)
            with self.assertRaisesRegex(ValueError, "超过当前上限"):
                inspect_upload_path(source, 4)

    def test_batch_upload_keeps_valid_files_and_reports_invalid_entries(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            directory = Path(temporary)
            valid = directory / "正常 文件.txt"
            valid.write_bytes(b"abc")
            oversized = directory / "too-large.bin"
            oversized.write_bytes(b"12345")
            missing = directory / "missing.txt"
            client = FakeUploadClient()
            operation = upload_operation(
                (valid, directory, oversized, missing), client, max_bytes=4
            )
            progress = []

            result = operation(lambda done, total: progress.append((done, total)), lambda: False)

            self.assertEqual(client.uploaded, [valid])
            self.assertEqual(client.list_calls, 1)
            self.assertEqual(result.level, "warning")
            self.assertIn("已上传 1 个文件", result.message)
            self.assertIn("3 个失败", result.message)
            self.assertEqual(progress[-1], (3, 3))

    def test_file_disappearing_between_validation_and_upload_is_partial_failure(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            directory = Path(temporary)
            first = directory / "first.bin"
            second = directory / "second.bin"
            first.write_bytes(b"one")
            second.write_bytes(b"two")

            def remove_second(uploaded: Path) -> None:
                if uploaded == first:
                    second.unlink()

            client = FakeUploadClient(remove_second)
            operation = upload_operation((first, second), client)
            progress = []

            result = operation(lambda done, total: progress.append((done, total)), lambda: False)

            self.assertEqual(client.uploaded, [first])
            self.assertEqual(result.level, "warning")
            self.assertIn("second.bin", result.message)
            self.assertEqual(progress[-1], (6, 6))

    def test_cancellation_stops_before_the_next_file(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            directory = Path(temporary)
            first = directory / "first.bin"
            second = directory / "second.bin"
            first.write_bytes(b"one")
            second.write_bytes(b"two")
            cancelled = False

            def cancel_after_first(_uploaded: Path) -> None:
                nonlocal cancelled
                cancelled = True

            client = FakeUploadClient(cancel_after_first)
            operation = upload_operation((first, second), client)

            with self.assertRaises(FileTransferCancelled):
                operation(lambda _done, _total: None, lambda: cancelled)
            self.assertEqual(client.uploaded, [first])
            self.assertEqual(client.list_calls, 0)


if __name__ == "__main__":
    unittest.main()
