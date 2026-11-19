"""诊断：VS Code 弹出菜单（文件/编辑/Go 的下拉）里的项目为什么没被翻译。

做法：连上带调试端口的 VS Code → 用 CDP 真实鼠标事件点开菜单栏的 "Go" →
检查菜单项在 DOM 里是否存在、以及我们的扫描规则（READ_JS）能不能扫到它们，
扫不到就打印是被哪条规则排除的。

用法：python tools/diag_vscode_menu.py [port]
"""
import json
import os
import sys
import time

BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, BASE)

from app import cdp
from app.engines.inject import READ_JS, DEFAULT_SKIP

PORT = int(sys.argv[1]) if len(sys.argv) > 1 else 9401

FIND_MENU_JS = r"""
(() => {
  const out = [];
  for (const el of document.querySelectorAll('.menubar-menu-title, .menubar-menu-button')) {
    const t = (el.innerText || '').trim();
    const r = el.getBoundingClientRect();
    if (r.width < 2) continue;
    out.push({t: t, x: Math.round(r.left + r.width / 2), y: Math.round(r.top + r.height / 2)});
  }
  return JSON.stringify(out);
})()
"""

# 菜单打开后：菜单项在不在 DOM 里、是哪个容器、能不能被常规选择器命中
PROBE_JS = r"""
(() => {
  const res = {found: [], containers: {}};
  const kw = 'Go to Definition';
  const all = document.querySelectorAll('span,div,a,label,button,p,li');
  for (const el of all) {
    const t = (el.innerText || '').replace(/\s+/g, ' ').trim();
    if (t.indexOf('Go to') < 0 && t.indexOf('Alto') < 0) continue;
    if (t.length > 200) continue;
    const r = el.getBoundingClientRect();
    if (r.width < 2 || r.height < 2) continue;
    const cls = (el.className || '').toString().slice(0, 60);
    const chain = (function (e) {
      const p = [];
      while (e && p.length < 4) { p.push(e.tagName + '.' + String(e.className || '').slice(0, 24)); e = e.parentElement; }
      return p.join(' < ');
    })(el);
    res.found.push({t: t.slice(0, 50), cls: cls, chain: chain,
                    box: [Math.round(r.left), Math.round(r.top), Math.round(r.width), Math.round(r.height)],
                    vis: getComputedStyle(el).visibility, op: getComputedStyle(el).opacity});
    if (res.found.length > 14) break;
  }
  return JSON.stringify(res);
})()
"""

# 用真实的 READ_JS 扫一遍，看菜单项是否进入候选
SCAN_JS = READ_JS.replace("__SKIP__", json.dumps(DEFAULT_SKIP))

# 逐条判定：为什么某个元素会被 READ_JS 排除
JUDGE_JS = r"""
(() => {
  const SKIP = __SKIP__;
  const EN = /[A-Za-z]{2}/;
  const el = (function () {
    const cands = document.querySelectorAll('span,div,a,label,button,p,li');
    for (const e of cands) {
      const t = (e.innerText || '').replace(/\s+/g, ' ').trim();
      if (t === 'Go to Definition') return e;
    }
    return null;
  })();
  if (!el) return JSON.stringify({err: 'no such element'});
  const t = (el.innerText || '').replace(/\s+/g, ' ').trim();
  const r = el.getBoundingClientRect();
  const cs = getComputedStyle(el);
  const reasons = [];
  if (el.getAttribute('data-ti-done') === '1') reasons.push('data-ti-done');
  if (t.length < 3 || t.length > 600) reasons.push('length=' + t.length);
  if (!EN.test(t)) reasons.push('no-en');
  if (r.width < 3 || r.height < 3) reasons.push('box-small');
  if (r.bottom < 0 || r.top > innerHeight || r.right < 0 || r.left > innerWidth) reasons.push('offscreen');
  if (cs.visibility === 'hidden' || cs.display === 'none' || cs.opacity === '0') reasons.push('invisible:' + cs.visibility + '/' + cs.display + '/' + cs.opacity);
  if (SKIP && el.closest(SKIP)) reasons.push('skip-closest');
  for (const c of el.children) {
    if (((c.innerText || '').replace(/\s+/g, ' ').trim()) === t) { reasons.push('dup-child'); break; }
  }
  let textKids = 0;
  for (const c of el.children) if (((c.innerText || '').trim()).length > 0) textKids++;
  if (textKids >= 3) reasons.push('container3 kids=' + textKids);
  if (textKids >= 2 && t.length > 60) reasons.push('container2-long');
  return JSON.stringify({text: t, box: [Math.round(r.left), Math.round(r.top), Math.round(r.width), Math.round(r.height)],
                         cls: (el.className || '').toString().slice(0, 60), reasons: reasons});
})()
"""


def main() -> int:
    pages = cdp.list_pages(PORT)
    if not pages:
        print("端口 %d 没有调试目标（先带 --remote-debugging-port=%d 启动 VS Code）" % (PORT, PORT))
        return 2
    page = None
    for p in pages:
        if p.get("type") == "page" and not (p.get("url") or "").startswith("devtools://"):
            page = p
            break
    if page is None:
        print("没找到 page target")
        return 2
    print("target:", (page.get("title") or "")[:50])
    pg = cdp.CDPPage(page["webSocketDebuggerUrl"])

    raw = pg.eval(FIND_MENU_JS, timeout=8)
    menus = raw if isinstance(raw, list) else json.loads(raw)
    print("\n菜单栏项:", [m["t"] for m in menus][:12])
    go = None
    for m in menus:
        if m["t"].strip().lower() in ("go", "转到"):
            go = m
            break
    if go is None:
        print("没找到 Go 菜单项")
        pg.close()
        return 3

    print("点开 Go 菜单 @", go["x"], go["y"])
    for typ in ("mousePressed", "mouseReleased"):
        pg.call("Input.dispatchMouseEvent", {
            "type": typ, "x": go["x"], "y": go["y"],
            "button": "left", "buttons": 1 if typ == "mousePressed" else 0,
            "clickCount": 1,
        }, timeout=6)
    time.sleep(0.5)

    raw = pg.eval(PROBE_JS, timeout=8)
    probe = raw if isinstance(raw, dict) else json.loads(raw)
    print("\nDOM 里带 'Go to' 的可见元素:", len(probe["found"]))
    for f in probe["found"][:8]:
        print("   %-46r box=%s vis=%s op=%s" % (f["t"], f["box"], f["vis"], f["op"]))
        print("       chain:", f["chain"])

    raw = pg.eval(SCAN_JS, timeout=8)
    blocks = raw if isinstance(raw, list) else json.loads(raw)
    hits = [b for b in blocks if "Go to" in b["text"]]
    print("\nREAD_JS 扫到的块数:", len(blocks), "| 其中含 'Go to':", len(hits))
    for b in hits[:8]:
        print("   ", b["text"][:50])

    raw = pg.eval(JUDGE_JS.replace("__SKIP__", json.dumps(DEFAULT_SKIP)), timeout=8)
    judge = raw if isinstance(raw, dict) else json.loads(raw)
    print("\n逐条判定 'Go to Definition':")
    print("  ", json.dumps(judge, ensure_ascii=False)[:600])

    pg.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
