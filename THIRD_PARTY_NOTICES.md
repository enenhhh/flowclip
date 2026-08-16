# Third-Party Notices

FlowClip's own source code is licensed under the MIT License. Complete upstream
license and notice files for redistributed components are included in
`THIRD_PARTY_LICENSES/`. The original text in that directory takes precedence
over this summary.

## Redistributed runtime components

| Component | Version | Purpose | License files |
| --- | --- | --- | --- |
| CPython | 3.11 | Frozen desktop runtime | `PYTHON-PSF.txt` |
| Pillow | 10.4.0 | Image decoding and PNG conversion | `PILLOW.txt` |
| PyStray | 0.19.5 | System tray and macOS menu bar | `PYSTRAY-GPL-3.0.txt`, `PYSTRAY-LGPL-3.0.txt` |
| six | 1.17.0 | PyStray/python-xlib compatibility | `SIX-MIT.txt` |
| python-xlib | 0.33 | Linux X11 tray backend | `PYTHON-XLIB-LGPL-2.1.txt` |
| PyObjC / Cocoa / Quartz | 10.3.1 | macOS clipboard and menu bar integration | `PYOBJC-MIT.txt` |
| PyInstaller bootloader | 6.10.0 | Native desktop launcher | `PYINSTALLER.txt` |
| NanoHTTPD | 2.3.1 | Android embedded HTTP server | `NANOHTTPD-BSD-3-CLAUSE.txt` |

Linux clipboard operation additionally invokes the separately installed
`wl-clipboard` or `xclip` program. Those programs are not bundled in FlowClip's
Linux archive and remain subject to their distribution packages' licenses.

## Build and test components

The Android Gradle project redistributes the Gradle 8.9 Wrapper JAR and uses JUnit
4.13.2, Hamcrest Core 1.3 and JSON-java 20240303 for local tests. Their files are
`GRADLE-LICENSE.txt`, `GRADLE-NOTICE.txt`, `JUNIT-EPL-1.0.txt`,
`HAMCREST-BSD.txt` and `JSON-JAVA.txt`. Android SDK and Android Gradle Plugin
artifacts are downloaded by the builder and are not included in FlowClip release
archives.

The universal desktop build lock also pins the following build-time packages.
Platform markers select only the packages needed by the native build host;
modules not imported by FlowClip are not copied into the frozen runtime.

| Component | Version | Purpose | License files |
| --- | --- | --- | --- |
| altgraph | 0.17.5 | PyInstaller dependency graph | `ALTGRAPH-MIT.txt` |
| macholib | 1.16.3 | PyInstaller macOS binary analysis | `MACHOLIB-MIT.txt` |
| packaging | 26.3 | Version and marker handling | `PACKAGING-LICENSE.txt`, `PACKAGING-APACHE-2.0.txt`, `PACKAGING-BSD-2-CLAUSE.txt` |
| pefile | 2024.8.26 | PyInstaller Windows PE analysis | `PEFILE-MIT.txt` |
| PyInstaller Hooks Contrib | 2026.6 | PyInstaller package hooks | `PYINSTALLER-HOOKS-CONTRIB.txt` |
| pywin32-ctypes | 0.2.3 | PyInstaller Windows integration | `PYWIN32-CTYPES-BSD-3-CLAUSE.txt` |
| setuptools | 78.1.1 | Python build tooling | `SETUPTOOLS-MIT.txt` |

Release maintainers must re-check both this notice and the license bundle whenever
a dependency version changes.
