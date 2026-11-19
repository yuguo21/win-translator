"""诊断 read() 为什么返回空：把每一步的中间状态都打出来。"""
import json
import os
import sys
import time

BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, BASE)

from app import cdp, winutil
from app.config import Config
from app.engines.inject import InjectChannel, HOVER_READ_JS


def main() -> int:
    port = int(sys.argv[1]) if len(sys.argv) > 1 else 9405
    import ctypes
    import subprocess
    import ctypes.wintypes as wt
    user32 = ctypes.windll.user32

    out = subprocess.run(["powershell", "-NoProfile", "-Command",
                          "(Get-NetTCPConnection -LocalPort %d -State Listen).OwningProcess | Select-Object -Unique" % port],
                         capture_output=True, text=True, timeout=20)
    pids = [int(x) for x in out.stdout.split() if x.strip().isdigit()]
    EnumProc = ctypes.WINFUNCTYPE(wt.BOOL, wt.HWND, wt.LPARAM)
    found = []

    def cb(hwnd, lparam):
        q = wt.DWORD()
        user32.GetWindowThreadProcessId(hwnd, ctypes.byref(q))
        if q.value in pids and user32.IsWindowVisible(hwnd):
            r = wt.RECT()
            user32.GetWindowRect(hwnd, ctypes.byref(r))
            if (r.right - r.left) > 200 and (r.bottom - r.top) > 200:
                found.append(hwnd)
        return True

    user32.EnumWindows(EnumProc(cb), 0)
    hwnd = found[0] if found else 0

    pages = cdp.list_pages(port)
    page = next((p for p in pages if p.get("type") == "page"
                 and not (p.get("url") or "").startswith("devtools://")), None)
    pg = cdp.CDPPage(page["webSocketDebuggerUrl"])

    items = pg.eval(r"""
    (() => { const out=[];
      for (const a of document.querySelectorAll('.monaco-menu .action-label')) {
        const t=(a.innerText||'').trim(); const r=a.getBoundingClientRect();
        if (r.width>3&&r.height>3&&t) out.push({t:t,x:r.left+r.width/2,y:r.top+r.height/2}); }
      return JSON.stringify(out.slice(0,3)); })()
    """, timeout=8)
    items = items if isinstance(items, list) else json.loads(items)
    print("菜单项:", [i["t"] for i in items])
    if not items:
        print("菜单没开，先手动点开 Go 菜单再跑")
        pg.close()
        return 2
    it = items[0]
    dpr = float(pg.eval("(window.devicePixelRatio || 1)", timeout=5) or 1) or 1.0
    crect = winutil.get_client_rect(hwnd)
    user32.SetCursorPos(int(crect[0] + it["x"] * dpr), int(crect[1] + it["y"] * dpr))
    time.sleep(0.35)
    print("鼠标 -> (%d,%d) 页面坐标 (%.1f,%.1f)" % (int(crect[0] + it["x"] * dpr),
                                                    int(crect[1] + it["y"] * dpr), it["x"], it["y"]))

    cfg = Config(os.path.join(BASE, "config.json"))
    cfg.set("capture", "inject_ports", [port], False)
    cfg.set("capture", "ui_hover", True, False)
    skip = cfg.get("capture", "inject_skip", "")

    class W:
        pass

    w = W()
    w.title = page.get("title") or ""
    w.hwnd = hwnd
    ch = InjectChannel(cfg)
    ok = ch.attach(w)
    print("attach:", ok, "bound_root:", ch._bound_root, "dpr:", ch._dpr, "err:", ch.last_error)
    if not ok:
        pg.close()
        return 3

    print("\n[读之前] 页面里的坐标:", pg.eval("JSON.stringify([window.__ti && window.__ti.mx, window.__ti && window.__ti.my, !!(window.__ti && window.__ti.hoverInited)])", timeout=6))
    blocks = ch.read()
    print("[ch.read] 块数:", len(blocks), "err:", ch.last_error)
    print("[读之后] 页面里的坐标:", pg.eval("JSON.stringify([window.__ti && window.__ti.mx, window.__ti && window.__ti.my])", timeout=6))

    # 手动跑同一份 JS，坐标用页面里记录的
    raw = pg.eval(HOVER_READ_JS.replace("__SKIP__", json.dumps(skip)), timeout=8)
    print("[直接跑 HOVER_READ_JS] ->", (raw if isinstance(raw, str) else json.dumps(raw, ensure_ascii=False))[:300])

    pg.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
