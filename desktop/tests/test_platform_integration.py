from __future__ import annotations

import json
import os
import plistlib
import stat
import subprocess
import sys
import tempfile
import unittest
from dataclasses import asdict
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock, patch


SOURCE = Path(__file__).resolve().parents[1] / "src"
sys.path.insert(0, str(SOURCE))

from flowclip import config as config_module  # noqa: E402
from flowclip import platform_integration as integration  # noqa: E402


class ConfigDirectoryTests(unittest.TestCase):
    def test_platform_config_directories(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory).resolve()
            home = root / "home"
            roaming = root / "roaming"
            xdg = root / "xdg"

            self.assertEqual(
                integration.user_config_directory(
                    "FlowClip",
                    platform="win32",
                    environment={"APPDATA": str(roaming)},
                    home=home,
                ),
                roaming / "FlowClip",
            )
            self.assertEqual(
                integration.user_config_directory(
                    "FlowClip",
                    platform="linux",
                    environment={"XDG_CONFIG_HOME": str(xdg)},
                    home=home,
                ),
                xdg / "FlowClip",
            )
            self.assertEqual(
                integration.user_config_directory(
                    "FlowClip", platform="darwin", environment={}, home=home
                ),
                home / "Library" / "Application Support" / "FlowClip",
            )

    def test_relative_xdg_path_is_ignored(self) -> None:
        home = Path("home-root")
        self.assertEqual(
            integration.user_config_directory(
                "FlowClip",
                platform="linux",
                environment={"XDG_CONFIG_HOME": "relative/path"},
                home=home,
            ),
            home / ".config" / "FlowClip",
        )

    def test_legacy_config_is_copied_to_canonical_location(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            canonical = root / "canonical" / "FlowClip"
            legacy = root / ".flowclip"
            legacy.mkdir()
            raw = asdict(config_module.AppConfig.defaults())
            raw["device_name"] = "Migrated device"
            (legacy / "config.json").write_text(
                json.dumps(raw, ensure_ascii=False), encoding="utf-8"
            )

            with (
                patch.object(
                    config_module,
                    "user_config_directory",
                    return_value=canonical,
                ),
                patch.object(
                    config_module,
                    "legacy_config_directories",
                    return_value=(legacy,),
                ),
            ):
                loaded = config_module.load_config()

            self.assertEqual(loaded.device_name, "Migrated device")
            self.assertTrue((canonical / "config.json").is_file())
            self.assertTrue((legacy / "config.json").is_file())

    def test_default_device_fallback_is_platform_neutral(self) -> None:
        with patch.object(config_module.socket, "gethostname", return_value=""):
            self.assertEqual(config_module.AppConfig.defaults().device_name, "FlowClip")


class StartupIntegrationTests(unittest.TestCase):
    def test_linux_autostart_round_trip_and_exec_escaping(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "autostart" / "flowclip.desktop"
            arguments = ["/opt/Flow Clip/100%$/flowclip", "--minimized"]
            with (
                patch.object(integration.sys, "platform", "linux"),
                patch.object(integration, "startup_arguments", return_value=arguments),
                patch.object(
                    integration, "_linux_autostart_path", return_value=path
                ),
            ):
                integration.set_run_at_startup(True)
                contents = path.read_text(encoding="utf-8")
                self.assertIn("[Desktop Entry]", contents)
                self.assertIn("100%%\\$", contents)
                self.assertIn('"--minimized"', contents)
                integration.set_run_at_startup(False)

            self.assertFalse(path.exists())

    def test_macos_launch_agent_round_trip(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "LaunchAgents" / "flowclip.plist"
            arguments = ["/Applications/FlowClip.app/Contents/MacOS/FlowClip", "--minimized"]
            with (
                patch.object(integration.sys, "platform", "darwin"),
                patch.object(integration, "startup_arguments", return_value=arguments),
                patch.object(
                    integration, "_macos_launch_agent_path", return_value=path
                ),
            ):
                integration.set_run_at_startup(True)
                payload = plistlib.loads(path.read_bytes())
                self.assertEqual(payload["Label"], integration.APP_ID)
                self.assertEqual(payload["ProgramArguments"], arguments)
                self.assertTrue(payload["RunAtLoad"])
                integration.set_run_at_startup(False)

            self.assertFalse(path.exists())

    def test_windows_registry_uses_quoted_command_line(self) -> None:
        context = MagicMock()
        key = object()
        context.__enter__.return_value = key
        fake_winreg = SimpleNamespace(
            HKEY_CURRENT_USER=object(),
            KEY_SET_VALUE=2,
            REG_SZ=1,
            CreateKeyEx=MagicMock(return_value=context),
            SetValueEx=MagicMock(),
            DeleteValue=MagicMock(),
        )
        arguments = [r"C:\Program Files\FlowClip\FlowClip.exe", "--minimized"]

        with patch.dict(sys.modules, {"winreg": fake_winreg}):
            integration._set_windows_startup(True, arguments)

        written = fake_winreg.SetValueEx.call_args.args[4]
        self.assertEqual(written, subprocess.list2cmdline(arguments))

    def test_startup_write_failure_is_not_silent(self) -> None:
        with (
            patch.object(
                integration, "_linux_autostart_path", return_value=Path("autostart")
            ),
            patch.object(
                integration,
                "write_private_file",
                side_effect=PermissionError("denied"),
            ),
        ):
            with self.assertRaisesRegex(
                integration.StartupIntegrationError, "Linux.*denied"
            ):
                integration._set_linux_startup(True, ["flowclip", "--minimized"])

    def test_unknown_platform_reports_unsupported_startup(self) -> None:
        with (
            patch.object(integration.sys, "platform", "unsupported-os"),
            patch.object(integration, "startup_arguments", return_value=["flowclip"]),
        ):
            with self.assertRaisesRegex(
                integration.StartupIntegrationError, "unsupported-os"
            ):
                integration.set_run_at_startup(True)


class WindowsInstanceNameTests(unittest.TestCase):
    def test_default_name_preserves_release_single_instance_scope(self) -> None:
        self.assertEqual(
            integration._windows_instance_mutex_name({}),
            "Local\\FlowClip.Desktop.1",
        )

    def test_same_scope_produces_stable_name(self) -> None:
        environment = {"FLOWCLIP_INSTANCE_SCOPE": "product-smoke-test"}
        self.assertEqual(
            integration._windows_instance_mutex_name(environment),
            integration._windows_instance_mutex_name(environment),
        )

    def test_different_scopes_produce_different_names(self) -> None:
        first = integration._windows_instance_mutex_name(
            {"FLOWCLIP_INSTANCE_SCOPE": "first"}
        )
        second = integration._windows_instance_mutex_name(
            {"FLOWCLIP_INSTANCE_SCOPE": "second"}
        )
        self.assertNotEqual(first, second)
        self.assertRegex(first, r"^Local\\FlowClip\.Desktop\.1\.[0-9a-f]{16}$")


@unittest.skipIf(os.name == "nt", "POSIX permission and flock semantics")
class PosixIntegrationTests(unittest.TestCase):
    def tearDown(self) -> None:
        integration.release_single_instance()

    def test_private_config_permissions(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            config_directory = Path(directory) / "FlowClip"
            integration.ensure_private_directory(config_directory)
            path = config_directory / "config.json"
            integration.write_private_file(path, b"{}")

            self.assertEqual(stat.S_IMODE(config_directory.stat().st_mode), 0o700)
            self.assertEqual(stat.S_IMODE(path.stat().st_mode), 0o600)

    def test_unix_lock_detects_an_existing_owner(self) -> None:
        import fcntl

        with tempfile.TemporaryDirectory() as directory:
            config_directory = Path(directory) / "FlowClip"
            integration.ensure_private_directory(config_directory)
            lock_path = config_directory / ".instance.lock"
            external = os.open(lock_path, os.O_RDWR | os.O_CREAT, 0o600)
            try:
                fcntl.flock(external, fcntl.LOCK_EX | fcntl.LOCK_NB)
                with patch.object(
                    integration,
                    "user_config_directory",
                    return_value=config_directory,
                ):
                    self.assertFalse(
                        integration._acquire_unix_single_instance("FlowClip")
                    )
            finally:
                fcntl.flock(external, fcntl.LOCK_UN)
                os.close(external)

            with patch.object(
                integration, "user_config_directory", return_value=config_directory
            ):
                self.assertTrue(integration._acquire_unix_single_instance("FlowClip"))
            self.assertEqual(lock_path.read_text(encoding="ascii").strip(), str(os.getpid()))


if __name__ == "__main__":
    unittest.main()
