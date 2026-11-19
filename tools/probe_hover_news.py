"""复现新华网图片列表：悬停在标题/图片/播放按钮上，到底哪些东西会被替换。

用户反馈：这种格式"还是会覆盖原有内容"。
这里逐个悬停点记录：命中了哪个元素、写了什么、哪些元素被 display:none、
图片是否还在、标题链接是否还能点。

用法：python tools\probe_hover_news.py
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

SET_POINT_JS = """
((x, y) => {
  const st = window.__tt = window.__tt || {};
  if (!st.inited) return "not-inited";
  st.mx = x; st.my = y;
  return "ok";
})(%d, %d)
"""

RECT_JS = """
((sel) => {
  const e = document.querySelector(sel);
  if (!e) return null;
  const r = e.getBoundingClientRect();
  return {l: r.left, t: r.top, r: r.right, b: r.bottom, w: Math.round(r.width), h: Math.round(r.height)};
})(%s)
"""

# 第 2 个 item（idx=1）的完整状态
STATE_JS = """
(() => {
  const item = document.querySelectorAll('.item')[1];
  const img = item.querySelector('img');
  const tit = item.querySelector('.tit');
  const a = tit.querySelector('a:not([data-tt-repl])');
  const repl = tit.querySelector('[data-tt-repl="1"]');
  const hidden = [];
  for (const k of tit.querySelectorAll('*')) {
    if (getComputedStyle(k).display === 'none') hidden.push(k.tagName + (k.getAttribute('data-tt-repl') ? '(repl)' : ''));
  }
  const ir = img.getBoundingClientRect();
  return JSON.stringify({
    item_text: (item.innerText || '').replace(/\\s+/g, ' ').trim(),
    tit_text: (tit.innerText || '').replace(/\\s+/g, ' ').trim(),
    tit_done: tit.getAttribute('data-tt-done') === '1',
    a_display: a ? getComputedStyle(a).display : 'no-a',
    a_href: a ? a.getAttribute('href') : '',
    a_text: a ? (a.textContent || '').trim() : '',
    img_visible: getComputedStyle(img).display !== 'none' && ir.width > 1 && ir.height > 1,
    repl_text: repl ? repl.textContent : '',
    hidden_in_tit: hidden
  });
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
    print("页面:", pg.eval("location.href", timeout=5))

    ri = _j(pg.eval(RECT_JS % "'.item:nth-child(2)'", timeout=5))
    rt = _j(pg.eval(RECT_JS % "'.item:nth-child(2) .tit'", timeout=5))
    ra = _j(pg.eval(RECT_JS % "'.item:nth-child(2) .tit a'", timeout=5))
    rg = _j(pg.eval(RECT_JS % "'.item:nth-child(2) img'", timeout=5))
    rp = _j(pg.eval(RECT_JS % "'.item:nth-child(2) .btn-play'", timeout=5))
    print("item:", ri, "\n tit:", rt, "\n a:", ra, "\n img:", rg, "\n btn-play:", rp)

    points = [
        ("标题链接文字上（命中 <a>）", (ra["l"] + ra["r"]) / 2, (ra["t"] + ra["b"]) / 2),
        ("标题区域空白（命中 div.tit）", rt["r"] - 2, (rt["t"] + rt["b"]) / 2),
        ("图片上（命中 <img>，应不翻）", (rg["l"] + rg["r"]) / 2, (rg["t"] + rg["b"]) / 2),
        ("播放按钮上（命中 div.btn-play）", (rp["l"] + rp["r"]) / 2, (rp["t"] + rp["b"]) / 2),
    ]

    for label, x, y in points:
        print("\n== %s (x=%d, y=%d) ==" % (label, x, y))
        pg.eval(RESTORE_JS, timeout=6)
        pg.eval(SET_POINT_JS % (int(x), int(y)), timeout=5)
        raw = _j(pg.eval(POLL_JS, timeout=6))
        print("  命中 -> %s" % (raw,))
        translated = bool(isinstance(raw, dict) and raw.get("id"))
        if translated:
            payload = {"id": raw["id"], "t": "【译文】" + raw["text"][:16]}
            pg.eval(APPLY_JS_TPL.replace("__PAYLOAD__", json.dumps(payload, ensure_ascii=False)), timeout=6)
        st = _j(pg.eval(STATE_JS, timeout=6))
        print("  item 可见文字: %r" % st["item_text"])
        print("  tit: done=%s 显示=%r | 隐藏元素=%s" % (st["tit_done"], st["tit_text"], st["hidden_in_tit"]))
        print("  标题链接: display=%s href=%s 文字=%r" % (st["a_display"], st["a_href"], st["a_text"]))
        print("  图片可见=%s | 替换块=%r" % (st["img_visible"], st["repl_text"]))
        pg.eval(RESTORE_JS, timeout=6)
        after = _j(pg.eval(STATE_JS, timeout=6))
        print("  还原后: tit=%r | a.display=%s a 文字=%r | 图片=%s" % (
            after["tit_text"], after["a_display"], after["a_text"], after["img_visible"]))

    pg.eval(RESTORE_JS, timeout=6)
    pg.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
