from __future__ import annotations

import json
import platform
import urllib.error
import urllib.parse
import urllib.request
from http.client import HTTPResponse
from typing import BinaryIO

from . import __version__
from .model import ClipItem, ProtocolError


class ConnectionFailure(RuntimeError):
    pass


def _url_origin(url: str) -> tuple[str, str, int]:
    parsed = urllib.parse.urlsplit(url)
    scheme = parsed.scheme.lower()
    try:
        port = parsed.port
    except ValueError as exc:
        raise ConnectionFailure("服务器重定向地址无效") from exc
    if scheme not in {"http", "https"} or not parsed.hostname:
        raise ConnectionFailure("服务器重定向地址无效")
    return scheme, parsed.hostname.lower(), port or (443 if scheme == "https" else 80)


class SameOriginRedirectHandler(urllib.request.HTTPRedirectHandler):
    def redirect_request(
        self,
        req: urllib.request.Request,
        fp: BinaryIO,
        code: int,
        msg: str,
        headers: object,
        newurl: str,
    ) -> urllib.request.Request | None:
        redirected = super().redirect_request(req, fp, code, msg, headers, newurl)
        if redirected is not None and _url_origin(req.full_url) != _url_origin(redirected.full_url):
            raise urllib.error.HTTPError(
                req.full_url,
                code,
                "拒绝向其他服务器转发认证信息",
                headers,
                fp,
            )
        return redirected


class ApiClient:
    def __init__(self, base_url: str, token: str, max_bytes: int) -> None:
        self.base_url = base_url.rstrip("/")
        self.token = token
        self.max_bytes = max_bytes
        self._maximum_json_bytes = ((max_bytes + 2) // 3) * 4 + 16 * 1024
        self._opener = urllib.request.build_opener(SameOriginRedirectHandler())

    @staticmethod
    def _read_limited(response: HTTPResponse | BinaryIO, limit: int) -> bytes:
        content_length = response.headers.get("Content-Length")
        if content_length is not None:
            try:
                declared = int(content_length)
            except ValueError as exc:
                raise ConnectionFailure("服务器返回了无效的 Content-Length") from exc
            if declared < 0 or declared > limit:
                raise ConnectionFailure("服务器响应超过大小限制")
        body = response.read(limit + 1)
        if len(body) > limit:
            raise ConnectionFailure("服务器响应超过大小限制")
        return body

    def _request(
        self,
        method: str,
        path: str,
        body: bytes | None = None,
        authenticated: bool = True,
        response_limit: int = 64 * 1024,
    ) -> tuple[int, bytes, dict[str, str]]:
        headers = {
            "Accept": "application/json",
            "User-Agent": f"FlowClip-Desktop/{__version__} ({platform.system()})",
        }
        if authenticated:
            if (
                not 16 <= len(self.token) <= 512
                or any(not 0x21 <= ord(character) <= 0x7E for character in self.token)
            ):
                raise ConnectionFailure(
                    "共享密钥只能使用 16 到 512 个可见 ASCII 字符，不能包含空格或中文"
                )
            headers["Authorization"] = f"Bearer {self.token}"
        if body is not None:
            headers["Content-Type"] = "application/json"
        request = urllib.request.Request(
            self.base_url + path, data=body, headers=headers, method=method
        )
        try:
            with self._opener.open(request, timeout=10) as response:
                response_body = self._read_limited(response, response_limit)
                return response.status, response_body, dict(response.headers.items())
        except urllib.error.HTTPError as exc:
            fallback = str(getattr(exc, "reason", "")) or f"HTTP {exc.code}"
            try:
                payload = json.loads(self._read_limited(exc, 64 * 1024).decode("utf-8"))
                detail = payload.get("error", fallback)
            except (ConnectionFailure, OSError, ValueError, UnicodeDecodeError):
                detail = fallback
            finally:
                exc.close()
            raise ConnectionFailure(str(detail)) from exc
        except (urllib.error.URLError, TimeoutError, OSError) as exc:
            reason = getattr(exc, "reason", exc)
            raise ConnectionFailure(f"无法连接服务器：{reason}") from exc

    def health(self) -> dict[str, object]:
        _status, body, _headers = self._request(
            "GET", "/api/v1/health", authenticated=False
        )
        try:
            return json.loads(body.decode("utf-8"))
        except (ValueError, UnicodeDecodeError) as exc:
            raise ConnectionFailure("服务器返回了无效响应") from exc

    def fetch(self, after: int) -> tuple[int, ClipItem | None]:
        query = urllib.parse.urlencode({"after": after})
        status, body, headers = self._request(
            "GET",
            f"/api/v1/clipboard?{query}",
            response_limit=self._maximum_json_bytes,
        )
        if status == 204:
            try:
                revision = int(headers.get("X-FlowClip-Revision", after))
            except ValueError:
                revision = after
            return revision, None
        try:
            raw = json.loads(body.decode("utf-8"))
            revision = int(raw["revision"])
            item = ClipItem.from_dict(raw["item"], self.max_bytes)
            return revision, item
        except (KeyError, TypeError, ValueError, UnicodeDecodeError, ProtocolError) as exc:
            raise ConnectionFailure("服务器返回了无效的剪贴板数据") from exc

    def push(self, item: ClipItem) -> int:
        body = json.dumps(item.to_dict(), ensure_ascii=False).encode("utf-8")
        _status, response, _headers = self._request(
            "POST", "/api/v1/clipboard", body
        )
        try:
            return int(json.loads(response.decode("utf-8"))["revision"])
        except (KeyError, TypeError, ValueError, UnicodeDecodeError) as exc:
            raise ConnectionFailure("服务器返回了无效响应") from exc
