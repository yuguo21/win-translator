"""图片悬停测试：停在图片上必须"什么都不做"，图片不能消失。

用户反馈：鼠标悬停到图片上，图片消失了。
根因是向上找到了图片的父容器、被整块替换，写回时把所有子元素（含 <img>）display:none。

用法：python tools\probe_hover_media.py
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

PAGE = "hover_media.html"

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
  return {l: r.left, t: r.top, r: r.right, b: r.bottom};
})(%s)
"""

# 图片是否还"看得见"：display 非 none、宽高非 0、且排在可见区域内
STATE_JS = """
(() => {
  const out = {};
  for (const id of ['pic1', 'pic2']) {
    const e = document.getElementById(id);
    if (!e) { out[id] = 'missing'; continue; }
    const cs = getComputedStyle(e);
    const r = e.getBoundingClientRect();
    out[id] = {
      display: cs.display,
      visibility: cs.visibility,
      w: Math.round(r.width), h: Math.round(r.height),
      visible: cs.display !== 'none' && cs.visibility !== 'hidden' && r.width > 1 && r.height > 1
    };
  }
  out['card_inner'] = (document.querySelector('#card').innerText || '').replace(/\\s+/g, ' ').trim();
  out['wrap2_inner'] = (document.querySelector('#wrap2').innerText || '').replace(/\\s+/g, ' ').trim();
  out['repls'] = document.querySelectorAll('[data-tt-repl="1"]').length;
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
    print("页面:", pg.eval("location.href", timeout=5))

    r1 = _j(pg.eval(RECT_JS % "'#pic1'", timeout=5))
    r2 = _j(pg.eval(RECT_JS % "'#pic2'", timeout=5))
    rc = _j(pg.eval(RECT_JS % "'#cap'", timeout=5))
    rp = _j(pg.eval(RECT_JS % "'#wrap2 p'", timeout=5))
    print("pic1:", r1, "\npic2:", r2, "\ncap:", rc, "\np:", rp)

    checks = []
    points = [
        ("悬停在 #pic1 图片上", (r1["l"] + r1["r"]) / 2, (r1["t"] + r1["b"]) / 2, False),
        ("悬停在 #pic2 图片上", (r2["l"] + r2["r"]) / 2, (r2["t"] + r2["b"]) / 2, False),
        ("悬停在 caption 文字上（应正常翻）", (rc["l"] + rc["r"]) / 2, (rc["t"] + rc["b"]) / 2, True),
        ("悬停在 <p> 上（应正常翻）", (rp["l"] + rp["r"]) / 2, (rp["t"] + rp["b"]) / 2, True),
    ]

    for label, x, y, should_tr in points:
        print("\n== %s (x=%d, y=%d) ==" % (label, x, y))
        pg.eval(RESTORE_JS, timeout=6)
        pg.eval(SET_POINT_JS % (int(x), int(y)), timeout=5)
        raw = _j(pg.eval(POLL_JS, timeout=6))
        translated = bool(isinstance(raw, dict) and raw.get("id"))
        print("  命中 -> %s | 触发翻译: %s" % (raw, translated))
        if translated:
            payload = {"id": raw["id"], "t": "【译文】" + raw["text"][:14]}
            pg.eval(APPLY_JS_TPL.replace("__PAYLOAD__", json.dumps(payload, ensure_ascii=False)), timeout=6)
        st = _j(pg.eval(STATE_JS, timeout=6))
        print("  pic1 可见=%s (display=%s, %sx%s) | pic2 可见=%s (display=%s, %sx%s)" % (
            st["pic1"]["visible"] if st["pic1"] != "missing" else "missing",
            st["pic1"]["display"] if st["pic1"] != "missing" else "-",
            st["pic1"]["w"] if st["pic1"] != "missing" else "-",
            st["pic1"]["h"] if st["pic1"] != "missing" else "-",
            st["pic2"]["visible"] if st["pic2"] != "missing" else "missing",
            st["pic2"]["display"] if st["pic2"] != "missing" else "-",
            st["pic2"]["w"] if st["pic2"] != "missing" else "-",
            st["pic2"]["h"] if st["pic2"] != "missing" else "-"))
        print("  card=%r | wrap2=%r | 替换块=%d" % (st["card_inner"], st["wrap2_inner"], st["repls"]))

        ok_vis = (st["pic1"] == "missing" or st["pic1"]["visible"]) and (st["pic2"] == "missing" or st["pic2"]["visible"])
        ok_tr = (translated == should_tr)
        checks.append((label, ok_vis, ok_tr))
        print("  判定: 图片一直可见=%s | 翻译行为符合预期=%s" % (ok_vis, ok_tr))
        pg.eval(RESTORE_JS, timeout=6)
        after = _j(pg.eval(STATE_JS, timeout=6))
        print("  还原后 card=%r | wrap2=%r" % (after["card_inner"], after["wrap2_inner"]))

    pg.eval(RESTORE_JS, timeout=6)
    pg.close()
    bad = [c for c in checks if not (c[1] and c[2])]
    print("\n结论:", "全部通过" if not bad else ("有问题: %s" % bad))
    return 0 if not bad else 3


if __name__ == "__main__":
    sys.exit(main())
