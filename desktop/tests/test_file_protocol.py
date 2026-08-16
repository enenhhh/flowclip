from __future__ import annotations

import http.client
import json
import socket
import sys
import tempfile
import time
import unittest
import uuid
from pathlib import Path


SOURCE = Path(__file__).resolve().parents[1] / "src"
sys.path.insert(0, str(SOURCE))

from flowclip.client import ApiClient  # noqa: E402
from flowclip.file_store import FileStore  # noqa: E402
from flowclip.file_store import FileStoreError  # noqa: E402
from flowclip.model import ClipItem  # noqa: E402
from flowclip.server import ClipboardServer  # noqa: E402


TOKEN = "file-test-token-long-enough"


def metadata(size: int, **changes: object) -> dict[str, object]:
    raw: dict[str, object] = {
        "id": str(uuid.uuid4()),
        "origin": "电脑一",
        "filename": "中文 文件.bin",
        "mime": "application/octet-stream",
        "size": size,
        "createdAt": 123456,
    }
    raw.update(changes)
    return raw


class FileProtocolTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.file_store = FileStore(
            Path(self.temporary.name) / "store",
            file_max_bytes=1024,
            storage_limit_bytes=1500,
            max_items=4,
        )
        self.server = ClipboardServer(
            "127.0.0.1",
            0,
            TOKEN,
            1024 * 1024,
            file_store=self.file_store,
        )
        self.server.start()

    def tearDown(self) -> None:
        self.server.stop()
        self.temporary.cleanup()

    def request(
        self,
        method: str,
        path: str,
        body: bytes | None = None,
        *,
        token: str | None = TOKEN,
        headers: dict[str, str] | None = None,
    ) -> tuple[int, bytes, dict[str, str]]:
        request_headers = dict(headers or {})
        if token is not None:
            request_headers["Authorization"] = f"Bearer {token}"
        connection = http.client.HTTPConnection(
            "127.0.0.1", self.server.port, timeout=3
        )
        try:
            connection.request(method, path, body=body, headers=request_headers)
            response = connection.getresponse()
            try:
                return response.status, response.read(), dict(response.headers.items())
            finally:
                response.close()
        finally:
            connection.close()

    def post_metadata(self, raw: dict[str, object]) -> tuple[int, dict[str, object]]:
        body = json.dumps(raw, ensure_ascii=False).encode("utf-8")
        status, response, _headers = self.request(
            "POST",
            "/api/v1/files",
            body,
            headers={"Content-Type": "application/json"},
        )
        return status, json.loads(response.decode("utf-8"))

    def test_health_advertises_files_without_authentication(self) -> None:
        status, body, _headers = self.request(
            "GET", "/api/v1/health", token=None
        )
        payload = json.loads(body.decode("utf-8"))
        self.assertEqual(status, 200)
        self.assertEqual(payload["version"], 1)
        self.assertIn("clipboard-v1", payload["capabilities"])
        self.assertIn("files-v1", payload["capabilities"])
        self.assertEqual(payload["fileMaxBytes"], 1024)

    def test_every_file_endpoint_requires_bearer_token(self) -> None:
        file_id = str(uuid.uuid4())
        cases = (
            ("GET", "/api/v1/files", None, {}),
            (
                "POST",
                "/api/v1/files",
                json.dumps(metadata(1, id=file_id)).encode("utf-8"),
                {"Content-Type": "application/json"},
            ),
            (
                "PUT",
                f"/api/v1/files/{file_id}",
                b"x",
                {"Content-Length": "1"},
            ),
            ("GET", f"/api/v1/files/{file_id}", None, {}),
            ("DELETE", f"/api/v1/files/{file_id}", None, {}),
        )
        for method, path, body, headers in cases:
            with self.subTest(method=method, path=path):
                status, _body, _response_headers = self.request(
                    method, path, body, token=None, headers=headers
                )
                self.assertEqual(status, 401)

    def test_unauthorized_body_is_not_parsed_as_another_request(self) -> None:
        connection = socket.create_connection(
            ("127.0.0.1", self.server.port), timeout=3
        )
        try:
            body = b"GET /api/v1/health HTTP/1.1\r\nHost: localhost\r\n\r\n"
            request = (
                "POST /api/v1/files HTTP/1.1\r\n"
                "Host: localhost\r\n"
                "Content-Type: application/json\r\n"
                f"Content-Length: {len(body)}\r\n"
                "Connection: keep-alive\r\n\r\n"
            ).encode("ascii")
            connection.sendall(request + body)
            received = bytearray()
            while True:
                chunk = connection.recv(4096)
                if not chunk:
                    break
                received.extend(chunk)
        finally:
            connection.close()
        self.assertEqual(received.count(b"HTTP/1.1"), 1)
        self.assertIn(b"401 Unauthorized", received)
        self.assertIn(b"Connection: close", received)
        self.assertIn(b"WWW-Authenticate: Bearer", received)

    def test_repository_directory_has_one_active_server(self) -> None:
        competing_store = FileStore(
            Path(self.temporary.name) / "store",
            file_max_bytes=1024,
            storage_limit_bytes=1500,
            max_items=4,
        )
        with self.assertRaisesRegex(FileStoreError, "另一个服务器"):
            ClipboardServer(
                "127.0.0.1",
                0,
                TOKEN,
                1024 * 1024,
                file_store=competing_store,
            )

    def test_create_upload_list_range_download_and_delete(self) -> None:
        content = b"0123456789"
        raw = metadata(len(content), filename="中文图片.png", mime="image/png")
        status, created = self.post_metadata(raw)
        self.assertEqual(status, 201)
        self.assertEqual(created["file"]["status"], "pending")
        create_revision = created["revision"]

        status, uploaded_body, _headers = self.request(
            "PUT",
            f"/api/v1/files/{raw['id']}",
            content,
            headers={"Content-Length": str(len(content))},
        )
        uploaded = json.loads(uploaded_body.decode("utf-8"))
        self.assertEqual(status, 200)
        self.assertEqual(uploaded["file"]["status"], "ready")
        self.assertGreater(uploaded["revision"], create_revision)

        status, list_body, _headers = self.request("GET", "/api/v1/files")
        listing = json.loads(list_body.decode("utf-8"))
        self.assertEqual(status, 200)
        self.assertEqual(listing["files"][0]["filename"], "中文图片.png")
        self.assertEqual(listing["files"][0]["sha256"], uploaded["file"]["sha256"])

        status, ranged, headers = self.request(
            "GET",
            f"/api/v1/files/{raw['id']}",
            headers={"Range": "bytes=3-6"},
        )
        self.assertEqual(status, 206)
        self.assertEqual(ranged, b"3456")
        self.assertEqual(headers["Content-Range"], "bytes 3-6/10")
        self.assertEqual(headers["ETag"], f'"{uploaded["file"]["sha256"]}"')
        self.assertIn("filename*=UTF-8''", headers["Content-Disposition"])

        status, full, _headers = self.request(
            "GET",
            f"/api/v1/files/{raw['id']}",
            headers={"Range": "bytes=3-", "If-Range": '"wrong"'},
        )
        self.assertEqual(status, 200)
        self.assertEqual(full, content)

        status, deleted_body, _headers = self.request(
            "DELETE", f"/api/v1/files/{raw['id']}"
        )
        self.assertEqual(status, 200)
        self.assertGreater(
            json.loads(deleted_body.decode("utf-8"))["revision"],
            uploaded["revision"],
        )
        self.assertEqual(
            self.request("GET", f"/api/v1/files/{raw['id']}")[0], 404
        )

    def test_invalid_range_is_ignored_and_unsatisfied_range_is_416(self) -> None:
        content = b"range"
        raw = metadata(len(content))
        self.assertEqual(self.post_metadata(raw)[0], 201)
        self.assertEqual(
            self.request(
                "PUT",
                f"/api/v1/files/{raw['id']}",
                content,
                headers={"Content-Length": str(len(content))},
            )[0],
            200,
        )

        status, body, _headers = self.request(
            "GET",
            f"/api/v1/files/{raw['id']}",
            headers={"Range": f"bytes={'9' * 5000}-"},
        )
        self.assertEqual(status, 200)
        self.assertEqual(body, content)
        status, _body, headers = self.request(
            "GET",
            f"/api/v1/files/{raw['id']}",
            headers={"Range": "bytes=99-"},
        )
        self.assertEqual(status, 416)
        self.assertEqual(headers["Content-Range"], f"bytes */{len(content)}")

    def test_oversized_json_integer_returns_bad_request(self) -> None:
        raw = metadata(0)
        encoded = (
            "{"
            f'"id":"{raw["id"]}",'
            '"origin":"desktop",'
            '"filename":"number.bin",'
            '"mime":"application/octet-stream",'
            '"size":0,'
            f'"createdAt":{"9" * 5000}'
            "}"
        ).encode("ascii")
        status, _body, _headers = self.request(
            "POST",
            "/api/v1/files",
            encoded,
            headers={"Content-Type": "application/json"},
        )
        self.assertEqual(status, 400)

    def test_transfer_encoding_is_rejected(self) -> None:
        raw = metadata(1)
        body = json.dumps(raw).encode("utf-8")
        status, _body, _headers = self.request(
            "POST",
            "/api/v1/files",
            body,
            headers={
                "Content-Type": "application/json",
                "Content-Length": str(len(body)),
                "Transfer-Encoding": "identity",
            },
        )
        self.assertEqual(status, 400)

    def test_list_hides_pending_uploads(self) -> None:
        raw = metadata(10)
        self.assertEqual(self.post_metadata(raw)[0], 201)

        status, body, _headers = self.request("GET", "/api/v1/files")

        self.assertEqual(status, 200)
        self.assertEqual(json.loads(body.decode("utf-8"))["files"], [])

    def test_stop_waits_for_partial_upload_before_store_is_reused(self) -> None:
        raw = metadata(1024)
        self.assertEqual(self.post_metadata(raw)[0], 201)
        port = self.server.port
        self.server._server._request_timeout = 0.2
        connection = socket.create_connection(("127.0.0.1", port), timeout=3)
        try:
            request = (
                f"PUT /api/v1/files/{raw['id']} HTTP/1.1\r\n"
                f"Host: 127.0.0.1:{port}\r\n"
                f"Authorization: Bearer {TOKEN}\r\n"
                "Content-Type: application/octet-stream\r\n"
                "Content-Length: 1024\r\n"
                "Connection: close\r\n\r\n"
            ).encode("ascii")
            connection.sendall(request + b"x")
            part = Path(self.temporary.name) / "store" / f"{raw['id']}.part"
            deadline = time.monotonic() + 3
            while not part.exists() and time.monotonic() < deadline:
                time.sleep(0.01)
            self.assertTrue(part.exists(), "partial upload did not start")

            self.server.stop()

            self.assertEqual(self.file_store.list_records()[1], [])
            self.assertFalse(part.exists())
            replacement_store = FileStore(
                Path(self.temporary.name) / "store",
                file_max_bytes=1024,
                storage_limit_bytes=1500,
                max_items=4,
            )
            self.server = ClipboardServer(
                "127.0.0.1",
                port,
                TOKEN,
                1024 * 1024,
                file_store=replacement_store,
            )
            self.file_store = replacement_store
            self.server.start()
            replacement = metadata(1)
            self.assertEqual(self.post_metadata(replacement)[0], 201)
        finally:
            connection.close()

    def test_busy_transfer_slot_aborts_new_pending_upload(self) -> None:
        self.server.stop()
        self.file_store = FileStore(
            Path(self.temporary.name) / "busy-store",
            file_max_bytes=1024,
            storage_limit_bytes=2048,
            max_items=4,
        )
        self.server = ClipboardServer(
            "127.0.0.1",
            0,
            TOKEN,
            1024 * 1024,
            file_store=self.file_store,
            transfer_workers=1,
        )
        self.server.start()

        first = metadata(1024, filename="first.bin")
        second = metadata(1, filename="second.bin")
        self.assertEqual(self.post_metadata(first)[0], 201)
        slow = socket.create_connection(("127.0.0.1", self.server.port), timeout=3)
        try:
            request = (
                f"PUT /api/v1/files/{first['id']} HTTP/1.1\r\n"
                f"Host: 127.0.0.1:{self.server.port}\r\n"
                f"Authorization: Bearer {TOKEN}\r\n"
                "Content-Type: application/octet-stream\r\n"
                "Content-Length: 1024\r\n"
                "Connection: close\r\n\r\n"
            ).encode("ascii")
            slow.sendall(request + b"x")
            part = (
                Path(self.temporary.name)
                / "busy-store"
                / f"{first['id']}.part"
            )
            deadline = time.monotonic() + 3
            while not part.exists() and time.monotonic() < deadline:
                time.sleep(0.01)
            self.assertTrue(part.exists(), "first upload did not occupy a slot")

            self.assertEqual(self.post_metadata(second)[0], 201)
            status, _body, _headers = self.request(
                "PUT",
                f"/api/v1/files/{second['id']}",
                b"y",
                headers={"Content-Length": "1"},
            )
            self.assertEqual(status, 503)
            _revision, records = self.file_store.list_records()
            self.assertNotIn(second["id"], {record.file_id for record in records})
        finally:
            slow.close()

    def test_metadata_body_accepts_exactly_16_kib_and_rejects_more(self) -> None:
        raw = metadata(0)
        encoded = json.dumps(raw, ensure_ascii=False).encode("utf-8")
        exact = encoded + b" " * (16 * 1024 - len(encoded))
        status, body, _headers = self.request(
            "POST",
            "/api/v1/files",
            exact,
            headers={"Content-Type": "application/json"},
        )
        self.assertEqual(status, 201, body)
        self.request("DELETE", f"/api/v1/files/{raw['id']}")

        too_large = encoded + b" " * (16 * 1024 + 1 - len(encoded))
        status, _body, _headers = self.request(
            "POST",
            "/api/v1/files",
            too_large,
            headers={"Content-Type": "application/json"},
        )
        self.assertEqual(status, 413)

    def test_content_length_mismatch_removes_pending_record(self) -> None:
        raw = metadata(4)
        self.assertEqual(self.post_metadata(raw)[0], 201)
        status, _body, _headers = self.request(
            "PUT",
            f"/api/v1/files/{raw['id']}",
            b"abc",
            headers={"Content-Length": "3"},
        )
        self.assertEqual(status, 400)
        listing = json.loads(self.request("GET", "/api/v1/files")[1].decode("utf-8"))
        self.assertEqual(listing["files"], [])

    def test_single_file_and_total_quota_limits_have_distinct_statuses(self) -> None:
        self.assertEqual(self.post_metadata(metadata(1025))[0], 413)
        self.assertEqual(self.post_metadata(metadata(1000))[0], 201)
        self.assertEqual(self.post_metadata(metadata(600))[0], 507)

    def test_clipboard_protocol_still_round_trips(self) -> None:
        client = ApiClient(
            f"http://127.0.0.1:{self.server.port}", TOKEN, 1024 * 1024
        )
        item = ClipItem.create(
            origin="phone",
            kind="text",
            mime="text/plain; charset=utf-8",
            data="文件协议后的剪贴板".encode("utf-8"),
        )
        revision = client.push(item)
        self.assertEqual(client.fetch(0), (revision, item))


if __name__ == "__main__":
    unittest.main()
