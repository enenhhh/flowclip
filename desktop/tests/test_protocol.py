from __future__ import annotations

import json
import socket
import sys
import unittest
import urllib.error
import urllib.request
from email.message import Message
from io import BytesIO
from pathlib import Path


SOURCE = Path(__file__).resolve().parents[1] / "src"
sys.path.insert(0, str(SOURCE))

from flowclip.client import (  # noqa: E402
    ApiClient,
    ConnectionFailure,
    SameOriginRedirectHandler,
)
from flowclip.model import ClipItem, ProtocolError  # noqa: E402
from flowclip.server import (  # noqa: E402
    ClipboardServer,
    ClipboardStore,
    ReusableThreadingHTTPServer,
    make_handler,
)


TOKEN = "test-token-that-is-long-enough"


class ModelTests(unittest.TestCase):
    def test_text_round_trip(self) -> None:
        original = ClipItem.create(
            origin="windows-one",
            kind="text",
            mime="text/plain; charset=utf-8",
            data="FlowClip 中文".encode("utf-8"),
        )
        parsed = ClipItem.from_dict(original.to_dict(), 1024)
        self.assertEqual(parsed, original)
        self.assertEqual(parsed.checksum, original.checksum)

    def test_image_round_trip(self) -> None:
        data = b"\x89PNG\r\n\x1a\n" + bytes(range(64))
        original = ClipItem.create(
            origin="windows-one",
            kind="image",
            mime="image/png",
            filename="sample.png",
            data=data,
        )
        self.assertEqual(ClipItem.from_dict(original.to_dict(), 1024), original)

    def test_checksum_is_required(self) -> None:
        item = ClipItem.create(
            origin="phone", kind="text", mime="text/plain", data=b"before"
        )
        raw = item.to_dict()
        raw["data"] = "YWZ0ZXI="
        with self.assertRaisesRegex(ProtocolError, "校验"):
            ClipItem.from_dict(raw, 1024)

    def test_size_limit_is_enforced_after_base64_decode(self) -> None:
        item = ClipItem.create(
            origin="phone", kind="image", mime="image/png", data=b"a" * 65
        )
        with self.assertRaisesRegex(ProtocolError, "大小限制"):
            ClipItem.from_dict(item.to_dict(), 64)

    def test_non_ascii_mime_is_rejected(self) -> None:
        raw = ClipItem.create(
            origin="phone", kind="text", mime="text/plain", data=b"text"
        ).to_dict()
        raw["mime"] = "text/plain; name=\u4e2d"
        with self.assertRaisesRegex(ProtocolError, "ASCII"):
            ClipItem.from_dict(raw, 1024)

    def test_supported_mime_shapes_are_accepted_and_normalized(self) -> None:
        cases = (
            ("text", "TEXT/PLAIN; CHARSET=UTF-8", "text/plain; charset=utf-8"),
            ("image", "image/svg+xml", "image/svg+xml"),
            (
                "image",
                "image/vnd.microsoft.icon; profile=srgb; version=1",
                "image/vnd.microsoft.icon; profile=srgb; version=1",
            ),
        )
        for kind, mime, expected in cases:
            with self.subTest(mime=mime):
                raw = ClipItem.create(
                    origin="phone", kind=kind, mime=mime, data=b"content"
                ).to_dict()
                self.assertEqual(ClipItem.from_dict(raw, 1024).mime, expected)

    def test_malformed_mime_shapes_are_rejected(self) -> None:
        invalid = (
            "image/",
            "/png",
            "image//png",
            "image /png",
            "image/png; broken",
            'image/png; name="sample"',
            "image/png; name=sample=extra",
        )
        for mime in invalid:
            with self.subTest(mime=mime):
                raw = ClipItem.create(
                    origin="phone", kind="image", mime=mime, data=b"content"
                ).to_dict()
                with self.assertRaisesRegex(ProtocolError, "MIME"):
                    ClipItem.from_dict(raw, 1024)

    def test_filename_must_be_a_string_when_present(self) -> None:
        raw = ClipItem.create(
            origin="phone", kind="image", mime="image/png", data=b"content"
        ).to_dict()
        for filename in (123, None, ["sample.png"]):
            with self.subTest(filename=filename):
                raw["filename"] = filename
                with self.assertRaisesRegex(ProtocolError, "filename"):
                    ClipItem.from_dict(raw, 1024)

        del raw["filename"]
        self.assertEqual(ClipItem.from_dict(raw, 1024).filename, "")

    def test_base64_must_use_canonical_padding_and_pad_bits(self) -> None:
        raw = ClipItem.create(
            origin="phone", kind="text", mime="text/plain", data=b"a"
        ).to_dict()
        for encoded in ("YQ", "YR=="):
            with self.subTest(encoded=encoded):
                raw["data"] = encoded
                with self.assertRaisesRegex(ProtocolError, "Base64"):
                    ClipItem.from_dict(raw, 1024)

    def test_text_with_nul_is_rejected(self) -> None:
        item = ClipItem.create(
            origin="phone", kind="text", mime="text/plain", data=b"a\0b"
        )
        with self.assertRaisesRegex(ProtocolError, "NUL"):
            ClipItem.from_dict(item.to_dict(), 1024)


class ClientBoundaryTests(unittest.TestCase):
    class Response(BytesIO):
        def __init__(self, data: bytes, content_length: str | None = None) -> None:
            super().__init__(data)
            self.headers = Message()
            if content_length is not None:
                self.headers["Content-Length"] = content_length

    def test_response_body_limit_is_enforced(self) -> None:
        response = self.Response(b"x" * 11)
        with self.assertRaisesRegex(ConnectionFailure, "大小限制"):
            ApiClient._read_limited(response, 10)

    def test_non_ascii_token_reports_a_clear_error(self) -> None:
        client = ApiClient("http://127.0.0.1:8765", "中文密钥不能放进请求头1234", 1024)
        with self.assertRaisesRegex(ConnectionFailure, "ASCII"):
            client.fetch(0)

    def test_declared_response_size_limit_is_enforced_before_read(self) -> None:
        response = self.Response(b"", "11")
        with self.assertRaisesRegex(ConnectionFailure, "大小限制"):
            ApiClient._read_limited(response, 10)

    def test_cross_origin_redirect_is_rejected(self) -> None:
        request = urllib.request.Request(
            "http://127.0.0.1:8765/api/v1/clipboard",
            headers={"Authorization": f"Bearer {TOKEN}"},
        )
        with self.assertRaisesRegex(urllib.error.HTTPError, "其他服务器"):
            SameOriginRedirectHandler().redirect_request(
                request,
                self.Response(b""),
                302,
                "Found",
                Message(),
                "http://127.0.0.1:9999/collect",
            )

    def test_same_origin_redirect_remains_allowed(self) -> None:
        request = urllib.request.Request("https://clip.example/api")
        redirected = SameOriginRedirectHandler().redirect_request(
            request,
            self.Response(b""),
            302,
            "Found",
            Message(),
            "https://clip.example/api/v1/health",
        )
        self.assertIsNotNone(redirected)


class ServerTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.server = ClipboardServer("127.0.0.1", 0, TOKEN, 1024 * 1024)
        cls.server.start()
        cls.base_url = f"http://127.0.0.1:{cls.server.port}"

    @classmethod
    def tearDownClass(cls) -> None:
        cls.server.stop()

    def setUp(self) -> None:
        self.client = ApiClient(self.base_url, TOKEN, 1024 * 1024)

    def test_health_does_not_require_token(self) -> None:
        self.assertTrue(self.client.health()["ok"])

    def test_server_header_does_not_expose_runtime_versions(self) -> None:
        with urllib.request.urlopen(
            self.base_url + "/api/v1/health", timeout=3
        ) as response:
            self.assertEqual(response.headers.get("Server"), "FlowClip")

    def test_rejects_wrong_token(self) -> None:
        wrong = ApiClient(self.base_url, "incorrect-token-1234", 1024 * 1024)
        with self.assertRaisesRegex(ConnectionFailure, "认证失败"):
            wrong.fetch(0)

    def test_push_and_fetch_text(self) -> None:
        item = ClipItem.create(
            origin="phone-a",
            kind="text",
            mime="text/plain; charset=utf-8",
            data="跨端文本".encode("utf-8"),
        )
        revision = self.client.push(item)
        fetched_revision, fetched = self.client.fetch(0)
        self.assertEqual(fetched_revision, revision)
        self.assertEqual(fetched, item)
        unchanged_revision, unchanged = self.client.fetch(revision)
        self.assertEqual(unchanged_revision, revision)
        self.assertIsNone(unchanged)

    def test_latest_item_replaces_previous_item(self) -> None:
        first = ClipItem.create(
            origin="a", kind="text", mime="text/plain", data=b"first"
        )
        second = ClipItem.create(
            origin="b", kind="image", mime="image/png", filename="b.png", data=b"png"
        )
        self.client.push(first)
        expected_revision = self.client.push(second)
        revision, fetched = self.client.fetch(0)
        self.assertEqual(revision, expected_revision)
        self.assertEqual(fetched, second)

    def test_malformed_payload_is_rejected(self) -> None:
        item = ClipItem.create(
            origin="a", kind="text", mime="text/plain", data=b"original"
        ).to_dict()
        item["sha256"] = "0" * 64
        body = json.dumps(item).encode("utf-8")
        request = urllib.request.Request(
            self.base_url + "/api/v1/clipboard",
            data=body,
            headers={
                "Authorization": f"Bearer {TOKEN}",
                "Content-Type": "application/json",
            },
            method="POST",
        )
        with self.assertRaises(urllib.error.HTTPError) as raised:
            urllib.request.urlopen(request, timeout=3)
        self.assertEqual(raised.exception.code, 400)


class ServerBoundaryTests(unittest.TestCase):
    def test_connection_is_rejected_when_worker_limit_is_full(self) -> None:
        handler = make_handler(
            store=ClipboardStore(), token=TOKEN, max_bytes=1024
        )
        server = ReusableThreadingHTTPServer(
            ("127.0.0.1", 0), handler, max_workers=1
        )
        server._worker_slots.acquire()
        server_socket, client_socket = socket.socketpair()
        try:
            server.process_request(server_socket, ("127.0.0.1", 1))
            response = client_socket.recv(1024)
            self.assertIn(b"503 Service Unavailable", response)
        finally:
            server._worker_slots.release()
            client_socket.close()
            server.server_close()


if __name__ == "__main__":
    unittest.main()
