"""验证 UI 模式"不锁窗口、按鼠标自动跟随"能不能工作。

做法：把系统鼠标临时移到带端口的 VS Code 窗口里（不点击），
打开菜单后用 CDP 把页面鼠标移到某个菜单项上，
再用 UiaEngine（prepare 时 window=None）走 capture_once → 应该自动连上该窗口并读到那 1 块。

用法：python tools/test_ui_auto.py [port]
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
from app.engines.uia_engine import UiaEngine

user32 = ctypes.windll.user32

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
    if (r.width < 3 || r.height < 3 || !t) continue;
    out.push({t: t, x: Math.round(r.left + r.width / 2), y: Math.round(r.top + r.height / 2)});
  }
  return JSON.stringify(out.slice(0, 6));
})()
"""

REPL_JS = r"""(document.querySelectorAll('[data-ti-repl="1"]').length)"""


def windows_of_pid(pid: int):
    found = []
    EnumProc = ctypes.WINFUNCTYPE(wt.BOOL, wt.HWND, wt.LPARAM)

    def cb(hwnd, lparam):
        q = wt.DWORD()
        user32.GetWindowThreadProcessId(hwnd, ctypes.byref(q))
        if q.value == pid and user32.IsWindowVisible(hwnd):
            r = wt.RECT()
            user32.GetWindowRect(hwnd, ctypes.byref(r))
            w, h = r.right - r.left, r.bottom - r.top
            if w > 200 and h > 200:
                found.append((hwnd, r.left, r.top, w, h))
        return True

    user32.EnumWindows(EnumProc(cb), 0)
    return found


def cursor_pos():
    p = wt.POINT()
    user32.GetCursorPos(ctypes.byref(p))
    return p.x, p.y


def main() -> int:
    port = int(sys.argv[1]) if len(sys.argv) > 1 else 9403
    pids = []
    try:
        import subprocess
        out = subprocess.run(["powershell", "-NoProfile", "-Command",
                              "(Get-NetTCPConnection -LocalPort %d -State Listen).OwningProcess | Select-Object -Unique" % port],
                             capture_output=True, text=True, timeout=20)
        pids = [int(x) for x in out.stdout.split() if x.strip().isdigit()]
    except Exception:
        pass
    if not pids:
        print("端口 %d 没有进程" % port)
        return 2
    wins = windows_of_pid(pids[0])
    if not wins:
        print("这个进程没有可见大窗口")
        return 3
    hwnd, wx, wy, ww, wh = max(wins, key=lambda t: t[3] * t[4])
    print("目标窗口 hwnd=%s rect=(%d,%d,%d,%d)" % (hwnd, wx, wy, ww, wh))

    old = cursor_pos()
    cx, cy = wx + ww // 2, wy + wh // 2
    user32.SetCursorPos(cx, cy)
    time.sleep(0.6)
    print("系统鼠标已临时移到 (%d,%d)，原位置 %s" % (cx, cy, old))

    pages = cdp.list_pages(port)
    page = next((p for p in pages if p.get("type") == "page"
                 and not (p.get("url") or "").startswith("devtools://")), None)
    pg = cdp.CDPPage(page["webSocketDebuggerUrl"])

    raw = pg.eval(MENU_TITLE_JS, timeout=8)
    menus = raw if isinstance(raw, list) else json.loads(raw)
    go = next((m for m in menus if m["t"].strip().lower() == "go"), None)

    def open_menu():
        for typ in ("keyDown", "keyUp"):
            pg.call("Input.dispatchKeyEvent", {"type": typ, "key": "Escape", "code": "Escape",
                                                "windowsVirtualKeyCode": 27}, timeout=6)
        time.sleep(0.25)
        if not go:
            return
        for typ in ("mousePressed", "mouseReleased"):
            pg.call("Input.dispatchMouseEvent", {"type": typ, "x": go["x"], "y": go["y"],
                                                  "button": "left", "buttons": 1 if typ == "mousePressed" else 0,
                                                  "clickCount": 1}, timeout=6)
        time.sleep(0.45)

    user32.SetForegroundWindow(hwnd)
    time.sleep(0.3)
    open_menu()
    raw = pg.eval(ITEMS_JS, timeout=8)
    items = raw if isinstance(raw, list) else json.loads(raw)
    if not items:
        open_menu()                       # 再试一次
        raw = pg.eval(ITEMS_JS, timeout=8)
        items = raw if isinstance(raw, list) else json.loads(raw)
    if not items:
        user32.SetCursorPos(old[0], old[1])
        print("菜单没打开，测不了")
        return 4
    item = items[0]
    # 把"系统鼠标"移到这个菜单项的真实位置上（模拟用户把鼠标停上去）
    crect = winutil.get_client_rect(hwnd)
    dpr = float(pg.eval("(window.devicePixelRatio || 1)", timeout=5) or 1) or 1.0
    if crect:
        user32.SetCursorPos(int(crect[0] + item["x"] * dpr), int(crect[1] + item["y"] * dpr))
    time.sleep(0.35)
    print("系统鼠标移到菜单项 %r（页面坐标 %d,%d，dpr=%.2f）" % (item["t"][:24], item["x"], item["y"], dpr))

    cfg = Config(os.path.join(BASE, "config.json"))
    cfg.set("capture", "inject_ports", [port], False)
    cfg.set("capture", "ui_hover", True, False)
    eng = UiaEngine(cfg)
    eng.prepare(None)                   # 关键：不锁窗口
    print("prepare(None) -> auto_follow =", eng._auto_follow)

    items_read = eng.capture_once()
    print("capture_once 读到 %d 块（不锁窗口、按鼠标自动跟随）:" % len(items_read))
    for it in items_read:
        print("   ", it.text[:44])
    auto_ok = len(items_read) == 1 and item["t"][:10] in items_read[0].text

    n = eng.apply([(it, "【译】" + it.text[:10]) for it in items_read])
    print("写回:", n)
    repl = int(pg.eval(REPL_JS, timeout=6) or 0)
    print("页面上 repl 数量:", repl)
    eng.restore()
    repl2 = int(pg.eval(REPL_JS, timeout=6) or 0)
    print("还原后 repl:", repl2)

    eng.close()
    pg.close()
    user32.SetCursorPos(old[0], old[1])
    print("鼠标位置已恢复")

    ok = auto_ok and n >= 1 and repl2 == 0
    print("\n结论:", "通过（不锁窗口也能按鼠标自动翻译）" if ok else "不符合预期")
    return 0 if ok else 5


if __name__ == "__main__":
    sys.exit(main())
