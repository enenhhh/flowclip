from __future__ import annotations

import hashlib
import http.client
import json
import mimetypes
import os
import platform
import re
import time
import uuid
from pathlib import Path
from typing import BinaryIO, Callable
from urllib.parse import urlsplit

from . import __version__
from .client import ConnectionFailure
from .file_lock import ExclusiveFileLock, LockUnavailableError
from .file_model import FileMetadata, FileProtocolError, FileRecord
from .file_store import TRANSFER_CHUNK_BYTES


FILE_RESPONSE_MAX_BYTES = 2 * 1024 * 1024
ProgressCallback = Callable[[int, int], None]
CancellationCallback = Callable[[], bool]
FileOpener = Callable[[Path, str], BinaryIO]
_CONTENT_RANGE_PATTERN = re.compile(r"bytes (\d+)-(\d+)/(\d+)$")


class FileTransferCancelled(ConnectionFailure):
    pass


class FileIntegrityError(ConnectionFailure):
    pass


class _ProgressReporter:
    def __init__(
        self,
        callback: ProgressCallback | None,
        total: int,
        interval: float = 0.1,
    ) -> None:
        self.callback = callback
        self.total = total
        self.interval = interval
        self._last_at = 0.0
        self._last_value: int | None = None

    def report(self, completed: int, *, force: bool = False) -> None:
        if self.callback is None:
            return
        now = time.monotonic()
        if (
            force
            or self._last_value is None
            or completed == self.total
            or now - self._last_at >= self.interval
        ):
            self.callback(completed, self.total)
            self._last_at = now
            self._last_value = completed


class FileTransferClient:
    def __init__(
        self,
        base_url: str,
        token: str,
        *,
        timeout: float = 10.0,
        chunk_bytes: int = TRANSFER_CHUNK_BYTES,
        file_opener: FileOpener = open,
    ) -> None:
        parsed = urlsplit(base_url.rstrip("/"))
        try:
            port = parsed.port
        except ValueError as exc:
            raise ConnectionFailure("服务器地址端口无效") from exc
        if (
            parsed.scheme.lower() not in {"http", "https"}
            or not parsed.hostname
            or parsed.username is not None
            or parsed.password is not None
            or parsed.path not in {"", "/"}
            or parsed.query
            or parsed.fragment
        ):
            raise ConnectionFailure("服务器地址必须是无额外路径的 HTTP 或 HTTPS URL")
        if not 16 <= len(token) <= 512 or any(
            not 0x21 <= ord(character) <= 0x7E for character in token
        ):
            raise ConnectionFailure(
                "共享密钥只能使用 16 到 512 个可见 ASCII 字符，不能包含空格或中文"
            )
        if timeout <= 0:
            raise ValueError("timeout 必须大于零")
        if (
            isinstance(chunk_bytes, bool)
            or not isinstance(chunk_bytes, int)
            or not 1 <= chunk_bytes <= TRANSFER_CHUNK_BYTES
        ):
            raise ValueError("chunk_bytes 必须介于 1 和 1 MiB 之间")

        self.base_url = base_url.rstrip("/")
        self.token = token
        self.timeout = timeout
        self.chunk_bytes = chunk_bytes
        self._file_opener = file_opener
        self._scheme = parsed.scheme.lower()
        self._host = parsed.hostname
        self._port = port or (443 if self._scheme == "https" else 80)
        self.last_revision = 0

    def _connection(self, timeout: float | None = None) -> http.client.HTTPConnection:
        connection_type: type[http.client.HTTPConnection]
        if self._scheme == "https":
            connection_type = http.client.HTTPSConnection
        else:
            connection_type = http.client.HTTPConnection
        return connection_type(
            self._host,
            self._port,
            timeout=self.timeout if timeout is None else timeout,
        )

    def _headers(self, *, content_type: str | None = None) -> dict[str, str]:
        headers = {
            "Accept": "application/json",
            "Authorization": f"Bearer {self.token}",
            "User-Agent": f"FlowClip-Desktop/{__version__} ({platform.system()})",
            "Connection": "close",
        }
        if content_type is not None:
            headers["Content-Type"] = content_type
        return headers

    @staticmethod
    def _check_cancelled(cancelled: CancellationCallback | None) -> None:
        if cancelled is not None and cancelled():
            raise FileTransferCancelled("文件传输已取消")

    @staticmethod
    def _read_limited(
        response: http.client.HTTPResponse,
        limit: int = FILE_RESPONSE_MAX_BYTES,
    ) -> bytes:
        declared_raw = response.getheader("Content-Length")
        if declared_raw is not None:
            try:
                declared = int(declared_raw)
            except ValueError as exc:
                raise ConnectionFailure("服务器返回了无效的 Content-Length") from exc
            if declared < 0 or declared > limit:
                raise ConnectionFailure("服务器响应超过大小限制")
        body = response.read(limit + 1)
        if len(body) > limit:
            raise ConnectionFailure("服务器响应超过大小限制")
        return body

    @classmethod
    def _json_response(
        cls,
        response: http.client.HTTPResponse,
        expected_statuses: set[int],
    ) -> dict[str, object]:
        body = cls._read_limited(response)
        if 300 <= response.status < 400:
            raise ConnectionFailure("文件传输拒绝 HTTP 重定向")
        if response.status not in expected_statuses:
            fallback = f"HTTP {response.status} {response.reason}"
            try:
                raw_error = json.loads(body.decode("utf-8"))
                detail = raw_error.get("error", fallback)
            except (UnicodeDecodeError, ValueError, AttributeError):
                detail = fallback
            raise ConnectionFailure(str(detail))
        try:
            payload = json.loads(body.decode("utf-8"))
        except (UnicodeDecodeError, ValueError) as exc:
            raise ConnectionFailure("服务器返回了无效 JSON") from exc
        if not isinstance(payload, dict):
            raise ConnectionFailure("服务器返回了无效 JSON")
        return payload

    def _request_json(
        self,
        method: str,
        path: str,
        *,
        payload: dict[str, object] | None = None,
        expected_statuses: set[int],
        authenticated: bool = True,
        timeout: float | None = None,
    ) -> dict[str, object]:
        body = None
        headers = self._headers(content_type="application/json" if payload is not None else None)
        if not authenticated:
            headers.pop("Authorization", None)
        if payload is not None:
            body = json.dumps(payload, ensure_ascii=False, separators=(",", ":")).encode(
                "utf-8"
            )
        connection = self._connection() if timeout is None else self._connection(timeout)
        try:
            connection.request(method, path, body=body, headers=headers)
            response = connection.getresponse()
            try:
                return self._json_response(response, expected_statuses)
            finally:
                response.close()
        except FileTransferCancelled:
            raise
        except ConnectionFailure:
            raise
        except (http.client.HTTPException, TimeoutError, OSError) as exc:
            raise ConnectionFailure(f"无法连接服务器：{exc}") from exc
        finally:
            connection.close()

    def health(self) -> dict[str, object]:
        return self._request_json(
            "GET",
            "/api/v1/health",
            expected_statuses={200},
            authenticated=False,
        )

    def list_files(self) -> list[FileRecord]:
        payload = self._request_json(
            "GET", "/api/v1/files", expected_statuses={200}
        )
        revision = payload.get("revision")
        files = payload.get("files")
        if (
            isinstance(revision, bool)
            or not isinstance(revision, int)
            or revision < 0
            or not isinstance(files, list)
        ):
            raise ConnectionFailure("服务器返回了无效文件列表")
        try:
            records = [
                FileRecord.from_dict(raw, max_bytes=2**63 - 1) for raw in files
            ]
        except FileProtocolError as exc:
            raise ConnectionFailure("服务器返回了无效文件列表") from exc
        if len({record.file_id for record in records}) != len(records):
            raise ConnectionFailure("服务器返回了重复文件 ID")
        self.last_revision = revision
        return sorted(
            records,
            key=lambda record: (record.metadata.created_at, record.file_id),
            reverse=True,
        )

    def delete_file(self, file_id: str) -> int:
        canonical = self._canonical_id(file_id)
        payload = self._request_json(
            "DELETE", f"/api/v1/files/{canonical}", expected_statuses={200}
        )
        revision = payload.get("revision")
        if isinstance(revision, bool) or not isinstance(revision, int) or revision < 0:
            raise ConnectionFailure("服务器返回了无效删除响应")
        self.last_revision = revision
        return revision

    @staticmethod
    def _canonical_id(file_id: str) -> str:
        try:
            parsed = uuid.UUID(file_id)
        except (ValueError, AttributeError) as exc:
            raise ConnectionFailure("文件 ID 必须是规范 UUID") from exc
        canonical = str(parsed)
        if file_id != canonical:
            raise ConnectionFailure("文件 ID 必须是规范 UUID")
        return canonical

    def _abort_remote(self, file_id: str) -> None:
        for attempt in range(3):
            try:
                self._request_json(
                    "DELETE",
                    f"/api/v1/files/{file_id}",
                    expected_statuses={200, 404},
                    timeout=min(self.timeout, 1.0),
                )
                return
            except ConnectionFailure:
                if attempt < 2:
                    time.sleep(0.05)

    def upload_file(
        self,
        path: str | os.PathLike[str],
        *,
        origin: str,
        mime: str | None = None,
        file_id: str | None = None,
        created_at: int | None = None,
        progress: ProgressCallback | None = None,
        cancelled: CancellationCallback | None = None,
    ) -> FileRecord:
        source_path = Path(path)
        try:
            size = source_path.stat().st_size
        except OSError as exc:
            raise ConnectionFailure(f"无法读取本地文件：{exc}") from exc
        if size < 0:
            raise ConnectionFailure("本地文件大小无效")
        selected_mime = mime or mimetypes.guess_type(source_path.name)[0] or "application/octet-stream"
        selected_id = file_id or str(uuid.uuid4())
        timestamp = int(time.time() * 1000) if created_at is None else created_at
        try:
            metadata = FileMetadata.from_dict(
                {
                    "id": selected_id,
                    "origin": origin,
                    "filename": source_path.name,
                    "mime": selected_mime,
                    "size": size,
                    "createdAt": timestamp,
                },
                max_bytes=2**63 - 1,
            )
        except FileProtocolError as exc:
            raise ConnectionFailure(str(exc)) from exc

        self._check_cancelled(cancelled)
        try:
            source = self._file_opener(source_path, "rb")
        except OSError as exc:
            raise ConnectionFailure(f"无法打开本地文件：{exc}") from exc

        pending_created = False
        upload_confirmed = False
        reporter = _ProgressReporter(progress, metadata.size)
        with source:
            try:
                create_payload = self._request_json(
                    "POST",
                    "/api/v1/files",
                    payload=metadata.to_dict(),
                    expected_statuses={201},
                )
                try:
                    pending = FileRecord.from_dict(
                        create_payload["file"], max_bytes=2**63 - 1
                    )
                except (KeyError, FileProtocolError) as exc:
                    raise ConnectionFailure("服务器返回了无效待上传记录") from exc
                if pending.file_id != metadata.file_id or pending.status != "pending":
                    raise ConnectionFailure("服务器返回了不匹配的待上传记录")
                pending_created = True
                reporter.report(0, force=True)

                connection = self._connection()
                digest = hashlib.sha256()
                transferred = 0
                try:
                    connection.putrequest(
                        "PUT",
                        f"/api/v1/files/{metadata.file_id}",
                        skip_accept_encoding=True,
                    )
                    for name, value in self._headers(
                        content_type="application/octet-stream"
                    ).items():
                        connection.putheader(name, value)
                    connection.putheader("Content-Length", str(metadata.size))
                    connection.endheaders()
                    while transferred < metadata.size:
                        self._check_cancelled(cancelled)
                        requested = min(self.chunk_bytes, metadata.size - transferred)
                        chunk = source.read(requested)
                        if not chunk:
                            raise ConnectionFailure("本地文件在上传期间变短")
                        if not isinstance(chunk, bytes) or len(chunk) > requested:
                            raise ConnectionFailure("本地文件读取器返回了无效分块")
                        connection.send(chunk)
                        digest.update(chunk)
                        transferred += len(chunk)
                        reporter.report(transferred)
                    response = connection.getresponse()
                    try:
                        response_payload = self._json_response(response, {200})
                    finally:
                        response.close()
                except FileTransferCancelled:
                    raise
                except ConnectionFailure:
                    raise
                except (http.client.HTTPException, TimeoutError, OSError) as exc:
                    raise ConnectionFailure(f"上传文件失败：{exc}") from exc
                finally:
                    connection.close()

                try:
                    ready = FileRecord.from_dict(
                        response_payload["file"], max_bytes=2**63 - 1
                    )
                except (KeyError, FileProtocolError) as exc:
                    raise ConnectionFailure("服务器返回了无效上传结果") from exc
                checksum = digest.hexdigest()
                if (
                    ready.file_id != metadata.file_id
                    or ready.status != "ready"
                    or ready.size != metadata.size
                    or ready.sha256 != checksum
                ):
                    raise FileIntegrityError("服务器文件大小或 SHA-256 校验失败")
                revision = response_payload.get("revision")
                if isinstance(revision, int) and not isinstance(revision, bool):
                    self.last_revision = revision
                upload_confirmed = True
                reporter.report(metadata.size, force=True)
                return ready
            finally:
                if pending_created and not upload_confirmed:
                    self._abort_remote(metadata.file_id)

    def upload(self, *args: object, **kwargs: object) -> FileRecord:
        return self.upload_file(*args, **kwargs)  # type: ignore[arg-type]

    @staticmethod
    def _destination_name(filename: str, number: int) -> str:
        if number == 0:
            return filename
        source = Path(filename)
        return f"{source.stem} ({number}){source.suffix}"

    @classmethod
    def _commit_part(cls, part: Path, directory: Path, filename: str) -> Path:
        number = 0
        while True:
            candidate = directory / cls._destination_name(filename, number)
            try:
                descriptor = os.open(
                    candidate,
                    os.O_WRONLY | os.O_CREAT | os.O_EXCL,
                    0o600,
                )
            except FileExistsError:
                number += 1
                continue
            except OSError as exc:
                raise ConnectionFailure(f"无法创建下载文件：{exc}") from exc
            os.close(descriptor)
            try:
                os.replace(part, candidate)
            except OSError as exc:
                try:
                    candidate.unlink()
                except OSError:
                    pass
                raise ConnectionFailure(f"无法完成下载文件：{exc}") from exc
            return candidate
            number += 1

    @staticmethod
    def _remove_part(path: Path) -> None:
        try:
            path.unlink()
        except FileNotFoundError:
            pass
        except OSError:
            pass

    def _download_record(self, file_id: str) -> FileRecord:
        for record in self.list_files():
            if record.file_id == file_id:
                if record.status != "ready":
                    raise ConnectionFailure("文件尚未上传完成")
                return record
        raise ConnectionFailure("文件不存在")

    def download_file(
        self,
        file_id: str,
        directory: str | os.PathLike[str],
        *,
        progress: ProgressCallback | None = None,
        cancelled: CancellationCallback | None = None,
    ) -> Path:
        canonical = self._canonical_id(file_id)
        target_directory = Path(directory)
        try:
            target_directory.mkdir(parents=True, exist_ok=True)
        except OSError as exc:
            raise ConnectionFailure(f"无法创建下载目录：{exc}") from exc
        if not target_directory.is_dir():
            raise ConnectionFailure("下载路径不是目录")

        transfer_lock = ExclusiveFileLock(
            target_directory / ".flowclip-locks" / f"{canonical}.lock"
        )
        try:
            transfer_lock.acquire()
        except (OSError, LockUnavailableError) as exc:
            raise ConnectionFailure("该文件正在被另一个下载任务处理") from exc

        part = target_directory / f".flowclip-{canonical}.part"
        integrity_failed = False
        try:
            self._check_cancelled(cancelled)
            record = self._download_record(canonical)
            if record.sha256 is None:
                raise ConnectionFailure("服务器文件缺少 SHA-256")
            reporter = _ProgressReporter(progress, record.size)

            try:
                existing = part.stat().st_size
            except FileNotFoundError:
                existing = 0
            except OSError as exc:
                raise ConnectionFailure(f"无法读取临时下载文件：{exc}") from exc
            if existing > record.size:
                self._remove_part(part)
                existing = 0

            digest = hashlib.sha256()
            if existing:
                try:
                    with self._file_opener(part, "rb") as current:
                        hashed = 0
                        while hashed < existing:
                            self._check_cancelled(cancelled)
                            chunk = current.read(min(self.chunk_bytes, existing - hashed))
                            if not chunk:
                                raise ConnectionFailure("临时下载文件大小发生变化")
                            if not isinstance(chunk, bytes) or len(chunk) > self.chunk_bytes:
                                raise ConnectionFailure("临时文件读取器返回了无效分块")
                            digest.update(chunk)
                            hashed += len(chunk)
                except OSError as exc:
                    raise ConnectionFailure(f"无法读取临时下载文件：{exc}") from exc
                if existing == record.size:
                    if digest.hexdigest() == record.sha256:
                        destination = self._commit_part(
                            part, target_directory, record.metadata.filename
                        )
                        reporter.report(record.size, force=True)
                        return destination
                    self._remove_part(part)
                    existing = 0
                    digest = hashlib.sha256()

            reporter.report(existing, force=True)
            connection = self._connection()
            headers = self._headers()
            headers["Accept"] = "application/octet-stream"
            if existing:
                headers["Range"] = f"bytes={existing}-"
                headers["If-Range"] = f'"{record.sha256}"'
            try:
                connection.request(
                    "GET", f"/api/v1/files/{canonical}", headers=headers
                )
                response = connection.getresponse()
                try:
                    if 300 <= response.status < 400:
                        raise ConnectionFailure("文件传输拒绝 HTTP 重定向")
                    if response.status not in {200, 206}:
                        self._json_response(response, {200, 206})

                    if response.status == 206:
                        content_range = response.getheader("Content-Range", "")
                        matched = _CONTENT_RANGE_PATTERN.fullmatch(content_range)
                        if matched is None:
                            raise ConnectionFailure("服务器返回了无效 Content-Range")
                        start, end, total = (int(value) for value in matched.groups())
                        if (
                            start != existing
                            or total != record.size
                            or end != record.size - 1
                            or end < start
                        ):
                            raise ConnectionFailure("服务器返回的续传范围不匹配")
                        mode = "ab"
                        transferred = existing
                        expected_body = record.size - existing
                    else:
                        mode = "wb"
                        transferred = 0
                        expected_body = record.size
                        digest = hashlib.sha256()
                        if existing:
                            reporter.report(0, force=True)

                    declared_raw = response.getheader("Content-Length")
                    if declared_raw is not None:
                        try:
                            declared = int(declared_raw)
                        except ValueError as exc:
                            raise ConnectionFailure(
                                "服务器返回了无效的 Content-Length"
                            ) from exc
                        if declared != expected_body:
                            raise ConnectionFailure("服务器返回的文件长度不匹配")

                    try:
                        with self._file_opener(part, mode) as output:
                            received = 0
                            while received < expected_body:
                                self._check_cancelled(cancelled)
                                requested = min(
                                    self.chunk_bytes, expected_body - received
                                )
                                chunk = response.read(requested)
                                if not chunk:
                                    raise ConnectionFailure("下载连接在文件完成前中断")
                                if len(chunk) > requested:
                                    raise ConnectionFailure("服务器返回了无效文件分块")
                                output.write(chunk)
                                digest.update(chunk)
                                received += len(chunk)
                                transferred += len(chunk)
                                reporter.report(transferred)
                            output.flush()
                            os.fsync(output.fileno())
                    except FileTransferCancelled:
                        raise
                    except OSError as exc:
                        raise ConnectionFailure(f"写入下载文件失败：{exc}") from exc
                finally:
                    response.close()
            except FileTransferCancelled:
                raise
            except ConnectionFailure:
                raise
            except (http.client.HTTPException, TimeoutError, OSError) as exc:
                raise ConnectionFailure(f"下载文件失败：{exc}") from exc
            finally:
                connection.close()

            try:
                final_size = part.stat().st_size
            except OSError as exc:
                raise ConnectionFailure(f"无法校验下载文件：{exc}") from exc
            if final_size != record.size or digest.hexdigest() != record.sha256:
                integrity_failed = True
                raise FileIntegrityError("下载文件大小或 SHA-256 校验失败")

            destination = self._commit_part(
                part, target_directory, record.metadata.filename
            )
            reporter.report(record.size, force=True)
            return destination
        except FileTransferCancelled:
            self._remove_part(part)
            raise
        except FileIntegrityError:
            integrity_failed = True
            self._remove_part(part)
            raise
        finally:
            if integrity_failed:
                self._remove_part(part)
            transfer_lock.release()

    def download(self, *args: object, **kwargs: object) -> Path:
        return self.download_file(*args, **kwargs)  # type: ignore[arg-type]
