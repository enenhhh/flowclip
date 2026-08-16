# 部署说明

本文适用于 FlowClip 1.4.0。FlowClip 使用“一台服务器，多台桌面或 Android 客户端”的结构：Windows、Linux、macOS 和 Android 均可承担内置 HTTP 服务器角色。

## 部署前确认

- 服务器设备应持续在线，并使用稳定的局域网、虚拟网或隧道地址。
- 所有客户端必须使用同一共享密钥和带显式端口的服务器 URL。
- 同一组设备只应启用一台内置服务器。
- FlowClip 内置服务器只提供 HTTP，不实现 TLS 或端到端加密。
- 互联网入口必须由 HTTPS 反向代理、HTTPS 隧道或加密虚拟局域网保护。
- 服务器只在内存中保留最新一条剪贴板内容；普通文件及索引会持久保存，必须由用户删除。

## 安装桌面成品

- Windows：下载 `FlowClip-<version>-windows-x86_64.exe`，核对校验和后直接运行。
- Linux：解压 `FlowClip-<version>-linux-x86_64.tar.gz`，按会话安装 `wl-clipboard` 或 `xclip`，运行归档内的 `FlowClip/FlowClip`。
- macOS：打开 DMG 或解压 ZIP，把 `FlowClip.app` 放到固定位置后运行。

登录自启动记录的是应用当前的绝对路径。启用自启动后再移动 EXE、Linux 可执行文件或 macOS app，会让原入口失效；移动后应重新打开 FlowClip，关闭再开启“登录系统时启动”并保存。

GitHub Release 还包含 `SHA256SUMS` 和 provenance attestation。Windows/macOS 当前未签名构建的系统提示见本文“安装警告”。

## 局域网部署

### 服务器设置

桌面服务器配置：

- 启用内置服务器：开
- 监听地址：`0.0.0.0`
- 监听端口：例如 `8765`
- 共享密钥：保留随机生成的长密钥
- 自动同步：按需开启

应用启用内置服务器后仍然同时是客户端，它通过 `http://127.0.0.1:端口` 与自己的服务器同步。

如果只允许同一台机器上的隧道或反向代理访问，可以把监听地址改成 `127.0.0.1`。这样局域网其他设备无法直接访问；需要同时服务局域网和本机隧道时，继续监听 `0.0.0.0`，并用防火墙限制来源。

服务器 IP 可能变化时，应在路由器中配置 DHCP 静态租约。不要依赖临时地址写入所有客户端。

Android 服务器配置：

- 将此手机作为服务器：开
- 手机服务器端口：选择 `1024` 至 `65535` 的未占用端口，例如 `8765`
- 共享密钥：使用与客户端相同的长密钥
- 后台自动接收：可独立开启或关闭

保存后，Android 用常驻通知的 `connectedDevice` 前台服务维持服务器，并在界面列出当前非回环 IPv4 URL。手机服务器监听可用网络接口，界面不能像桌面端一样绑定指定地址；只应把显示的私网或受控虚拟网 IP 提供给客户端，并用 Wi-Fi 隔离、路由器 ACL 或系统网络策略限制来源。

### 防火墙

Windows 首次监听时可能显示防火墙提示，只允许可信的专用网络。也可在管理员 PowerShell 中仅放行需要的端口：

```powershell
New-NetFirewallRule -DisplayName "FlowClip 8765" -Direction Inbound -Protocol TCP -LocalPort 8765 -Action Allow -Profile Private
```

Linux 应使用发行版自己的防火墙工具，只允许实际使用的局域网、Tailscale/ZeroTier 接口或反向代理来源。macOS 开启应用防火墙时，首次监听可能要求确认接收入站连接。Android 服务器没有应用内来源 ACL；不要在访客 Wi-Fi、公共热点或未经控制的网络上开放。

无论哪个平台，都不要为了排错而永久关闭整机防火墙。

### 客户端设置

桌面客户端：

- 启用内置服务器：关
- 服务器地址：`http://192.168.1.20:8765`
- 共享密钥：与服务器完全一致
- 公网地址必须使用 HTTPS：开

Android 客户端：

- 服务器地址：`http://192.168.1.20:8765`
- 共享密钥：与服务器完全一致
- 允许可信局域网 HTTP 明文连接：开
- 公网地址必须 HTTPS：开
- 后台自动接收：按需开启

Android 的局域网 HTTP 开关默认关闭，这是一次额外的明文风险确认。桌面端没有单独的局域网开关，但只在地址是受信范围内的字面 IP 时默认允许 HTTP。

### 文件存储与限制

- 桌面服务器默认把文件放在 `~/Downloads/FlowClip Server`，默认单文件 2048 MB、总配额 8192 MB、最多 100 条记录；目录、单文件上限和总配额可在界面修改。桌面客户端下载默认保存到 `~/Downloads/FlowClip`。
- Android 默认单文件 256 MB，可配置到 2048 MB；手机服务器最多 50 个完成与待上传记录，其中待上传预留最多 8 个。它没有独立总配额，受设备剩余空间限制。
- Android 10+ 的普通文件位于公共 `Download/FlowClip`；Android 8/9 位于应用专属外部下载目录。剪贴板图片的 `Pictures/FlowClip` 是另一个目录。
- 退出桌面应用、停止 Android 服务或重启设备不会删除完成文件。请从 FlowClip 文件列表删除不再需要的服务器副本，并分别管理各客户端下载的副本。

桌面客户端可恢复网络中断留下的下载临时文件，但只有桌面服务器提供 `Range`、`ETag` 和 `If-Range` 支持。Android 服务器只返回完整文件，桌面客户端会安全地从头重下；Android 客户端当前也不续传。主动取消会清理当前未完成文件。

## 地址和 HTTPS 校验

服务器地址必须满足以下格式：

- 仅允许 `http://` 或 `https://`。
- 必须显式包含 1 至 65535 的端口，HTTPS 的默认端口也要写成 `:443`。
- 不能包含用户名、密码、额外路径、查询参数或 URL 片段。

桌面端把以下字面 IP 视为可使用 HTTP 的本地或受控范围：

- IPv4 回环、链路本地和 RFC 1918 私网；
- `100.64.0.0/10`，用于 Tailscale 等共享地址空间；
- IPv6 回环、链路本地和 ULA。

任何主机名，即使是 `server.local` 或解析到私网地址，也不会被当作私网字面 IP。默认 HTTPS 校验开启时，这类 HTTP 地址会被拒绝。

Android 的规则分为两个开关：

1. 私网、回环、链路本地或 `100.64.0.0/10` 的字面 IP 使用 HTTP 时，必须开启“允许可信局域网 HTTP 明文连接”。
2. 公网 IP 或任何域名使用 HTTP 时，必须关闭“公网地址必须 HTTPS”。关闭局域网开关不能绕过公网 HTTPS 校验。

关闭“公网地址必须 HTTPS”只会移除客户端阻止。FlowClip 不会因此获得 TLS，共享密钥、文本、图片、文件名和文件正文仍可能被窃听或篡改。普通公网部署不要关闭。

HTTPS 使用 Python 或 Android 平台提供的默认 TLS/CA 校验。自签名证书只有在正确加入客户端所用的信任配置、主机名匹配且证书链有效时才能连接；不要通过修改 FlowClip 来跳过证书校验。

## 加密虚拟局域网

Tailscale 或 ZeroTier 是异地访问的首选。它们在设备之间建立外层加密网络，不要求路由器公开 FlowClip 端口。

1. 在服务器和所有客户端安装同一种虚拟网络工具。
2. 桌面服务器监听 `0.0.0.0:8765` 或明确监听对应虚拟网接口；Android 服务器选择端口后使用界面显示的虚拟网 IPv4 URL（若系统将其列出）。
3. 客户端填写服务器虚拟 IP 和显式端口，例如 `http://100.64.10.20:8765`。
4. Android 仍需开启“允许可信局域网 HTTP 明文连接”。
5. 在虚拟网络 ACL 中只允许自己的设备访问 FlowClip 端口。

这里的 HTTP 依赖虚拟网络提供外层加密。共享密钥仍然必须启用，且不能与虚拟网络账号或密钥复用。

## Cloudflare Tunnel

有域名时，可以由 Cloudflare Tunnel 在公网侧终止 HTTPS，并把请求转发到服务器本机：

```bash
cloudflared tunnel login
cloudflared tunnel create flowclip
cloudflared tunnel route dns flowclip clip.example.com
```

示例 `config.yml`：

```yaml
tunnel: <TUNNEL_ID>
credentials-file: /absolute/path/to/<TUNNEL_ID>.json
ingress:
  - hostname: clip.example.com
    service: http://127.0.0.1:8765
  - service: http_status:404
```

服务器可监听 `127.0.0.1:8765`，然后运行：

```bash
cloudflared tunnel run flowclip
```

客户端填写 `https://clip.example.com:443`。建议用 Cloudflare 访问策略或防火墙进一步限制来源；FlowClip 共享密钥仍然不能省略。

## FRP 与反向代理

不要把 FRP 的 TCP 远程端口直接作为 FlowClip 公网入口。即使 FRP 客户端和服务端之间启用了 TLS，访问 `http://公网主机:远程端口` 的客户端到公网入口这一段仍可能是明文。

安全链路应为：

```text
FlowClip 内置 HTTP 服务器
  -> 受保护的 FRP 回源通道
  -> 仅在公网主机回环地址监听的回源端口
  -> Nginx/Caddy HTTPS 反向代理和有效证书
  -> https://clip.example.com:443
```

必须同时满足：

- FRP 回源端口不对公网网卡监听；
- 客户端入口使用系统信任的 HTTPS 证书；
- FlowClip 密钥、FRP 密钥和证书私钥相互独立；
- 反向代理限制请求体大小并设置合理超时；
- 公网防火墙只开放 HTTPS 入口。

如果无法验证完整链路，请改用 Tailscale、ZeroTier 或 Cloudflare Tunnel。

## 桌面后台和登录自启动

桌面端自动同步只在 FlowClip 进程运行时生效。

- Windows：托盘可用时关闭窗口会隐藏；登录自启动写入当前用户 `HKCU\Software\Microsoft\Windows\CurrentVersion\Run` 的 `FlowClip` 值。
- Linux：Tk 主窗口和托盘都需要 X11/XWayland 的 `DISPLAY`。Wayland 剪贴板可以使用 `wl-clipboard`，但完全没有 XWayland 的纯 Wayland 会话无法启动当前桌面应用。已有 XWayland 但没有兼容托盘时，窗口保持可见，关闭窗口会退出。登录自启动文件位于 `$XDG_CONFIG_HOME/autostart/io.github.flowclip.FlowClip.desktop`；未设置绝对路径的 `XDG_CONFIG_HOME` 时使用 `~/.config/autostart/io.github.flowclip.FlowClip.desktop`。
- macOS：使用菜单栏入口；登录自启动文件是 `~/Library/LaunchAgents/io.github.flowclip.FlowClip.plist`。

“启动后隐藏到托盘”和登录自启动都会依赖托盘或菜单栏初始化成功。若托盘不可用，FlowClip 会禁用隐藏功能并保留窗口，避免进程在用户不可见时运行。

三平台均限制同一用户只运行一个 FlowClip 实例。Windows 使用命名互斥体，Linux 和 macOS 使用配置目录中的 `.instance.lock`。

## Android 后台行为

- Android 不在后台读取或自动发送系统剪贴板。
- 用户可在应用前台点击发送，或通过其他应用的系统分享菜单发送文本、图片或普通文件。
- 自动接收和手机服务器共用 `connectedDevice` 类型的前台服务；任一功能开启都会显示常驻通知。
- 文件上传/下载可在界面或常驻通知中取消；文件操作结束后，如果自动接收和手机服务器都关闭，前台服务会退出。
- 网络恢复时服务立即重试；失败重连采用逐步退避。
- 开机和应用升级后会在配置有效且自动接收或手机服务器仍启用时尝试恢复。
- 用户强行停止应用、从系统活动应用页面停止服务或被厂商冻结后，需要重新打开 FlowClip。
- Android 13+ 应允许通知，否则后台服务和收到内容的提示可能受限。

主界面和常驻通知都可以停止后台功能。通知中的“停止”会同时关闭自动接收和手机服务器；部分厂商还需要在电池设置中允许后台活动。

## 配置与本地数据

| 平台 | 配置位置 |
| --- | --- |
| Windows | `%APPDATA%\FlowClip\config.json` |
| Linux | `${XDG_CONFIG_HOME}/FlowClip/config.json`；未设置时为 `~/.config/FlowClip/config.json` |
| macOS | `~/Library/Application Support/FlowClip/config.json` |
| Android | 应用私有 `SharedPreferences`，名称为 `flowclip_settings` |

Linux 仅接受绝对路径的 `XDG_CONFIG_HOME`；未设置或使用相对路径时回退到 `~/.config`。

桌面配置目录在 Linux/macOS 上设为 `0700`，配置文件设为 `0600`；Windows 使用当前用户配置目录继承的访问控制。共享密钥是明文配置字段，并未存入系统密钥链。Android 使用 `MODE_PRIVATE`，但同样不是面向已解锁/已取得设备控制权攻击者的端到端密钥保护。

配置文件不包含剪贴板历史。桌面端还保存文件上限、总配额、服务端目录和下载目录；Android 保存手机服务器开关、端口和文件上限。桌面端会把旧的 `~/.flowclip/config.json` 内容迁移到规范目录，但不会自动删除旧文件；确认新配置可用后应手动移除旧副本。更完整的数据保存和删除说明见 [隐私与数据处理](PRIVACY.md)。

## Android 项目与签名

`android/` 保留 Gradle 工程、单元测试和 Lint 配置。Release workflow 会执行未签名 Release 构建验证，但公开 Release 只上传 Android Gradle 项目归档，不把未签名 APK 当作正式安装包，也不代表项目维护者已经发布签名 APK。

需要自行分发 APK 时，必须创建并长期备份自己的 Release 签名密钥。Debug APK 和 Debug 证书只适合测试；丢失 Release 密钥后，后续版本无法覆盖安装。具体命令见 [构建与发布](BUILDING.md)。

## 安装警告

- Windows Release 未配置代码签名证书时可能触发 SmartScreen。先核对 `SHA256SUMS`，再通过 Windows 的详情流程确认，不要全局关闭 SmartScreen。
- macOS Release workflow 未提供 Developer ID secrets 时，应用没有签名和公证，会触发 Gatekeeper。先核对校验和，再使用 Finder 或“隐私与安全”中的系统确认流程；不要全局关闭 Gatekeeper。
- Linux 运行前安装当前剪贴板会话需要的 `wl-clipboard` 或 `xclip`，并确认 Tk 可访问 `DISPLAY`。SSH、纯终端和没有 X11/XWayland 的纯 Wayland 会话不支持当前桌面应用。

## 故障排查

- **认证失败**：确认所有设备使用完全相同的 16 至 512 字符可见 ASCII 密钥。
- **无法连接服务器**：检查服务器是否启用、IP/端口是否正确、监听地址是否覆盖目标接口，以及防火墙是否放行。
- **Android 没有显示本机地址**：确认手机已有可用网络和非回环 IPv4 地址；切换 Wi-Fi/VPN 后等待界面刷新。端口仍需保持不变。
- **Android 拒绝局域网 HTTP**：开启“允许可信局域网 HTTP 明文连接”，并使用私网字面 IP，不要使用 `.local` 主机名。
- **公网 HTTP 被拒绝**：优先改为有效 HTTPS 或加密 VPN；只有已有外层保护时才考虑关闭公网 HTTPS 校验。
- **HTTPS 无法连接**：检查证书链、主机名、系统时间、隧道和反向代理；URL 必须显式写 `:443`。
- **Linux 应用无法启动**：检查 Tk 8.6 和 `DISPLAY`；纯 Wayland 会话需要启用 XWayland。
- **Linux 剪贴板不可用**：Wayland 检查 `WAYLAND_DISPLAY`、`wl-copy` 和 `wl-paste`；X11 检查 `DISPLAY` 和 `xclip`。
- **Linux 托盘不可用**：这是桌面环境或 XWayland 能力限制；保留主窗口即可继续同步。
- **端口被占用**：换一个端口，并同步更新防火墙、隧道、映射和所有客户端。
- **图片超过限制**：Android 参与时把全部设备的上限设置为 8 MB 或更低。
- **文件无法续传**：只有桌面客户端连接支持 Range 的桌面服务器时才会续传网络中断留下的临时文件；Android 客户端或 Android 服务器会重新开始完整下载。
- **文件超过限制或空间不足**：核对客户端和服务器的单文件上限、桌面总配额、文件数量与设备剩余空间。
- **后台服务停止**：检查通知权限、电池优化、厂商后台限制，并重新打开 Android 应用；这会同时影响自动接收和手机服务器。
