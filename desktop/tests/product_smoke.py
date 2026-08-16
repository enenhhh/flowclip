from __future__ import annotations

import ctypes
import hashlib
import io
import os
import shutil
import subprocess
import sys
import tempfile
import time
import tkinter as tk
import uuid
from pathlib import Path

from PIL import Image


ROOT = Path(__file__).resolve().parents[2]
SOURCE = ROOT / "desktop" / "src"
sys.path.insert(0, str(SOURCE))

from flowclip.client import ApiClient  # noqa: E402
from flowclip.clipboard import ClipboardError  # noqa: E402
from flowclip.clipboard_windows import WindowsClipboard  # noqa: E402
from flowclip.config import AppConfig, save_config  # noqa: E402
from flowclip.file_client import FileTransferClient  # noqa: E402
from flowclip.model import ClipItem  # noqa: E402


user32 = ctypes.WinDLL("user32", use_last_error=True)
user32.FindWindowW.argtypes = [ctypes.c_wchar_p, ctypes.c_wchar_p]
user32.FindWindowW.restype = ctypes.c_void_p
user32.PostMessageW.argtypes = [ctypes.c_void_p, ctypes.c_uint, ctypes.c_void_p, ctypes.c_void_p]
user32.PostMessageW.restype = ctypes.c_bool


def wait_until(predicate, message: str, timeout: float = 20.0) -> None:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if predicate():
            return
        time.sleep(0.2)
    raise RuntimeError(message)


def stop_process_tree(process: subprocess.Popen[bytes] | None) -> None:
    if process is None or process.poll() is not None:
        return
    subprocess.run(
        ["taskkill", "/PID", str(process.pid), "/T", "/F"],
        check=False,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        creationflags=subprocess.CREATE_NO_WINDOW,
    )
    try:
        process.wait(timeout=5)
    except subprocess.TimeoutExpired:
        process.kill()


def free_port() -> int:
    import socket

    with socket.socket() as probe:
        probe.bind(("127.0.0.1", 0))
        return int(probe.getsockname()[1])


def main() -> None:
    executable = ROOT / "dist" / "FlowClip.exe"
    if not executable.is_file():
        raise RuntimeError(f"成品不存在：{executable}")

    cache_root = Path(os.environ.get("FLOWCLIP_SMOKE_CACHE", r"D:\DevCache"))
    cache_root.mkdir(parents=True, exist_ok=True)
    profile = Path(tempfile.mkdtemp(prefix="flowclip-smoke-", dir=cache_root))
    primary: subprocess.Popen[bytes] | None = None
    secondary: subprocess.Popen[bytes] | None = None
    root = tk.Tk()
    root.withdraw()
    root.update_idletasks()
    root.update()
    local_clipboard = WindowsClipboard(root.winfo_id())
    try:
        try:
            original = local_clipboard.capture("smoke-backup", 100 * 1024 * 1024)
        except ClipboardError:
            original = None

        seed = ClipItem.create(
            origin="smoke-seed",
            kind="text",
            mime="text/plain; charset=utf-8",
            data=b"FlowClip product smoke seed",
        )
        local_clipboard.apply(seed)

        port = free_port()
        token = "flowclip-smoke-token-1234567890"
        previous_appdata = os.environ.get("APPDATA")
        os.environ["APPDATA"] = str(profile)
        try:
            config = AppConfig.defaults()
            config.server_enabled = True
            config.listen_host = "127.0.0.1"
            config.listen_port = port
            config.server_url = f"http://127.0.0.1:{port}"
            config.token = token
            config.auto_sync = True
            config.poll_interval = 0.5
            config.start_minimized = True
            config.file_store_directory = str(profile / "server-files")
            config.download_directory = str(profile / "downloads")
            save_config(config)
        finally:
            if previous_appdata is None:
                os.environ.pop("APPDATA", None)
            else:
                os.environ["APPDATA"] = previous_appdata

        child_environment = os.environ.copy()
        child_environment["APPDATA"] = str(profile)
        child_environment["TEMP"] = str(cache_root / "temp")
        child_environment["TMP"] = str(cache_root / "temp")
        child_environment["FLOWCLIP_INSTANCE_SCOPE"] = (
            "product-smoke-" + uuid.uuid4().hex
        )
        primary = subprocess.Popen(
            [str(executable), "--minimized"],
            env=child_environment,
            creationflags=subprocess.CREATE_NO_WINDOW,
        )
        client = ApiClient(config.effective_server_url, token, 1024 * 1024)

        def server_ready() -> bool:
            exit_code = primary.poll()
            if exit_code is not None:
                raise RuntimeError(
                    f"成品在内置服务器启动前退出，退出码 {exit_code}"
                )
            try:
                return bool(client.health().get("ok"))
            except Exception:
                return False

        wait_until(server_ready, "成品内置服务器未在 20 秒内启动")
        health = client.health()
        if "files-v1" not in health.get("capabilities", []):
            raise RuntimeError("成品内置服务器未声明 files-v1")

        def seed_uploaded() -> bool:
            try:
                _revision, item = client.fetch(0)
                return item is not None and item.origin == config.device_id
            except Exception:
                return False

        wait_until(seed_uploaded, "成品自动发送未完成初始化")

        expected_text = f"FlowClip product smoke {uuid.uuid4().hex}"
        client.push(
            ClipItem.create(
                origin="smoke-remote",
                kind="text",
                mime="text/plain; charset=utf-8",
                data=expected_text.encode("utf-8"),
            )
        )

        def text_applied() -> bool:
            try:
                item = local_clipboard.capture("probe", 1024 * 1024)
                return (
                    item is not None
                    and item.kind == "text"
                    and item.data.decode("utf-8") == expected_text
                )
            except (ClipboardError, UnicodeDecodeError):
                return False

        wait_until(text_applied, "成品未把远端文本写回 Windows 剪贴板")

        image = Image.new("RGB", (7, 5), (17, 34, 51))
        output = io.BytesIO()
        image.save(output, format="PNG")
        client.push(
            ClipItem.create(
                origin="smoke-remote",
                kind="image",
                mime="image/png",
                filename="smoke.png",
                data=output.getvalue(),
            )
        )

        def image_applied() -> bool:
            try:
                item = local_clipboard.capture("probe", 1024 * 1024)
                if item is None or item.kind != "image":
                    return False
                with Image.open(io.BytesIO(item.data)) as received:
                    rgb = received.convert("RGB")
                    return rgb.size == (7, 5) and rgb.getpixel((3, 2)) == (17, 34, 51)
            except (ClipboardError, OSError):
                return False

        wait_until(image_applied, "成品未把远端图片写回 Windows 图片剪贴板")

        file_client = FileTransferClient(config.effective_server_url, token)
        source = profile / "中文-smoke.bin"
        file_content = bytes(range(251)) * 8192
        source.write_bytes(file_content)
        uploaded = file_client.upload_file(source, origin="product-smoke")
        listed = file_client.list_files()
        if not listed or listed[0].file_id != uploaded.file_id:
            raise RuntimeError("成品文件列表未返回刚上传的文件")
        downloaded = file_client.download_file(
            uploaded.file_id, config.download_directory
        )
        if hashlib.sha256(downloaded.read_bytes()).digest() != hashlib.sha256(
            file_content
        ).digest():
            raise RuntimeError("成品文件下载 SHA-256 校验失败")
        file_client.delete_file(uploaded.file_id)
        if file_client.list_files():
            raise RuntimeError("成品文件删除后列表仍非空")

        secondary = subprocess.Popen(
            [str(executable), "--minimized"],
            env=child_environment,
            creationflags=subprocess.CREATE_NO_WINDOW,
        )

        def dialog_visible() -> bool:
            return bool(user32.FindWindowW("#32770", "FlowClip"))

        wait_until(dialog_visible, "第二实例未显示单实例提示", timeout=10)
        dialog = user32.FindWindowW("#32770", "FlowClip")
        user32.PostMessageW(dialog, 0x0010, None, None)
        secondary.wait(timeout=5)
        if primary.poll() is not None:
            raise RuntimeError("关闭第二实例时误退出了主实例")

        print(
            f"health=ok text_apply=ok image_apply=ok file_roundtrip=ok "
            f"single_instance=ok "
            f"server_port={port} primary_pid={primary.pid}"
        )
    finally:
        dialog = user32.FindWindowW("#32770", "FlowClip")
        if dialog:
            user32.PostMessageW(dialog, 0x0010, None, None)
        stop_process_tree(secondary)
        stop_process_tree(primary)
        try:
            if "original" in locals() and original is not None:
                local_clipboard.apply(original)
            else:
                root.clipboard_clear()
                root.update()
        finally:
            root.destroy()
            shutil.rmtree(profile, ignore_errors=True)


if __name__ == "__main__":
    main()
