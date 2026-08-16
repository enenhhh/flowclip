from __future__ import annotations

import importlib.util
import sys
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch


DESKTOP = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location("flowclip_build", DESKTOP / "build.py")
assert SPEC is not None and SPEC.loader is not None
build = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = build
SPEC.loader.exec_module(build)


def load_spec_calls(platform: str) -> dict[str, list[dict[str, object]]]:
    source_path = DESKTOP / "FlowClip.spec"
    source = source_path.read_text(encoding="utf-8")
    calls: dict[str, list[dict[str, object]]] = {}

    def recorder(name: str):
        def record(*args: object, **kwargs: object) -> object:
            calls.setdefault(name, []).append({"args": args, "kwargs": kwargs})
            if name == "Analysis":
                return SimpleNamespace(pure=[], scripts=[], binaries=[], datas=[])
            return object()

        return record

    namespace = {
        "SPECPATH": str(DESKTOP),
        "Analysis": recorder("Analysis"),
        "PYZ": recorder("PYZ"),
        "EXE": recorder("EXE"),
        "COLLECT": recorder("COLLECT"),
        "BUNDLE": recorder("BUNDLE"),
    }
    original_path = sys.path.copy()
    try:
        with patch.object(sys, "platform", platform):
            exec(compile(source, str(source_path), "exec"), namespace)
    finally:
        sys.path[:] = original_path
    return calls


class BuildScriptTests(unittest.TestCase):
    def test_supported_platform_families_are_explicit(self) -> None:
        self.assertEqual(build.platform_family("win32"), "windows")
        self.assertEqual(build.platform_family("darwin"), "macos")
        self.assertEqual(build.platform_family("linux"), "linux")
        with self.assertRaisesRegex(RuntimeError, "Unsupported build platform"):
            build.platform_family("freebsd14")

    def test_build_uses_controlled_spec_in_external_work_directory(self) -> None:
        output = Path("D:/release")
        work = Path("D:/build")
        command = build.build_command(output, work)
        self.assertEqual(command[-1], str(DESKTOP / "FlowClip.spec"))
        self.assertEqual(command[command.index("--distpath") + 1], str(output))
        self.assertEqual(
            command[command.index("--workpath") + 1], str(work / "pyinstaller")
        )

    def test_spec_sets_both_macos_bundle_versions(self) -> None:
        source = (DESKTOP / "FlowClip.spec").read_text(encoding="utf-8")
        self.assertIn("version=__version__", source)
        self.assertIn('info_plist={"CFBundleVersion": __version__}', source)

    def test_windows_spec_excludes_only_platform_safe_modules(self) -> None:
        calls = load_spec_calls("win32")
        analysis = calls["Analysis"][0]["kwargs"]
        excluded = set(analysis["excludes"])
        self.assertEqual(
            excluded,
            {
                "PIL.ImageCms",
                "PIL.ImageFont",
                "PIL.ImageMath",
                "PIL.ImageTk",
                "flowclip.clipboard_linux",
                "flowclip.clipboard_macos",
                "packaging",
                "pkg_resources",
                "pystray._appindicator",
                "pystray._darwin",
                "pystray._dummy",
                "pystray._gtk",
                "pystray._util.gtk",
                "pystray._util.notify_dbus",
                "pystray._xorg",
            },
        )
        self.assertTrue(
            {
                "PIL.WebPImagePlugin",
                "PIL._webp",
                "PIL.PngImagePlugin",
                "PIL.JpegImagePlugin",
                "PIL.GifImagePlugin",
                "PIL.BmpImagePlugin",
                "PIL.ImageGrab",
                "ssl",
                "_ssl",
                "tkinter",
                "_tkinter",
                "pystray._win32",
                "flowclip.clipboard_windows",
            }.isdisjoint(excluded)
        )
        self.assertEqual(analysis["hiddenimports"], ["pystray._win32"])

    def test_non_windows_specs_do_not_apply_windows_exclusions(self) -> None:
        for platform in ("darwin", "linux"):
            with self.subTest(platform=platform):
                calls = load_spec_calls(platform)
                analysis = calls["Analysis"][0]["kwargs"]
                self.assertEqual(analysis["excludes"], [])

    def test_upx_is_disabled_for_every_native_package(self) -> None:
        for platform in ("win32", "darwin", "linux"):
            with self.subTest(platform=platform):
                calls = load_spec_calls(platform)
                for call in calls.get("EXE", []) + calls.get("COLLECT", []):
                    self.assertIs(call["kwargs"]["upx"], False)

    def test_windows_remains_onefile_with_version_resources(self) -> None:
        calls = load_spec_calls("win32")
        self.assertNotIn("COLLECT", calls)
        exe = calls["EXE"][0]["kwargs"]
        self.assertEqual(
            exe["version"], str(DESKTOP / "assets" / "version_info.txt")
        )
        self.assertEqual(exe["icon"], [str(DESKTOP / "assets" / "flowclip.ico")])

    def test_pyinstaller_cache_follows_work_directory(self) -> None:
        work = Path("D:/build")
        environment = build.build_environment(work)
        self.assertEqual(
            environment["PYINSTALLER_CONFIG_DIR"],
            str(work / "pyinstaller-config"),
        )


if __name__ == "__main__":
    unittest.main()
