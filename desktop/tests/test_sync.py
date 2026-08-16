from __future__ import annotations

import sys
import unittest
from pathlib import Path
from typing import Callable


SOURCE = Path(__file__).resolve().parents[1] / "src"
sys.path.insert(0, str(SOURCE))

from flowclip.clipboard import ClipboardError  # noqa: E402
from flowclip.config import AppConfig  # noqa: E402
from flowclip.model import ClipItem  # noqa: E402
from flowclip.sync import SyncEngine  # noqa: E402


def text_item(origin: str, text: str) -> ClipItem:
    return ClipItem.create(
        origin=origin,
        kind="text",
        mime="text/plain; charset=utf-8",
        data=text.encode("utf-8"),
    )


class FakeClipboard:
    def __init__(self, item: ClipItem | None = None) -> None:
        self.sequence = 0
        self.item = item
        self.applied: list[ClipItem] = []

    def sequence_number(self) -> int:
        return self.sequence

    def capture(self, _origin: str, _max_bytes: int) -> ClipItem | None:
        return self.item

    def replace(self, item: ClipItem) -> None:
        self.item = item
        self.sequence += 1

    def apply(self, item: ClipItem, expected_sequence: int | None = None) -> int:
        if expected_sequence is not None and expected_sequence != self.sequence:
            raise ClipboardError("本机剪贴板已变化，未覆盖新内容")
        self.item = item
        self.sequence += 1
        self.applied.append(item)
        return self.sequence


class FakeClient:
    def __init__(self) -> None:
        self.revision = 0
        self.remote: ClipItem | None = None
        self.pushed: list[ClipItem] = []
        self.fetch_calls = 0
        self.on_push: Callable[[], None] | None = None
        self.on_fetch: Callable[[], None] | None = None

    def push(self, item: ClipItem) -> int:
        self.pushed.append(item)
        self.revision += 1
        self.remote = item
        if self.on_push is not None:
            self.on_push()
        return self.revision

    def fetch(self, after: int) -> tuple[int, ClipItem | None]:
        self.fetch_calls += 1
        item = self.remote if self.revision != after else None
        if self.on_fetch is not None:
            self.on_fetch()
        return self.revision, item


class SyncEngineTests(unittest.TestCase):
    def make_engine(
        self, clipboard: FakeClipboard, client: FakeClient
    ) -> SyncEngine:
        config = AppConfig.defaults()
        config.auto_sync = False
        engine = SyncEngine(config, clipboard, lambda _level, _message: None)
        engine._client = client
        return engine

    def test_first_sync_applies_remote_when_local_clipboard_is_unchanged(self) -> None:
        clipboard = FakeClipboard(text_item("local", "before"))
        client = FakeClient()
        client.remote = text_item("remote", "latest")
        client.revision = 1
        engine = self.make_engine(clipboard, client)

        engine._sync_once()

        self.assertEqual(clipboard.applied, [client.remote])
        self.assertEqual(client.pushed, [])

    def test_local_change_wins_before_remote_fetch(self) -> None:
        clipboard = FakeClipboard(text_item("local", "before"))
        client = FakeClient()
        client.remote = text_item("remote", "latest")
        client.revision = 1
        engine = self.make_engine(clipboard, client)
        local = text_item(engine.config.device_id, "new local")
        clipboard.replace(local)

        engine._sync_once()

        self.assertEqual(client.pushed, [local])
        self.assertEqual(client.fetch_calls, 0)
        self.assertEqual(clipboard.applied, [])

    def test_change_during_manual_upload_is_sent_on_next_sync(self) -> None:
        first = text_item("local", "first")
        clipboard = FakeClipboard(first)
        clipboard.sequence = 1
        client = FakeClient()
        engine = self.make_engine(clipboard, client)
        second = text_item(engine.config.device_id, "second")
        client.on_push = lambda: clipboard.replace(second)

        engine.send_now()
        client.on_push = None
        engine._sync_once()

        self.assertEqual(client.pushed, [first, second])

    def test_receive_does_not_overwrite_change_during_fetch(self) -> None:
        clipboard = FakeClipboard(text_item("local", "before"))
        client = FakeClient()
        client.remote = text_item("remote", "latest")
        client.revision = 1
        engine = self.make_engine(clipboard, client)
        client.on_fetch = lambda: clipboard.replace(
            text_item(engine.config.device_id, "typed while fetching")
        )

        with self.assertRaisesRegex(ClipboardError, "未覆盖"):
            engine.receive_now()

        self.assertEqual(clipboard.applied, [])

    def test_ignored_token_does_not_hide_a_different_user_change(self) -> None:
        clipboard = FakeClipboard()
        client = FakeClient()
        engine = self.make_engine(clipboard, client)
        engine.suppress_clipboard_text("secret-token")
        user_item = text_item(engine.config.device_id, "user text")
        clipboard.replace(user_item)

        engine._sync_once()

        self.assertEqual(client.pushed, [user_item])
