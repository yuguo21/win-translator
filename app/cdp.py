"""极简 Chrome DevTools Protocol 客户端（纯标准库，零依赖）。

用途：网页翻译模式把译文**直接写进网页**（替换原文），而不是在屏幕上贴图。

实现要点：
- 通过 `http://127.0.0.1:<port>/json` 拿到页面 target 的 WebSocket 地址；
- 手写最小 WebSocket 客户端（握手 + 帧编解码），只做文本帧，够 CDP 用；
- 每个请求带自增 id，按 id 匹配响应。

浏览器需要以调试模式启动（`--remote-debugging-port=9222`），
见 app/engines/web_engine.py 里的说明。
"""
import base64
import json
import os
import socket
import struct
import threading
import time
import urllib.request
from typing import Dict, List, Optional

DEFAULT_PORTS = (9222, 9223, 9333, 8222)


# ---------------- HTTP 部分 ----------------
def _get_json(url: str, timeout: float = 2.0):
    with urllib.request.urlopen(url, timeout=timeout) as r:
        return json.loads(r.read().decode("utf-8"))


def find_port(ports=DEFAULT_PORTS) -> Optional[int]:
    """找到第一个可用的调试端口。"""
    for p in ports:
        try:
            _get_json(f"http://127.0.0.1:{p}/json/version", timeout=1.2)
            return p
        except Exception:
            continue
    return None


def list_pages(port: int) -> List[dict]:
    """列出所有网页标签。"""
    try:
        targets = _get_json(f"http://127.0.0.1:{port}/json")
    except Exception:
        return []
    return [t for t in targets if t.get("type") == "page"]


def pick_page(port: int, title_hint: str = "", url_hint: str = "") -> Optional[dict]:
    """按标题/URL 片段挑一个标签；都不匹配就取第一个。"""
    pages = list_pages(port)
    if not pages:
        return None
    hint = (title_hint or "").strip()
    for p in pages:
        t = (p.get("title") or "").strip()
        if hint and (t == hint or hint.startswith(t) or t.startswith(hint.split(" - ")[0])
                     or hint.split(" - ")[0] in t):
            return p
    uh = (url_hint or "").strip()
    for p in pages:
        if uh and uh in (p.get("url") or ""):
            return p
    return pages[0]


# ---------------- WebSocket 部分 ----------------
class WSClient:
    """最小 WebSocket 客户端：只发/收文本帧。"""

    GUID = "258EAFA5-E914-47DA-95CA-C5AB0DC85B11"

    def __init__(self, ws_url: str, timeout: float = 5.0):
        # ws://127.0.0.1:9222/devtools/page/XXX
        assert ws_url.startswith("ws://"), ws_url
        rest = ws_url[5:]
        host_port, _, path = rest.partition("/")
        host, _, port = host_port.partition(":")
        self.sock = socket.create_connection((host, int(port or 80)), timeout=timeout)
        self.sock.settimeout(timeout)
        key = base64.b64encode(os.urandom(16)).decode()
        req = (
            f"GET /{path} HTTP/1.1\r\n"
            f"Host: {host_port}\r\n"
            "Upgrade: websocket\r\n"
            "Connection: Upgrade\r\n"
            f"Sec-WebSocket-Key: {key}\r\n"
            "Sec-WebSocket-Version: 13\r\n\r\n"
        )
        self.sock.sendall(req.encode())
        resp = self._read_until_headers()
        if b"101" not in resp.split(b"\r\n")[0]:
            raise RuntimeError(f"WebSocket 握手失败: {resp[:120]!r}")

    def _read_until_headers(self) -> bytes:
        buf = b""
        while b"\r\n\r\n" not in buf:
            chunk = self.sock.recv(4096)
            if not chunk:
                break
            buf += chunk
        return buf

    # --- 帧 ---
    def send_text(self, text: str) -> None:
        data = text.encode("utf-8")
        header = bytearray()
        header.append(0x81)                                  # FIN + text
        mask_bit = 0x80
        n = len(data)
        if n < 126:
            header.append(mask_bit | n)
        elif n < (1 << 16):
            header.append(mask_bit | 126)
            header += struct.pack(">H", n)
        else:
            header.append(mask_bit | 127)
            header += struct.pack(">Q", n)
        mask = os.urandom(4)
        header += mask
        masked = bytes(b ^ mask[i % 4] for i, b in enumerate(data))
        self.sock.sendall(bytes(header) + masked)

    def _recv_frame(self):
        def readn(k: int) -> bytes:
            b = b""
            while len(b) < k:
                c = self.sock.recv(k - len(b))
                if not c:
                    raise ConnectionError("连接已关闭")
                b += c
            return b

        b1, b2 = readn(2)
        fin = b1 & 0x80
        opcode = b1 & 0x0F
        masked = b2 & 0x80
        length = b2 & 0x7F
        if length == 126:
            length = struct.unpack(">H", readn(2))[0]
        elif length == 127:
            length = struct.unpack(">Q", readn(8))[0]
        mask = readn(4) if masked else None
        payload = readn(length)
        if mask:
            payload = bytes(c ^ mask[i % 4] for i, c in enumerate(payload))
        return fin, opcode, payload

    def recv_text(self, timeout: Optional[float] = None) -> str:
        if timeout is not None:
            self.sock.settimeout(timeout)
        chunks = []
        while True:
            fin, opcode, payload = self._recv_frame()
            if opcode == 0x8:          # close
                raise ConnectionError("服务器关闭了连接")
            if opcode == 0x9:          # ping -> pong
                continue
            if opcode in (0x1, 0x0, 0xA):
                chunks.append(payload)
            if fin:
                return b"".join(chunks).decode("utf-8", "replace")

    def close(self) -> None:
        try:
            self.sock.close()
        except Exception:
            pass


class CDPPage:
    """一个页面 target 的 CDP 会话。

    控制器线程和界面线程（停止时还原页面）都会调用，同一 socket 上并发收发会串包，
    所以每次 call 都在同一把锁里完成"发请求 + 等对应 id 的响应"。
    """

    def __init__(self, ws_url: str, timeout: float = 5.0):
        self.ws = WSClient(ws_url, timeout=timeout)
        self._id = 0
        self._lock = threading.Lock()

    def call(self, method: str, params: Optional[dict] = None, timeout: float = 10.0):
        with self._lock:
            self._id += 1
            mid = self._id
            msg = {"id": mid, "method": method, "params": params or {}}
            self.ws.send_text(json.dumps(msg))
            deadline = time.time() + timeout
            while time.time() < deadline:
                raw = self.ws.recv_text(timeout=max(0.2, deadline - time.time()))
                try:
                    obj = json.loads(raw)
                except Exception:
                    continue
                if obj.get("id") == mid:
                    if "error" in obj:
                        raise RuntimeError(f"CDP 错误: {obj['error']}")
                    return obj.get("result", {})
            raise TimeoutError(f"CDP 调用超时: {method}")

    def eval(self, expression: str, timeout: float = 10.0):
        """执行 JS 并取回 byValue 结果。"""
        res = self.call("Runtime.evaluate", {
            "expression": expression,
            "returnByValue": True,
            "awaitPromise": True,
            "userGesture": True,
        }, timeout=timeout)
        if res.get("exceptionDetails"):
            raise RuntimeError(f"JS 异常: {res['exceptionDetails']}")
        return res.get("result", {}).get("value")

    def close(self) -> None:
        self.ws.close()


def connect(port: int, title_hint: str = "", url_hint: str = "") -> Optional[CDPPage]:
    page = pick_page(port, title_hint, url_hint)
    if not page:
        return None
    ws_url = page.get("webSocketDebuggerUrl")
    if not ws_url:
        return None
    return CDPPage(ws_url)
