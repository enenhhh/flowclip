from __future__ import annotations

import os
from pathlib import Path


class LockUnavailableError(RuntimeError):
    pass


class ExclusiveFileLock:
    def __init__(self, path: str | os.PathLike[str]) -> None:
        self.path = Path(path)
        self._descriptor: int | None = None

    def acquire(self) -> None:
        if self._descriptor is not None:
            raise RuntimeError("文件锁已经持有")
        self.path.parent.mkdir(parents=True, exist_ok=True)
        descriptor = os.open(self.path, os.O_RDWR | os.O_CREAT, 0o600)
        try:
            os.set_inheritable(descriptor, False)
            if os.fstat(descriptor).st_size == 0:
                os.write(descriptor, b"\0")
            os.lseek(descriptor, 0, os.SEEK_SET)
            if os.name == "nt":
                import msvcrt

                msvcrt.locking(descriptor, msvcrt.LK_NBLCK, 1)
            else:
                import fcntl

                fcntl.flock(descriptor, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError as exc:
            os.close(descriptor)
            raise LockUnavailableError(f"文件锁正被其他进程使用：{self.path}") from exc
        self._descriptor = descriptor

    def release(self) -> None:
        descriptor = self._descriptor
        if descriptor is None:
            return
        self._descriptor = None
        try:
            os.lseek(descriptor, 0, os.SEEK_SET)
            if os.name == "nt":
                import msvcrt

                msvcrt.locking(descriptor, msvcrt.LK_UNLCK, 1)
            else:
                import fcntl

                fcntl.flock(descriptor, fcntl.LOCK_UN)
        finally:
            os.close(descriptor)

    def __enter__(self) -> "ExclusiveFileLock":
        self.acquire()
        return self

    def __exit__(self, *_args: object) -> None:
        self.release()
