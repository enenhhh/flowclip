from __future__ import annotations

import argparse
import ctypes
import sys
import tkinter as tk
from pathlib import Path
from tkinter import messagebox


def _prepare_import_path() -> None:
    if not getattr(sys, "frozen", False):
        source = Path(__file__).resolve().parent / "src"
        sys.path.insert(0, str(source))


def _show_message(title: str, message: str, *, error: bool) -> None:
    temporary_root: tk.Tk | None = None
    try:
        temporary_root = tk.Tk()
        temporary_root.withdraw()
        temporary_root.update_idletasks()
        show = messagebox.showerror if error else messagebox.showinfo
        show(title, message, parent=temporary_root)
    except tk.TclError:
        stream = sys.stderr if error else sys.stdout
        print(f"{title}: {message}", file=stream)
    finally:
        if temporary_root is not None:
            try:
                temporary_root.destroy()
            except tk.TclError:
                pass


def main() -> int:
    _prepare_import_path()
    parser = argparse.ArgumentParser(add_help=False)
    parser.add_argument("--minimized", action="store_true")
    parser.add_argument("--package-smoke", action="store_true")
    args, _unknown = parser.parse_known_args()

    if args.package_smoke:
        import flowclip.file_ui  # noqa: F401
        import flowclip.ui  # noqa: F401

        if sys.platform == "win32":
            import flowclip.clipboard_windows  # noqa: F401
        elif sys.platform == "darwin":
            import flowclip.clipboard_macos  # noqa: F401
        elif sys.platform.startswith("linux"):
            import flowclip.clipboard_linux  # noqa: F401
        else:
            return 1
        return 0

    from flowclip.config import load_config
    from flowclip.platform_integration import (
        SingleInstanceError,
        acquire_single_instance,
        release_single_instance,
    )

    try:
        acquired = acquire_single_instance()
    except SingleInstanceError as exc:
        _show_message("FlowClip 启动失败", str(exc), error=True)
        return 1
    if not acquired:
        _show_message(
            "FlowClip",
            "FlowClip 已在运行，请查看系统托盘或菜单栏。",
            error=False,
        )
        return 0

    try:
        if sys.platform == "win32":
            try:
                ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID(
                    "FlowClip.Desktop.1"
                )
            except (AttributeError, OSError):
                pass

        root: tk.Tk | None = None
        try:
            config = load_config()
            from flowclip.ui import FlowClipWindow

            root = tk.Tk()
            FlowClipWindow(root, config, minimized=args.minimized)
            root.mainloop()
            return 0
        except (OSError, RuntimeError, ValueError, tk.TclError) as exc:
            if root is not None:
                try:
                    root.destroy()
                except tk.TclError:
                    pass
            _show_message("FlowClip 启动失败", str(exc), error=True)
            return 1
    finally:
        release_single_instance()


if __name__ == "__main__":
    raise SystemExit(main())
