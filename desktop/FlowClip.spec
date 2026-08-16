# -*- mode: python ; coding: utf-8 -*-

import sys
from pathlib import Path


ROOT = Path(SPECPATH).resolve()
SOURCE = ROOT / "src"
sys.path.insert(0, str(SOURCE))

from flowclip import __version__


WINDOWS_EXCLUDES = [
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
]


if sys.platform == "win32":
    hidden_imports = ["pystray._win32"]
    excluded_imports = WINDOWS_EXCLUDES
elif sys.platform == "darwin":
    hidden_imports = ["pystray._darwin", "AppKit", "Foundation"]
    excluded_imports = []
elif sys.platform.startswith("linux"):
    hidden_imports = ["pystray._xorg"]
    excluded_imports = []
else:
    raise RuntimeError(f"Unsupported build platform: {sys.platform}")

a = Analysis(
    [str(ROOT / "main.py")],
    pathex=[str(SOURCE)],
    binaries=[],
    datas=[],
    hiddenimports=hidden_imports,
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=excluded_imports,
    noarchive=False,
    optimize=0,
)
pyz = PYZ(a.pure)

if sys.platform == "darwin":
    exe = EXE(
        pyz,
        a.scripts,
        [],
        exclude_binaries=True,
        name="FlowClip",
        debug=False,
        bootloader_ignore_signals=False,
        strip=False,
        upx=False,
        console=False,
        disable_windowed_traceback=False,
        argv_emulation=False,
        target_arch=None,
        codesign_identity=None,
        entitlements_file=None,
    )
    coll = COLLECT(
        exe,
        a.binaries,
        a.datas,
        strip=False,
        upx=False,
        upx_exclude=[],
        name="FlowClip",
    )
    app = BUNDLE(
        coll,
        name="FlowClip.app",
        icon=str(ROOT / "assets" / "flowclip.icns"),
        bundle_identifier="com.flowclip.desktop",
        version=__version__,
        info_plist={"CFBundleVersion": __version__},
    )
elif sys.platform == "win32":
    exe = EXE(
        pyz,
        a.scripts,
        a.binaries,
        a.datas,
        [],
        name="FlowClip",
        debug=False,
        bootloader_ignore_signals=False,
        strip=False,
        upx=False,
        upx_exclude=[],
        runtime_tmpdir=None,
        console=False,
        disable_windowed_traceback=False,
        argv_emulation=False,
        target_arch=None,
        codesign_identity=None,
        entitlements_file=None,
        version=str(ROOT / "assets" / "version_info.txt"),
        icon=[str(ROOT / "assets" / "flowclip.ico")],
    )
else:
    exe = EXE(
        pyz,
        a.scripts,
        a.binaries,
        a.datas,
        [],
        name="FlowClip",
        debug=False,
        bootloader_ignore_signals=False,
        strip=False,
        upx=False,
        upx_exclude=[],
        runtime_tmpdir=None,
        console=False,
        disable_windowed_traceback=False,
        argv_emulation=False,
        target_arch=None,
        codesign_identity=None,
        entitlements_file=None,
    )
