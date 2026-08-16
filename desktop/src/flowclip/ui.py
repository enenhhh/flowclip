from __future__ import annotations

import queue
import secrets
import sys
import threading
import tkinter as tk
import tkinter.font as tkfont
from dataclasses import replace
from tkinter import messagebox, ttk
from typing import Any, Callable

from PIL import Image

from .artwork import create_app_icon
from .client import ConnectionFailure
from .clipboard import ClipboardBackend, ClipboardError, create_clipboard
from .config import AppConfig, save_config, set_run_at_startup
from .file_ui import FileSettings, FileTransferWindow
from .network import local_ipv4_addresses, server_ipv4_addresses
from .server import ClipboardServer
from .sync import SyncEngine
from .windows_drop import FileDropRegistration, register_file_drop


COLORS = {
    "window": "#f4f6f8",
    "panel": "#ffffff",
    "text": "#17202a",
    "muted": "#68737d",
    "line": "#d9dee3",
    "accent": "#117864",
    "accent_dark": "#0b5b4c",
    "success": "#117864",
    "error": "#b03a2e",
    "warning": "#9a6700",
}


class FlowClipWindow:
    def __init__(self, root: tk.Tk, config: AppConfig, minimized: bool = False) -> None:
        self.root = root
        self.config = config
        self.clipboard: ClipboardBackend | None = None
        self.engine: SyncEngine | None = None
        self.server: ClipboardServer | None = None
        self.tray: Any | None = None
        self.file_window: FileTransferWindow | None = None
        self._events: queue.Queue[tuple[str, object]] = queue.Queue()
        self._busy_count = 0
        self._closing = False
        self._quit_pending = False
        self._drop_registration: FileDropRegistration | None = None

        self._build_style()
        self._build_window()
        self._load_fields(config)
        drop_error: str | None = None
        try:
            self._drop_registration = register_file_drop(
                self.root,
                lambda paths: self._events.put(("__file_drop__", paths)),
            )
        except OSError as exc:
            drop_error = str(exc)
        try:
            self._start_runtime()
        except (OSError, RuntimeError, ValueError) as exc:
            self._set_status("error", f"启动失败：{exc}")
        self._start_tray()
        self.root.after(100, self._drain_events)
        self.root.after(10_000, self._refresh_local_addresses)
        self.root.protocol(
            "WM_DELETE_WINDOW", self.hide_window if self.tray is not None else self.quit
        )
        if drop_error is not None:
            self._events.put(("warning", f"主窗口文件拖放不可用：{drop_error}"))
        if (minimized or config.start_minimized) and self.tray is not None:
            self.root.after(200, self.hide_window)

    def _build_style(self) -> None:
        style = ttk.Style(self.root)
        if "vista" in style.theme_names():
            style.theme_use("vista")
        candidates = {
            "win32": ("Microsoft YaHei UI", "Segoe UI"),
            "darwin": ("PingFang SC", "Helvetica Neue"),
        }.get(sys.platform, ("Noto Sans CJK SC", "Noto Sans", "DejaVu Sans"))
        available = set(tkfont.families(self.root))
        default_family = str(tkfont.nametofont("TkDefaultFont").actual("family"))
        self._font_family = next(
            (candidate for candidate in candidates if candidate in available), default_family
        )
        self.root.configure(background=COLORS["window"])
        style.configure("TFrame", background=COLORS["window"])
        style.configure("Panel.TFrame", background=COLORS["panel"])
        style.configure(
            "Title.TLabel",
            background=COLORS["window"],
            foreground=COLORS["text"],
            font=(self._font_family, 18, "bold"),
        )
        style.configure(
            "Subtitle.TLabel",
            background=COLORS["window"],
            foreground=COLORS["muted"],
            font=(self._font_family, 9),
        )
        style.configure(
            "Section.TLabel",
            background=COLORS["panel"],
            foreground=COLORS["text"],
            font=(self._font_family, 10, "bold"),
        )
        style.configure(
            "Panel.TLabel",
            background=COLORS["panel"],
            foreground=COLORS["text"],
            font=(self._font_family, 9),
        )
        style.configure(
            "Muted.TLabel",
            background=COLORS["panel"],
            foreground=COLORS["muted"],
            font=(self._font_family, 8),
        )
        style.configure(
            "Warning.TLabel",
            background=COLORS["panel"],
            foreground=COLORS["warning"],
            font=(self._font_family, 8),
        )
        style.configure(
            "Status.TLabel",
            background=COLORS["panel"],
            foreground=COLORS["muted"],
            font=(self._font_family, 9),
        )
        style.configure(
            "Accent.TButton",
            font=(self._font_family, 9, "bold"),
            padding=(12, 7),
        )
        style.configure("TButton", font=(self._font_family, 9), padding=(10, 6))
        style.configure(
            "TCheckbutton", background=COLORS["panel"], font=(self._font_family, 9)
        )
        style.configure("TEntry", padding=5)

    def _build_window(self) -> None:
        self.root.title("FlowClip 多端剪贴板")
        window_width = 820
        window_height = min(650, max(600, self.root.winfo_screenheight() - 90))
        window_x = max(0, (self.root.winfo_screenwidth() - window_width) // 2)
        self.root.geometry(f"{window_width}x{window_height}+{window_x}+0")
        self.root.minsize(720, 600)
        try:
            icon = create_app_icon(64)
            self._icon_photo = tk.PhotoImage(data=self._image_to_png(icon))
            self.root.iconphoto(True, self._icon_photo)
        except tk.TclError:
            self._icon_photo = None

        outer = ttk.Frame(self.root, padding=(20, 12, 20, 10))
        outer.pack(fill="both", expand=True)

        header = ttk.Frame(outer)
        header.pack(fill="x", pady=(0, 10))
        title_block = ttk.Frame(header)
        title_block.pack(side="left", fill="x", expand=True)
        ttk.Label(title_block, text="FlowClip", style="Title.TLabel").pack(anchor="w")
        ttk.Label(
            title_block,
            text="同一密钥下的设备同步剪贴板并高速传输文件",
            style="Subtitle.TLabel",
        ).pack(anchor="w", pady=(2, 0))
        self.connection_label = ttk.Label(header, text="正在启动", style="Subtitle.TLabel")
        self.connection_label.pack(side="right", anchor="e")

        content = ttk.Frame(outer)
        content.pack(fill="both", expand=True)
        content.columnconfigure(0, weight=1, uniform="col")
        content.columnconfigure(1, weight=1, uniform="col")
        content.rowconfigure(0, weight=1)

        left = ttk.Frame(content, style="Panel.TFrame", padding=14)
        right = ttk.Frame(content, style="Panel.TFrame", padding=14)
        left.grid(row=0, column=0, sticky="nsew", padx=(0, 7))
        right.grid(row=0, column=1, sticky="nsew", padx=(7, 0))

        self.device_name_var = tk.StringVar()
        self.server_url_var = tk.StringVar()
        self.token_var = tk.StringVar()
        self.auto_sync_var = tk.BooleanVar()
        self.interval_var = tk.StringVar()
        self.max_size_var = tk.StringVar()
        self.server_enabled_var = tk.BooleanVar()
        self.listen_host_var = tk.StringVar()
        self.listen_port_var = tk.StringVar()
        self.start_minimized_var = tk.BooleanVar()
        self.run_at_startup_var = tk.BooleanVar()
        self.require_public_https_var = tk.BooleanVar()
        self.show_token_var = tk.BooleanVar(value=False)
        self.status_var = tk.StringVar(value="正在启动…")
        self.lan_var = tk.StringVar(value="")
        self.https_warning_var = tk.StringVar(value="")

        ttk.Label(left, text="设备与服务器", style="Section.TLabel").pack(anchor="w")
        self._field(left, "设备名称", self.device_name_var)
        self._field(left, "服务器地址", self.server_url_var)

        token_row = self._field(left, "共享密钥", self.token_var, show="•")
        token_actions = ttk.Frame(token_row, style="Panel.TFrame")
        token_actions.pack(fill="x", pady=(4, 0))
        ttk.Checkbutton(
            token_actions,
            text="显示",
            variable=self.show_token_var,
            command=self._toggle_token,
        ).pack(side="left")
        ttk.Button(token_actions, text="复制", command=self._copy_token).pack(side="left", padx=4)
        ttk.Button(token_actions, text="重新生成", command=self._regenerate_token).pack(side="left")

        ttk.Separator(left).pack(fill="x", pady=10)
        ttk.Label(left, text="本机作为服务器", style="Section.TLabel").pack(anchor="w")
        ttk.Checkbutton(
            left,
            text="启用内置服务器",
            variable=self.server_enabled_var,
            command=self._update_server_fields,
        ).pack(anchor="w", pady=(10, 3))
        ttk.Label(
            left,
            text="启用后本机客户端自动连接 127.0.0.1",
            style="Muted.TLabel",
        ).pack(anchor="w")
        server_grid = ttk.Frame(left, style="Panel.TFrame")
        server_grid.pack(fill="x", pady=(10, 0))
        server_grid.columnconfigure(0, weight=1)
        server_grid.columnconfigure(1, weight=1)
        host_box = ttk.Frame(server_grid, style="Panel.TFrame")
        port_box = ttk.Frame(server_grid, style="Panel.TFrame")
        host_box.grid(row=0, column=0, sticky="ew", padx=(0, 5))
        port_box.grid(row=0, column=1, sticky="ew", padx=(5, 0))
        ttk.Label(host_box, text="监听地址", style="Panel.TLabel").pack(anchor="w")
        self.listen_host_entry = ttk.Entry(host_box, textvariable=self.listen_host_var)
        self.listen_host_entry.pack(fill="x", pady=(4, 0))
        ttk.Label(port_box, text="端口", style="Panel.TLabel").pack(anchor="w")
        self.listen_port_entry = ttk.Entry(port_box, textvariable=self.listen_port_var)
        self.listen_port_entry.pack(fill="x", pady=(4, 0))
        self.lan_label = ttk.Label(
            left, textvariable=self.lan_var, style="Muted.TLabel", wraplength=320
        )
        self.lan_label.pack(anchor="w", fill="x", pady=(9, 0))

        ttk.Label(right, text="同步选项", style="Section.TLabel").pack(anchor="w")
        ttk.Checkbutton(
            right, text="自动同步剪贴板", variable=self.auto_sync_var
        ).pack(anchor="w", pady=(10, 4))
        ttk.Checkbutton(
            right,
            text="公网地址必须使用 HTTPS（推荐）",
            variable=self.require_public_https_var,
            command=self._update_https_warning,
        ).pack(anchor="w", pady=4)
        self.https_warning_label = ttk.Label(
            right,
            textvariable=self.https_warning_var,
            style="Warning.TLabel",
            wraplength=320,
            justify="left",
        )
        self.start_minimized_check = ttk.Checkbutton(
            right, text="启动后隐藏到托盘", variable=self.start_minimized_var
        )
        self.start_minimized_check.pack(anchor="w", pady=4)
        ttk.Checkbutton(
            right, text="登录系统时启动", variable=self.run_at_startup_var
        ).pack(anchor="w", pady=4)

        sync_grid = ttk.Frame(right, style="Panel.TFrame")
        sync_grid.pack(fill="x", pady=(12, 0))
        sync_grid.columnconfigure(0, weight=1)
        sync_grid.columnconfigure(1, weight=1)
        interval_box = ttk.Frame(sync_grid, style="Panel.TFrame")
        size_box = ttk.Frame(sync_grid, style="Panel.TFrame")
        interval_box.grid(row=0, column=0, sticky="ew", padx=(0, 5))
        size_box.grid(row=0, column=1, sticky="ew", padx=(5, 0))
        ttk.Label(interval_box, text="同步间隔（秒）", style="Panel.TLabel").pack(anchor="w")
        ttk.Entry(interval_box, textvariable=self.interval_var).pack(fill="x", pady=(4, 0))
        ttk.Label(size_box, text="单条上限（MB）", style="Panel.TLabel").pack(anchor="w")
        ttk.Entry(size_box, textvariable=self.max_size_var).pack(fill="x", pady=(4, 0))

        ttk.Separator(right).pack(fill="x", pady=10)
        ttk.Label(right, text="立即操作", style="Section.TLabel").pack(anchor="w")
        action_row = ttk.Frame(right, style="Panel.TFrame")
        action_row.pack(fill="x", pady=(10, 0))
        self.send_button = ttk.Button(
            action_row, text="发送当前剪贴板", command=self._send_now
        )
        self.send_button.pack(fill="x", pady=(0, 7))
        self.receive_button = ttk.Button(
            action_row, text="接收最新内容", command=self._receive_now
        )
        self.receive_button.pack(fill="x", pady=(0, 7))
        self.test_button = ttk.Button(
            action_row, text="测试连接", command=self._test_connection
        )
        self.test_button.pack(fill="x")

        ttk.Separator(right).pack(fill="x", pady=10)
        ttk.Label(right, text="状态", style="Section.TLabel").pack(anchor="w")
        self.status_label = ttk.Label(
            right,
            textvariable=self.status_var,
            style="Status.TLabel",
            wraplength=320,
            justify="left",
        )
        self.status_label.pack(anchor="w", fill="x", pady=(9, 0))

        footer = ttk.Frame(outer)
        footer.pack(fill="x", pady=(10, 0))
        self.exit_button = ttk.Button(footer, text="退出", command=self.quit)
        self.exit_button.pack(side="left")
        self.files_button = ttk.Button(
            footer, text="文件快传", command=self._show_file_window
        )
        self.files_button.pack(side="left", padx=(8, 0))
        self.hide_button = ttk.Button(
            footer, text="隐藏到托盘", command=self.hide_window
        )
        self.hide_button.pack(side="right", padx=(8, 0))
        self.save_button = ttk.Button(
            footer, text="保存并重启同步", style="Accent.TButton", command=self._save
        )
        self.save_button.pack(side="right")

    @staticmethod
    def _image_to_png(image: Image.Image) -> bytes:
        import io

        output = io.BytesIO()
        image.save(output, "PNG")
        return output.getvalue()

    def _field(
        self, parent: ttk.Frame, label: str, variable: tk.StringVar, show: str = ""
    ) -> ttk.Frame:
        frame = ttk.Frame(parent, style="Panel.TFrame")
        frame.pack(fill="x", pady=(7, 0))
        ttk.Label(frame, text=label, style="Panel.TLabel").pack(anchor="w")
        entry = ttk.Entry(frame, textvariable=variable, show=show)
        entry.pack(fill="x", pady=(4, 0))
        if variable is self.token_var:
            self.token_entry = entry
        return frame

    def _load_fields(self, config: AppConfig) -> None:
        self.device_name_var.set(config.device_name)
        self.server_url_var.set(config.server_url)
        self.token_var.set(config.token)
        self.auto_sync_var.set(config.auto_sync)
        self.interval_var.set(str(config.poll_interval))
        self.max_size_var.set(str(config.max_size_mb))
        self.server_enabled_var.set(config.server_enabled)
        self.listen_host_var.set(config.listen_host)
        self.listen_port_var.set(str(config.listen_port))
        self.start_minimized_var.set(config.start_minimized)
        self.run_at_startup_var.set(config.run_at_startup)
        self.require_public_https_var.set(config.require_public_https)
        self._update_server_fields()
        self._update_https_warning()

    def _read_fields(self) -> AppConfig:
        try:
            interval = float(self.interval_var.get().strip())
            max_size = int(self.max_size_var.get().strip())
            port = int(self.listen_port_var.get().strip())
        except ValueError as exc:
            raise ValueError("同步间隔、大小上限和端口必须是数字") from exc
        candidate = replace(
            self.config,
            device_name=self.device_name_var.get().strip(),
            server_url=self.server_url_var.get().strip().rstrip("/"),
            token=self.token_var.get(),
            auto_sync=self.auto_sync_var.get(),
            poll_interval=interval,
            max_size_mb=max_size,
            server_enabled=self.server_enabled_var.get(),
            listen_host=self.listen_host_var.get().strip(),
            listen_port=port,
            start_minimized=self.start_minimized_var.get(),
            run_at_startup=self.run_at_startup_var.get(),
            require_public_https=self.require_public_https_var.get(),
        )
        candidate.validate()
        return candidate

    def _update_server_fields(self) -> None:
        state = "normal" if self.server_enabled_var.get() else "disabled"
        self.listen_host_entry.configure(state=state)
        self.listen_port_entry.configure(state=state)
        try:
            port = int(self.listen_port_var.get())
        except ValueError:
            port = 0
        addresses = local_ipv4_addresses()
        server_addresses = server_ipv4_addresses(
            self.listen_host_var.get(), addresses
        )
        if self.server_enabled_var.get() and port and server_addresses:
            rendered = "、".join(
                f"http://{address}:{port}" for address in server_addresses
            )
            displayed = addresses or server_addresses
            self.lan_var.set(
                f"本机 IP：{'、'.join(displayed)}\n可连接地址：{rendered}"
            )
        elif addresses:
            self.lan_var.set(f"本机 IP：{'、'.join(addresses)}")
        else:
            self.lan_var.set("本机 IP：暂未找到可用 IPv4 地址")

    def _refresh_local_addresses(self) -> None:
        if self._closing:
            return
        self._update_server_fields()
        self.root.after(10_000, self._refresh_local_addresses)

    def _update_https_warning(self) -> None:
        if self.require_public_https_var.get():
            self.https_warning_var.set("")
            self.https_warning_label.pack_forget()
        else:
            self.https_warning_var.set(
                "已允许公网 HTTP：共享密钥和剪贴板内容可能被明文截获"
            )
            self.https_warning_label.pack(
                anchor="w", fill="x", before=self.start_minimized_check
            )

    def _toggle_token(self) -> None:
        self.token_entry.configure(show="" if self.show_token_var.get() else "•")

    def _copy_token(self) -> None:
        if self.engine is not None:
            self.engine.suppress_clipboard_text(self.token_var.get())
        self.root.clipboard_clear()
        self.root.clipboard_append(self.token_var.get())
        self._set_status("success", "共享密钥已复制")

    def _regenerate_token(self) -> None:
        if messagebox.askyesno(
            "重新生成密钥", "生成新密钥后，其他设备必须同步修改才能继续连接。是否继续？"
        ):
            self.token_var.set(secrets.token_urlsafe(32))

    def _start_runtime(self) -> None:
        new_server: ClipboardServer | None = None
        new_engine: SyncEngine | None = None
        try:
            self.config.validate()
            clipboard = self.clipboard
            if clipboard is None:
                clipboard = create_clipboard(self.root.winfo_id())
                self.clipboard = clipboard
            if self.config.server_enabled:
                new_server = ClipboardServer(
                    self.config.listen_host,
                    self.config.listen_port,
                    self.config.token,
                    self.config.max_bytes,
                    lambda message: self._events.put(("success", message)),
                    file_store_directory=self.config.file_store_directory,
                    file_max_bytes=self.config.file_max_bytes,
                    file_storage_limit_bytes=self.config.file_storage_limit_bytes,
                )
                new_server.start()
            new_engine = SyncEngine(self.config, clipboard, self._queue_status)
            new_engine.start()
        except Exception:
            if new_engine is not None:
                new_engine.stop()
            if new_server is not None:
                try:
                    new_server.stop()
                except OSError:
                    pass
            raise
        self.server = new_server
        self.engine = new_engine
        role = "服务器 + 客户端" if self.config.server_enabled else "客户端"
        self._set_status("success", f"{role}已启动")

    def _stop_runtime(self) -> None:
        if self.engine is not None:
            if not self.engine.stop():
                raise RuntimeError("同步线程未能及时停止，请稍后重试")
            self.engine = None
        if self.server is not None:
            self.server.stop()
            self.server = None

    def _queue_status(self, level: str, message: str) -> None:
        self._events.put((level, message))

    def _drain_events(self) -> None:
        if self._closing:
            return
        try:
            while True:
                level, message = self._events.get_nowait()
                if level == "__finished__":
                    self._background_finished()
                    if self._closing:
                        return
                elif level == "__command__":
                    self._handle_tray_command(str(message))
                    if self._closing:
                        return
                elif level == "__tray_failed__":
                    self._disable_tray(str(message))
                elif level == "__file_drop__":
                    if isinstance(message, tuple) and all(
                        isinstance(path, str) for path in message
                    ):
                        self._handle_file_drop(message)
                else:
                    self._set_status(level, str(message))
        except queue.Empty:
            pass
        self.root.after(100, self._drain_events)

    def _set_status(self, level: str, message: str) -> None:
        color = COLORS.get(level, COLORS["muted"])
        self.status_label.configure(foreground=color)
        self.status_var.set(message)
        self.connection_label.configure(foreground=color)
        self.connection_label.configure(text=message)
        if self.tray is not None:
            self.tray.title = f"FlowClip - {message}"

    def _run_background(
        self, operation: Callable[[], str | None], success_default: str
    ) -> None:
        if self._closing or self._quit_pending:
            return
        if self._busy_count:
            self._set_status("warning", "请等待当前操作完成")
            return
        self._busy_count += 1
        self._set_buttons_enabled(False)

        def worker() -> None:
            try:
                message = operation()
                self._events.put(("success", message or success_default))
            except Exception as exc:
                self._events.put(("error", str(exc)))
            finally:
                self._events.put(("__finished__", ""))

        threading.Thread(target=worker, name="flowclip-action", daemon=True).start()

    def _background_finished(self) -> None:
        self._busy_count = max(0, self._busy_count - 1)
        if self._busy_count == 0:
            self._set_buttons_enabled(True)
            if self._quit_pending:
                self._finish_quit()

    def _set_buttons_enabled(self, enabled: bool) -> None:
        state = "normal" if enabled else "disabled"
        for button in (
            self.send_button,
            self.receive_button,
            self.test_button,
            self.save_button,
        ):
            button.configure(state=state)

    def _send_now(self) -> None:
        if self.engine is None:
            self._set_status("error", "同步服务未启动")
            return
        engine = self.engine
        self._run_background(lambda: engine.send_now(), "发送完成")

    def _receive_now(self) -> None:
        if self.engine is None:
            self._set_status("error", "同步服务未启动")
            return

        engine = self.engine

        def receive() -> str:
            return "已接收最新内容" if engine.receive_now() else "没有其他设备的新内容"

        self._run_background(receive, "接收完成")

    def _test_connection(self) -> None:
        try:
            candidate = self._read_fields()
        except ValueError as exc:
            self._set_status("error", str(exc))
            return

        def test() -> str:
            from .client import ApiClient

            data = ApiClient(
                candidate.effective_server_url, candidate.token, candidate.max_bytes
            ).health()
            if not data.get("ok"):
                raise ConnectionFailure("服务器健康检查失败")
            return "服务器可访问"

        self._run_background(test, "服务器可访问")

    def _save(self) -> None:
        if self._busy_count or self._file_transfer_active():
            self._set_status("warning", "请等待当前操作完成后再保存")
            return
        try:
            candidate = self._read_fields()
        except ValueError as exc:
            messagebox.showerror("无法保存", str(exc))
            return

        try:
            self._apply_config(candidate)
        except (OSError, RuntimeError, ValueError) as exc:
            messagebox.showerror("无法保存", str(exc))
            self._set_status("error", f"设置未生效：{exc}")
            return
        self._update_server_fields()
        self._set_status("success", "设置已保存，同步已重启")

    def _apply_config(self, candidate: AppConfig) -> None:
        previous = self.config
        self._stop_runtime()
        self.config = candidate
        try:
            self._start_runtime()
            save_config(candidate)
            set_run_at_startup(candidate.run_at_startup)
        except (OSError, RuntimeError, ValueError) as exc:
            try:
                self._stop_runtime()
            except (OSError, RuntimeError):
                pass
            self.config = previous
            recovery_error: Exception | None = None
            try:
                self._start_runtime()
                save_config(previous)
                set_run_at_startup(previous.run_at_startup)
            except (OSError, RuntimeError, ValueError) as recovery:
                recovery_error = recovery
            detail = str(exc)
            if recovery_error is not None:
                detail += f"\n恢复原设置也失败：{recovery_error}"
            raise RuntimeError(detail) from exc

    def _apply_file_settings(self, settings: FileSettings) -> AppConfig:
        if self._busy_count or self._file_transfer_active():
            raise RuntimeError("请等待当前操作完成后再保存")
        candidate = replace(
            self.config,
            file_max_size_mb=settings.file_max_size_mb,
            file_storage_limit_mb=settings.file_storage_limit_mb,
            file_store_directory=settings.file_store_directory,
            download_directory=settings.download_directory,
        )
        candidate.validate()
        self._apply_config(candidate)
        self._update_server_fields()
        self._set_status("success", "存储设置已保存，同步已重启")
        return self.config

    def _ensure_file_window(self) -> FileTransferWindow | None:
        if self._closing or self._quit_pending:
            return None
        if self.file_window is None:
            self.file_window = FileTransferWindow(
                self.root,
                lambda: self.config,
                self._apply_file_settings,
                self._set_file_status,
            )
        return self.file_window

    def _set_file_status(self, level: str, message: str) -> None:
        if not self._closing:
            self._set_status(level, message)

    def _handle_file_drop(self, paths: tuple[str, ...]) -> None:
        file_window = self._ensure_file_window()
        if file_window is not None:
            file_window.upload_paths(paths)

    def _show_file_window(self) -> None:
        file_window = self._ensure_file_window()
        if file_window is None:
            return
        file_window.show()

    def _file_transfer_active(self) -> bool:
        return self.file_window is not None and self.file_window.active

    def _start_tray(self) -> None:
        try:
            import pystray

            menu = pystray.Menu(
                pystray.MenuItem("打开 FlowClip", self._tray_show, default=True),
                pystray.MenuItem("文件快传", self._tray_files),
                pystray.MenuItem("发送当前剪贴板", self._tray_send),
                pystray.MenuItem("接收最新内容", self._tray_receive),
                pystray.Menu.SEPARATOR,
                pystray.MenuItem("退出", self._tray_quit),
            )
            self.tray = pystray.Icon(
                "FlowClip", create_app_icon(64), "FlowClip", menu
            )
            if sys.platform == "darwin":
                self.tray.run_detached()
            else:
                tray = self.tray

                def run_tray() -> None:
                    try:
                        tray.run()
                    except Exception as exc:
                        self._events.put(("__tray_failed__", str(exc)))

                threading.Thread(
                    target=run_tray, name="flowclip-tray", daemon=True
                ).start()
        except Exception as exc:
            self._disable_tray(str(exc))

    def _disable_tray(self, detail: str) -> None:
        tray = self.tray
        self.tray = None
        if tray is not None:
            try:
                tray.stop()
            except Exception:
                pass
        self.root.protocol("WM_DELETE_WINDOW", self.quit)
        self.hide_button.configure(state="disabled")
        self.start_minimized_check.configure(state="disabled")
        self.show_window()
        self._set_status("warning", f"系统托盘不可用，窗口关闭将退出：{detail}")

    def _tray_show(self, _icon: object, _item: object) -> None:
        self._events.put(("__command__", "show"))

    def _tray_send(self, _icon: object, _item: object) -> None:
        self._events.put(("__command__", "send"))

    def _tray_files(self, _icon: object, _item: object) -> None:
        self._events.put(("__command__", "files"))

    def _tray_receive(self, _icon: object, _item: object) -> None:
        self._events.put(("__command__", "receive"))

    def _tray_quit(self, _icon: object, _item: object) -> None:
        self._events.put(("__command__", "quit"))

    def _handle_tray_command(self, command: str) -> None:
        actions = {
            "show": self.show_window,
            "files": self._show_file_window,
            "send": self._send_now,
            "receive": self._receive_now,
            "quit": self.quit,
        }
        action = actions.get(command)
        if action is not None:
            action()

    def show_window(self) -> None:
        self.root.deiconify()
        self.root.lift()
        if sys.platform == "win32":
            self.root.focus_force()
        else:
            self.root.focus_set()

    def hide_window(self) -> None:
        if self.tray is None:
            self.quit()
        else:
            self.root.withdraw()

    def quit(self) -> None:
        if self._closing:
            return
        if self._busy_count or self._file_transfer_active():
            self._quit_pending = True
            self._set_buttons_enabled(False)
            self.exit_button.configure(state="disabled")
            if self.file_window is not None:
                self.file_window.cancel_active()
            self._set_status("warning", "正在等待当前操作结束后退出")
            self.root.after(100, self._continue_pending_quit)
            return
        self._finish_quit()

    def _continue_pending_quit(self) -> None:
        if self._closing or not self._quit_pending:
            return
        if self._busy_count or self._file_transfer_active():
            self.root.after(100, self._continue_pending_quit)
            return
        self._finish_quit()

    def _finish_quit(self) -> None:
        if self._closing:
            return
        if self._busy_count or self._file_transfer_active():
            return
        self._closing = True
        try:
            self._stop_runtime()
        except (OSError, RuntimeError) as exc:
            self._closing = False
            self._quit_pending = False
            self.exit_button.configure(state="normal")
            self._set_buttons_enabled(True)
            self._set_status("error", str(exc))
            return
        if self.tray is not None:
            self.tray.stop()
            self.tray = None
        if self.file_window is not None:
            try:
                self.file_window.close()
            except (OSError, RuntimeError, tk.TclError):
                pass
        if self._drop_registration is not None:
            try:
                self._drop_registration.close()
            except OSError:
                pass
        self.root.destroy()
