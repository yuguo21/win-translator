"""验证"不会把容器整块清空"。

背景：之前鼠标停在菜单面板的空白边角时，程序会把整个菜单容器当成一段文字，
写回时清空了它里面所有文本节点 —— 结果菜单项全变空白、顶部压出一整行拼接中文。

这个测试做两件事：
1. 把系统鼠标停在菜单面板的空白处 → 读到的应该是 0 块（容器不算文字块）；
2. 手动把一个容器塞进内部块表、强行写回 → 写回应被拒绝（返回 -1），菜单项文字必须完好。

用法：python tools/test_ui_container.py [port]（需要带调试端口启动的 VS Code）
"""
import ctypes
import ctypes.wintypes as wt
import json
import os
import subprocess
import sys
import time

BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, BASE)

from app import cdp, winutil
from app.config import Config
from app.engines.base import TextItem
from app.engines.inject import InjectChannel, APPLY_JS

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

# 菜单面板里每一项的 label 文字 + 菜单整体矩形
MENU_INFO_JS = r"""
(() => {
  const m = document.querySelector('.monaco-menu');
  if (!m) return JSON.stringify({ok: false});
  const r = m.getBoundingClientRect();
  const labels = [];
  for (const a of m.querySelectorAll('.action-label')) {
    const t = (a.innerText || '').trim();
    if (t) labels.push(t);
  }
  return JSON.stringify({ok: true, rect: {x: r.left, y: r.top, w: r.width, h: r.height},
                         labels: labels, count: labels.length});
})()
"""


def window_of_port(port: int) -> int:
    try:
        out = subprocess.run(["powershell", "-NoProfile", "-Command",
                              "(Get-NetTCPConnection -LocalPort %d -State Listen).OwningProcess | Select-Object -Unique" % port],
                             capture_output=True, text=True, timeout=20)
        pids = [int(x) for x in out.stdout.split() if x.strip().isdigit()]
    except Exception:
        return 0
    best = 0
    EnumProc = ctypes.WINFUNCTYPE(wt.BOOL, wt.HWND, wt.LPARAM)

    def cb(hwnd, lparam):
        nonlocal best
        q = wt.DWORD()
        user32.GetWindowThreadProcessId(hwnd, ctypes.byref(q))
        if q.value in pids and user32.IsWindowVisible(hwnd):
            r = wt.RECT()
            user32.GetWindowRect(hwnd, ctypes.byref(r))
            if (r.right - r.left) > 200 and (r.bottom - r.top) > 200:
                best = hwnd
        return True

    user32.EnumWindows(EnumProc(cb), 0)
    return best


def main() -> int:
    port = int(sys.argv[1]) if len(sys.argv) > 1 and sys.argv[1].isdigit() else 9400
    hwnd = window_of_port(port)
    print("目标窗口 hwnd=%s" % hwnd)
    pages = cdp.list_pages(port)
    page = next((p for p in pages if p.get("type") == "page"
                 and not (p.get("url") or "").startswith("devtools://")), None)
    pg = cdp.CDPPage(page["webSocketDebuggerUrl"])

    raw = pg.eval(MENU_TITLE_JS, timeout=8)
    menus = raw if isinstance(raw, list) else json.loads(raw)
    go = next((m for m in menus if m["t"].strip().lower() == "go"), None)
    for typ in ("keyDown", "keyUp"):
        pg.call("Input.dispatchKeyEvent", {"type": typ, "key": "Escape", "code": "Escape",
                                            "windowsVirtualKeyCode": 27}, timeout=6)
    time.sleep(0.25)
    if go:
        for typ in ("mousePressed", "mouseReleased"):
            pg.call("Input.dispatchMouseEvent", {"type": typ, "x": go["x"], "y": go["y"],
                                                  "button": "left",
                                                  "buttons": 1 if typ == "mousePressed" else 0,
                                                  "clickCount": 1}, timeout=6)
    time.sleep(0.45)

    raw = pg.eval(MENU_INFO_JS, timeout=8)
    info = raw if isinstance(raw, dict) else json.loads(raw)
    if not info.get("ok"):
        print("菜单没打开，测不了")
        pg.close()
        return 2
    print("菜单项 %d 个: %s" % (info["count"], [s[:18] for s in info["labels"][:5]]))

    cfg = Config(os.path.join(BASE, "config.json"))
    cfg.set("capture", "inject_ports", [port], False)
    cfg.set("capture", "ui_hover", True, False)
    title = page.get("title") or ""

    class W:
        pass

    w = W()
    w.title = title
    w.hwnd = hwnd
    ch = InjectChannel(cfg)
    if not ch.attach(w):
        print("attach 失败:", ch.last_error)
        pg.close()
        return 3

    old = winutil.get_cursor_pos()
    rect = info["rect"]
    dpr = float(pg.eval("(window.devicePixelRatio || 1)", timeout=5) or 1) or 1.0
    crect = winutil.get_client_rect(hwnd)
    # 停在菜单面板右下角的空白处（离右边 8px、离底边 8px）
    px = rect["x"] + rect["w"] - 8
    py = rect["y"] + rect["h"] - 8
    if crect:
        user32.SetCursorPos(int(crect[0] + px * dpr), int(crect[1] + py * dpr))
    time.sleep(0.3)

    blocks = ch.read()
    print("\n[1] 鼠标停在菜单空白处 -> 读到 %d 块 %s" % (len(blocks), [b["text"][:20] for b in blocks]))
    ok1 = len(blocks) == 0

    # [2] 强行把容器当目标写回：必须被拒绝，且菜单项文字不被破坏
    n = pg.eval(r"""
    (() => {
      window.__ti = window.__ti || {seq: 1, blocks: {}};
      const m = document.querySelector('.monaco-menu');
      window.__ti.blocks['__container_probe'] = m;
      return 1;
    })()
    """, timeout=8)
    js = APPLY_JS.replace("__PAYLOAD__", json.dumps(
        {"id": "__container_probe", "t": "【译】整块菜单", "orig": "不存在的原文"}, ensure_ascii=False))
    res = pg.eval(js, timeout=8)
    print("[2] 对容器强行写回 -> 返回值 %r（-1 表示已拒绝）" % res)
    ok2 = int(res) == -1

    raw = pg.eval(MENU_INFO_JS, timeout=8)
    info2 = raw if isinstance(raw, dict) else json.loads(raw)
    same = (info2.get("count") == info["count"]
            and all(a == b for a, b in zip(info["labels"], info2["labels"])))
    print("[3] 菜单项文字是否完好: %s（%d -> %d 项）" % (same, info["count"], info2.get("count", 0)))
    if not same:
        print("     现在:", [s[:18] for s in (info2.get("labels") or [])[:5]])

    # 正常悬停一项，确认收紧规则后还能翻
    items = pg.eval(r"""
    (() => {
      const out = [];
      for (const a of document.querySelectorAll('.monaco-menu .action-label')) {
        const t = (a.innerText || '').trim();
        const r = a.getBoundingClientRect();
        if (r.width > 3 && r.height > 3 && t) out.push({t: t, x: r.left + r.width / 2, y: r.top + r.height / 2});
      }
      return JSON.stringify(out.slice(0, 3));
    })()
    """, timeout=8)
    items = items if isinstance(items, list) else json.loads(items)
    ok3 = False
    if items and crect:
        user32.SetCursorPos(int(crect[0] + items[0]["x"] * dpr), int(crect[1] + items[0]["y"] * dpr))
        time.sleep(0.35)
        print("     调试：光标实际位置=%s 页面坐标=%s dpr=%s"
              % (winutil.get_cursor_pos(),
                 pg.eval("JSON.stringify([window.__ti && window.__ti.mx, window.__ti && window.__ti.my])", timeout=6),
                 dpr))
        h2 = winutil.get_window_hwnd_under_cursor()
        print("     调试：光标下 hwnd=%s toplevel=%s bound=%s rect=%s"
              % (h2, winutil.toplevel_hwnd(h2) if h2 else 0, ch._bound_root,
                 winutil.get_client_rect(hwnd)))
        blocks3 = ch.read()
        print("     调试：read 之后坐标=%s err=%s"
              % (pg.eval("JSON.stringify([window.__ti && window.__ti.mx, window.__ti && window.__ti.my])", timeout=6),
                 ch.last_error))
        print("[4] 鼠标停在菜单项 %r -> 读到 %d 块 %s"
              % (items[0]["t"][:20], len(blocks3), [b["text"][:20] for b in blocks3]))
        ok3 = len(blocks3) == 1 and items[0]["t"][:10] in blocks3[0]["text"]
        if blocks3:
            ch.apply([(TextItem(0, 0, 0, 0, b["text"], 1.0), "【译】" + b["text"][:10]) for b in blocks3])
            ch.restore()

    user32.SetCursorPos(old[0], old[1])
    pg.eval("(() => { if (window.__ti && window.__ti.blocks) delete window.__ti.blocks['__container_probe']; return 1; })()",
            timeout=6)
    ch.close()
    pg.close()
    ok = ok1 and ok2 and same and ok3
    print("\n结论:", "通过（容器不会被写成空白，菜单项仍可正常翻）" if ok else "不符合预期")
    return 0 if ok else 6


if __name__ == "__main__":
    sys.exit(main())
