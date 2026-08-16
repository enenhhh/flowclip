# 参与贡献

感谢你改进 FlowClip。提交代码前，请先搜索现有 Issue 和 Pull Request，避免重复工作。涉及安全漏洞时，请遵循 [安全政策](SECURITY.md)，不要公开提交攻击细节。

## 开发环境

桌面端需要 Python 3.11。建议使用独立虚拟环境，并从提交到仓库的 build lock 安装依赖：

```bash
python -m venv .venv
python -m pip install --require-hashes --only-binary=:all: -r desktop/requirements-build.txt
python -m pip check
```

这里的 `python` 必须解析到 Python 3.11。不要在开发、CI 或 Release 指令中无版本约束地升级 pip。

运行桌面单元测试：

```bash
python -m unittest discover -s desktop/tests -p "test_*.py"
```

桌面程序必须在目标操作系统上原生构建。构建脚本不能从 Windows 交叉生成 Linux 或 macOS 成品；Linux 和 macOS 正式产物只能在对应原生系统或项目的对应 GitHub runner 上生成：

```bash
python desktop/build.py
```

Android 工程需要 JDK 17、Android SDK Platform 35 和 Build Tools 35：

```bash
cd android
./gradlew testDebugUnitTest lintDebug assembleRelease
```

Windows 上使用 `gradlew.bat`。Release APK 默认未签名；任何签名密钥或密码都不得进入源码、测试夹具或 CI 日志。

Android 构建需要放到仓库外时，使用 `docs/BUILDING.md` 记录的 `-PflowclipBuildDir=<absolute-path>`，不要改写或提交本地输出路径。Gradle Wrapper 校验、`android/gradle/verification-metadata.xml` 和 Release provenance 等供应链 metadata 必须保持启用。

## Python 依赖与锁文件

只在 `desktop/requirements.in` 和 `desktop/requirements-build.in` 中维护人工审查的直接及传递依赖精确 pin。`desktop/requirements.txt` 与 `desktop/requirements-build.txt` 是跨 Windows、Linux 和 macOS 的 universal hash locks，不得手工修改。

在独立的 Python 3.11 工具环境中固定使用 `uv==0.12.5`，并从仓库根目录重新生成两个锁文件：

```bash
python -m pip install --only-binary=:all: "uv==0.12.5"
uv pip compile desktop/requirements.in --universal --python-version 3.11 --generate-hashes --no-header --no-build --output-file desktop/requirements.txt
uv pip compile desktop/requirements-build.in --universal --python-version 3.11 --generate-hashes --no-header --no-build --output-file desktop/requirements-build.txt
```

依赖 Pull Request 必须同时提交 `.in` 变更和重新生成的 `.txt`，审查解析差异，并在受影响的原生平台执行带 `--require-hashes --only-binary=:all:` 的安装及 `pip check`。universal lock 不是跨平台构建能力；三个桌面成品仍需各自的原生环境验证。

## 修改原则

- 保持同步协议向后兼容；协议变化必须同步更新协议文档和跨端测试。
- 剪贴板、托盘、自启动、单实例与文件路径属于平台相关行为。修改后至少在受影响的平台验证文本和图片收发。
- Linux 需要说明并验证 X11/Wayland 差异；macOS 需要验证 Intel/Apple Silicon 和 Gatekeeper 行为。
- 不提交 `dist/`、本地构建目录、虚拟环境、Android `local.properties` 或签名材料。
- 新增或升级依赖前说明必要性、许可证、体积和安全影响；Python 依赖必须更新输入及 hash lock，Android 依赖必须审查 `verification-metadata.xml`，并同步更新 `THIRD_PARTY_NOTICES.md` 与 `THIRD_PARTY_LICENSES/`。
- 用户可见行为变化应记录在 `CHANGELOG.md` 的 `Unreleased` 部分。

## Pull Request

Pull Request 应聚焦单一问题，并包含：

- 问题背景、实现方案和兼容性影响；
- 已运行的测试与目标平台；
- 界面变化的截图或录屏；
- 网络协议、权限、后台行为或依赖变化的安全说明；
- 关联 Issue，例如 `Closes #123`。

CI 必须通过。维护者可能要求补充测试、拆分无关改动或调整文档后再合并。参与本项目即表示同意遵守 [行为准则](CODE_OF_CONDUCT.md)。
