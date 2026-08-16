from __future__ import annotations

import builtins
import hashlib
import json
import os
import threading
import time
from contextlib import contextmanager
from pathlib import Path
from typing import BinaryIO, Callable, Iterator

from .file_model import (
    DEFAULT_FILE_MAX_BYTES,
    FileMetadata,
    FileProtocolError,
    FileRecord,
)
from .file_lock import ExclusiveFileLock, LockUnavailableError


DEFAULT_STORAGE_LIMIT_BYTES = 20 * 1024 * 1024 * 1024
DEFAULT_MAX_FILES = 100
TRANSFER_CHUNK_BYTES = 1024 * 1024
INDEX_SCHEMA_VERSION = 1
DEFAULT_PENDING_TTL_SECONDS = 10 * 60


class FileStoreError(RuntimeError):
    pass


class FileNotFoundError(FileStoreError):
    pass


class FileConflictError(FileStoreError):
    pass


class FileBusyError(FileConflictError):
    pass


class FileNotReadyError(FileConflictError):
    pass


class FileLimitError(FileStoreError):
    pass


class FileCapacityError(FileStoreError):
    pass


class FileSizeMismatchError(FileStoreError):
    pass


class FileStore:
    def __init__(
        self,
        directory: str | os.PathLike[str],
        *,
        file_max_bytes: int = DEFAULT_FILE_MAX_BYTES,
        storage_limit_bytes: int = DEFAULT_STORAGE_LIMIT_BYTES,
        max_items: int = DEFAULT_MAX_FILES,
        chunk_bytes: int = TRANSFER_CHUNK_BYTES,
        pending_ttl_seconds: float = DEFAULT_PENDING_TTL_SECONDS,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        for name, value in (
            ("file_max_bytes", file_max_bytes),
            ("storage_limit_bytes", storage_limit_bytes),
            ("max_items", max_items),
            ("chunk_bytes", chunk_bytes),
        ):
            if isinstance(value, bool) or not isinstance(value, int) or value <= 0:
                raise ValueError(f"{name} 必须是正整数")
        if file_max_bytes > storage_limit_bytes:
            raise ValueError("storage_limit_bytes 不能小于 file_max_bytes")
        if chunk_bytes > TRANSFER_CHUNK_BYTES:
            raise ValueError("chunk_bytes 不能超过 1 MiB")
        if (
            isinstance(pending_ttl_seconds, bool)
            or not isinstance(pending_ttl_seconds, (int, float))
            or pending_ttl_seconds <= 0
        ):
            raise ValueError("pending_ttl_seconds 必须大于零")

        self.directory = Path(directory)
        self.file_max_bytes = file_max_bytes
        self.storage_limit_bytes = storage_limit_bytes
        self.max_items = max_items
        self.chunk_bytes = chunk_bytes
        self.pending_ttl_seconds = float(pending_ttl_seconds)
        self._clock = clock
        self._index_path = self.directory / "index.json"
        self._lock = threading.RLock()
        self._records: dict[str, FileRecord] = {}
        self._revision = 0
        self._active: dict[str, int] = {}
        self._uploading: set[str] = set()
        self._pending_since: dict[str, float] = {}
        self._server_lease: ExclusiveFileLock | None = None
        self._recover()

    def acquire_server_lease(self) -> None:
        with self._lock:
            if self._server_lease is not None:
                raise FileStoreError("文件仓库已经被服务器使用")
            lease = ExclusiveFileLock(self.directory / ".flowclip-server.lock")
            try:
                lease.acquire()
            except (OSError, LockUnavailableError) as exc:
                raise FileStoreError("文件仓库正被另一个服务器使用") from exc
            self._server_lease = lease

    def release_server_lease(self) -> None:
        with self._lock:
            lease = self._server_lease
            self._server_lease = None
            if lease is not None:
                lease.release()

    @property
    def revision(self) -> int:
        with self._lock:
            return self._revision

    @property
    def used_bytes(self) -> int:
        with self._lock:
            return sum(record.size for record in self._records.values())

    def _blob_path(self, file_id: str) -> Path:
        return self.directory / f"{file_id}.blob"

    def _part_path(self, file_id: str) -> Path:
        return self.directory / f"{file_id}.part"

    def _recover(self) -> None:
        if not self.directory.exists():
            return
        if not self.directory.is_dir():
            raise FileStoreError("文件仓库路径不是目录")

        index_existed = self._index_path.exists()
        records: dict[str, FileRecord] = {}
        revision = 0
        if index_existed:
            try:
                raw = json.loads(self._index_path.read_text(encoding="utf-8"))
                if (
                    not isinstance(raw, dict)
                    or raw.get("schemaVersion") != INDEX_SCHEMA_VERSION
                    or isinstance(raw.get("revision"), bool)
                    or not isinstance(raw.get("revision"), int)
                    or raw["revision"] < 0
                    or not isinstance(raw.get("files"), list)
                ):
                    raise FileProtocolError("文件索引格式无效")
                revision = raw["revision"]
                for value in raw["files"]:
                    record = FileRecord.from_dict(value, max_bytes=2**63 - 1)
                    if record.file_id in records:
                        raise FileProtocolError("文件索引包含重复 ID")
                    records[record.file_id] = record
            except (OSError, UnicodeDecodeError, json.JSONDecodeError, FileProtocolError) as exc:
                raise FileStoreError("无法读取文件仓库索引") from exc

        changed = False
        for part in self.directory.glob("*.part"):
            try:
                part.unlink()
                changed = True
            except builtins.FileNotFoundError:
                pass
            except OSError as exc:
                raise FileStoreError("无法清理未完成上传") from exc

        ready_blob_names: set[str] = set()
        recovered: dict[str, FileRecord] = {}
        for file_id, record in records.items():
            if record.status == "pending":
                changed = True
                continue
            blob = self._blob_path(file_id)
            try:
                exists = blob.is_file()
            except OSError:
                exists = False
            if not exists:
                changed = True
                continue
            recovered[file_id] = record
            ready_blob_names.add(blob.name)

        for blob in self.directory.glob("*.blob"):
            if blob.name not in ready_blob_names:
                try:
                    blob.unlink()
                    changed = True
                except builtins.FileNotFoundError:
                    pass
                except OSError as exc:
                    raise FileStoreError("无法清理孤立文件") from exc

        self._records = recovered
        self._revision = revision + (1 if changed and records else 0)
        if index_existed and changed:
            self._write_index_locked()

    def _write_index_locked(self) -> None:
        self.directory.mkdir(parents=True, exist_ok=True)
        temporary = self.directory / "index.json.tmp"
        payload = {
            "schemaVersion": INDEX_SCHEMA_VERSION,
            "revision": self._revision,
            "files": [
                self._records[file_id].to_dict()
                for file_id in sorted(self._records)
            ],
        }
        encoded = (json.dumps(payload, ensure_ascii=False, indent=2) + "\n").encode(
            "utf-8"
        )
        try:
            with temporary.open("wb") as output:
                output.write(encoded)
                output.flush()
                os.fsync(output.fileno())
            os.replace(temporary, self._index_path)
        except OSError as exc:
            try:
                temporary.unlink()
            except OSError:
                pass
            raise FileStoreError("无法原子写入文件仓库索引") from exc

    def _expire_pending(self) -> None:
        stale: list[str] = []
        now = self._clock()
        with self._lock:
            for file_id, created_at in self._pending_since.items():
                record = self._records.get(file_id)
                if (
                    record is not None
                    and record.status == "pending"
                    and not self._active.get(file_id, 0)
                    and now - created_at >= self.pending_ttl_seconds
                ):
                    stale.append(file_id)
            if not stale:
                return

            previous_revision = self._revision
            previous_records = {file_id: self._records[file_id] for file_id in stale}
            previous_times = {
                file_id: self._pending_since[file_id] for file_id in stale
            }
            for file_id in stale:
                self._records.pop(file_id, None)
                self._pending_since.pop(file_id, None)
            self._revision += 1
            try:
                self._write_index_locked()
            except Exception:
                self._records.update(previous_records)
                self._pending_since.update(previous_times)
                self._revision = previous_revision
                raise
            for file_id in stale:
                try:
                    self._part_path(file_id).unlink()
                except OSError:
                    pass

    def list_records(
        self, *, ready_only: bool = False
    ) -> tuple[int, list[FileRecord]]:
        self._expire_pending()
        with self._lock:
            records = sorted(
                (
                    record
                    for record in self._records.values()
                    if not ready_only or record.status == "ready"
                ),
                key=lambda record: (record.metadata.created_at, record.file_id),
                reverse=True,
            )
            return self._revision, records

    def get_record(self, file_id: str) -> FileRecord:
        self._expire_pending()
        with self._lock:
            try:
                return self._records[file_id]
            except KeyError as exc:
                raise FileNotFoundError("文件不存在") from exc

    def create_pending(self, metadata: FileMetadata) -> tuple[int, FileRecord]:
        if metadata.size > self.file_max_bytes:
            raise FileLimitError("文件超过服务器单文件大小限制")
        self._expire_pending()
        with self._lock:
            if metadata.file_id in self._records:
                raise FileConflictError("文件 ID 已存在")
            if len(self._records) >= self.max_items:
                raise FileCapacityError("服务器文件数量已达到上限")
            used = sum(record.size for record in self._records.values())
            if metadata.size > self.storage_limit_bytes - used:
                raise FileCapacityError("服务器文件存储配额不足")

            previous_revision = self._revision
            record = FileRecord(metadata, "pending")
            self._records[metadata.file_id] = record
            self._pending_since[metadata.file_id] = self._clock()
            self._revision += 1
            try:
                self._write_index_locked()
            except Exception:
                self._records.pop(metadata.file_id, None)
                self._pending_since.pop(metadata.file_id, None)
                self._revision = previous_revision
                raise
            return self._revision, record

    def abort_pending(self, file_id: str) -> bool:
        with self._lock:
            record = self._records.get(file_id)
            if record is None or record.status != "pending":
                return False
            if self._active.get(file_id, 0):
                raise FileBusyError("文件正在传输")
            previous_revision = self._revision
            self._records.pop(file_id)
            pending_since = self._pending_since.pop(file_id, None)
            self._revision += 1
            try:
                self._write_index_locked()
            except Exception:
                self._records[file_id] = record
                if pending_since is not None:
                    self._pending_since[file_id] = pending_since
                self._revision = previous_revision
                raise
            try:
                self._part_path(file_id).unlink()
            except builtins.FileNotFoundError:
                pass
            except OSError:
                pass
            return True

    def _begin_upload(self, file_id: str, content_length: int) -> FileRecord:
        self._expire_pending()
        with self._lock:
            record = self._records.get(file_id)
            if record is None:
                raise FileNotFoundError("待上传文件不存在")
            if record.status != "pending":
                raise FileConflictError("文件已经上传完成")
            if file_id in self._uploading or self._active.get(file_id, 0):
                raise FileBusyError("文件正在传输")
            if content_length != record.size:
                raise FileSizeMismatchError("Content-Length 与声明文件大小不一致")
            self._uploading.add(file_id)
            self._active[file_id] = self._active.get(file_id, 0) + 1
            return record

    def _end_active(self, file_id: str) -> None:
        with self._lock:
            count = self._active.get(file_id, 0)
            if count <= 1:
                self._active.pop(file_id, None)
            else:
                self._active[file_id] = count - 1
            self._uploading.discard(file_id)

    def _drop_failed_pending(self, file_id: str) -> None:
        with self._lock:
            record = self._records.get(file_id)
            if record is None or record.status != "pending":
                return
            self._records.pop(file_id)
            self._pending_since.pop(file_id, None)
            self._revision += 1
            try:
                self._write_index_locked()
            except FileStoreError:
                # The in-memory record is still removed so quota cannot remain stuck.
                pass

    def upload(
        self,
        file_id: str,
        source: BinaryIO,
        content_length: int,
    ) -> tuple[int, FileRecord]:
        if isinstance(content_length, bool) or not isinstance(content_length, int):
            raise FileSizeMismatchError("Content-Length 无效")
        try:
            pending = self._begin_upload(file_id, content_length)
        except FileSizeMismatchError:
            self.abort_pending(file_id)
            raise

        part = self._part_path(file_id)
        blob = self._blob_path(file_id)
        blob_installed = False
        digest = hashlib.sha256()
        remaining = pending.size
        try:
            self.directory.mkdir(parents=True, exist_ok=True)
            with part.open("wb") as output:
                while remaining:
                    requested = min(self.chunk_bytes, remaining)
                    chunk = source.read(requested)
                    if not chunk:
                        raise FileSizeMismatchError("上传内容短于 Content-Length")
                    if not isinstance(chunk, bytes) or len(chunk) > requested:
                        raise FileSizeMismatchError("上传数据流返回了无效分块")
                    output.write(chunk)
                    digest.update(chunk)
                    remaining -= len(chunk)
                output.flush()
                os.fsync(output.fileno())
            os.replace(part, blob)
            blob_installed = True

            ready = FileRecord(pending.metadata, "ready", digest.hexdigest())
            with self._lock:
                current = self._records.get(file_id)
                if current != pending:
                    raise FileConflictError("文件记录在上传期间发生变化")
                previous_revision = self._revision
                self._records[file_id] = ready
                pending_since = self._pending_since.pop(file_id, None)
                self._revision += 1
                try:
                    self._write_index_locked()
                except Exception:
                    self._records[file_id] = pending
                    if pending_since is not None:
                        self._pending_since[file_id] = pending_since
                    self._revision = previous_revision
                    raise
                return self._revision, ready
        except Exception:
            try:
                part.unlink()
            except OSError:
                pass
            if blob_installed:
                try:
                    blob.unlink()
                except OSError:
                    pass
            self._drop_failed_pending(file_id)
            raise
        finally:
            self._end_active(file_id)

    @contextmanager
    def open_download(self, file_id: str) -> Iterator[tuple[FileRecord, BinaryIO]]:
        with self._lock:
            record = self._records.get(file_id)
            if record is None:
                raise FileNotFoundError("文件不存在")
            if record.status != "ready":
                raise FileNotReadyError("文件尚未上传完成")
            self._active[file_id] = self._active.get(file_id, 0) + 1
        try:
            try:
                stream = self._blob_path(file_id).open("rb")
            except OSError as exc:
                raise FileNotFoundError("文件内容不存在") from exc
            with stream:
                yield record, stream
        finally:
            self._end_active(file_id)

    def delete(self, file_id: str) -> tuple[int, FileRecord]:
        with self._lock:
            record = self._records.get(file_id)
            if record is None:
                raise FileNotFoundError("文件不存在")
            if self._active.get(file_id, 0):
                raise FileBusyError("文件正在传输")
            previous_revision = self._revision
            self._records.pop(file_id)
            pending_since = self._pending_since.pop(file_id, None)
            self._revision += 1
            try:
                self._write_index_locked()
            except Exception:
                self._records[file_id] = record
                if pending_since is not None:
                    self._pending_since[file_id] = pending_since
                self._revision = previous_revision
                raise
            revision = self._revision
            for path in (self._part_path(file_id), self._blob_path(file_id)):
                try:
                    path.unlink()
                except builtins.FileNotFoundError:
                    pass
                except OSError:
                    # It is no longer referenced and will be removed during recovery.
                    pass
            return revision, record
