# 构建与发布

本文适用于 FlowClip 1.4.0。桌面端使用 Python 3.11 和 PyInstaller，Android 使用 Java 17、Gradle 8.9 与 Android SDK 35。

## 原生构建原则

PyInstaller 不是交叉编译器。三个桌面产物必须在对应系统的原生 runner 上分别生成：

| 构建系统 | 原始输出 |
| --- | --- |
| Windows x86_64 | `dist/FlowClip.exe` |
| Linux x86_64 | `dist/FlowClip` |
| macOS | `dist/FlowClip.app` |

不要把 Windows 生成的目录改名后当作 Linux/macOS 成品，也不要在 Wine 中生成正式 Windows Release。Python、Tk、Pillow、PyStray 和平台剪贴板组件都包含平台相关二进制或系统集成。某一平台的源码检查或模拟测试不能证明另一个平台的产物可运行；Linux 和 macOS 正式成品只能在对应的原生系统或项目的对应 GitHub runner 上构建，并在目标环境验证。

## 桌面开发环境

通用要求：

- Python 3.11，且包含 `tkinter`/Tk 8.6；
- 可访问 Python 包索引或已经准备好依赖缓存；
- 目标桌面系统的图形会话；Linux 的 Tk 8.6 当前需要 X11/XWayland `DISPLAY`；
- 足够空间存放虚拟环境和 PyInstaller 工作目录。

### Python 依赖锁

`desktop/requirements.in` 是运行时直接及传递依赖的人工审查精确 pin 集合，`desktop/requirements-build.in` 在此基础上固定测试和打包所需的直接及传递依赖。对应的 `.txt` 文件是提交到仓库的 universal hash locks，包含解析结果、环境标记和 SHA-256 哈希；不要手工编辑锁文件。

维护锁文件时必须使用 Python 3.11，并在独立工具环境中固定安装 `uv==0.12.5`。以下命令从仓库根目录运行；`--universal` 为所有受支持桌面平台解析同一组带环境标记的锁，`--no-build` 禁止解析过程中从源码构建包：

```bash
python -m pip install --only-binary=:all: "uv==0.12.5"
uv pip compile desktop/requirements.in --universal --python-version 3.11 --generate-hashes --no-header --no-build --output-file desktop/requirements.txt
uv pip compile desktop/requirements-build.in --universal --python-version 3.11 --generate-hashes --no-header --no-build --output-file desktop/requirements-build.txt
```

修改任一 `.in` 文件后应重新生成并审查两个锁文件，再把输入文件和生成结果放在同一个 Pull Request 中。不要在可复现构建流程中无版本约束地升级 pip；如确需更换 pip，必须固定并单独审查版本。

安装运行时、测试或构建依赖时必须从相应 `.txt` 锁文件安装。测试和打包使用包含完整依赖集合的 build lock：

```bash
python -m pip install --require-hashes --only-binary=:all: -r desktop/requirements-build.txt
python -m pip check
```

`--require-hashes` 拒绝锁外或哈希不匹配的文件，`--only-binary=:all:` 在目标平台没有已锁定 wheel 时直接失败，而不是临时执行第三方源码构建。`pip check` 必须紧随安装执行。universal lock 统一依赖输入，但不能替代 Windows、Linux 和 macOS 的原生安装与构建验证。

### Windows

在仓库根目录运行：

```powershell
py -3.11 -m venv .venv
.\.venv\Scripts\python.exe -m pip install --require-hashes --only-binary=:all: -r desktop\requirements-build.txt
.\.venv\Scripts\python.exe -m pip check
.\.venv\Scripts\python.exe -m unittest discover -s desktop\tests -p "test_*.py"
.\.venv\Scripts\python.exe desktop\build.py
```

构建脚本生成单文件 GUI 程序，嵌入 ICO 和 Windows 版本资源。

### Linux

安装带 Tk 的 Python 3.11，并按当前桌面会话安装剪贴板工具。不同发行版提供 Python 3.11 的方式不同，不要假设系统默认 `python3` 一定是 3.11。Debian/Ubuntu 的剪贴板工具示例：

```bash
sudo apt-get update
# Wayland:
sudo apt-get install wl-clipboard
# X11:
sudo apt-get install xclip
```

然后在仓库根目录运行：

```bash
python3.11 -m venv .venv
. .venv/bin/activate
python -c "import tkinter; print(tkinter.TkVersion)"
python -m pip install --require-hashes --only-binary=:all: -r desktop/requirements-build.txt
python -m pip check
python -m unittest discover -s desktop/tests -p "test_*.py"
python desktop/build.py
```

`python-xlib` 由 Linux 条件依赖安装，供托盘的 X11 后端使用。Wayland 原生剪贴板需要 `wl-copy` 和 `wl-paste`；Tk 主窗口与托盘仍需要桌面环境提供 XWayland，完全没有 `DISPLAY` 的纯 Wayland 会话当前不受支持。

PyInstaller Linux 可执行文件依赖构建机的 glibc 基线。正式 x86_64 Release 使用 GitHub `ubuntu-22.04` runner 构建；其他发行版需要实际验证，不能仅凭构建成功声称兼容。

### macOS

准备带 Tk 的 Python 3.11，然后运行：

```bash
python3.11 -m venv .venv
. .venv/bin/activate
python -m pip install --require-hashes --only-binary=:all: -r desktop/requirements-build.txt
python -m pip check
python -m unittest discover -s desktop/tests -p "test_*.py"
python desktop/build.py
```

`pyobjc-framework-Cocoa` 和 PyStray 所需的 `pyobjc-framework-Quartz` 只在 macOS 安装，并与 `pyobjc-core` 固定为同一版本；Cocoa 用于 `NSPasteboard`。构建脚本生成 ICNS，并通过受控的 `desktop/FlowClip.spec` 让 PyInstaller 创建 bundle identifier 为 `com.flowclip.desktop` 的 `FlowClip.app`。应用的 `CFBundleShortVersionString` 和 `CFBundleVersion` 均取自桌面源码版本，Release 在归档前会再次校验。登录自启动另用 `io.github.flowclip.FlowClip` 作为 LaunchAgent label。

macOS 产物只能保证与实际构建 runner 的架构一致。Release 文件名通过 `uname -m` 标明 `x86_64` 或 `arm64`，不要手工把一个架构重命名成另一个架构。

## 自定义输出位置

`desktop/build.py` 支持独立的成品和工作目录：

```bash
python desktop/build.py --output-dir /absolute/output/path --work-dir /absolute/work/path
```

默认成品目录是仓库根目录的 `dist/`。默认工作目录是根目录的 `.build/`；也可以通过 `FLOWCLIP_BUILD_DIR` 修改默认工作目录。PyInstaller 的分析文件、临时文件和配置缓存都会放入该工作目录。成品与工作目录均已从 Git 提交中忽略。

`desktop/requirements*.in` 保存人工审查的直接及传递依赖精确 pin，`desktop/requirements*.txt` 保存由固定版 uv 生成的 universal hash locks。升级任一依赖时必须在三个桌面 runner 上重新安装并测试，同时复核 `THIRD_PARTY_NOTICES.md` 与 `THIRD_PARTY_LICENSES/`。版本和哈希锁定减少了解析及下载漂移，但操作系统、Python runner 和原生工具链仍会影响字节级构建结果。

## 桌面测试

跨平台单元测试入口：

```bash
python -m unittest discover -s desktop/tests -p "test_*.py"
```

测试覆盖剪贴板与 `files-v1` 协议边界、流式文件读写、Range/ETag、服务器限制、配置迁移、同步冲突、剪贴板后端选择、自启动、单实例和无界面冻结包导入冒烟。Release 在三个原生产物上运行 `--package-smoke`，Windows 还执行完整成品回环测试。模拟测试不能代替目标系统的真机验证。发布前还应人工验证：

- 文本和图片的发送、接收与自动回传抑制；
- 本机剪贴板在接收过程中变化时不会被旧内容覆盖；
- 托盘/菜单栏打开、隐藏、退出和单实例行为；
- 登录自启动创建、下次登录启动和关闭后移除；
- Linux X11 与 Wayland 会话；
- macOS Gatekeeper 行为和应用架构；
- Windows 防火墙与 SmartScreen 行为。
- 大文件上传、下载、取消、网络中断后的桌面续传及 SHA-256 校验。

## Android 工程

Android 工程要求：

- JDK 17；
- Android SDK Platform 35；
- Android Build Tools 35.0.0；
- Gradle Wrapper 8.9。

Linux/macOS：

```bash
cd android
./gradlew testDebugUnitTest lintDebug assembleRelease
```

Windows：

```powershell
cd android
.\gradlew.bat --no-daemon testDebugUnitTest lintDebug assembleDebug assembleRelease
```

`assembleRelease` 生成未签名 APK。运行这些命令只得到本机构建结果，不等于项目维护者已经发布签名 APK。GitHub Release 只附加 Android Gradle 项目归档，不把未签名输出作为正式安装包发布。

需要减少系统盘占用时，可传入仓库外的构建根目录：

```powershell
.\gradlew.bat --no-daemon -PflowclipBuildDir=D:\DevCache\flowclip-android-build testDebugUnitTest lintDebug assembleDebug assembleRelease
```

此时 APK 位于 `<flowclipBuildDir>/app/outputs/apk/`。Android 工程根目录的 `README.md` 包含独立归档所需的环境、输出与 Release 签名说明，`.gitignore` 会排除本地 SDK 路径、构建目录和签名材料。

Android 运行时依赖 `org.nanohttpd:nanohttpd:2.3.1` 提供内置 HTTP 服务器，许可证为 BSD-3-Clause；完整依赖摘要见仓库根目录的 `THIRD_PARTY_NOTICES.md`。

Gradle Wrapper 的分发包带 SHA-256 校验。`android/gradle/verification-metadata.xml` 保存 Gradle 依赖及插件工件的校验 metadata；依赖变更必须审查并更新该文件，不能通过关闭依赖验证绕过不匹配。项目优先使用国内 Maven 镜像，并保留官方仓库作为后备；CI 网络故障时需要区分镜像不可达与源码构建失败。

## Android Release 签名

需要自行分发 APK 时，在隔离位置生成自己的长期 Release 密钥：

```bash
keytool -genkeypair -v -keystore flowclip-release.jks -alias flowclip -keyalg RSA -keysize 4096 -validity 10000
./gradlew assembleRelease
zipalign -p -f 4 app/build/outputs/apk/release/app-release-unsigned.apk FlowClip-aligned.apk
apksigner sign --ks flowclip-release.jks --out FlowClip-release.apk FlowClip-aligned.apk
apksigner verify --verbose --print-certs FlowClip-release.apk
```

`.jks`、密码和 CI secret 不得提交到仓库。签名密钥决定后续版本能否覆盖安装，至少保存两份离线备份。Debug 密钥和 Debug APK 只适合测试。

## GitHub CI

`.github/workflows/ci.yml` 在 push、Pull Request 和手动触发时执行：

- Windows、Ubuntu 和 macOS 上的 Python 3.11 哈希锁安装、wheel-only 校验、`pip check`、编译检查与桌面单元测试；
- 桌面、Windows 版本资源和 Android `versionName` 的一致性检查；
- Android 单元测试、Lint 和未签名 Release 构建验证。

`.github/workflows/codeql.yml` 扫描 Python 和 Java，`dependency-review.yml` 检查 Pull Request 的高危依赖变化，Dependabot 每周检查 Python、Gradle 和 GitHub Actions 依赖。所有第三方 Action 都固定到经过 GitHub 验证的 40 位提交 SHA；更新时必须核对上游具体版本标签、提交签名和变更记录。

CI 在 GitHub 托管 runner 上运行，不会占用开发者电脑同时编译多个平台。

## GitHub Release

`.github/workflows/release.yml` 只接受与源码版本一致、形如 `vMAJOR.MINOR.PATCH` 的稳定语义化标签，例如：

```bash
git tag v1.4.0
git push origin v1.4.0
```

自动 Release 当前不接受 prerelease 或 build metadata 标签；需要预发布时应先增加独立的预发布标记、macOS bundle 版本映射和发布测试，不能把它伪装成稳定 Release。

工作流会：

1. 检查标签来自仓库默认分支，并验证桌面、Windows 资源和 Android 版本一致；
2. 在 Windows、Ubuntu 和 macOS 原生 runner 上运行测试、构建及冻结成品导入冒烟；
3. 运行 Android 单元测试、Lint 和未签名构建验证；
4. 打包 Windows EXE/ZIP、Linux tar.gz、macOS ZIP/DMG、Android Gradle 项目和完整第三方许可证；
5. 生成 `SHA256SUMS`；
6. 为发布文件生成 GitHub build provenance attestation；
7. 创建带当前 workflow run 所有权标记的 draft，上传全部资产后再公开；工作流拒绝覆盖已有的同名 GitHub Release。

普通失败或取消会触发独立清理 job。清理逻辑同时核对数值 Release ID、标签、draft 状态和当前 `GITHUB_RUN_ID` 隐藏标记，不会删除已有 Release、已公开 Release 或标签。runner 被平台强制终止、GitHub API 完全不可用等情况仍可能留下草稿；恢复时只能人工删除带对应 `flowclip-draft-owner` 标记的 draft，再重跑，不要删除发布标签。

源码和应用内部使用纯语义版本号 `1.4.0`；Git 标签使用惯例前缀
`v1.4.0`，Release 资产名直接包含完整标签。不要把 `v` 写入 Python
`__version__`、Android `versionName` 或 Windows 版本资源。

当前产物命名示例：

```text
FlowClip-v1.4.0-windows-x86_64.exe
FlowClip-v1.4.0-windows-x86_64.zip
FlowClip-v1.4.0-linux-x86_64.tar.gz
FlowClip-v1.4.0-macos-<runner-arch>.zip
FlowClip-v1.4.0-macos-<runner-arch>.dmg
FlowClip-v1.4.0-android-gradle-project.zip
FlowClip-v1.4.0-third-party-licenses.zip
LICENSE.txt
THIRD_PARTY_NOTICES.md
SHA256SUMS
```

各平台归档都携带 `THIRD_PARTY_NOTICES.md` 和完整 `THIRD_PARTY_LICENSES/`；Release 另提供独立许可证 ZIP，便于只下载裸 Windows EXE 的用户取得对应文本。

## 桌面签名与系统提示

当前 Release workflow 不依赖桌面签名 secrets：

- Windows EXE 未使用 Authenticode 证书，下载后可能出现 SmartScreen 提示。
- macOS app 未提供 Developer ID Application 证书、签名密码和 Apple 公证凭据时，不会完成签名/公证，下载后会出现 Gatekeeper 提示。

正式面向大量用户分发时，应在仓库 secret 中配置签名材料并增加独立签名步骤。任何来自 fork 的 Pull Request 都不能获得这些 secrets。签名完成后必须再次验证最终归档和校验和。

不要指导用户全局关闭 SmartScreen 或 Gatekeeper。测试未签名构建时，应先核对 `SHA256SUMS`，再使用操作系统针对单个应用提供的确认流程。

## 发布检查表

- 所有版本字段一致，`CHANGELOG.md` 已更新；
- Windows、Linux、macOS 和 Android CI 全部通过；
- 已取得目标平台文本/图片/文件、托盘、自启动和服务器模式的原生验证结果；
- Release 标签来自受保护的主分支；
- Android 项目归档不包含 `local.properties`、构建目录或签名材料；
- 所有发布文件都列入 `SHA256SUMS`；
- 已记录未签名桌面产物的系统提示；
- GitHub Private Vulnerability Reporting 和分支保护已启用。
