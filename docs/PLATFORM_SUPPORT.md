# 平台支持说明

本文描述 FlowClip 1.4.0 的实现范围、运行依赖和已知限制。“源码包含实现”不等于“所有发行版和硬件均已实测”；正式支持范围以 CI runner 和 Release 文件名为准。

## 功能矩阵

| 功能 | Windows | Linux | macOS | Android |
| --- | --- | --- | --- | --- |
| 文本发送/接收 | 是 | 是 | 是 | 是 |
| 图片发送/接收 | 是 | 是 | 是 | 是 |
| 自动检测并发送剪贴板 | 是 | 是 | 是 | 否 |
| 自动接收 | 应用运行时 | 应用运行时 | 应用运行时 | 前台服务 |
| 手动发送/接收 | 是 | 是 | 是 | 是 |
| 普通文件上传/下载/删除 | 是 | 是 | 是 | 是 |
| 从窗口拖放上传 | 是，本地文件 | 否 | 否 | 是，标准 Android 内容 |
| 文件传输取消 | 是 | 是 | 是 | 是 |
| 中断下载续传 | 桌面服务器支持 | 桌面服务器支持 | 桌面服务器支持 | 否 |
| 运行内置 HTTP 服务器 | 是 | 是 | 是 | 是，前台服务 |
| 托盘或菜单栏 | 系统托盘 | 依赖 X11/XWayland | 菜单栏 | 常驻通知 |
| 登录/开机恢复 | 当前用户 Run 键 | XDG Autostart | LaunchAgent | Boot Receiver |
| 重复启动处理 | Win32 命名互斥体 | 进程级 `flock` | 进程级 `flock` | Activity `singleTop`，不等同桌面进程锁 |

剪贴板协议在所有平台只同步最新一条文本或图片，不同步富文本格式、剪贴板历史或应用专属的私有剪贴板格式。普通文件由独立的 `files-v1` 列表管理，不会自动写入或读取系统剪贴板。

## 文件传输

文件上传分为元数据预留和原始二进制正文两步。正文使用固定 `Content-Length` 的 `application/octet-stream` 流，不做 Base64；下载同样流式写入目标，不需要把整个文件装入内存。上传方、服务器和下载方都会检查大小，完成记录包含原始文件的 SHA-256。

桌面端的默认设置和边界：

- 单文件上限 2048 MB，可配置 1 至 10240 MB；
- 服务端总配额默认 8192 MB，可配置为不小于单文件上限且不超过 51200 MB；
- 服务端最多 100 条记录，未完成上传预留 10 分钟后清理；
- 服务端目录默认为 `~/Downloads/FlowClip Server`，客户端目录默认为 `~/Downloads/FlowClip`，均要求绝对路径；
- 下载先写入隐藏 `.flowclip-<id>.part`，完成校验后以不覆盖同名文件的方式提交。

桌面服务器对完整文件提供基于 SHA-256 的 `ETag`、`Accept-Ranges: bytes` 和单段 Range 响应。桌面客户端会用 `Range` 与 `If-Range` 恢复因连接错误留下的临时下载；服务器返回完整 `200` 时会从头覆盖临时文件。用户主动取消下载会删除临时文件，上传取消会尽力删除远端预留，因此上传本身不续传。

Android 默认单文件上限为 256 MB，可配置 1 至 2048 MB。手机服务器最多保存 50 个完成与待上传记录，其中待上传预留最多 8 个；没有独立的总配额设置，实际还受设备剩余空间约束。Android 客户端和服务器均流式传输并支持取消，但取消或失败会丢弃未完成输出，当前不提供断点续传。Android 服务器对文件下载返回完整 `200`，不提供 `Range`/`ETag`。

## Windows

### 剪贴板

Windows 后端直接调用 Win32 API：

- `CF_UNICODETEXT` 读写 Unicode 文本；
- `CF_DIB` 写入图片；
- 使用系统剪贴板序列号检测变化和避免覆盖用户刚复制的新内容；
- 读取图片时支持直接复制的位图，也会尝试读取剪贴板文件列表中的首个有效图片文件；
- 同步前把图片规范化为 PNG。

剪贴板被其他程序短暂占用时会重试，持续占用则在界面显示错误。

### 桌面集成

- 系统托盘提供打开、发送、接收和退出。
- 托盘可用时关闭窗口只隐藏应用。
- “登录系统时启动”写入当前用户 Run 注册表，并以 `--minimized` 启动。
- 同一用户只允许一个进程运行。
- 主窗口和“文件快传”窗口通过 Win32 `WM_DROPFILES` 接收资源管理器中的一个或多个本地文件；多文件由现有单 worker 顺序上传。虚拟文件、纯文字和位图拖放不属于该接口范围。

### 支持范围

目标用户系统为 Windows 10/11 x64。GitHub CI 和 Release 使用 `windows-2022` x64 runner。当前公开流程不做 Authenticode 签名，因此 SmartScreen 可能显示提示。

## Linux

### Wayland

Wayland 后端要求：

- 图形会话存在 `WAYLAND_DISPLAY`；
- Tk 8.6 主窗口仍能通过 X11/XWayland 取得 `DISPLAY`；
- `wl-copy` 和 `wl-paste` 都在 `PATH` 中；
- 安装包通常名为 `wl-clipboard`。

FlowClip 使用 MIME 类型查询文本和图片，支持常见 PNG、JPEG、WebP、BMP 输入，并在同步前转成 PNG。缺少任意一个 `wl-clipboard` 命令时，Wayland 原生剪贴板后端不会启用。该后端不等于原生 Wayland 界面；当前 Tk 8.6 GUI 仍要求 X11/XWayland。

### X11

X11 后端要求：

- 图形会话存在 `DISPLAY`；
- `xclip` 在 `PATH` 中。

FlowClip 使用 X11 `CLIPBOARD` selection 和 TARGETS/MIME 类型读写文本或图片。

同时存在 Wayland 和 X11/XWayland 环境变量时，FlowClip 优先选择完整的 `wl-clipboard` 后端；如果它不可用但 `DISPLAY` 与 `xclip` 可用，会回退到 X11/XWayland。

### 托盘限制

Linux 的 PyStray 后端使用 X11。已有 XWayland 的 Wayland 桌面是否显示托盘，还取决于桌面环境和托盘扩展：

- 托盘成功：关闭窗口会隐藏，托盘菜单可重新打开。
- 托盘失败：FlowClip 禁用“启动后隐藏到托盘”，保持主窗口可见，关闭窗口会退出。

剪贴板后端和托盘后端彼此独立：在 XWayland 会话中，Wayland 剪贴板可以正常工作，但桌面环境仍可能没有托盘。完全没有 `DISPLAY`/XWayland 的纯 Wayland 会话无法创建 Tk 主窗口，当前不受支持。

### 支持范围

GitHub CI 和 Release 使用 Ubuntu 22.04 x86_64。其他采用兼容 glibc、Tk 8.6 和 X11/XWayland 的发行版可尝试运行，但属于尽力支持。PyInstaller 产物的 glibc 基线由构建 runner 决定。

SSH、TTY、容器和没有图形会话变量的环境不支持桌面剪贴板。FlowClip 不是无头服务器守护进程；即使只想使用内置服务器，当前桌面应用仍会初始化 GUI 和剪贴板后端。

## macOS

### 剪贴板

macOS 后端通过 PyObjC 使用 Cocoa `NSPasteboard`：

- `public.utf8-plain-text` 读写文本；
- 优先读取 `public.png`，也支持读取 `public.tiff`；
- 使用 pasteboard `changeCount` 检测变化；
- 写入图片时统一使用 PNG。

### 菜单栏和自启动

- PyStray 的 Cocoa 后端提供菜单栏入口。
- 关闭窗口时应用可保留在菜单栏。
- “登录系统时启动”写入 `~/Library/LaunchAgents/io.github.flowclip.FlowClip.plist`，并以 `--minimized` 启动。
- 使用配置目录中的 `.instance.lock` 阻止同一用户重复启动。

### 支持范围

GitHub CI 和 Release 使用 `macos-14` runner。归档文件名使用 runner 实际报告的 `x86_64` 或 `arm64`，不声称一个单架构构建是 Universal 2。

当前 Release workflow 没有 Developer ID 签名和 Apple 公证步骤。未提供相应 secrets 时，Gatekeeper 会提示应用来自未识别开发者。应核对 `SHA256SUMS` 并使用系统针对单个应用的确认流程，不要全局关闭 Gatekeeper。

## Android

### 支持范围

- `minSdk 26`：Android 8.0；
- `targetSdk 35`、`compileSdk 35`；
- Java 17 源码；
- 使用 Android 平台 API 和原生控件，不包含 WebView 或第三方后台框架；内置 HTTP 服务器依赖 NanoHTTPD 2.3.1（BSD-3-Clause）。

### 剪贴板限制

Android 的系统隐私规则不允许 FlowClip 在后台持续读取剪贴板，因此：

- 自动发送不可用；
- 用户必须在 FlowClip 位于前台时点击发送，或通过系统分享菜单发送文本/图片；
- 后台自动接收可以运行，因为它不读取本机剪贴板；
- 收到文本时写入系统剪贴板；
- 收到图片时保存为文件并通过通知提供打开入口，而不是依赖所有 Android 版本都支持图片剪贴板。

Android 10+ 图片保存到公共 `Pictures/FlowClip`，会出现在系统媒体库中。Android 8/9 保存到应用专属外部图片目录，卸载应用时删除。

### 文件与手机服务器

- Android 10+ 接收或托管的普通文件保存到 MediaStore 公共 `Download/FlowClip`；卸载应用不会自动删除这些文件。
- Android 8/9 使用应用专属外部下载目录的 `FlowClip` 子目录；卸载应用时由系统删除。
- 文件选择器和系统分享入口可以发送普通文件；发送前会先流式复制到应用缓存，以取得稳定大小并执行上限检查，完成或失败后删除缓存。
- 主页面通过 Android 标准 `DragEvent`/`ClipData` 接收最多 16 项拖放内容，并用 `ACTION_SEND`/`ACTION_SEND_MULTIPLE` 兼容应用图标和系统分享入口。外部 URI 只接受 `content://`，图片 URI 也按文件快传处理；批量文件在同一个可取消任务中依次上传。
- “将此手机作为服务器”允许指定 `1024` 至 `65535` 的端口。服务监听可用网络接口，界面列出当前非回环 IPv4 URL；Android 界面不提供单独的监听地址绑定选项。
- 手机服务器与桌面服务器一样只提供 HTTP。共享密钥负责鉴权，但不负责加密。

### 厂商中转站兼容边界

HarmonyOS 4 超级中转站、荣耀收藏空间/任意门、ColorOS 中转站和 OriginOS 超级拖放都没有公开统一的第三方 Android 接收 SDK。FlowClip 因此采用 Android 官方拖放与分享协议做尽力兼容，不使用厂商私有接口。厂商系统仍可能只向白名单或推荐目标交付某些素材，实际支持的入口、素材类型和机型应以真机测试为准。HarmonyOS NEXT 原生应用不在本 Android 工程范围内。

参考资料：[Android 拖放](https://developer.android.com/develop/ui/views/touch-and-input/drag-drop)、[接收分享内容](https://developer.android.com/training/sharing/receive)、[HarmonyOS 4](https://consumer.huawei.com/cn/harmonyos-4/)、[荣耀收藏空间](https://www.honor.com/cn/support/content/zh-cn15854479/)、[ColorOS 14 中转站](https://www.coloros.com/article/a00000019/)、[OriginOS 4 超级拖放](https://bbs.vivo.com.cn/newbbs/thread/37560654)。

### 后台限制

自动接收和手机服务器共用 `connectedDevice` 前台服务与常驻通知。任一功能启用时服务都需要运行；文件传输进行中，界面和通知还提供取消操作。服务会在开机、应用升级、普通进程回收和网络恢复后尽力恢复，但不保证绕过：

- 用户强行停止；
- 系统“活动应用”停止操作；
- 厂商冻结或严格电池策略；
- 通知权限被拒绝；
- 后台启动限制。

这些情况发生后需要重新打开 FlowClip。主界面和通知操作都可以主动停止服务。

## 桌面配置与权限

| 平台 | 配置目录 | 自启动入口 |
| --- | --- | --- |
| Windows | `%APPDATA%\FlowClip` | `HKCU\Software\Microsoft\Windows\CurrentVersion\Run` |
| Linux | `$XDG_CONFIG_HOME/FlowClip`；默认 `~/.config/FlowClip` | `$XDG_CONFIG_HOME/autostart/io.github.flowclip.FlowClip.desktop`；默认位于 `~/.config/autostart` |
| macOS | `~/Library/Application Support/FlowClip` | `~/Library/LaunchAgents/io.github.flowclip.FlowClip.plist` |

Linux/macOS 配置目录使用 `0700`，配置和锁文件使用 `0600`。Windows 依赖当前用户配置目录的 ACL。配置中包含设备 ID、设备名、服务器地址、共享密钥和同步选项，不包含剪贴板历史。

Linux 只接受绝对路径的 `XDG_CONFIG_HOME`；变量未设置或是相对路径时回退到 `~/.config`。

## 架构和发布边界

- Windows Release 当前固定构建 x86_64 EXE。
- Linux Release 当前固定构建 Ubuntu 22.04 x86_64 tar.gz。
- macOS Release 架构取决于 `macos-14` runner，文件名记录实际架构。
- Android 代码是 Java，不捆绑原生 ABI 库；公开 Release 当前提供 Gradle 项目归档，不提供由项目维护者签名的 APK。
- 三个平台的 PyInstaller 成品必须分别在原生 runner 构建。

新增架构或旧系统支持前，应先增加对应 CI/真机验证，不能只修改文件名或平台表。Windows 上的本机构建结果不能作为 Linux 或 macOS 已验证的证据，反之亦然。
