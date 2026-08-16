from __future__ import annotations

import threading
from collections.abc import Callable

from .client import ApiClient, ConnectionFailure
from .clipboard import ClipboardBackend, ClipboardError
from .config import AppConfig
from .model import ClipItem


StatusCallback = Callable[[str, str], None]


class SyncEngine:
    def __init__(
        self,
        config: AppConfig,
        clipboard: ClipboardBackend,
        status_callback: StatusCallback,
    ) -> None:
        self.config = config
        self.clipboard = clipboard
        self.status_callback = status_callback
        self._client = ApiClient(
            config.effective_server_url, config.token, config.max_bytes
        )
        self._stop = threading.Event()
        self._wake = threading.Event()
        self._lock = threading.Lock()
        self._thread: threading.Thread | None = None
        self._revision = 0
        self._last_sequence = clipboard.sequence_number()
        self._last_signature: str | None = None
        self._ignore_lock = threading.Lock()
        self._ignored_signature: str | None = None
        self._initialized = False
        self._connected = False
        self._last_error: str | None = None

    def start(self) -> None:
        if self._thread is not None:
            return
        self._thread = threading.Thread(
            target=self._run, name="flowclip-sync", daemon=True
        )
        self._thread.start()

    def stop(self) -> bool:
        self._stop.set()
        self._wake.set()
        if self._thread is not None:
            self._thread.join(timeout=12)
            if self._thread.is_alive():
                return False
            self._thread = None
        return True

    def trigger(self) -> None:
        self._wake.set()

    def suppress_clipboard_text(self, text: str) -> None:
        item = ClipItem.create(
            origin=self.config.device_id,
            kind="text",
            mime="text/plain; charset=utf-8",
            data=text.encode("utf-8"),
        )
        with self._ignore_lock:
            self._ignored_signature = item.signature

    def test_connection(self) -> dict[str, object]:
        return self._client.health()

    def send_now(self) -> None:
        with self._lock:
            sequence, item = self._capture_stable()
            if item is None:
                raise ClipboardError("当前剪贴板没有文本或图片")
            self._revision = self._client.push(item)
            self._last_signature = item.signature
            self._last_sequence = sequence
        self.status_callback(
            "success", f"已发送{'图片' if item.kind == 'image' else '文本'}"
        )

    def receive_now(self) -> bool:
        with self._lock:
            observed_sequence = self.clipboard.sequence_number()
            revision, item = self._client.fetch(0)
            if self.clipboard.sequence_number() != observed_sequence:
                raise ClipboardError("接收期间本机剪贴板已变化，未覆盖新内容")
            if item is None or item.origin == self.config.device_id:
                self._revision = revision
                return False
            self._apply_remote(item, observed_sequence)
            self._revision = revision
        return True

    def _apply_remote(self, item: ClipItem, expected_sequence: int) -> None:
        written_sequence = self.clipboard.apply(item, expected_sequence)
        self._last_signature = item.signature
        self._last_sequence = written_sequence
        self.status_callback(
            "success",
            f"已接收 {item.origin[:8]} 的{'图片' if item.kind == 'image' else '文本'}",
        )

    def _run(self) -> None:
        delay = self.config.poll_interval
        while not self._stop.is_set():
            if self.config.auto_sync:
                try:
                    self._sync_once()
                    if not self._connected:
                        self._connected = True
                        self.status_callback("success", "同步连接正常")
                    self._last_error = None
                    delay = self.config.poll_interval
                except (ConnectionFailure, ClipboardError, OSError) as exc:
                    message = str(exc)
                    if message != self._last_error:
                        self.status_callback("error", message)
                        self._last_error = message
                    self._connected = False
                    delay = min(max(delay * 1.8, 2.0), 30.0)
                except Exception as exc:
                    message = f"同步出现意外错误：{exc}"
                    if message != self._last_error:
                        self.status_callback("error", message)
                        self._last_error = message
                    self._connected = False
                    delay = min(max(delay * 1.8, 2.0), 30.0)
            self._wake.wait(delay if self.config.auto_sync else 60.0)
            self._wake.clear()

    def _sync_once(self) -> None:
        with self._lock:
            sequence = self.clipboard.sequence_number()
            if not self._initialized:
                if sequence == self._last_sequence:
                    revision, remote = self._client.fetch(self._revision)
                    if self.clipboard.sequence_number() != sequence:
                        return
                    self._initialized = True
                    if remote is not None and remote.origin != self.config.device_id:
                        self._apply_remote(remote, sequence)
                        self._revision = revision
                        return
                    self._revision = revision
                    self._last_sequence = -1
                else:
                    self._initialized = True

                sequence = self.clipboard.sequence_number()
            if sequence != self._last_sequence:
                captured_sequence, item = self._capture_stable()
                if item is None:
                    self._last_sequence = captured_sequence
                elif self._consume_ignored_signature(item.signature):
                    self._last_signature = item.signature
                    self._last_sequence = captured_sequence
                elif item.signature == self._last_signature:
                    self._last_sequence = captured_sequence
                else:
                    self._revision = self._client.push(item)
                    self._last_signature = item.signature
                    self._last_sequence = captured_sequence
                    self.status_callback(
                        "success",
                        f"已自动发送{'图片' if item.kind == 'image' else '文本'}",
                    )
                    return

            observed_sequence = self.clipboard.sequence_number()
            revision, remote = self._client.fetch(self._revision)
            if self.clipboard.sequence_number() != observed_sequence:
                return
            if remote is not None and remote.origin != self.config.device_id:
                self._apply_remote(remote, observed_sequence)
            self._revision = revision

    def _capture_stable(self) -> tuple[int, ClipItem | None]:
        for _attempt in range(2):
            sequence = self.clipboard.sequence_number()
            item = self.clipboard.capture(self.config.device_id, self.config.max_bytes)
            if self.clipboard.sequence_number() == sequence:
                return sequence, item
        raise ClipboardError("读取期间剪贴板持续变化，请稍后重试")

    def _consume_ignored_signature(self, signature: str) -> bool:
        with self._ignore_lock:
            ignored = self._ignored_signature
            if ignored is None:
                return False
            self._ignored_signature = None
            return signature == ignored
