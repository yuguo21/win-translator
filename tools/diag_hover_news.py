"""诊断：悬停标题链接时，POLL 到底选中了哪个元素、写回落到了谁身上。

用法：python tools\diag_hover_news.py
"""
import json
import os
import sys
import time

BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, BASE)

from app import cdp
from app.engines.web_engine import APPLY_JS_TPL, POLL_JS, RESTORE_JS
from tools import _testenv

PAGE = "hover_scope_news.html"

DIAG_JS = """
((x, y) => {
  const st = window.__tt = window.__tt || {};
  if (!st.inited) return "not-inited";
  st.mx = x; st.my = y;
  const hit = document.elementFromPoint(x, y);
  const chain = [];
  let c = hit;
  while (c && c !== document.body && c !== document.documentElement) {
    chain.push(c.tagName + (c.className ? '.' + String(c.className).replace(/ /g, '_') : ''));
    c = c.parentElement;
  }
  const hitText = (hit.innerText || '').replace(/\\s+/g, ' ').trim();
  return JSON.stringify({hit: chain[0], chain: chain, hit_text_len: hitText.length});
})(%d, %d)
"""

# 写回后：done 标记和 repl 块分别落在谁身上
HOST_JS = """
(() => {
  const out = {done: [], repl: [], loose: []};
  for (const e of document.querySelectorAll('[data-tt-done="1"]')) {
    out.done.push(e.tagName + (e.className ? '.' + String(e.className).replace(/ /g, '_') : ''));
  }
  for (const e of document.querySelectorAll('[data-tt-repl="1"]')) {
    const p = e.parentElement;
    out.repl.push((p ? p.tagName + (p.className ? '.' + String(p.className).replace(/ /g, '_') : '') : '?'));
  }
  for (const e of document.querySelectorAll('[data-tt-loose="1"]')) out.loose.push('loose-span');
  // 第 2 个 item 里被 display:none 的元素
  const item = document.querySelectorAll('.item')[1];
  const hidden = [];
  for (const k of item.querySelectorAll('*')) {
    if (getComputedStyle(k).display === 'none') hidden.push(k.tagName + (k.className ? '.' + String(k.className).replace(/ /g, '_') : ''));
  }
  out.hidden_in_item = hidden;
  const img = item.querySelector('img');
  const ir = img.getBoundingClientRect();
  out.img_rect = Math.round(ir.width) + 'x' + Math.round(ir.height);
  return JSON.stringify(out);
})()
"""


def _j(v):
    if isinstance(v, (dict, list)):
        return v
    try:
        return json.loads(v)
    except Exception:
        return v


def main() -> int:
    port = _testenv.ensure(_testenv.page_url(PAGE))
    time.sleep(1.5)
    pages = cdp.list_pages(port)
    pg = cdp.CDPPage(pages[0]["webSocketDebuggerUrl"])
    if PAGE not in (pg.eval("location.href", timeout=5) or ""):
        _testenv._navigate(_testenv.page_url(PAGE))
        time.sleep(1.2)
    pg.eval(POLL_JS, timeout=5)

    for label, x, y in [("标题链接", 387, 199), ("播放按钮", 391, 100), ("图片左边缘", 290, 100)]:
        pg.eval(RESTORE_JS, timeout=6)
        d = _j(pg.eval(DIAG_JS % (x, y), timeout=6))
        print("\n== %s (%d,%d) ==" % (label, x, y))
        print("  elementFromPoint:", d["hit"], "| 祖先链:", " -> ".join(d["chain"]))
        raw = _j(pg.eval(POLL_JS, timeout=6))
        print("  POLL 返回:", raw)
        if isinstance(raw, dict) and raw.get("id"):
            payload = {"id": raw["id"], "t": "【译文】测试"}
            pg.eval(APPLY_JS_TPL.replace("__PAYLOAD__", json.dumps(payload, ensure_ascii=False)), timeout=6)
        print("  写回落点:", _j(pg.eval(HOST_JS, timeout=6)))
        pg.eval(RESTORE_JS, timeout=6)

    pg.eval(RESTORE_JS, timeout=6)
    pg.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
