# 首次上传到 GitHub

本文适用于把一份不含 `.git` 元数据的 FlowClip 源码上传到新的 GitHub
仓库。推荐使用 Git 命令行；GitHub 网页上传容易漏掉 `.github`、`.gitignore`
等隐藏路径。

## 1. 创建空仓库

在 GitHub 创建一个空仓库。不要勾选自动生成 README、License 或
`.gitignore`，否则首次推送会产生无关的合并提交。

## 2. 推送源码

在联网电脑上安装 Git，进入解压后的源码根目录并执行：

```bash
git init -b main
git add .
git status --short
git commit -m "Release FlowClip 1.4.0"
git remote add origin https://github.com/你的账号/FlowClip.git
git push -u origin main
```

提交前确认没有 APK、EXE、`local.properties`、`build/`、`.gradle/`、
`__pycache__/`、JKS、密码文件或其他本机密钥。绝对不要把
`D:\FlowClip-Signing-Backup` 上传到 GitHub。

## 3. 创建 v1.4.0 Release

确认 GitHub 仓库默认分支是 `main`、Actions 已启用，然后执行：

```bash
git tag -a v1.4.0 -m "FlowClip 1.4.0"
git push origin v1.4.0
```

Release workflow 会在 GitHub 的原生 runner 上构建和验证 Windows、Linux、
macOS 产物，并发布 Android Gradle 工程、许可证、校验和与来源证明。标签必须
指向默认分支中的提交；不要预先手工创建同名 Release。

自动 Release 不持有 Android Release 私钥。工作流完成后，可以在 GitHub
Release 编辑页面单独附加本机已签名的
`FlowClip-v1.4.0-android-release.apk` 及其独立 SHA-256 文件。不要用本机的
`SHA256SUMS` 覆盖 Actions 生成的清单，因为不同 runner 生成的桌面二进制哈希
不会相同。
