from __future__ import annotations

import hmac
import json
import re
import socket
import threading
import time
import uuid
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Callable
from urllib.parse import parse_qs, quote, urlsplit

from .file_model import DEFAULT_FILE_MAX_BYTES, FileMetadata, FileProtocolError
from .file_store import (
    DEFAULT_MAX_FILES,
    DEFAULT_STORAGE_LIMIT_BYTES,
    TRANSFER_CHUNK_BYTES,
    FileBusyError,
    FileCapacityError,
    FileConflictError,
    FileLimitError,
    FileNotFoundError as StoreFileNotFoundError,
    FileNotReadyError,
    FileSizeMismatchError,
    FileStore,
    FileStoreError,
)
from .model import ClipItem, ProtocolError


FILE_METADATA_MAX_BYTES = 16 * 1024
DEFAULT_TRANSFER_WORKERS = 4
DEFAULT_MAX_TRANSFER_DURATION = 6 * 60 * 60
_RANGE_PATTERN = re.compile(r"bytes=(\d*)-(\d*)$")
_RANGE_UNSATISFIABLE = object()


class _TransferReader:
    def __init__(self, source: object, deadline: float) -> None:
        self._source = source
        self._deadline = deadline

    def read(self, size: int = -1) -> bytes:
        if time.monotonic() >= self._deadline:
            raise TimeoutError("文件传输超过最长时限")
        read1 = getattr(self._source, "read1", None)
        if callable(read1):
            return read1(size)
        read = getattr(self._source, "read")
        return read(size)


class ClipboardStore:
    def __init__(self) -> None:
        self.server_id = str(uuid.uuid4())
        self._lock = threading.Lock()
        self._revision = 0
        self._item: ClipItem | None = None

    def put(self, item: ClipItem) -> int:
        with self._lock:
            if self._item is not None and self._item.item_id == item.item_id:
                return self._revision
            self._revision += 1
            self._item = item
            return self._revision

    def latest_if_changed(self, revision: int) -> tuple[int, ClipItem | None]:
        with self._lock:
            if self._item is None or revision == self._revision:
                return self._revision, None
            return self._revision, self._item


class ReusableThreadingHTTPServer(ThreadingHTTPServer):
    allow_reuse_address = True
    daemon_threads = True

    def __init__(
        self,
        server_address: tuple[str, int],
        handler: type[BaseHTTPRequestHandler],
        *,
        max_workers: int = 32,
        request_timeout: float = 10.0,
        request_deadline: float = 30.0,
        max_transfer_duration: float = DEFAULT_MAX_TRANSFER_DURATION,
    ) -> None:
        self._worker_slots = threading.BoundedSemaphore(max_workers)
        self._request_timeout = request_timeout
        self._request_deadline = request_deadline
        if max_transfer_duration <= 0:
            raise ValueError("max_transfer_duration 必须大于零")
        self._max_transfer_duration = max_transfer_duration
        self._long_request_lock = threading.Lock()
        self._long_requests: set[int] = set()
        self._active_condition = threading.Condition()
        self._active_requests: set[socket.socket] = set()
        super().__init__(server_address, handler)

    def mark_long_request(self, request: socket.socket, active: bool) -> None:
        with self._long_request_lock:
            if active:
                self._long_requests.add(id(request))
            else:
                self._long_requests.discard(id(request))

    def _is_long_request(self, request: socket.socket) -> bool:
        with self._long_request_lock:
            return id(request) in self._long_requests

    def get_request(self) -> tuple[socket.socket, tuple[str, int]]:
        request, address = super().get_request()
        request.settimeout(self._request_timeout)
        return request, address

    def process_request(
        self, request: socket.socket, client_address: tuple[str, int]
    ) -> None:
        if not self._worker_slots.acquire(blocking=False):
            try:
                request.sendall(
                    b"HTTP/1.1 503 Service Unavailable\r\n"
                    b"Content-Length: 0\r\nConnection: close\r\n\r\n"
                )
            except OSError:
                pass
            self.shutdown_request(request)
            return
        with self._active_condition:
            self._active_requests.add(request)
        try:
            super().process_request(request, client_address)
        except Exception:
            with self._active_condition:
                self._active_requests.discard(request)
                self._active_condition.notify_all()
            self._worker_slots.release()
            raise

    def close_active_requests(self, timeout: float = 12.0) -> bool:
        with self._active_condition:
            requests = tuple(self._active_requests)
        for request in requests:
            try:
                request.shutdown(socket.SHUT_RDWR)
            except OSError:
                pass
            try:
                request.close()
            except OSError:
                pass

        deadline = time.monotonic() + timeout
        with self._active_condition:
            while self._active_requests:
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    return False
                self._active_condition.wait(remaining)
        return True

    def process_request_thread(
        self, request: socket.socket, client_address: tuple[str, int]
    ) -> None:
        finished = threading.Event()

        def enforce_deadline() -> None:
            if finished.wait(self._request_deadline):
                return
            transfer_deadline = time.monotonic() + self._max_transfer_duration
            while self._is_long_request(request):
                remaining = transfer_deadline - time.monotonic()
                if remaining <= 0:
                    break
                if finished.wait(min(1.0, remaining)):
                    return
            try:
                request.shutdown(socket.SHUT_RDWR)
            except OSError:
                pass

        deadline = threading.Thread(
            target=enforce_deadline,
            name="flowclip-request-deadline",
            daemon=True,
        )
        deadline.start()
        try:
            super().process_request_thread(request, client_address)
        finally:
            finished.set()
            self.mark_long_request(request, False)
            with self._active_condition:
                self._active_requests.discard(request)
                self._active_condition.notify_all()
            self._worker_slots.release()


def _canonical_file_id(path: str) -> str | None:
    prefix = "/api/v1/files/"
    if not path.startswith(prefix):
        return None
    value = path[len(prefix) :]
    if not value or "/" in value:
        return None
    try:
        parsed = uuid.UUID(value)
    except ValueError:
        return None
    return value if str(parsed) == value else None


def _byte_range(value: str, size: int) -> tuple[int, int] | object | None:
    matched = _RANGE_PATTERN.fullmatch(value.strip())
    if matched is None:
        return None
    start_raw, end_raw = matched.groups()
    if not start_raw and not end_raw:
        return None
    if len(start_raw) > 20 or len(end_raw) > 20:
        return None
    if size == 0:
        return _RANGE_UNSATISFIABLE
    if not start_raw:
        suffix = int(end_raw)
        if suffix <= 0:
            return _RANGE_UNSATISFIABLE
        return max(0, size - suffix), size - 1
    start = int(start_raw)
    if start >= size:
        return _RANGE_UNSATISFIABLE
    end = size - 1 if not end_raw else min(int(end_raw), size - 1)
    if end < start:
        return _RANGE_UNSATISFIABLE
    return start, end


def _content_disposition(filename: str) -> str:
    fallback = "".join(
        character if 0x20 <= ord(character) <= 0x7E and character not in {'"', "\\"} else "_"
        for character in filename
    )
    fallback = fallback or "download"
    encoded = quote(filename.encode("utf-8"), safe="")
    return f"attachment; filename=\"{fallback}\"; filename*=UTF-8''{encoded}"


def make_handler(
    *,
    store: ClipboardStore,
    token: str,
    max_bytes: int,
    event_callback: Callable[[str], None] | None = None,
    file_store: FileStore | None = None,
    transfer_workers: int = DEFAULT_TRANSFER_WORKERS,
    max_transfer_duration: float = DEFAULT_MAX_TRANSFER_DURATION,
) -> type[BaseHTTPRequestHandler]:
    if file_store is None:
        from .config import config_directory

        file_store = FileStore(config_directory() / "files")
    if transfer_workers <= 0:
        raise ValueError("transfer_workers 必须是正整数")
    if max_transfer_duration <= 0:
        raise ValueError("max_transfer_duration 必须大于零")

    resolved_file_store = file_store
    transfer_slots = threading.BoundedSemaphore(transfer_workers)
    maximum_json_bytes = ((max_bytes + 2) // 3) * 4 + 16 * 1024

    class FlowClipHandler(BaseHTTPRequestHandler):
        server_version = "FlowClip"
        sys_version = ""
        protocol_version = "HTTP/1.1"

        def version_string(self) -> str:
            return self.server_version

        def log_message(self, _format: str, *_args: object) -> None:
            return

        def _authorized(self) -> bool:
            authorization = self.headers.get("Authorization", "")
            expected = f"Bearer {token}"
            return hmac.compare_digest(authorization.encode(), expected.encode())

        def _send_json(
            self,
            status: int,
            payload: dict[str, object],
            *,
            close: bool = False,
            headers: dict[str, str] | None = None,
        ) -> None:
            encoded = json.dumps(payload, ensure_ascii=False).encode("utf-8")
            if close and not getattr(self, "_request_body_consumed", False):
                self._drain_request_body()
            self.send_response(status)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.send_header("Content-Length", str(len(encoded)))
            self.send_header("Cache-Control", "no-store")
            self.send_header("X-Content-Type-Options", "nosniff")
            for name, value in (headers or {}).items():
                self.send_header(name, value)
            if close:
                self.send_header("Connection", "close")
                self.close_connection = True
            self.end_headers()
            self.wfile.write(encoded)

        def _reject_unauthorized(self) -> None:
            self._send_json(
                HTTPStatus.UNAUTHORIZED,
                {"error": "认证失败"},
                close=True,
                headers={"WWW-Authenticate": 'Bearer realm="FlowClip"'},
            )

        def _content_length(self, maximum: int | None = None) -> int:
            if self.headers.get_all("Transfer-Encoding", []):
                raise ValueError("不支持 Transfer-Encoding")
            values = self.headers.get_all("Content-Length", [])
            if len(values) != 1:
                raise ValueError("缺少或重复的 Content-Length")
            value = values[0].strip()
            if not value or any(character not in "0123456789" for character in value):
                raise ValueError("Content-Length 无效")
            length = int(value)
            if maximum is not None and length > maximum:
                raise OverflowError("请求体超过限制")
            return length

        def _drain_request_body(self) -> None:
            if self.command not in {"POST", "PUT"}:
                return
            values = self.headers.get_all("Content-Length", [])
            if len(values) != 1 or not values[0].strip().isdigit():
                return
            length = int(values[0].strip())
            if not 0 < length <= 64 * 1024:
                return
            previous_timeout = self.connection.gettimeout()
            try:
                self.connection.settimeout(0.05)
                remaining = length
                while remaining:
                    chunk = self.rfile.read(min(16 * 1024, remaining))
                    if not chunk:
                        break
                    remaining -= len(chunk)
            except (OSError, TimeoutError):
                pass
            finally:
                try:
                    self.connection.settimeout(previous_timeout)
                except OSError:
                    pass

        def _read_json(self, length: int) -> object:
            body = self.rfile.read(length)
            if len(body) != length:
                raise TimeoutError("请求体不完整")
            self._request_body_consumed = True
            return json.loads(body.decode("utf-8"))

        def _mark_long_request(self, active: bool) -> None:
            server = self.server
            if isinstance(server, ReusableThreadingHTTPServer):
                server.mark_long_request(self.connection, active)

        def _send_store_error(self, exc: FileStoreError) -> None:
            if isinstance(exc, StoreFileNotFoundError):
                status = HTTPStatus.NOT_FOUND
            elif isinstance(exc, (FileBusyError, FileNotReadyError, FileConflictError)):
                status = HTTPStatus.CONFLICT
            elif isinstance(exc, FileLimitError):
                status = HTTPStatus.REQUEST_ENTITY_TOO_LARGE
            elif isinstance(exc, FileCapacityError):
                status = HTTPStatus.INSUFFICIENT_STORAGE
            elif isinstance(exc, FileSizeMismatchError):
                status = HTTPStatus.BAD_REQUEST
            else:
                status = HTTPStatus.INTERNAL_SERVER_ERROR
            self._send_json(status, {"error": str(exc)}, close=True)

        def _serve_clipboard_get(self, query: str) -> None:
            try:
                after_raw = parse_qs(query).get("after", ["0"])[0]
                after = int(after_raw)
                if after < 0:
                    raise ValueError
            except ValueError:
                self._send_json(HTTPStatus.BAD_REQUEST, {"error": "after 参数无效"})
                return
            revision, item = store.latest_if_changed(after)
            if item is None:
                self.send_response(HTTPStatus.NO_CONTENT)
                self.send_header("Content-Length", "0")
                self.send_header("Cache-Control", "no-store")
                self.send_header("X-FlowClip-Revision", str(revision))
                self.end_headers()
                return
            self._send_json(
                HTTPStatus.OK,
                {"revision": revision, "item": item.to_dict()},
            )

        def _serve_file_list(self) -> None:
            revision, records = resolved_file_store.list_records(ready_only=True)
            self._send_json(
                HTTPStatus.OK,
                {"revision": revision, "files": [record.to_dict() for record in records]},
            )

        def _serve_file_download(self, file_id: str) -> None:
            if not transfer_slots.acquire(blocking=False):
                self._send_json(
                    HTTPStatus.SERVICE_UNAVAILABLE,
                    {"error": "文件传输任务已满，请稍后重试"},
                    close=True,
                )
                return
            self._mark_long_request(True)
            self.close_connection = True
            try:
                with resolved_file_store.open_download(file_id) as (record, stream):
                    etag = f'"{record.sha256}"'
                    range_header = self.headers.get("Range")
                    if_range = self.headers.get("If-Range")
                    selected: tuple[int, int] | None = None
                    range_requested = bool(range_header and (if_range is None or if_range == etag))
                    if range_requested:
                        parsed_range = _byte_range(range_header or "", record.size)
                        if parsed_range is _RANGE_UNSATISFIABLE:
                            self.send_response(HTTPStatus.REQUESTED_RANGE_NOT_SATISFIABLE)
                            self.send_header("Content-Length", "0")
                            self.send_header("Content-Range", f"bytes */{record.size}")
                            self.send_header("ETag", etag)
                            self.send_header("Connection", "close")
                            self.end_headers()
                            return
                        if isinstance(parsed_range, tuple):
                            selected = parsed_range

                    if selected is None:
                        status = HTTPStatus.OK
                        start, end = 0, record.size - 1
                        content_length = record.size
                    else:
                        status = HTTPStatus.PARTIAL_CONTENT
                        start, end = selected
                        content_length = end - start + 1

                    self.send_response(status)
                    self.send_header("Content-Type", record.metadata.mime)
                    self.send_header("Content-Length", str(content_length))
                    self.send_header("Content-Disposition", _content_disposition(record.metadata.filename))
                    self.send_header("ETag", etag)
                    self.send_header("Accept-Ranges", "bytes")
                    self.send_header("Cache-Control", "no-store")
                    self.send_header("X-Content-Type-Options", "nosniff")
                    self.send_header("Connection", "close")
                    if selected is not None:
                        self.send_header(
                            "Content-Range", f"bytes {start}-{end}/{record.size}"
                        )
                    self.end_headers()

                    if content_length:
                        stream.seek(start)
                    remaining = content_length
                    transfer_deadline = time.monotonic() + max_transfer_duration
                    while remaining:
                        if time.monotonic() >= transfer_deadline:
                            raise TimeoutError("文件传输超过最长时限")
                        chunk = stream.read(min(TRANSFER_CHUNK_BYTES, remaining))
                        if not chunk:
                            raise OSError("服务器文件内容短于索引大小")
                        self.wfile.write(chunk)
                        remaining -= len(chunk)
            except (StoreFileNotFoundError, FileNotReadyError, FileStoreError) as exc:
                if not self.wfile.closed:
                    try:
                        self._send_store_error(exc)
                    except OSError:
                        pass
            except (BrokenPipeError, ConnectionResetError, TimeoutError, OSError):
                pass
            finally:
                self._mark_long_request(False)
                transfer_slots.release()

        def do_GET(self) -> None:  # noqa: N802
            parsed = urlsplit(self.path)
            if parsed.path == "/api/v1/health":
                self._send_json(
                    HTTPStatus.OK,
                    {
                        "ok": True,
                        "version": 1,
                        "serverId": store.server_id,
                        "capabilities": ["clipboard-v1", "files-v1"],
                        "fileMaxBytes": resolved_file_store.file_max_bytes,
                    },
                )
                return
            if parsed.path == "/api/v1/clipboard":
                if not self._authorized():
                    self._reject_unauthorized()
                    return
                self._serve_clipboard_get(parsed.query)
                return
            if parsed.path == "/api/v1/files":
                if not self._authorized():
                    self._reject_unauthorized()
                    return
                self._serve_file_list()
                return
            file_id = _canonical_file_id(parsed.path)
            if file_id is not None:
                if not self._authorized():
                    self._reject_unauthorized()
                    return
                self._serve_file_download(file_id)
                return
            self._send_json(HTTPStatus.NOT_FOUND, {"error": "接口不存在"})

        def _serve_clipboard_post(self) -> None:
            if self.headers.get_content_type() != "application/json":
                self._send_json(
                    HTTPStatus.UNSUPPORTED_MEDIA_TYPE,
                    {"error": "仅接受 application/json"},
                    close=True,
                )
                return
            try:
                length = self._content_length(maximum_json_bytes)
                if length <= 0:
                    raise ValueError
            except (ValueError, OverflowError):
                self._send_json(
                    HTTPStatus.REQUEST_ENTITY_TOO_LARGE,
                    {"error": "请求体大小无效或超过限制"},
                    close=True,
                )
                return
            try:
                raw = self._read_json(length)
                item = ClipItem.from_dict(raw, max_bytes)
            except (ValueError, ProtocolError) as exc:
                self._send_json(HTTPStatus.BAD_REQUEST, {"error": str(exc)})
                return
            except (TimeoutError, OSError):
                try:
                    self._send_json(
                        HTTPStatus.REQUEST_TIMEOUT,
                        {"error": "读取请求体超时"},
                        close=True,
                    )
                except OSError:
                    pass
                return
            revision = store.put(item)
            if event_callback is not None:
                event_callback(
                    f"收到 {item.origin} 的{'图片' if item.kind == 'image' else '文本'}"
                )
            self._send_json(
                HTTPStatus.OK,
                {"ok": True, "revision": revision, "serverId": store.server_id},
            )

        def _serve_file_post(self) -> None:
            if self.headers.get_content_type() != "application/json":
                self._send_json(
                    HTTPStatus.UNSUPPORTED_MEDIA_TYPE,
                    {"error": "仅接受 application/json"},
                    close=True,
                )
                return
            try:
                length = self._content_length(FILE_METADATA_MAX_BYTES)
                if length <= 0:
                    raise ValueError
            except OverflowError:
                self._send_json(
                    HTTPStatus.REQUEST_ENTITY_TOO_LARGE,
                    {"error": "文件元数据超过 16 KiB 限制"},
                    close=True,
                )
                return
            except ValueError as exc:
                self._send_json(
                    HTTPStatus.BAD_REQUEST,
                    {"error": str(exc)},
                    close=True,
                )
                return
            try:
                raw = self._read_json(length)
                metadata = FileMetadata.from_dict(
                    raw, max_bytes=resolved_file_store.file_max_bytes
                )
                revision, record = resolved_file_store.create_pending(metadata)
            except (ValueError, FileProtocolError) as exc:
                status = (
                    HTTPStatus.REQUEST_ENTITY_TOO_LARGE
                    if "大小限制" in str(exc)
                    else HTTPStatus.BAD_REQUEST
                )
                self._send_json(status, {"error": str(exc)})
                return
            except FileStoreError as exc:
                self._send_store_error(exc)
                return
            except (TimeoutError, OSError):
                try:
                    self._send_json(
                        HTTPStatus.REQUEST_TIMEOUT,
                        {"error": "读取请求体超时"},
                        close=True,
                    )
                except OSError:
                    pass
                return
            self._send_json(
                HTTPStatus.CREATED,
                {"ok": True, "revision": revision, "file": record.to_dict()},
            )

        def do_POST(self) -> None:  # noqa: N802
            path = urlsplit(self.path).path
            if path not in {"/api/v1/clipboard", "/api/v1/files"}:
                self._send_json(
                    HTTPStatus.NOT_FOUND, {"error": "接口不存在"}, close=True
                )
                return
            if not self._authorized():
                self._reject_unauthorized()
                return
            if path == "/api/v1/clipboard":
                self._serve_clipboard_post()
            else:
                self._serve_file_post()

        def do_PUT(self) -> None:  # noqa: N802
            file_id = _canonical_file_id(urlsplit(self.path).path)
            if file_id is None:
                self._send_json(
                    HTTPStatus.NOT_FOUND, {"error": "接口不存在"}, close=True
                )
                return
            if not self._authorized():
                self._reject_unauthorized()
                return
            try:
                length = self._content_length()
            except ValueError as exc:
                try:
                    resolved_file_store.abort_pending(file_id)
                except FileStoreError:
                    pass
                self._send_json(
                    HTTPStatus.BAD_REQUEST,
                    {"error": str(exc)},
                    close=True,
                )
                return
            try:
                record = resolved_file_store.get_record(file_id)
            except FileStoreError as exc:
                self._send_store_error(exc)
                return
            if length != record.size:
                try:
                    resolved_file_store.abort_pending(file_id)
                except FileStoreError:
                    pass
                self._send_json(
                    HTTPStatus.BAD_REQUEST,
                    {"error": "Content-Length 与声明文件大小不一致"},
                    close=True,
                )
                return
            if not transfer_slots.acquire(blocking=False):
                try:
                    resolved_file_store.abort_pending(file_id)
                except FileStoreError:
                    pass
                self._send_json(
                    HTTPStatus.SERVICE_UNAVAILABLE,
                    {"error": "文件传输任务已满，请稍后重试"},
                    close=True,
                )
                return

            self._mark_long_request(True)
            try:
                reader = _TransferReader(
                    self.rfile, time.monotonic() + max_transfer_duration
                )
                revision, ready = resolved_file_store.upload(file_id, reader, length)
                self._request_body_consumed = True
                if event_callback is not None:
                    event_callback(f"收到 {ready.metadata.origin} 的文件 {ready.metadata.filename}")
                self._send_json(
                    HTTPStatus.OK,
                    {"ok": True, "revision": revision, "file": ready.to_dict()},
                    close=True,
                )
            except FileStoreError as exc:
                try:
                    self._send_store_error(exc)
                except OSError:
                    pass
            except (TimeoutError, OSError):
                try:
                    self._send_json(
                        HTTPStatus.REQUEST_TIMEOUT,
                        {"error": "上传文件超时或连接中断"},
                        close=True,
                    )
                except OSError:
                    pass
            finally:
                self._mark_long_request(False)
                transfer_slots.release()

        def do_DELETE(self) -> None:  # noqa: N802
            file_id = _canonical_file_id(urlsplit(self.path).path)
            if file_id is None:
                self._send_json(HTTPStatus.NOT_FOUND, {"error": "接口不存在"})
                return
            if not self._authorized():
                self._reject_unauthorized()
                return
            try:
                revision, _record = resolved_file_store.delete(file_id)
            except FileStoreError as exc:
                self._send_store_error(exc)
                return
            self._send_json(
                HTTPStatus.OK, {"ok": True, "revision": revision}, close=True
            )

    return FlowClipHandler


class ClipboardServer:
    def __init__(
        self,
        host: str,
        port: int,
        token: str,
        max_bytes: int,
        event_callback: Callable[[str], None] | None = None,
        *,
        file_store: FileStore | None = None,
        file_store_directory: str | Path | None = None,
        file_max_bytes: int = DEFAULT_FILE_MAX_BYTES,
        file_storage_limit_bytes: int = DEFAULT_STORAGE_LIMIT_BYTES,
        file_max_items: int = DEFAULT_MAX_FILES,
        transfer_workers: int = DEFAULT_TRANSFER_WORKERS,
        max_workers: int = 32,
        request_timeout: float = 10.0,
        request_deadline: float = 30.0,
        max_transfer_duration: float = DEFAULT_MAX_TRANSFER_DURATION,
    ) -> None:
        if file_store is not None and file_store_directory is not None:
            raise ValueError("file_store 与 file_store_directory 不能同时指定")
        if file_store is None:
            if file_store_directory is None:
                from .config import config_directory

                file_store_directory = config_directory() / "files"
            file_store = FileStore(
                file_store_directory,
                file_max_bytes=file_max_bytes,
                storage_limit_bytes=file_storage_limit_bytes,
                max_items=file_max_items,
            )

        self.store = ClipboardStore()
        self.file_store = file_store
        file_store.acquire_server_lease()
        try:
            handler = make_handler(
                store=self.store,
                token=token,
                max_bytes=max_bytes,
                event_callback=event_callback,
                file_store=file_store,
                transfer_workers=transfer_workers,
                max_transfer_duration=max_transfer_duration,
            )
            self._server = ReusableThreadingHTTPServer(
                (host, port),
                handler,
                max_workers=max_workers,
                request_timeout=request_timeout,
                request_deadline=request_deadline,
                max_transfer_duration=max_transfer_duration,
            )
        except Exception:
            file_store.release_server_lease()
            raise
        self._lease_active = True
        self._thread: threading.Thread | None = None

    @property
    def port(self) -> int:
        return int(self._server.server_address[1])

    def start(self) -> None:
        if self._thread is not None:
            return
        self._thread = threading.Thread(
            target=self._server.serve_forever,
            name="flowclip-server",
            daemon=True,
        )
        self._thread.start()

    def stop(self) -> None:
        if self._thread is not None:
            self._server.shutdown()
        self._server.server_close()
        requests_stopped = self._server.close_active_requests()
        if self._thread is not None:
            self._thread.join(timeout=3)
            if self._thread.is_alive():
                raise RuntimeError("服务器监听线程未能及时停止")
            self._thread = None
        if not requests_stopped:
            raise RuntimeError("服务器请求线程未能及时停止")
        if self._lease_active:
            self.file_store.release_server_lease()
            self._lease_active = False
