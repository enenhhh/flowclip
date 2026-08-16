from __future__ import annotations

import hashlib
import io
import json
import sys
import tempfile
import threading
import unittest
import uuid
from pathlib import Path
from unittest import mock


SOURCE = Path(__file__).resolve().parents[1] / "src"
sys.path.insert(0, str(SOURCE))

from flowclip.file_model import FileMetadata  # noqa: E402
from flowclip.file_store import (  # noqa: E402
    TRANSFER_CHUNK_BYTES,
    FileBusyError,
    FileCapacityError,
    FileSizeMismatchError,
    FileStore,
)


def metadata(size: int, *, file_id: str | None = None, created_at: int = 1) -> FileMetadata:
    return FileMetadata.from_dict(
        {
            "id": file_id or str(uuid.uuid4()),
            "origin": "测试设备",
            "filename": "测试.bin",
            "mime": "application/octet-stream",
            "size": size,
            "createdAt": created_at,
        },
        max_bytes=10 * TRANSFER_CHUNK_BYTES,
    )


class GuardedReader(io.BytesIO):
    def __init__(self, value: bytes) -> None:
        super().__init__(value)
        self.requests: list[int] = []

    def read(self, size: int = -1) -> bytes:
        self.requests.append(size)
        if size < 0 or size > TRANSFER_CHUNK_BYTES:
            raise AssertionError(f"unbounded read: {size}")
        return super().read(size)


class FileStoreTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.directory = Path(self.temporary.name) / "store"

    def tearDown(self) -> None:
        self.temporary.cleanup()

    def make_store(
        self,
        *,
        maximum: int = 4 * TRANSFER_CHUNK_BYTES,
        quota: int = 8 * TRANSFER_CHUNK_BYTES,
        max_items: int = 100,
    ) -> FileStore:
        return FileStore(
            self.directory,
            file_max_bytes=maximum,
            storage_limit_bytes=quota,
            max_items=max_items,
        )

    def test_pending_and_ready_records_both_count_toward_quota(self) -> None:
        store = self.make_store(maximum=10, quota=10)
        first = metadata(6)
        store.create_pending(first)
        with self.assertRaisesRegex(FileCapacityError, "配额"):
            store.create_pending(metadata(5))
        store.upload(first.file_id, io.BytesIO(b"a" * 6), 6)
        store.create_pending(metadata(4))
        self.assertEqual(store.used_bytes, 10)

    def test_item_limit_is_enforced(self) -> None:
        store = self.make_store(maximum=1, quota=2, max_items=2)
        store.create_pending(metadata(0))
        store.create_pending(metadata(0))
        with self.assertRaisesRegex(FileCapacityError, "数量"):
            store.create_pending(metadata(0))

    def test_upload_is_chunked_hashed_and_atomically_ready(self) -> None:
        store = self.make_store()
        content = b"x" * (TRANSFER_CHUNK_BYTES * 2 + 123)
        item = metadata(len(content))
        store.create_pending(item)
        reader = GuardedReader(content)

        revision, ready = store.upload(item.file_id, reader, len(content))

        self.assertEqual(ready.status, "ready")
        self.assertEqual(ready.sha256, hashlib.sha256(content).hexdigest())
        self.assertEqual(revision, 2)
        self.assertTrue(reader.requests)
        self.assertLessEqual(max(reader.requests), TRANSFER_CHUNK_BYTES)
        self.assertFalse((self.directory / f"{item.file_id}.part").exists())
        self.assertEqual(
            (self.directory / f"{item.file_id}.blob").read_bytes(), content
        )
        index = json.loads((self.directory / "index.json").read_text(encoding="utf-8"))
        self.assertEqual(index["files"][0]["status"], "ready")

    def test_upload_failure_removes_part_pending_and_quota(self) -> None:
        store = self.make_store(maximum=10, quota=10)
        item = metadata(8)
        store.create_pending(item)
        with self.assertRaisesRegex(FileSizeMismatchError, "短于"):
            store.upload(item.file_id, GuardedReader(b"short"), 8)

        revision, records = store.list_records()
        self.assertEqual(records, [])
        self.assertEqual(store.used_bytes, 0)
        self.assertEqual(revision, 2)
        self.assertFalse(any(self.directory.glob("*.part")))
        self.assertFalse(any(self.directory.glob("*.blob")))

    def test_content_length_mismatch_releases_pending(self) -> None:
        store = self.make_store(maximum=10, quota=10)
        item = metadata(8)
        store.create_pending(item)
        with self.assertRaisesRegex(FileSizeMismatchError, "Content-Length"):
            store.upload(item.file_id, io.BytesIO(b"1234567"), 7)
        self.assertEqual(store.list_records()[1], [])

    def test_restart_cleans_pending_parts_and_orphan_blobs(self) -> None:
        store = self.make_store(maximum=20, quota=40)
        ready_meta = metadata(5, created_at=1)
        pending_meta = metadata(3, created_at=2)
        store.create_pending(ready_meta)
        store.upload(ready_meta.file_id, io.BytesIO(b"ready"), 5)
        store.create_pending(pending_meta)
        (self.directory / f"{pending_meta.file_id}.part").write_bytes(b"partial")
        orphan = self.directory / f"{uuid.uuid4()}.blob"
        orphan.write_bytes(b"orphan")

        recovered = self.make_store(maximum=20, quota=40)

        _revision, records = recovered.list_records()
        self.assertEqual([record.file_id for record in records], [ready_meta.file_id])
        self.assertFalse((self.directory / f"{pending_meta.file_id}.part").exists())
        self.assertFalse(orphan.exists())
        self.assertTrue((self.directory / f"{ready_meta.file_id}.blob").exists())

    def test_delete_cannot_race_active_download(self) -> None:
        store = self.make_store(maximum=10, quota=10)
        item = metadata(4)
        store.create_pending(item)
        store.upload(item.file_id, io.BytesIO(b"data"), 4)

        with store.open_download(item.file_id) as (_record, stream):
            self.assertEqual(stream.read(1), b"d")
            with self.assertRaisesRegex(FileBusyError, "传输"):
                store.delete(item.file_id)

        store.delete(item.file_id)
        self.assertFalse((self.directory / f"{item.file_id}.blob").exists())

    def test_stale_pending_record_releases_quota_without_restart(self) -> None:
        now = [100.0]
        store = FileStore(
            self.directory,
            file_max_bytes=10,
            storage_limit_bytes=10,
            pending_ttl_seconds=5,
            clock=lambda: now[0],
        )
        pending = metadata(10)
        store.create_pending(pending)
        self.assertEqual(store.used_bytes, 10)

        now[0] += 6
        revision, records = store.list_records()

        self.assertGreaterEqual(revision, 2)
        self.assertEqual(records, [])
        self.assertEqual(store.used_bytes, 0)
        store.create_pending(metadata(10))

    def test_delete_cleanup_blocks_same_uuid_reuse(self) -> None:
        store = self.make_store(maximum=10, quota=20)
        file_id = str(uuid.uuid4())
        first = metadata(4, file_id=file_id)
        store.create_pending(first)
        store.upload(file_id, io.BytesIO(b"old!"), 4)
        blob = self.directory / f"{file_id}.blob"
        unlink_started = threading.Event()
        allow_unlink = threading.Event()
        reuse_finished = threading.Event()
        failures: list[BaseException] = []
        original_unlink = Path.unlink

        def delayed_unlink(path: Path, *args: object, **kwargs: object) -> None:
            if path == blob:
                unlink_started.set()
                if not allow_unlink.wait(3):
                    raise RuntimeError("delete cleanup test timed out")
            original_unlink(path, *args, **kwargs)

        def delete_old() -> None:
            try:
                store.delete(file_id)
            except BaseException as exc:
                failures.append(exc)

        def reuse_id() -> None:
            try:
                replacement = metadata(3, file_id=file_id, created_at=2)
                store.create_pending(replacement)
                store.upload(file_id, io.BytesIO(b"new"), 3)
            except BaseException as exc:
                failures.append(exc)
            finally:
                reuse_finished.set()

        with mock.patch.object(Path, "unlink", delayed_unlink):
            deleting = threading.Thread(target=delete_old)
            deleting.start()
            self.assertTrue(unlink_started.wait(2))
            reusing = threading.Thread(target=reuse_id)
            reusing.start()
            self.assertFalse(reuse_finished.wait(0.1))
            allow_unlink.set()
            deleting.join(timeout=3)
            reusing.join(timeout=3)

        self.assertFalse(deleting.is_alive())
        self.assertFalse(reusing.is_alive())
        self.assertEqual(failures, [])
        with store.open_download(file_id) as (_record, stream):
            self.assertEqual(stream.read(), b"new")


if __name__ == "__main__":
    unittest.main()
