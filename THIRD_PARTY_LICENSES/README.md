# Third-Party License Bundle

These files are verbatim license or notice files supplied by the corresponding
upstream distributions. `THIRD_PARTY_NOTICES.md` describes which components are
bundled at runtime and which are used only to build or test FlowClip.

| Component | Version | Files in this directory |
| --- | --- | --- |
| CPython | 3.11 | `PYTHON-PSF.txt` |
| Pillow | 10.4.0 | `PILLOW.txt` |
| PyStray | 0.19.5 | `PYSTRAY-GPL-3.0.txt`, `PYSTRAY-LGPL-3.0.txt` |
| six | 1.17.0 | `SIX-MIT.txt` |
| python-xlib | 0.33 | `PYTHON-XLIB-LGPL-2.1.txt` |
| PyObjC / Cocoa / Quartz | 10.3.1 | `PYOBJC-MIT.txt` |
| PyInstaller bootloader | 6.10.0 | `PYINSTALLER.txt` |
| altgraph | 0.17.5 | `ALTGRAPH-MIT.txt` |
| macholib | 1.16.3 | `MACHOLIB-MIT.txt` |
| packaging | 26.3 | `PACKAGING-LICENSE.txt`, `PACKAGING-APACHE-2.0.txt`, `PACKAGING-BSD-2-CLAUSE.txt` |
| pefile | 2024.8.26 | `PEFILE-MIT.txt` |
| PyInstaller Hooks Contrib | 2026.6 | `PYINSTALLER-HOOKS-CONTRIB.txt` |
| pywin32-ctypes | 0.2.3 | `PYWIN32-CTYPES-BSD-3-CLAUSE.txt` |
| setuptools | 78.1.1 | `SETUPTOOLS-MIT.txt` |
| NanoHTTPD | 2.3.1 | `NANOHTTPD-BSD-3-CLAUSE.txt` |
| Gradle Wrapper / Gradle | 8.9 | `GRADLE-LICENSE.txt`, `GRADLE-NOTICE.txt` |
| JUnit | 4.13.2 | `JUNIT-EPL-1.0.txt` |
| Hamcrest Core | 1.3 | `HAMCREST-BSD.txt` |
| JSON-java | 20240303 | `JSON-JAVA.txt` |

The Pillow license file contains the notices for native image libraries shipped
by Pillow itself. Platform packages can contain only the subset applicable to
that platform. The complete bundle includes every package pinned by the desktop
build lock plus the Android build and test components listed above, keeping one
auditable license set for all FlowClip artifacts.
