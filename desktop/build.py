from __future__ import annotations

import argparse
import os
import platform
import subprocess
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parent
WORKSPACE = ROOT.parent
DEFAULT_DIST = WORKSPACE / "dist"
DEFAULT_BUILD = Path(
    os.environ.get("FLOWCLIP_BUILD_DIR", str(WORKSPACE / ".build"))
)


def platform_family(value: str | None = None) -> str:
    current = sys.platform if value is None else value
    if current == "win32":
        return "windows"
    if current == "darwin":
        return "macos"
    if current.startswith("linux"):
        return "linux"
    raise RuntimeError(f"Unsupported build platform: {current}")


def generate_icon(family: str) -> Path:
    sys.path.insert(0, str(ROOT / "src"))
    from flowclip.artwork import create_app_icon

    if family == "windows":
        target = ROOT / "assets" / "flowclip.ico"
    elif family == "macos":
        target = ROOT / "assets" / "flowclip.icns"
    else:
        target = ROOT / "assets" / "flowclip.png"
    target.parent.mkdir(parents=True, exist_ok=True)
    if family == "windows":
        image = create_app_icon(256)
        image.save(
            target,
            format="ICO",
            sizes=[
                (16, 16),
                (24, 24),
                (32, 32),
                (48, 48),
                (64, 64),
                (128, 128),
                (256, 256),
            ],
        )
    elif family == "macos":
        create_app_icon(1024).save(target, format="ICNS")
    else:
        create_app_icon(512).save(target, format="PNG")
    return target


def build_command(output_dir: Path, work_dir: Path) -> list[str]:
    return [
        sys.executable,
        "-m",
        "PyInstaller",
        "--noconfirm",
        "--clean",
        "--distpath",
        str(output_dir),
        "--workpath",
        str(work_dir / "pyinstaller"),
        str(ROOT / "FlowClip.spec"),
    ]


def build_environment(work_dir: Path) -> dict[str, str]:
    environment = os.environ.copy()
    environment["PYINSTALLER_CONFIG_DIR"] = str(work_dir / "pyinstaller-config")
    return environment


def main() -> int:
    parser = argparse.ArgumentParser(description="Build FlowClip for the host platform")
    parser.add_argument("--output-dir", type=Path, default=DEFAULT_DIST)
    parser.add_argument("--work-dir", type=Path, default=DEFAULT_BUILD)
    args = parser.parse_args()

    try:
        family = platform_family()
    except RuntimeError as exc:
        print(str(exc), file=sys.stderr)
        return 2

    generate_icon(family)
    output_dir = args.output_dir.resolve()
    work_dir = args.work_dir.resolve()
    output_dir.mkdir(parents=True, exist_ok=True)
    work_dir.mkdir(parents=True, exist_ok=True)
    command = build_command(output_dir, work_dir)
    print(
        f"Building FlowClip for {platform.system()} {platform.machine()} -> {output_dir}"
    )
    return subprocess.call(
        command,
        cwd=ROOT,
        env=build_environment(work_dir),
    )


if __name__ == "__main__":
    raise SystemExit(main())
