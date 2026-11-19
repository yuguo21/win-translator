"""验证 UI 注入的"悬停翻译"：鼠标停在哪一项就只翻哪一项（跟网页模式一致）。

流程：CDP 点开 VS Code 的 Go 菜单 → 把鼠标移到第 1 个菜单项 → 读（应只有 1 块）
→ 假译文写回 → 移到第 2 项 → 再读（应是另一块）→ 校验 → 还原。

用法：python tools/test_ui_hover.py [port]
"""
import ctypes
import ctypes.wintypes as wt
import json
import os
import sys
import time

BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, BASE)

from app import cdp, winutil
from app.config import Config
from app.engines.base import TextItem
from app.engines.inject import InjectChannel

user32 = ctypes.windll.user32


def windows_of_pid(pid: int):
    found = []
    EnumProc = ctypes.WINFUNCTYPE(wt.BOOL, wt.HWND, wt.LPARAM)

    def cb(hwnd, lparam):
        q = wt.DWORD()
        user32.GetWindowThreadProcessId(hwnd, ctypes.byref(q))
        if q.value == pid and user32.IsWindowVisible(hwnd):
            r = wt.RECT()
            user32.GetWindowRect(hwnd, ctypes.byref(r))
            if (r.right - r.left) > 200 and (r.bottom - r.top) > 200:
                found.append(hwnd)
        return True

    user32.EnumWindows(EnumProc(cb), 0)
    return found


def window_of_port(port: int) -> int:
    import subprocess
    try:
        out = subprocess.run(["powershell", "-NoProfile", "-Command",
                              "(Get-NetTCPConnection -LocalPort %d -State Listen).OwningProcess | Select-Object -Unique" % port],
                             capture_output=True, text=True, timeout=20)
        pids = [int(x) for x in out.stdout.split() if x.strip().isdigit()]
    except Exception:
        pids = []
    for pid in pids:
        wins = windows_of_pid(pid)
        if wins:
            return max(wins, key=lambda h: (len(wins), h))
    return 0

MENU_TITLE_JS = r"""
(() => {
  const out = [];
  for (const el of document.querySelectorAll('.menubar-menu-title')) {
    const t = (el.innerText || '').trim();
    const r = el.getBoundingClientRect();
    if (r.width > 2) out.push({t: t, x: Math.round(r.left + r.width / 2), y: Math.round(r.top + r.height / 2)});
  }
  return JSON.stringify(out);
})()
"""

ITEMS_JS = r"""
(() => {
  const out = [];
  for (const a of document.querySelectorAll('.monaco-menu .action-label')) {
    const t = (a.innerText || '').trim();
    const r = a.getBoundingClientRect();
    if (r.width < 3 || r.height < 3) continue;
    if (!t) continue;
    out.push({t: t, x: Math.round(r.left + r.width / 2), y: Math.round(r.top + r.height / 2)});
  }
  return JSON.stringify(out.slice(0, 10));
})()
"""

CHECK_JS = r"""
(() => {
  const repl = document.querySelectorAll('[data-ti-repl="1"]').length;
  const txt = [];
  for (const r of document.querySelectorAll('[data-ti-repl="1"]')) {
    txt.push((r.textContent || '').slice(0, 24));
  }
  return JSON.stringify({repl: repl, txt: txt});
})()
"""


class FakeWindow:
    def __init__(self, title, hwnd=0):
        self.title = title
        self.hwnd = hwnd


def move(hwnd, pg, x, y):
    """把系统鼠标移到页面坐标 (x,y) 处。

    程序读悬停位置时用的是"系统光标 → 窗口客户区 → CSS 像素"，
    所以这里必须真的移动系统鼠标，只派发 CDP 事件是不够的。
    """
    dpr = float(pg.eval("(window.devicePixelRatio || 1)", timeout=5) or 1) or 1.0
    r = winutil.get_client_rect(hwnd) if hwnd else None
    if r:
        user32.SetCursorPos(int(r[0] + x * dpr), int(r[1] + y * dpr))
    time.sleep(0.25)


def main() -> int:
    args = sys.argv[1:]
    port = 9402
    for a in args:
        if a.isdigit():
            port = int(a)
    keep = "--keep" in args

    pages = cdp.list_pages(port)
    page = next((p for p in pages if p.get("type") == "page"
                 and not (p.get("url") or "").startswith("devtools://")), None)
    if not page:
        print("端口 %d 没有 page target" % port)
        return 2
    title = page.get("title") or ""
    pg = cdp.CDPPage(page["webSocketDebuggerUrl"])

    raw = pg.eval(MENU_TITLE_JS, timeout=8)
    menus = raw if isinstance(raw, list) else json.loads(raw)
    go = next((m for m in menus if m["t"].strip().lower() == "go"), None)
    if not go:
        print("没找到 Go 菜单")
        return 3
    # 先把可能还开着的菜单关掉，否则这次点击只会把它切换掉
    for typ in ("keyDown", "keyUp"):
        pg.call("Input.dispatchKeyEvent", {"type": typ, "key": "Escape", "code": "Escape",
                                            "windowsVirtualKeyCode": 27}, timeout=6)
    time.sleep(0.25)
    for typ in ("mousePressed", "mouseReleased"):
        pg.call("Input.dispatchMouseEvent", {
            "type": typ, "x": go["x"], "y": go["y"], "button": "left",
            "buttons": 1 if typ == "mousePressed" else 0, "clickCount": 1}, timeout=6)
    time.sleep(0.4)

    raw = pg.eval(ITEMS_JS, timeout=8)
    items = raw if isinstance(raw, list) else json.loads(raw)
    if len(items) < 2:
        print("菜单项不足 2 个，无法测悬停")
        return 4
    print("菜单项:", [i["t"][:24] for i in items[:5]])
    hwnd = window_of_port(port)
    old = winutil.get_cursor_pos()
    print("目标窗口 hwnd=%s，原鼠标位置 %s" % (hwnd, old))

    cfg = Config(os.path.join(BASE, "config.json"))
    cfg.set("capture", "inject_ports", [port], False)
    cfg.set("capture", "ui_hover", True, False)
    ch = InjectChannel(cfg)
    if not ch.attach(FakeWindow(title, hwnd)):
        print("attach 失败:", ch.last_error)
        return 5

    ok_all = True
    # 第一项：鼠标移上去 → 应该只读到这一项
    move(hwnd, pg, items[0]["x"], items[0]["y"])
    blocks = ch.read()
    print("\n悬停第 1 项 %r -> 读到 %d 块" % (items[0]["t"][:24], len(blocks)))
    for b in blocks:
        print("   ", b["text"][:44])
    hit1 = blocks and items[0]["t"][:12] in blocks[0]["text"]
    print("   命中第 1 项:", hit1)
    n = ch.apply([(TextItem(0, 0, 0, 0, b["text"], 1.0), "【译】" + b["text"][:10]) for b in blocks])
    print("   写回:", n)

    # 第二项：移动鼠标 → 应读到另一项（第一项已翻不再返回）
    move(hwnd, pg, items[1]["x"], items[1]["y"])
    blocks2 = ch.read()
    print("\n悬停第 2 项 %r -> 读到 %d 块" % (items[1]["t"][:24], len(blocks2)))
    for b in blocks2:
        print("   ", b["text"][:44])
    hit2 = blocks2 and items[1]["t"][:12] in blocks2[0]["text"]
    print("   命中第 2 项:", hit2)
    n2 = ch.apply([(TextItem(0, 0, 0, 0, b["text"], 1.0), "【译】" + b["text"][:10]) for b in blocks2])
    print("   写回:", n2)

    raw = pg.eval(CHECK_JS, timeout=8)
    ck = raw if isinstance(raw, dict) else json.loads(raw)
    print("\n校验:", json.dumps(ck, ensure_ascii=False)[:200])
    ok_all = bool(hit1 and hit2 and n >= 1 and n2 >= 1 and ck.get("repl", 0) >= 2)

    if not keep:
        print("还原节点:", ch.restore())
        raw = pg.eval(CHECK_JS, timeout=8)
        ck2 = raw if isinstance(raw, dict) else json.loads(raw)
        print("还原后 repl:", ck2.get("repl"))
        ok_all = ok_all and ck2.get("repl", 0) == 0

    ch.close()
    pg.close()
    user32.SetCursorPos(old[0], old[1])
    print("\n结论:", "通过（悬停只翻鼠标下那一项）" if ok_all else "不符合预期")
    return 0 if ok_all else 6


if __name__ == "__main__":
    sys.exit(main())
