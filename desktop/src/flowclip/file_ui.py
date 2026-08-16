from __future__ import annotations

import queue
import stat as stat_module
import threading
import time
import tkinter as tk
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from tkinter import filedialog, messagebox, ttk
from typing import Callable, Iterable

from .client import ConnectionFailure
from .config import AppConfig
from .file_client import FileTransferCancelled, FileTransferClient, ProgressCallback
from .file_model import FileRecord
from .windows_drop import FileDropRegistration, register_file_drop


@dataclass(frozen=True, slots=True)
class FileSettings:
    file_max_size_mb: int
    file_storage_limit_mb: int
    file_store_directory: str
    download_directory: str


@dataclass(frozen=True, slots=True)
class _JobResult:
    message: str
    records: list[FileRecord] | None = None
    level: str = "success"


SettingsCallback = Callable[[FileSettings], AppConfig]
ConfigProvider = Callable[[], AppConfig]
Job = Callable[[ProgressCallback, Callable[[], bool]], _JobResult]
StatusCallback = Callable[[str, str], None]


def format_byte_count(value: int) -> str:
    units = ("B", "KB", "MB", "GB", "TB")
    amount = float(max(0, value))
    for unit in units:
        if amount < 1024 or unit == units[-1]:
            if unit == "B":
                return f"{int(amount)} {unit}"
            return f"{amount:.1f} {unit}"
        amount /= 1024
    return f"{value} B"


def format_transfer_rate(bytes_per_second: float) -> str:
    if bytes_per_second <= 0:
        return "--"
    return f"{format_byte_count(int(bytes_per_second))}/s"


def inspect_upload_path(path: Path, max_bytes: int) -> int:
    try:
        details = path.stat()
    except OSError as exc:
        raise ValueError(f"无法读取：{exc}") from exc
    if not stat_module.S_ISREG(details.st_mode):
        raise ValueError("仅支持上传文件，不支持文件夹")
    if details.st_size > max_bytes:
        raise ValueError(
            f"文件为 {format_byte_count(details.st_size)}，超过当前上限 "
            f"{format_byte_count(max_bytes)}"
        )
    return details.st_size


def _format_upload_failures(failures: list[tuple[str, str]], limit: int = 3) -> str:
    visible = "；".join(f"{name}（{detail}）" for name, detail in failures[:limit])
    remaining = len(failures) - min(len(failures), limit)
    if remaining:
        visible += f"；另有 {remaining} 个失败"
    return visible


class FileTransferWindow:
    def __init__(
        self,
        parent: tk.Misc,
        config_provider: ConfigProvider,
        settings_callback: SettingsCallback,
        status_callback: StatusCallback | None = None,
    ) -> None:
        self._parent = parent
        self._config_provider = config_provider
        self._settings_callback = settings_callback
        self._status_callback = status_callback
        self._records: dict[str, FileRecord] = {}
        self._events: queue.Queue[tuple[str, object]] = queue.Queue()
        self._cancel = threading.Event()
        self._worker: threading.Thread | None = None
        self._started_at = 0.0
        self._last_progress_at = 0.0
        self._last_progress_bytes = 0

        self.window = tk.Toplevel(parent)
        self.window.withdraw()
        self.window.title("FlowClip 文件快传")
        self.window.geometry("900x620")
        self.window.minsize(720, 520)
        self.window.protocol("WM_DELETE_WINDOW", self.hide)
        self._build()
        self._load_settings(self._config_provider())
        self._drop_registration: FileDropRegistration | None = None
        try:
            self._drop_registration = register_file_drop(
                self.window,
                lambda paths: self._events.put(("drop", paths)),
            )
        except OSError as exc:
            self._set_status(f"文件拖放不可用：{exc}", "warning")
        self.window.after(100, self._drain_events)

    def _build(self) -> None:
        outer = ttk.Frame(self.window, padding=14)
        outer.pack(fill="both", expand=True)

        header = ttk.Frame(outer)
        header.pack(fill="x", pady=(0, 10))
        ttk.Label(header, text="文件快传", style="Title.TLabel").pack(side="left")
        self._server_var = tk.StringVar()
        ttk.Label(
            header,
            textvariable=self._server_var,
            style="Subtitle.TLabel",
        ).pack(side="right", anchor="e")

        notebook = ttk.Notebook(outer)
        notebook.pack(fill="both", expand=True)
        transfer_tab = ttk.Frame(notebook, padding=10)
        settings_tab = ttk.Frame(notebook, padding=16)
        notebook.add(transfer_tab, text="文件")
        notebook.add(settings_tab, text="存储设置")

        toolbar = ttk.Frame(transfer_tab)
        toolbar.pack(fill="x", pady=(0, 8))
        self._upload_button = ttk.Button(
            toolbar, text="上传文件", command=self._choose_upload
        )
        self._upload_button.pack(side="left")
        self._download_button = ttk.Button(
            toolbar, text="下载", command=self._download_selected
        )
        self._download_button.pack(side="left", padx=(6, 0))
        self._delete_button = ttk.Button(
            toolbar, text="删除", command=self._delete_selected
        )
        self._delete_button.pack(side="left", padx=(6, 0))
        self._refresh_button = ttk.Button(
            toolbar, text="刷新", command=self.refresh
        )
        self._refresh_button.pack(side="right")
        self._cancel_button = ttk.Button(
            toolbar, text="取消", command=self._cancel_job, state="disabled"
        )
        self._cancel_button.pack(side="right", padx=(0, 6))

        table = ttk.Frame(transfer_tab)
        table.pack(fill="both", expand=True)
        columns = ("filename", "size", "origin", "created")
        self._tree = ttk.Treeview(
            table,
            columns=columns,
            show="headings",
            selectmode="browse",
        )
        self._tree.heading("filename", text="文件名")
        self._tree.heading("size", text="大小")
        self._tree.heading("origin", text="发送设备")
        self._tree.heading("created", text="时间")
        self._tree.column("filename", width=340, minwidth=180, stretch=True)
        self._tree.column("size", width=100, minwidth=80, anchor="e", stretch=False)
        self._tree.column("origin", width=150, minwidth=100, stretch=False)
        self._tree.column("created", width=150, minwidth=130, stretch=False)
        scrollbar = ttk.Scrollbar(table, orient="vertical", command=self._tree.yview)
        self._tree.configure(yscrollcommand=scrollbar.set)
        self._tree.pack(side="left", fill="both", expand=True)
        scrollbar.pack(side="right", fill="y")
        self._tree.bind("<<TreeviewSelect>>", self._selection_changed)
        self._tree.bind("<Double-1>", self._download_selected)

        progress_area = ttk.Frame(transfer_tab)
        progress_area.pack(fill="x", pady=(10, 0))
        self._progress = ttk.Progressbar(progress_area, maximum=1000, value=0)
        self._progress.pack(fill="x")
        progress_labels = ttk.Frame(progress_area)
        progress_labels.pack(fill="x", pady=(5, 0))
        self._status_var = tk.StringVar(value="尚未刷新")
        self._progress_var = tk.StringVar(value="")
        self._status_label = ttk.Label(
            progress_labels, textvariable=self._status_var, style="Status.TLabel"
        )
        self._status_label.pack(side="left", fill="x", expand=True)
        ttk.Label(
            progress_labels,
            textvariable=self._progress_var,
            style="Subtitle.TLabel",
        ).pack(side="right")

        self._store_directory_var = tk.StringVar()
        self._download_directory_var = tk.StringVar()
        self._file_max_var = tk.StringVar()
        self._storage_limit_var = tk.StringVar()

        settings_tab.columnconfigure(1, weight=1)
        ttk.Label(settings_tab, text="服务端存储目录").grid(
            row=0, column=0, sticky="w", pady=(0, 10)
        )
        ttk.Entry(settings_tab, textvariable=self._store_directory_var).grid(
            row=0, column=1, sticky="ew", padx=10, pady=(0, 10)
        )
        ttk.Button(
            settings_tab,
            text="选择...",
            command=lambda: self._choose_directory(self._store_directory_var),
        ).grid(row=0, column=2, pady=(0, 10))

        ttk.Label(settings_tab, text="下载目录").grid(
            row=1, column=0, sticky="w", pady=(0, 10)
        )
        ttk.Entry(settings_tab, textvariable=self._download_directory_var).grid(
            row=1, column=1, sticky="ew", padx=10, pady=(0, 10)
        )
        ttk.Button(
            settings_tab,
            text="选择...",
            command=lambda: self._choose_directory(self._download_directory_var),
        ).grid(row=1, column=2, pady=(0, 10))

        ttk.Label(settings_tab, text="单文件上限（MB）").grid(
            row=2, column=0, sticky="w", pady=(0, 10)
        )
        ttk.Entry(settings_tab, textvariable=self._file_max_var, width=16).grid(
            row=2, column=1, sticky="w", padx=10, pady=(0, 10)
        )
        ttk.Label(settings_tab, text="服务端总容量（MB）").grid(
            row=3, column=0, sticky="w", pady=(0, 10)
        )
        ttk.Entry(settings_tab, textvariable=self._storage_limit_var, width=16).grid(
            row=3, column=1, sticky="w", padx=10, pady=(0, 10)
        )
        self._save_settings_button = ttk.Button(
            settings_tab,
            text="保存存储设置",
            style="Accent.TButton",
            command=self._save_settings,
        )
        self._save_settings_button.grid(row=4, column=1, sticky="w", padx=10, pady=(8, 0))

        footer = ttk.Frame(outer)
        footer.pack(fill="x", pady=(10, 0))
        ttk.Button(footer, text="关闭", command=self.hide).pack(side="right")
        self._selection_changed()

    def show(self) -> None:
        config = self._config_provider()
        self._load_settings(config)
        self._server_var.set(config.effective_server_url)
        self.window.deiconify()
        self.window.lift()
        self.window.focus_set()
        if self._worker is None:
            self.refresh()

    def hide(self) -> None:
        self.window.withdraw()

    def close(self) -> None:
        if self._worker is not None:
            raise RuntimeError("文件操作仍在进行")
        registration = self._drop_registration
        if registration is not None:
            registration.close()
            self._drop_registration = None
        if self.window.winfo_exists():
            self.window.destroy()

    @property
    def active(self) -> bool:
        return self._worker is not None

    def cancel_active(self) -> None:
        self._cancel_job()

    def _load_settings(self, config: AppConfig) -> None:
        self._server_var.set(config.effective_server_url)
        self._store_directory_var.set(config.file_store_directory)
        self._download_directory_var.set(config.download_directory)
        self._file_max_var.set(str(config.file_max_size_mb))
        self._storage_limit_var.set(str(config.file_storage_limit_mb))

    def _choose_directory(self, variable: tk.StringVar) -> None:
        current = Path(variable.get()).expanduser()
        initial = current if current.is_dir() else current.parent
        selected = filedialog.askdirectory(
            parent=self.window,
            initialdir=str(initial) if initial.is_dir() else None,
            mustexist=False,
        )
        if selected:
            variable.set(selected)

    def _save_settings(self) -> None:
        if self._worker is not None:
            self._set_status("请等待当前文件操作结束", "warning")
            return
        try:
            values = FileSettings(
                file_max_size_mb=int(self._file_max_var.get().strip()),
                file_storage_limit_mb=int(self._storage_limit_var.get().strip()),
                file_store_directory=self._store_directory_var.get().strip(),
                download_directory=self._download_directory_var.get().strip(),
            )
            config = self._settings_callback(values)
        except (OSError, RuntimeError, ValueError) as exc:
            messagebox.showerror("无法保存存储设置", str(exc), parent=self.window)
            return
        self._load_settings(config)
        self._set_status("存储设置已保存", "success")

    def refresh(self) -> None:
        def operation(
            _progress: ProgressCallback, cancelled: Callable[[], bool]
        ) -> _JobResult:
            if cancelled():
                raise FileTransferCancelled("文件操作已取消")
            records = self._client().list_files()
            return _JobResult(f"已加载 {len(records)} 个文件", records)

        self._start_job("正在刷新文件列表", operation, cancellable=False)

    def _choose_upload(self) -> None:
        selected = filedialog.askopenfilenames(parent=self.window)
        if not selected:
            return
        self.upload_paths(selected)

    def upload_paths(self, paths: Iterable[str | Path]) -> bool:
        candidates = tuple(Path(path) for path in paths if str(path))
        if not candidates:
            self._set_status("没有可上传的文件", "warning")
            return False
        if self._worker is not None:
            self._set_status("请等待当前文件操作结束后再拖入", "warning")
            return False

        def operation(
            progress: ProgressCallback, cancelled: Callable[[], bool]
        ) -> _JobResult:
            current = self._config_provider()
            valid: list[tuple[Path, int]] = []
            failures: list[tuple[str, str]] = []
            for path in candidates:
                if cancelled():
                    raise FileTransferCancelled("文件传输已取消")
                try:
                    valid.append(
                        (path, inspect_upload_path(path, current.file_max_bytes))
                    )
                except ValueError as exc:
                    failures.append((path.name or str(path), str(exc)))

            total_bytes = sum(size for _path, size in valid)
            completed_bytes = 0
            uploaded = 0
            client = self._client(current)

            for path, expected_size in valid:
                if cancelled():
                    raise FileTransferCancelled("文件传输已取消")
                reported_total = expected_size

                def report_file(
                    completed: int,
                    total: int,
                    *,
                    offset: int = completed_bytes,
                ) -> None:
                    nonlocal total_bytes, reported_total
                    if total != reported_total:
                        total_bytes += total - reported_total
                        reported_total = total
                    progress(offset + completed, total_bytes)

                try:
                    client.upload_file(
                        path,
                        origin=current.device_id,
                        progress=report_file,
                        cancelled=cancelled,
                    )
                    uploaded += 1
                except FileTransferCancelled:
                    raise
                except (ConnectionFailure, OSError, ValueError) as exc:
                    failures.append((path.name or str(path), str(exc)))
                finally:
                    completed_bytes += reported_total
                    progress(completed_bytes, total_bytes)

            records: list[FileRecord] | None = None
            if uploaded:
                try:
                    records = client.list_files()
                except ConnectionFailure as exc:
                    failures.append(("刷新文件列表", str(exc)))

            if failures:
                detail = _format_upload_failures(failures)
                if uploaded:
                    return _JobResult(
                        f"已上传 {uploaded} 个文件，{len(failures)} 个失败：{detail}",
                        records,
                        "warning",
                    )
                return _JobResult(f"上传失败：{detail}", records, "error")

            return _JobResult(f"已上传 {uploaded} 个文件", records)

        label = candidates[0].name if len(candidates) == 1 else f"{len(candidates)} 个文件"
        return self._start_job(f"正在上传：{label}", operation)

    def _selected_record(self) -> FileRecord | None:
        selection = self._tree.selection()
        if not selection:
            return None
        return self._records.get(selection[0])

    def _download_selected(self, _event: object | None = None) -> None:
        record = self._selected_record()
        if record is None or self._worker is not None:
            return

        def operation(
            progress: ProgressCallback, cancelled: Callable[[], bool]
        ) -> _JobResult:
            current = self._config_provider()
            destination = self._client(current).download_file(
                record.file_id,
                current.download_directory,
                progress=progress,
                cancelled=cancelled,
            )
            return _JobResult(f"已下载到：{destination}")

        self._start_job(f"正在下载：{record.metadata.filename}", operation)

    def _delete_selected(self) -> None:
        record = self._selected_record()
        if record is None or self._worker is not None:
            return
        if not messagebox.askyesno(
            "删除服务器文件",
            f"确定删除“{record.metadata.filename}”吗？",
            parent=self.window,
        ):
            return

        def operation(
            _progress: ProgressCallback, cancelled: Callable[[], bool]
        ) -> _JobResult:
            if cancelled():
                raise FileTransferCancelled("文件操作已取消")
            client = self._client()
            client.delete_file(record.file_id)
            records = client.list_files()
            return _JobResult(f"已删除：{record.metadata.filename}", records)

        self._start_job("正在删除文件", operation, cancellable=False)

    def _client(self, config: AppConfig | None = None) -> FileTransferClient:
        current = self._config_provider() if config is None else config
        return FileTransferClient(current.effective_server_url, current.token)

    def _start_job(
        self,
        status: str,
        operation: Job,
        *,
        cancellable: bool = True,
    ) -> bool:
        if self._worker is not None:
            self._set_status("请等待当前文件操作结束", "warning")
            return False
        self._cancel.clear()
        self._started_at = time.monotonic()
        self._last_progress_at = self._started_at
        self._last_progress_bytes = 0
        self._progress.configure(value=0)
        self._progress_var.set("")
        self._set_status(status)
        self._set_busy(True, cancellable=cancellable)

        def report(completed: int, total: int) -> None:
            self._events.put(("progress", (completed, total, time.monotonic())))

        def worker() -> None:
            try:
                result = operation(report, self._cancel.is_set)
                self._events.put(("result", result))
            except FileTransferCancelled as exc:
                self._events.put(("cancelled", str(exc)))
            except (ConnectionFailure, OSError, ValueError) as exc:
                self._events.put(("error", str(exc)))
            except Exception as exc:
                self._events.put(("error", f"文件操作失败：{exc}"))
            finally:
                self._events.put(("finished", ""))

        self._worker = threading.Thread(
            target=worker,
            name="flowclip-file-transfer",
            daemon=True,
        )
        self._worker.start()
        return True

    def _cancel_job(self) -> None:
        if self._worker is None:
            return
        self._cancel.set()
        self._cancel_button.configure(state="disabled")
        self._set_status("正在取消文件传输", "warning")

    def _drain_events(self) -> None:
        if not self.window.winfo_exists():
            return
        try:
            while True:
                kind, payload = self._events.get_nowait()
                if kind == "progress":
                    completed, total, measured_at = payload  # type: ignore[misc]
                    self._show_progress(int(completed), int(total), float(measured_at))
                elif kind == "result":
                    result = payload
                    if isinstance(result, _JobResult):
                        if result.records is not None:
                            self._replace_records(result.records)
                        self._set_status(result.message, result.level)
                elif kind == "cancelled":
                    self._set_status(str(payload) or "文件传输已取消", "warning")
                elif kind == "error":
                    self._set_status(str(payload), "error")
                elif kind == "drop":
                    if isinstance(payload, tuple) and all(
                        isinstance(path, str) for path in payload
                    ):
                        self.upload_paths(payload)
                elif kind == "finished":
                    self._worker = None
                    self._set_busy(False)
        except queue.Empty:
            pass
        self.window.after(100, self._drain_events)

    def _show_progress(self, completed: int, total: int, measured_at: float) -> None:
        fraction = completed / total if total else 1.0
        self._progress.configure(value=max(0, min(1000, fraction * 1000)))
        elapsed = max(0.001, measured_at - self._started_at)
        average_rate = completed / elapsed
        interval = measured_at - self._last_progress_at
        if interval > 0 and completed >= self._last_progress_bytes:
            instant_rate = (completed - self._last_progress_bytes) / interval
            rate = instant_rate if self._last_progress_bytes else average_rate
        else:
            rate = average_rate
        self._last_progress_at = measured_at
        self._last_progress_bytes = completed
        self._progress_var.set(
            f"{format_byte_count(completed)} / {format_byte_count(total)}  "
            f"{format_transfer_rate(rate)}"
        )
        self._notify_status(
            "muted",
            f"文件传输：{format_byte_count(completed)} / {format_byte_count(total)}",
        )

    def _replace_records(self, records: list[FileRecord]) -> None:
        selected = self._tree.selection()
        selected_id = selected[0] if selected else None
        self._tree.delete(*self._tree.get_children())
        self._records = {record.file_id: record for record in records}
        for record in records:
            metadata = record.metadata
            try:
                created = datetime.fromtimestamp(metadata.created_at / 1000).strftime(
                    "%Y-%m-%d %H:%M"
                )
            except (OSError, OverflowError, ValueError):
                created = "--"
            origin = metadata.origin
            if len(origin) > 18:
                origin = f"{origin[:8]}...{origin[-6:]}"
            self._tree.insert(
                "",
                "end",
                iid=record.file_id,
                values=(metadata.filename, format_byte_count(metadata.size), origin, created),
            )
        if selected_id in self._records:
            self._tree.selection_set(selected_id)
        self._selection_changed()

    def _selection_changed(self, _event: object | None = None) -> None:
        selectable = self._selected_record() is not None and self._worker is None
        state = "normal" if selectable else "disabled"
        self._download_button.configure(state=state)
        self._delete_button.configure(state=state)

    def _set_busy(self, busy: bool, *, cancellable: bool = False) -> None:
        state = "disabled" if busy else "normal"
        self._upload_button.configure(state=state)
        self._refresh_button.configure(state=state)
        self._save_settings_button.configure(state=state)
        self._cancel_button.configure(
            state="normal" if busy and cancellable else "disabled"
        )
        self._selection_changed()

    def _set_status(self, message: str, level: str = "muted") -> None:
        colors = {
            "muted": "#68737d",
            "success": "#117864",
            "warning": "#9a6700",
            "error": "#b03a2e",
        }
        self._status_var.set(message)
        self._notify_status(level, message)
        try:
            self._status_label.configure(foreground=colors.get(level, colors["muted"]))
        except tk.TclError:
            return

    def _notify_status(self, level: str, message: str) -> None:
        if self._status_callback is None:
            return
        try:
            self._status_callback(level, message)
        except Exception:
            return
