# FlowClip Android

这是 FlowClip 1.4.0 的独立 Android Gradle 工程。它可以连接桌面端或其他 Android 设备，也可以在手机上启动 HTTP 服务器，并支持文本、图片和文件传输。主页面可接收 Android 标准拖放，系统分享入口支持单项和多项内容。

## 环境要求

- JDK 17
- Android SDK Platform 35
- Android Build Tools 35.0.0
- 可访问 Maven 仓库，或已准备好 Gradle 缓存

工程自带 Gradle Wrapper 8.9，不需要全局安装 Gradle。Wrapper 下载包带 SHA-256 校验。依赖仓库优先使用阿里云镜像，并保留 Google、Maven Central 和 Gradle Plugin Portal 作为后备。

## 验证与构建

Linux 或 macOS：

```bash
chmod +x gradlew
./gradlew --no-daemon testDebugUnitTest lintDebug assembleDebug assembleRelease
```

Windows：

```powershell
.\gradlew.bat --no-daemon testDebugUnitTest lintDebug assembleDebug assembleRelease
```

主要输出：

- `app/build/outputs/apk/debug/app-debug.apk`：可直接安装的测试包，使用 Debug 签名。
- `app/build/outputs/apk/release/app-release-unsigned.apk`：经过 R8 压缩的未签名 Release 包，不能直接作为正式更新发布。

如需减少系统盘占用，可在仓库外指定构建目录：

```powershell
.\gradlew.bat --no-daemon -PflowclipBuildDir=D:\DevCache\flowclip-android-build testDebugUnitTest lintDebug assembleDebug assembleRelease
```

使用该参数后，APK 位于 `<flowclipBuildDir>/app/outputs/apk/`；不传参数时仍使用标准的 `app/build/outputs/apk/`。

## Release 签名

签名密钥决定后续版本能否覆盖安装。请在隔离位置生成并长期备份密钥，不要把 `.jks`、密码或 `keystore.properties` 提交到仓库。

```bash
keytool -genkeypair -v -keystore flowclip-release.jks -alias flowclip -keyalg RSA -keysize 4096 -validity 10000
./gradlew --no-daemon assembleRelease
zipalign -p -f 4 app/build/outputs/apk/release/app-release-unsigned.apk FlowClip-aligned.apk
apksigner sign --ks flowclip-release.jks --out FlowClip-release.apk FlowClip-aligned.apk
apksigner verify --verbose --print-certs FlowClip-release.apk
```

Debug APK 仅用于测试。向用户分发前，应核对版本、签名证书和 SHA-256，并在真实 Android 设备上验证前台服务、后台自动接收、手机服务器、图片保存和大文件取消。

## 许可证与隐私

项目采用 MIT License。GitHub Release 的 Android 工程归档同时包含 `LICENSE.txt`、`THIRD_PARTY_NOTICES.md` 和 `THIRD_PARTY_LICENSES/`。FlowClip 不内置云服务；剪贴板和文件只发送到用户配置的服务器。公网部署应使用 HTTPS，并妥善保护共享密钥。
