from __future__ import annotations

import atexit
import ctypes
import errno
import hashlib
import os
import plistlib
import subprocess
import sys
import tempfile
from collections.abc import Mapping, Sequence
from pathlib import Path


APP_ID = "io.github.flowclip.FlowClip"
WINDOWS_RUN_KEY = r"Software\Microsoft\Windows\CurrentVersion\Run"


class PlatformIntegrationError(RuntimeError):
    pass


class StartupIntegrationError(PlatformIntegrationError):
    pass


class SingleInstanceError(PlatformIntegrationError):
    pass


def _xdg_config_home(
    *,
    environment: Mapping[str, str] | None = None,
    home: Path | None = None,
) -> Path:
    values = os.environ if environment is None else environment
    value = values.get("XDG_CONFIG_HOME", "")
    if value:
        candidate = Path(value)
        if candidate.is_absolute():
            return candidate
    return (Path.home() if home is None else home) / ".config"


def user_config_directory(
    app_name: str,
    *,
    platform: str | None = None,
    environment: Mapping[str, str] | None = None,
    home: Path | None = None,
) -> Path:
    current_platform = sys.platform if platform is None else platform
    values = os.environ if environment is None else environment
    user_home = Path.home() if home is None else home

    if current_platform == "win32":
        roaming = values.get("APPDATA", "")
        base = Path(roaming) if roaming else user_home / "AppData" / "Roaming"
        return base / app_name
    if current_platform == "darwin":
        return user_home / "Library" / "Application Support" / app_name
    return _xdg_config_home(environment=values, home=user_home) / app_name


def legacy_config_directories(
    app_name: str,
    *,
    canonical: Path | None = None,
    environment: Mapping[str, str] | None = None,
    home: Path | None = None,
) -> tuple[Path, ...]:
    values = os.environ if environment is None else environment
    user_home = Path.home() if home is None else home
    candidates: list[Path] = []
    appdata = values.get("APPDATA", "")
    if appdata:
        candidates.append(Path(appdata) / app_name)
    candidates.append(user_home / f".{app_name.lower()}")

    result: list[Path] = []
    for candidate in candidates:
        if candidate == canonical or candidate in result:
            continue
        result.append(candidate)
    return tuple(result)


def ensure_private_directory(path: Path) -> None:
    path.mkdir(parents=True, exist_ok=True)
    if os.name != "nt":
        path.chmod(0o700)


def write_private_file(path: Path, data: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary_name = tempfile.mkstemp(
        prefix=f".{path.name}.", suffix=".tmp", dir=path.parent
    )
    temporary = Path(temporary_name)
    try:
        if os.name != "nt":
            os.fchmod(descriptor, 0o600)
        with os.fdopen(descriptor, "wb") as output:
            descriptor = -1
            output.write(data)
            output.flush()
            os.fsync(output.fileno())
        os.replace(temporary, path)
        if os.name != "nt":
            path.chmod(0o600)
    except Exception:
        if descriptor >= 0:
            os.close(descriptor)
        try:
            temporary.unlink()
        except FileNotFoundError:
            pass
        raise


def secure_private_file(path: Path) -> None:
    if os.name != "nt" and path.exists():
        path.chmod(0o600)


def startup_arguments() -> list[str]:
    executable = Path(sys.executable).resolve()
    if getattr(sys, "frozen", False):
        return [str(executable), "--minimized"]

    if sys.platform == "win32" and executable.name.lower() == "python.exe":
        pythonw = executable.with_name("pythonw.exe")
        if pythonw.exists():
            executable = pythonw
    entry = Path(sys.argv[0]).resolve()
    return [str(executable), str(entry), "--minimized"]


def _desktop_exec_argument(value: str) -> str:
    # Desktop Entry Exec fields are parsed without a shell, but still reserve
    # percent field codes and a small set of characters inside quoted arguments.
    escaped = (
        value.replace("\\", "\\\\")
        .replace('"', '\\"')
        .replace("`", "\\`")
        .replace("$", "\\$")
        .replace("%", "%%")
    )
    return f'"{escaped}"'


def _linux_autostart_path() -> Path:
    return _xdg_config_home() / "autostart" / f"{APP_ID}.desktop"


def _macos_launch_agent_path() -> Path:
    return Path.home() / "Library" / "LaunchAgents" / f"{APP_ID}.plist"


def _set_windows_startup(enabled: bool, arguments: Sequence[str]) -> None:
    import winreg

    try:
        with winreg.CreateKeyEx(
            winreg.HKEY_CURRENT_USER, WINDOWS_RUN_KEY, 0, winreg.KEY_SET_VALUE
        ) as key:
            if enabled:
                winreg.SetValueEx(
                    key,
                    "FlowClip",
                    0,
                    winreg.REG_SZ,
                    subprocess.list2cmdline(list(arguments)),
                )
            else:
                try:
                    winreg.DeleteValue(key, "FlowClip")
                except FileNotFoundError:
                    pass
    except OSError as exc:
        raise StartupIntegrationError(f"无法更新 Windows 登录自启动：{exc}") from exc


def _set_linux_startup(enabled: bool, arguments: Sequence[str]) -> None:
    path = _linux_autostart_path()
    try:
        if not enabled:
            path.unlink(missing_ok=True)
            return
        rendered_arguments = " ".join(_desktop_exec_argument(value) for value in arguments)
        contents = (
            "[Desktop Entry]\n"
            "Type=Application\n"
            "Version=1.0\n"
            "Name=FlowClip\n"
            "Comment=Synchronize clipboard text and images\n"
            f"Exec={rendered_arguments}\n"
            "Terminal=false\n"
            "X-GNOME-Autostart-enabled=true\n"
        )
        write_private_file(path, contents.encode("utf-8"))
    except OSError as exc:
        raise StartupIntegrationError(f"无法更新 Linux 登录自启动：{exc}") from exc


def _set_macos_startup(enabled: bool, arguments: Sequence[str]) -> None:
    path = _macos_launch_agent_path()
    try:
        if not enabled:
            path.unlink(missing_ok=True)
            return
        payload = {
            "Label": APP_ID,
            "LimitLoadToSessionType": "Aqua",
            "ProcessType": "Interactive",
            "ProgramArguments": list(arguments),
            "RunAtLoad": True,
        }
        write_private_file(path, plistlib.dumps(payload, fmt=plistlib.FMT_XML))
    except (OSError, TypeError, ValueError) as exc:
        raise StartupIntegrationError(f"无法更新 macOS 登录自启动：{exc}") from exc


def set_run_at_startup(enabled: bool) -> None:
    arguments = startup_arguments()
    if sys.platform == "win32":
        _set_windows_startup(enabled, arguments)
    elif sys.platform == "darwin":
        _set_macos_startup(enabled, arguments)
    elif sys.platform.startswith(("linux", "freebsd")):
        _set_linux_startup(enabled, arguments)
    else:
        raise StartupIntegrationError(f"当前系统不支持登录自启动：{sys.platform}")


_instance_handle: int | None = None
_instance_kernel32: object | None = None
_instance_descriptor: int | None = None


def _windows_instance_mutex_name(
    environment: Mapping[str, str] | None = None,
) -> str:
    values = os.environ if environment is None else environment
    name = "Local\\FlowClip.Desktop.1"
    scope = values.get("FLOWCLIP_INSTANCE_SCOPE")
    if not scope:
        return name
    digest = hashlib.sha256(scope.encode("utf-8")).hexdigest()[:16]
    return f"{name}.{digest}"


def _acquire_windows_single_instance() -> bool:
    global _instance_handle, _instance_kernel32
    if _instance_handle is not None:
        return True

    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel32.CreateMutexW.argtypes = [ctypes.c_void_p, ctypes.c_bool, ctypes.c_wchar_p]
    kernel32.CreateMutexW.restype = ctypes.c_void_p
    kernel32.CloseHandle.argtypes = [ctypes.c_void_p]
    kernel32.CloseHandle.restype = ctypes.c_bool
    ctypes.set_last_error(0)
    handle = kernel32.CreateMutexW(None, False, _windows_instance_mutex_name())
    error = ctypes.get_last_error()
    if not handle:
        raise SingleInstanceError(f"无法创建单实例锁（Windows 错误 {error}）")
    if error == 183:
        kernel32.CloseHandle(handle)
        return False
    _instance_handle = int(handle)
    _instance_kernel32 = kernel32
    return True


def _acquire_unix_single_instance(app_name: str) -> bool:
    global _instance_descriptor
    if _instance_descriptor is not None:
        return True

    try:
        import fcntl
    except ImportError as exc:
        raise SingleInstanceError("当前系统缺少 fcntl，无法建立单实例锁") from exc

    directory = user_config_directory(app_name)
    descriptor: int | None = None
    try:
        ensure_private_directory(directory)
        flags = os.O_RDWR | os.O_CREAT
        if hasattr(os, "O_CLOEXEC"):
            flags |= os.O_CLOEXEC
        descriptor = os.open(directory / ".instance.lock", flags, 0o600)
        if os.name != "nt":
            os.fchmod(descriptor, 0o600)
        try:
            fcntl.flock(descriptor, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError as exc:
            os.close(descriptor)
            descriptor = None
            if exc.errno in {errno.EACCES, errno.EAGAIN}:
                return False
            raise
        os.ftruncate(descriptor, 0)
        os.write(descriptor, f"{os.getpid()}\n".encode("ascii"))
    except OSError as exc:
        if descriptor is not None:
            try:
                fcntl.flock(descriptor, fcntl.LOCK_UN)
            except OSError:
                pass
            os.close(descriptor)
        raise SingleInstanceError(f"无法创建单实例锁：{exc}") from exc

    assert descriptor is not None
    _instance_descriptor = descriptor
    return True


def acquire_single_instance(app_name: str = "FlowClip") -> bool:
    if sys.platform == "win32":
        return _acquire_windows_single_instance()
    return _acquire_unix_single_instance(app_name)


def release_single_instance() -> None:
    global _instance_handle, _instance_kernel32, _instance_descriptor
    if _instance_handle is not None:
        kernel32 = _instance_kernel32
        if kernel32 is not None:
            kernel32.CloseHandle(ctypes.c_void_p(_instance_handle))
        _instance_handle = None
        _instance_kernel32 = None

    if _instance_descriptor is not None:
        descriptor = _instance_descriptor
        _instance_descriptor = None
        try:
            import fcntl

            fcntl.flock(descriptor, fcntl.LOCK_UN)
        except (ImportError, OSError):
            pass
        try:
            os.close(descriptor)
        except OSError:
            pass


atexit.register(release_single_instance)
