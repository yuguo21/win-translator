"""诊断：鼠标停在某个菜单项上时，页面里到底命中了哪个元素、自身文字和整体文字各是什么。"""
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

user32 = ctypes.windll.user32


def window_of_port(port):
    out = subprocess.run(["powershell", "-NoProfile", "-Command",
                          "(Get-NetTCPConnection -LocalPort %d -State Listen).OwningProcess | Select-Object -Unique" % port],
                         capture_output=True, text=True, timeout=20)
    pids = [int(x) for x in out.stdout.split() if x.strip().isdigit()]
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


PROBE_JS = r"""
(() => {
  const st = window.__ti || {};
  const SKIP = __SKIP__;
  const DIRECT = (e) => { let s=''; for (const n of e.childNodes) if (n.nodeType===3) s += (n.nodeValue||''); return s.replace(/\s+/g,' ').trim(); };
  const WHOLE = (e) => (e.innerText||'').replace(/\s+/g,' ').trim();
  const mx = st.mx, my = st.my;
  const pt = (mx === null || mx === undefined) ? null : document.elementFromPoint(mx, my);
  const out = {mx: mx, my: my, haveSt: !!window.__ti, pt: null, cands: []};
  if (pt) {
    out.pt = {cls: pt.className || '', tag: pt.tagName, direct: DIRECT(pt), whole: WHOLE(pt),
              done: pt.getAttribute && pt.getAttribute('data-ti-done'),
              skipHit: (pt.closest && SKIP) ? (() => { for (const s of SKIP.split(',')) { const h = pt.closest(s.trim()); if (h) return s.trim() + ' > ' + (h.className||''); } return ''; })() : '',
              ancestors: (() => { const a=[]; let p=pt; for (let i=0;i<6&&p;i++){ a.push((p.className&&typeof p.className==='string'?p.className:p.tagName)); p=p.parentElement; } return a; })()};
    for (const c of [pt, ...pt.querySelectorAll('span,a,label,button,li,td,p')]) {
      const t = DIRECT(c);
      if (!t) continue;
      const r = c.getBoundingClientRect();
      out.cands.push({cls: (c.className || ''), tag: c.tagName, direct: t.slice(0, 40),
                      whole: WHOLE(c).slice(0, 40), eq: t === WHOLE(c),
                      len: t.length, w: Math.round(r.width), h: Math.round(r.height),
                      inRect: !(mx < r.left || mx > r.right || my < r.top || my > r.bottom),
                      done: c.getAttribute('data-ti-done')});
    }
  }
  if (st.blocks) out.blockCount = Object.keys(st.blocks).length;
  return JSON.stringify(out);
})()
"""


def main() -> int:
    port = int(sys.argv[1]) if len(sys.argv) > 1 else 9405
    hwnd = window_of_port(port)
    pages = cdp.list_pages(port)
    page = next((p for p in pages if p.get("type") == "page"
                 and not (p.get("url") or "").startswith("devtools://")), None)
    pg = cdp.CDPPage(page["webSocketDebuggerUrl"])

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
    print("菜单项:", [i["t"] for i in items])
    if not items:
        print("菜单没开")
        pg.close()
        return 2

    dpr = float(pg.eval("(window.devicePixelRatio || 1)", timeout=5) or 1) or 1.0
    crect = winutil.get_client_rect(hwnd)
    print("hwnd=%s client_rect=%s dpr=%s" % (hwnd, crect, dpr))
    it = items[0]
    px = int(crect[0] + it["x"] * dpr)
    py = int(crect[1] + it["y"] * dpr)
    user32.SetCursorPos(px, py)
    time.sleep(0.4)
    print("把系统鼠标移到 (%d,%d)（页面坐标 %.1f,%.1f）" % (px, py, it["x"], it["y"]))

    # 模拟程序每轮做的事：先同步坐标，再读
    pg.eval("((x,y)=>{const st=window.__ti=window.__ti||{seq:1,blocks:{}};st.hoverInited=true;st.mx=x;st.my=y;return 1;})(%s,%s)"
            % (round(it["x"], 1), round(it["y"], 1)), timeout=6)
    raw = pg.eval(PROBE_JS.replace("__SKIP__", json.dumps(
        ".monaco-editor,.monaco-list,.editor,.terminal,.xterm,.output,.debug-console,.notebook")), timeout=8)
    info = raw if isinstance(raw, dict) else json.loads(raw)
    print(json.dumps({k: v for k, v in info.items() if k != "cands"}, ensure_ascii=False, indent=2))
    print("鼠标下元素的候选列表：")
    for c in info["cands"][:10]:
        print("   ", json.dumps(c, ensure_ascii=False))
    pg.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
