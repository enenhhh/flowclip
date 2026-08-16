from __future__ import annotations

import io
import sys
import unittest
from pathlib import Path
from unittest.mock import patch

from PIL import Image


SOURCE = Path(__file__).resolve().parents[1] / "src"
sys.path.insert(0, str(SOURCE))

from flowclip import clipboard_linux as linux_module  # noqa: E402
from flowclip.clipboard import ClipboardError  # noqa: E402
from flowclip.clipboard_linux import LinuxClipboard  # noqa: E402
from flowclip.clipboard_macos import MacClipboard  # noqa: E402
from flowclip.model import ClipItem  # noqa: E402


def image_bytes(color: tuple[int, int, int] = (17, 34, 51)) -> bytes:
    output = io.BytesIO()
    with Image.new("RGB", (3, 2), color) as image:
        image.save(output, format="PNG")
    return output.getvalue()


def text_item(value: str) -> ClipItem:
    return ClipItem.create(
        origin="remote",
        kind="text",
        mime="text/plain; charset=utf-8",
        data=value.encode("utf-8"),
    )


def image_item(data: bytes) -> ClipItem:
    return ClipItem.create(
        origin="remote",
        kind="image",
        mime="image/png",
        filename="remote.png",
        data=data,
    )


def tools_available(*names: str):
    available = set(names)
    return lambda name: f"/usr/bin/{name}" if name in available else None


class FakeLinuxRunner:
    def __init__(self, types: list[str], payloads: dict[str, bytes]) -> None:
        self.types = list(types)
        self.payloads = dict(payloads)
        self.calls: list[tuple[list[str], bytes | None, int]] = []

    def __call__(
        self, arguments: list[str], input_data: bytes | None, limit: int
    ) -> bytes:
        self.calls.append((list(arguments), input_data, limit))
        if arguments == ["wl-paste", "--list-types"]:
            return "\n".join(self.types).encode("ascii")
        if arguments[:3] == ["wl-paste", "--no-newline", "--type"]:
            return self.payloads[arguments[3]]
        if arguments == [
            "xclip",
            "-selection",
            "clipboard",
            "-target",
            "TARGETS",
            "-out",
        ]:
            return "\n".join(self.types).encode("ascii")
        if (
            arguments[:4] == ["xclip", "-selection", "clipboard", "-target"]
            and arguments[-1] == "-out"
        ):
            return self.payloads[arguments[4]]
        if arguments[:2] == ["wl-copy", "--type"]:
            self._store(arguments[2], input_data)
            return b""
        if (
            arguments[:4] == ["xclip", "-selection", "clipboard", "-target"]
            and arguments[-1] == "-in"
        ):
            self._store(arguments[4], input_data)
            return b""
        raise AssertionError(f"unexpected clipboard command: {arguments!r}")

    def _store(self, mime: str, input_data: bytes | None) -> None:
        if input_data is None:
            raise AssertionError("clipboard write did not provide stdin")
        self.types = [mime]
        self.payloads = {mime: input_data}

    def set_text(self, value: str) -> None:
        mime = "text/plain;charset=utf-8"
        self.types = [mime]
        self.payloads = {mime: value.encode("utf-8")}

    @property
    def writes(self) -> list[tuple[list[str], bytes | None, int]]:
        return [
            call
            for call in self.calls
            if call[0][0] == "wl-copy" or call[0][-1] == "-in"
        ]


class FakePasteboard:
    def __init__(
        self,
        *,
        change_count: int = 0,
        text: str | None = None,
        image_type: str | None = None,
        image_data: bytes | None = None,
    ) -> None:
        self.change_count = change_count
        self.text = text
        self.image_type = image_type
        self.image_data = image_data
        self.clear_calls = 0
        self.text_writes: list[tuple[str, str]] = []
        self.data_writes: list[tuple[object, str]] = []

    def changeCount(self) -> int:
        return self.change_count

    def availableTypeFromArray_(self, candidates: list[str]) -> str | None:
        if self.image_type in candidates:
            return self.image_type
        return None

    def dataForType_(self, mime: str) -> bytes | None:
        return self.image_data if mime == self.image_type else None

    def stringForType_(self, _mime: str) -> str | None:
        return self.text

    def clearContents(self) -> int:
        self.clear_calls += 1
        self.change_count += 1
        self.text = None
        self.image_type = None
        self.image_data = None
        return self.change_count

    def setString_forType_(self, value: str, mime: str) -> bool:
        self.text_writes.append((value, mime))
        self.text = value
        self.change_count += 1
        return True

    def setData_forType_(self, value: object, mime: str) -> bool:
        self.data_writes.append((value, mime))
        self.image_type = mime
        self.change_count += 1
        return True

    def external_text_change(self, value: str) -> None:
        self.text = value
        self.image_type = None
        self.image_data = None
        self.change_count += 1


class LinuxClipboardTests(unittest.TestCase):
    def test_wayland_is_preferred_and_x11_is_used_as_fallback(self) -> None:
        wayland = LinuxClipboard(
            environment={"WAYLAND_DISPLAY": "wayland-0", "DISPLAY": ":0"},
            which=tools_available("wl-copy", "wl-paste", "xclip"),
            runner=FakeLinuxRunner([], {}),
        )
        self.assertEqual(wayland.mode, "wayland")

        x11 = LinuxClipboard(
            environment={"WAYLAND_DISPLAY": "wayland-0", "DISPLAY": ":0"},
            which=tools_available("wl-paste", "xclip"),
            runner=FakeLinuxRunner([], {}),
        )
        self.assertEqual(x11.mode, "x11")

    def test_wayland_captures_text_and_prefers_an_available_image(self) -> None:
        runner = FakeLinuxRunner(
            ["text/plain;charset=utf-8"],
            {"text/plain;charset=utf-8": "跨平台文本".encode("utf-8")},
        )
        clipboard = LinuxClipboard(
            environment={"WAYLAND_DISPLAY": "wayland-0"},
            which=tools_available("wl-copy", "wl-paste"),
            runner=runner,
        )
        with patch.object(linux_module, "_CACHE_SECONDS", 0.0):
            captured = clipboard.capture("linux-device", 1024 * 1024)
            self.assertIsNotNone(captured)
            assert captured is not None
            self.assertEqual(captured.kind, "text")
            self.assertEqual(captured.data.decode("utf-8"), "跨平台文本")

            png = image_bytes()
            runner.types = ["text/plain;charset=utf-8", "image/png"]
            runner.payloads["image/png"] = png
            captured = clipboard.capture("linux-device", 1024 * 1024)

        self.assertIsNotNone(captured)
        assert captured is not None
        self.assertEqual(captured.kind, "image")
        self.assertNotIn(
            ["wl-paste", "--no-newline", "--type", "text/plain;charset=utf-8"],
            [arguments for arguments, _data, _limit in runner.calls[-2:]],
        )
        with Image.open(io.BytesIO(captured.data)) as image:
            self.assertEqual(image.convert("RGB").getpixel((1, 1)), (17, 34, 51))

    def test_sequence_is_stable_until_content_changes(self) -> None:
        runner = FakeLinuxRunner([], {})
        runner.set_text("first")
        clipboard = LinuxClipboard(
            environment={"DISPLAY": ":0"},
            which=tools_available("xclip"),
            runner=runner,
        )
        with patch.object(linux_module, "_CACHE_SECONDS", 0.0):
            self.assertEqual(clipboard.sequence_number(), 0)
            self.assertEqual(clipboard.sequence_number(), 0)
            runner.set_text("second")
            self.assertEqual(clipboard.sequence_number(), 1)
            self.assertEqual(clipboard.sequence_number(), 1)
            runner.types = []
            runner.payloads = {}
            self.assertEqual(clipboard.sequence_number(), 2)

    def test_changed_sequence_rejects_apply_before_any_write(self) -> None:
        runner = FakeLinuxRunner([], {})
        runner.set_text("observed")
        clipboard = LinuxClipboard(
            environment={"WAYLAND_DISPLAY": "wayland-0"},
            which=tools_available("wl-copy", "wl-paste"),
            runner=runner,
        )
        self.assertEqual(clipboard.sequence_number(), 0)
        runner.set_text("changed by user")

        with self.assertRaisesRegex(ClipboardError, "未覆盖"):
            clipboard.apply(text_item("remote"), expected_sequence=0)

        self.assertEqual(runner.writes, [])

    def test_wayland_text_write_uses_exact_argv_and_stdin(self) -> None:
        runner = FakeLinuxRunner([], {})
        runner.set_text("before")
        clipboard = LinuxClipboard(
            environment={"WAYLAND_DISPLAY": "wayland-0"},
            which=tools_available("wl-copy", "wl-paste"),
            runner=runner,
        )

        revision = clipboard.apply(text_item("after"))

        self.assertEqual(revision, 1)
        self.assertEqual(
            runner.writes,
            [
                (
                    ["wl-copy", "--type", "text/plain;charset=utf-8"],
                    b"after",
                    64 * 1024,
                )
            ],
        )

    def test_x11_image_write_uses_exact_argv_and_png_stdin(self) -> None:
        runner = FakeLinuxRunner([], {})
        runner.set_text("before")
        clipboard = LinuxClipboard(
            environment={"DISPLAY": ":0"},
            which=tools_available("xclip"),
            runner=runner,
        )

        clipboard.apply(image_item(image_bytes((90, 80, 70))))

        self.assertEqual(len(runner.writes), 1)
        arguments, written, limit = runner.writes[0]
        self.assertEqual(
            arguments,
            [
                "xclip",
                "-selection",
                "clipboard",
                "-target",
                "image/png",
                "-in",
            ],
        )
        self.assertEqual(limit, 64 * 1024)
        self.assertIsNotNone(written)
        assert written is not None
        with Image.open(io.BytesIO(written)) as image:
            self.assertEqual(image.convert("RGB").getpixel((1, 1)), (90, 80, 70))

    def test_missing_session_or_tools_reports_install_guidance(self) -> None:
        cases = (
            ({}, tools_available("wl-copy", "wl-paste", "xclip")),
            ({"WAYLAND_DISPLAY": "wayland-0"}, tools_available("wl-paste")),
            ({"DISPLAY": ":0"}, tools_available()),
        )
        for environment, which in cases:
            with self.subTest(environment=environment):
                with self.assertRaisesRegex(
                    ClipboardError, "wl-clipboard.*xclip"
                ):
                    LinuxClipboard(environment=environment, which=which)


class MacClipboardTests(unittest.TestCase):
    def test_captures_unicode_text(self) -> None:
        pasteboard = FakePasteboard(text="macOS 文本")
        clipboard = MacClipboard(pasteboard=pasteboard, data_factory=lambda data: data)

        captured = clipboard.capture("mac-device", 1024)

        self.assertIsNotNone(captured)
        assert captured is not None
        self.assertEqual(captured.kind, "text")
        self.assertEqual(captured.data.decode("utf-8"), "macOS 文本")

    def test_image_capture_takes_priority_over_text(self) -> None:
        pasteboard = FakePasteboard(
            text="fallback",
            image_type=MacClipboard.PNG_TYPE,
            image_data=image_bytes((1, 2, 3)),
        )
        clipboard = MacClipboard(pasteboard=pasteboard, data_factory=lambda data: data)

        captured = clipboard.capture("mac-device", 1024 * 1024)

        self.assertIsNotNone(captured)
        assert captured is not None
        self.assertEqual(captured.kind, "image")
        with Image.open(io.BytesIO(captured.data)) as image:
            self.assertEqual(image.convert("RGB").getpixel((1, 1)), (1, 2, 3))

    def test_applies_text_and_image_through_native_types(self) -> None:
        text_board = FakePasteboard(change_count=4, text="before")
        text_clipboard = MacClipboard(
            pasteboard=text_board, data_factory=lambda data: data
        )
        written_sequence = text_clipboard.apply(
            text_item("mac text"), expected_sequence=4
        )
        self.assertEqual(written_sequence, 6)
        self.assertEqual(
            text_board.text_writes,
            [("mac text", MacClipboard.STRING_TYPE)],
        )

        factory_calls: list[bytes] = []

        def data_factory(data: bytes) -> object:
            factory_calls.append(data)
            return ("native-data", data)

        image_board = FakePasteboard(change_count=9)
        image_clipboard = MacClipboard(
            pasteboard=image_board, data_factory=data_factory
        )
        image_clipboard.apply(image_item(image_bytes((7, 8, 9))), expected_sequence=9)

        self.assertEqual(len(factory_calls), 1)
        self.assertEqual(
            image_board.data_writes,
            [(('native-data', factory_calls[0]), MacClipboard.PNG_TYPE)],
        )
        with Image.open(io.BytesIO(factory_calls[0])) as image:
            self.assertEqual(image.convert("RGB").getpixel((1, 1)), (7, 8, 9))

    def test_change_count_race_rejects_apply_without_clearing(self) -> None:
        pasteboard = FakePasteboard(change_count=12, text="observed")
        clipboard = MacClipboard(pasteboard=pasteboard, data_factory=lambda data: data)
        observed = clipboard.sequence_number()
        pasteboard.external_text_change("new local content")

        with self.assertRaisesRegex(ClipboardError, "未覆盖"):
            clipboard.apply(text_item("remote"), expected_sequence=observed)

        self.assertEqual(pasteboard.clear_calls, 0)
        self.assertEqual(pasteboard.text_writes, [])
        self.assertEqual(pasteboard.text, "new local content")


if __name__ == "__main__":
    unittest.main()
