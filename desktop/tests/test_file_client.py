from __future__ import annotations

import hashlib
import json
import sys
import tempfile
import threading
import unittest
import uuid
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path


SOURCE = Path(__file__).resolve().parents[1] / "src"
sys.path.insert(0, str(SOURCE))

from flowclip.client import ConnectionFailure  # noqa: E402
from flowclip.file_client import (  # noqa: E402
    FileIntegrityError,
    FileTransferCancelled,
    FileTransferClient,
)
from flowclip.file_lock import ExclusiveFileLock  # noqa: E402
from flowclip.file_store import TRANSFER_CHUNK_BYTES, FileStore  # noqa: E402
from flowclip.server import ClipboardServer  # noqa: E402


TOKEN = "client-test-token-long-enough"


class RecordingReader:
    def __init__(self, stream: object, requests: list[int]) -> None:
        self.stream = stream
        self.requests = requests

    def read(self, size: int = -1) -> bytes:
        self.requests.append(size)
        if size < 0 or size > TRANSFER_CHUNK_BYTES:
            raise AssertionError(f"unbounded file read: {size}")
        return self.stream.read(size)  # type: ignore[attr-defined,no-any-return]

    def __enter__(self) -> "RecordingReader":
        return self

    def __exit__(self, *_args: object) -> None:
        self.stream.close()  # type: ignore[attr-defined]


class FailingUploadConnection:
    def putrequest(self, *_args: object, **_kwargs: object) -> None:
        return

    def putheader(self, *_args: object) -> None:
        return

    def endheaders(self) -> None:
        raise OSError("connection lost")

    def close(self) -> None:
        return


class FullResponseServer:
    def __init__(self, content: bytes, *, redirect_list: bool = False) -> None:
        self.content = content
        self.file_id = str(uuid.uuid4())
        self.sha256 = hashlib.sha256(content).hexdigest()
        self.redirect_list = redirect_list
        outer = self

        class Handler(BaseHTTPRequestHandler):
            protocol_version = "HTTP/1.1"

            def log_message(self, _format: str, *_args: object) -> None:
                return

            def send_body(
                self,
                status: int,
                body: bytes,
                content_type: str,
                **headers: str,
            ) -> None:
                self.send_response(status)
                self.send_header("Content-Type", content_type)
                self.send_header("Content-Length", str(len(body)))
                self.send_header("Connection", "close")
                for name, value in headers.items():
                    self.send_header(name.replace("_", "-"), value)
                self.end_headers()
                self.wfile.write(body)

            def do_GET(self) -> None:  # noqa: N802
                if self.headers.get("Authorization") != f"Bearer {TOKEN}":
                    self.send_body(401, b'{"error":"auth"}', "application/json")
                    return
                if self.path == "/api/v1/files" and outer.redirect_list:
                    self.send_body(
                        302,
                        b"",
                        "text/plain",
                        Location="http://127.0.0.1:1/collect",
                    )
                    return
                if self.path == "/api/v1/files":
                    record = {
                        "id": outer.file_id,
                        "origin": "fallback",
                        "filename": "回退.bin",
                        "mime": "application/octet-stream",
                        "size": len(outer.content),
                        "createdAt": 1,
                        "status": "ready",
                        "sha256": outer.sha256,
                    }
                    body = json.dumps({"revision": 1, "files": [record]}).encode(
                        "utf-8"
                    )
                    self.send_body(200, body, "application/json")
                    return
                if self.path == f"/api/v1/files/{outer.file_id}":
                    self.send_body(
                        200,
                        outer.content,
                        "application/octet-stream",
                        ETag=f'"{outer.sha256}"',
                    )
                    return
                self.send_body(404, b'{"error":"missing"}', "application/json")

        self.server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)

    @property
    def port(self) -> int:
        return int(self.server.server_address[1])

    def start(self) -> None:
        self.thread.start()

    def stop(self) -> None:
        self.server.shutdown()
        self.server.server_close()
        self.thread.join(timeout=3)


class FileClientIntegrationTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name)
        store = FileStore(
            self.root / "store",
            file_max_bytes=4 * TRANSFER_CHUNK_BYTES,
            storage_limit_bytes=8 * TRANSFER_CHUNK_BYTES,
        )
        self.server = ClipboardServer(
            "127.0.0.1",
            0,
            TOKEN,
            1024 * 1024,
            file_store=store,
        )
        self.server.start()

    def tearDown(self) -> None:
        self.server.stop()
        self.temporary.cleanup()

    def client(self, **options: object) -> FileTransferClient:
        return FileTransferClient(
            f"http://127.0.0.1:{self.server.port}", TOKEN, **options
        )

    def test_upload_download_hash_progress_chunking_and_conflict_name(self) -> None:
        source = self.root / "中文.bin"
        content = bytes(range(251)) * (TRANSFER_CHUNK_BYTES // 251 * 2 + 5)
        source.write_bytes(content)
        read_requests: list[int] = []

        def opener(path: Path, mode: str):  # type: ignore[no-untyped-def]
            stream = open(path, mode)
            if Path(path) == source and mode == "rb":
                return RecordingReader(stream, read_requests)
            return stream

        upload_progress: list[tuple[int, int]] = []
        client = self.client(file_opener=opener)
        ready = client.upload_file(
            source,
            origin="desktop",
            progress=lambda completed, total: upload_progress.append((completed, total)),
        )

        self.assertEqual(ready.sha256, hashlib.sha256(content).hexdigest())
        self.assertTrue(read_requests)
        self.assertLessEqual(max(read_requests), TRANSFER_CHUNK_BYTES)
        self.assertEqual(upload_progress[-1], (len(content), len(content)))
        self.assertEqual(client.list_files()[0], ready)

        downloads = self.root / "downloads"
        downloads.mkdir()
        (downloads / source.name).write_bytes(b"existing")
        download_progress: list[tuple[int, int]] = []
        destination = client.download_file(
            ready.file_id,
            downloads,
            progress=lambda completed, total: download_progress.append(
                (completed, total)
            ),
        )

        self.assertEqual(destination.name, "中文 (1).bin")
        self.assertEqual(destination.read_bytes(), content)
        self.assertEqual((downloads / source.name).read_bytes(), b"existing")
        self.assertEqual(download_progress[-1], (len(content), len(content)))
        self.assertFalse((downloads / f".flowclip-{ready.file_id}.part").exists())

    def test_upload_cancellation_aborts_pending_record(self) -> None:
        source = self.root / "cancel.bin"
        source.write_bytes(b"x" * (TRANSFER_CHUNK_BYTES * 2 + 1))
        checks = 0

        def cancelled() -> bool:
            nonlocal checks
            checks += 1
            return checks >= 3

        with self.assertRaisesRegex(FileTransferCancelled, "取消"):
            self.client().upload_file(
                source, origin="desktop", cancelled=cancelled
            )
        self.assertEqual(self.client().list_files(), [])

    def test_download_cancellation_removes_local_part(self) -> None:
        source = self.root / "cancel-download.bin"
        source.write_bytes(b"z" * (TRANSFER_CHUNK_BYTES * 2 + 1))
        client = self.client()
        ready = client.upload_file(source, origin="desktop")
        downloads = self.root / "cancelled"
        checks = 0

        def cancelled() -> bool:
            nonlocal checks
            checks += 1
            return checks >= 3

        with self.assertRaisesRegex(FileTransferCancelled, "取消"):
            client.download_file(
                ready.file_id, downloads, cancelled=cancelled
            )
        self.assertFalse((downloads / f".flowclip-{ready.file_id}.part").exists())


class FileClientCompatibilityTests(unittest.TestCase):
    def test_download_lock_rejects_same_file_in_another_task(self) -> None:
        content = b"locked"
        server = FullResponseServer(content)
        server.start()
        try:
            with tempfile.TemporaryDirectory() as directory:
                target = Path(directory)
                lock = ExclusiveFileLock(
                    target / ".flowclip-locks" / f"{server.file_id}.lock"
                )
                lock.acquire()
                try:
                    with self.assertRaisesRegex(ConnectionFailure, "另一个下载任务"):
                        FileTransferClient(
                            f"http://127.0.0.1:{server.port}", TOKEN
                        ).download_file(server.file_id, target)
                finally:
                    lock.release()
        finally:
            server.stop()

    def test_atomic_destination_reservation_keeps_both_files(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            target = Path(directory)
            first = target / "first.part"
            second = target / "second.part"
            first.write_bytes(b"first")
            second.write_bytes(b"second")
            barrier = threading.Barrier(2)
            destinations: list[Path] = []

            def commit(part: Path) -> None:
                barrier.wait()
                destinations.append(
                    FileTransferClient._commit_part(part, target, "same.bin")
                )

            threads = [
                threading.Thread(target=commit, args=(first,)),
                threading.Thread(target=commit, args=(second,)),
            ]
            for thread in threads:
                thread.start()
            for thread in threads:
                thread.join(timeout=3)

            self.assertTrue(all(not thread.is_alive() for thread in threads))
            self.assertEqual({path.name for path in destinations}, {"same.bin", "same (1).bin"})
            self.assertEqual({path.read_bytes() for path in destinations}, {b"first", b"second"})

    def test_failed_put_deletes_remote_pending_record(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            source = Path(directory) / "failed.bin"
            source.write_bytes(b"payload")
            file_id = str(uuid.uuid4())

            class Client(FileTransferClient):
                def __init__(self) -> None:
                    super().__init__("http://127.0.0.1:1", TOKEN)
                    self.deleted = False

                def _connection(self):  # type: ignore[no-untyped-def]
                    return FailingUploadConnection()

                def _request_json(  # type: ignore[no-untyped-def]
                    self, method, path, **_options
                ):
                    if method == "POST":
                        return {
                            "file": {
                                "id": file_id,
                                "origin": "desktop",
                                "filename": source.name,
                                "mime": "application/octet-stream",
                                "size": source.stat().st_size,
                                "createdAt": 1,
                                "status": "pending",
                            }
                        }
                    if method == "DELETE":
                        self.deleted = True
                        return {"revision": 2}
                    raise AssertionError((method, path))

            client = Client()
            with self.assertRaisesRegex(ConnectionFailure, "上传文件失败"):
                client.upload_file(
                    source,
                    origin="desktop",
                    file_id=file_id,
                    created_at=1,
                )
            self.assertTrue(client.deleted)

    def test_resume_falls_back_to_full_200_response(self) -> None:
        content = b"fallback-content" * 1000
        server = FullResponseServer(content)
        server.start()
        try:
            with tempfile.TemporaryDirectory() as directory:
                target = Path(directory)
                part = target / f".flowclip-{server.file_id}.part"
                part.write_bytes(content[:123])
                progress: list[tuple[int, int]] = []
                client = FileTransferClient(
                    f"http://127.0.0.1:{server.port}", TOKEN
                )
                destination = client.download_file(
                    server.file_id,
                    target,
                    progress=lambda completed, total: progress.append(
                        (completed, total)
                    ),
                )
                self.assertEqual(destination.read_bytes(), content)
                self.assertFalse(part.exists())
                self.assertIn((0, len(content)), progress)
                self.assertEqual(progress[-1], (len(content), len(content)))
        finally:
            server.stop()

    def test_http_redirect_is_explicitly_rejected(self) -> None:
        server = FullResponseServer(b"data", redirect_list=True)
        server.start()
        try:
            client = FileTransferClient(
                f"http://127.0.0.1:{server.port}", TOKEN
            )
            with self.assertRaisesRegex(ConnectionFailure, "重定向"):
                client.list_files()
        finally:
            server.stop()

    def test_checksum_failure_deletes_part_file(self) -> None:
        content = b"checksum"
        server = FullResponseServer(content)
        server.sha256 = "0" * 64
        server.start()
        try:
            with tempfile.TemporaryDirectory() as directory:
                target = Path(directory)
                client = FileTransferClient(
                    f"http://127.0.0.1:{server.port}", TOKEN
                )
                with self.assertRaisesRegex(FileIntegrityError, "SHA-256"):
                    client.download_file(server.file_id, target)
                self.assertFalse(
                    (target / f".flowclip-{server.file_id}.part").exists()
                )
        finally:
            server.stop()


if __name__ == "__main__":
    unittest.main()
