# FlowClip 同步与文件协议 v1

FlowClip 1.4.0 在同一 HTTP API 中提供 `clipboard-v1` 和 `files-v1`。控制消息与剪贴板内容使用 JSON；普通文件正文直接使用原始二进制流。FlowClip 自身不实现 TLS 或端到端加密，公网传输必须由 HTTPS 隧道或加密 VPN 保护。

客户端配置的基础 URL 必须显式包含端口，例如局域网 `http://192.168.1.20:8765` 或公网 HTTPS `https://clip.example.com:443`。Windows、Linux、macOS 和 Android 默认开启公网 HTTPS 校验；Android 使用私网 HTTP 时还要求用户开启“允许可信局域网 HTTP 明文连接”。关闭校验只会移除客户端阻止，不会为协议增加加密。

## 鉴权和通用响应

除健康检查外，请求必须带：

```http
Authorization: Bearer <shared-token>
```

共享密钥必须包含 16 到 512 个可见 ASCII 字符，码值范围为 `0x21` 到 `0x7E`，不能包含空格、中文或控制字符。Bearer 鉴权只能阻止不知道密钥的请求，不能替代 HTTPS、VPN 或其他链路保护。

JSON 响应使用 `application/json; charset=utf-8`。错误响应采用 `{"error":"说明"}`；鉴权失败为 `401`。服务端关闭缓存并设置 `X-Content-Type-Options: nosniff`。FlowClip 客户端不跟随文件传输重定向，部署反向代理时应直接在配置的同源 URL 上提供接口。

## 健康检查

```http
GET /api/v1/health
```

响应至少包含：

```json
{
  "ok": true,
  "version": 1,
  "serverId": "uuid",
  "capabilities": ["clipboard-v1", "files-v1"]
}
```

桌面服务器还返回 `fileMaxBytes`。健康检查不需要鉴权，不应放入设备名、密钥或文件列表等敏感信息。

## clipboard-v1

### 上传最新内容

```http
POST /api/v1/clipboard
Content-Type: application/json
Content-Length: <bytes>
```

文本示例：

```json
{
  "id": "uuid",
  "origin": "device-uuid",
  "kind": "text",
  "mime": "text/plain; charset=utf-8",
  "filename": "",
  "data": "VGV4dA==",
  "sha256": "1f...64个十六进制字符",
  "createdAt": 1786600000000
}
```

图片使用 `kind: "image"`、`image/*` MIME、原始图片字节的规范 Base64 和建议文件名。`sha256` 针对 Base64 解码后的原始字节。成功响应包含当前 `revision`；相同 `id` 重复上传不会递增 revision。

桌面端默认允许 20 MB 原始内容，Android 默认及最高允许 8 MB。有效上限取发送端、服务器和接收端配置中的最小值；Base64 JSON 通常比原始内容大约多三分之一。

### 获取最新内容

```http
GET /api/v1/clipboard?after=<revision>
```

- 有更新：`200`，响应 `{"revision":3,"item":{...}}`。
- 无更新或服务器当前为空：`204`，`X-FlowClip-Revision` 给出当前 revision。
- 字段、Base64 或 SHA-256 无效：`400`。
- 请求体超过服务器限制：`413`。

客户端使用 `origin` 避免应用自己刚上传的内容，并使用内容签名抑制远端写入本机剪贴板后产生的回传。最新剪贴板正文只在服务器进程内存中保存；桌面服务器重启时 clipboard revision 重置，Android 会保留 revision 但不会保留正文。

## files-v1

文件 revision 与剪贴板 revision 相互独立。文件完成后持久保存，服务器重启不会像剪贴板内容一样自动清除。文件记录格式为：

```json
{
  "id": "规范小写 UUID",
  "origin": "device-uuid",
  "filename": "report.zip",
  "mime": "application/zip",
  "size": 1048576,
  "createdAt": 1786600000000,
  "status": "ready",
  "sha256": "64个小写十六进制字符"
}
```

预留记录使用 `status: "pending"` 且不包含 `sha256`；完成记录使用 `status: "ready"` 并必须包含原始文件字节的 SHA-256。文件名必须是单个安全名称，不能包含路径分隔符、控制字符、相对路径名称或 Windows 保留设备名。所有实现统一限制为 UTF-8 编码不超过 180 字节、UTF-16 不超过 255 个单元。MIME 必须是有效的 ASCII `type/subtype`。

### 列出文件

```http
GET /api/v1/files
```

成功返回完成文件，不暴露未完成预留：

```json
{"revision":7,"files":[{...}]}
```

### 预留并上传

第一步提交不含 `status` 和 `sha256` 的元数据：

```http
POST /api/v1/files
Content-Type: application/json
Content-Length: <metadata-bytes>

{"id":"uuid","origin":"device","filename":"report.zip","mime":"application/zip","size":1048576,"createdAt":1786600000000}
```

服务器以 `201` 返回 `{"ok":true,"revision":N,"file":{...pending...}}`。元数据请求体最大 16 KiB。ID 已存在、容量不足或字段无效时，服务器拒绝预留。

第二步上传正文：

```http
PUT /api/v1/files/<id>
Content-Type: application/octet-stream
Content-Length: 1048576

<原始文件字节>
```

服务器拒绝 `Transfer-Encoding`，要求唯一且与预留大小完全一致的 `Content-Length`，并以有限大小的缓冲区流式写入临时目标。成功后原子完成文件、计算 SHA-256，并返回 `{"ok":true,"revision":N,"file":{...ready...}}`。文件正文不做 Base64，也不要求一次装入内存。中断或失败的上传不会作为完成文件列出；客户端取消上传时会尽力删除远端预留，上传不支持断点续传。

### 下载和 Range

```http
GET /api/v1/files/<id>
Accept: application/octet-stream
```

完整下载返回 `200`、准确的 `Content-Length` 和原始文件字节。客户端必须根据列表记录校验最终大小和 SHA-256。

桌面服务器还返回：

```http
ETag: "<sha256>"
Accept-Ranges: bytes
Content-Disposition: attachment; ...
```

它支持一个 `bytes` 范围。满足的 Range 返回 `206` 和 `Content-Range`；超出文件范围返回 `416` 和 `Content-Range: bytes */<size>`。请求带 `If-Range: "<sha256>"` 且值与当前文件不匹配时，服务器忽略 Range 并返回完整 `200`。桌面客户端用此行为恢复网络中断留下的 `.part` 文件；收到完整 `200` 时会从头覆盖。用户主动取消会删除临时文件，因此取消操作本身不能恢复。

Android 服务器当前始终返回完整 `200` 文件，不提供 `Range`、`ETag` 或 `206`。桌面客户端连接 Android 服务器时会安全地从头下载；Android 客户端也不保存可续传的部分文件。

### 删除文件

```http
DELETE /api/v1/files/<id>
```

成功返回 `{"ok":true,"revision":N}`，并删除服务器上的索引记录和文件正文。删除不会撤回其他客户端已经下载的副本。正在传输的文件可能返回 `409`。

## 文件限制和常见状态码

- 桌面默认单文件 2048 MB、总配额 8192 MB、最多 100 条记录；配置范围分别为 1 至 10240 MB，以及不小于单文件上限且不超过 51200 MB。
- Android 默认单文件 256 MB、最高 2048 MB；手机服务器最多 50 个完成与待上传记录，其中待上传预留最多 8 个，没有独立总配额。
- `400`：字段、ID、Content-Length 或请求格式无效。
- `401`：Bearer 密钥错误或缺失。
- `404`：接口或文件不存在。
- `409`：文件 ID 冲突、文件未完成、正在传输或文件列表已满。
- `411`：Android 服务器缺少 Content-Length；桌面服务器对此类无效正文返回 `400`。
- `413`：元数据、剪贴板内容或文件超过上限。
- `416`：桌面服务器无法满足 Range。
- `507`：桌面服务器总配额不足。

所有上限都应在客户端和服务器两侧执行。反向代理还需要配置足够的请求体上限和流式传输超时，但不应取消 FlowClip 自身的限制。
