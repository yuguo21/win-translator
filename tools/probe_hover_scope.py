"""把鼠标"悬停"到容器的不同位置，看实际会翻什么、会不会把里面的内容吃掉。

用户的两个样本：
  1) <div id=test1><p>..</p><div><span>jam</span></div></div>
  2) <div id=test1>my name is lily.<p>..</p><div><span>jam</span></div></div>   ← 容器带裸文本

用法：
  python tools\probe_hover_scope.py             # 样本 1
  python tools\probe_hover_scope.py 2           # 样本 2（带裸文本）
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

SET_POINT_JS = """
((x, y) => {
  const st = window.__tt = window.__tt || {};
  if (!st.inited) { return "not-inited"; }
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

# 容器里"裸文本"（直接文本节点，不在任何子元素里）的位置
LOOSE_RECT_JS = """
((sel) => {
  const t1 = document.querySelector(sel);
  for (const n of t1.childNodes) {
    if (n.nodeType === 3 && (n.nodeValue || '').trim()) {
      const rg = document.createRange();
      rg.selectNodeContents(n);
      const r = rg.getBoundingClientRect();
      return {l: r.left, t: r.top, r: r.right, b: r.bottom, text: n.nodeValue.trim()};
    }
  }
  return null;
})(%s)
"""

READ_STATE_JS = """
((sel) => {
  const t1 = document.querySelector(sel);
  const p = t1.querySelector('p');
  // 写回时会在段落里插一个 <span data-tt-repl>，选页面原生 span 必须排除它
  const span = t1.querySelector('span:not([data-tt-repl])');
  const repls = t1.querySelectorAll('[data-tt-repl="1"]');
  // 容器自己的直接文本节点（裸文本）——最能证明"有没有被吃掉"
  let loose = '';
  for (const n of t1.childNodes) if (n.nodeType === 3) loose += n.nodeValue;
  const pDirect = p ? (() => {
    let s = '';
    for (const n of p.childNodes) if (n.nodeType === 3) s += n.nodeValue;
    return s.trim();
  })() : null;
  const orig = [];
  const w = document.createTreeWalker(t1, NodeFilter.SHOW_TEXT, null);
  let n2;
  while ((n2 = w.nextNode())) {
    if (n2.__ttOrig !== undefined) orig.push(n2.__ttOrig.trim());
  }
  return JSON.stringify({
    inner: (t1.innerText || '').replace(/\\s+/g, ' ').trim(),
    loose_text: loose.replace(/\\s+/g, ' ').trim(),
    p_direct_text: pDirect,
    p_done: p ? p.getAttribute('data-tt-done') === '1' : null,
    span_text: span ? (span.textContent || '').trim() : null,
    span_display: span ? getComputedStyle(span).display : null,
    repl_count: repls.length,
    kept_originals: orig
  });
})(%s)
"""


def _j(v):
    """pg.eval 已经帮我们把 JSON 解析成对象了，这里两种都兼容。"""
    if isinstance(v, (dict, list)):
        return v
    if isinstance(v, str):
        try:
            return json.loads(v)
        except Exception:
            return v
    return v


def hover_and_translate(pg, x, y, label):
    pg.eval(SET_POINT_JS % (x, y), timeout=5)
    raw = pg.eval(POLL_JS, timeout=6)
    print("  命中 -> %s" % (raw,))
    data = _j(raw)
    if not isinstance(data, dict) or not data:
        print("  [%s] 没有要翻的段落（不翻，不改页面）" % label)
        return None
    if data.get("err"):
        print("  [%s] 引擎拒绝：%s" % (label, data["err"]))
        return None
    payload = {"id": data["id"], "t": "【译文】" + data["text"][:14]}
    js = APPLY_JS_TPL.replace("__PAYLOAD__", json.dumps(payload, ensure_ascii=False))
    ok = pg.eval(js, timeout=6)
    print("  [%s] 写回 %s | 被选中的原文=%r" % (label, ok, data["text"]))
    return data


def main() -> int:
    sample = sys.argv[1] if len(sys.argv) > 1 else "1"
    page = "hover_scope2.html" if sample.strip() == "2" else "hover_scope.html"

    port = _testenv.ensure(_testenv.page_url(page))
    time.sleep(1.5)
    pages = cdp.list_pages(port)
    pg = cdp.CDPPage(pages[0]["webSocketDebuggerUrl"])
    if page not in (pg.eval("location.href", timeout=5) or ""):
        _testenv._navigate(_testenv.page_url(page))
        time.sleep(1.2)
    pg.eval(POLL_JS, timeout=5)          # 初始化（装 mousemove 监听）
    print("样本 %s | 页面: %s" % (sample, pg.eval("location.href", timeout=5)))

    r1 = _j(pg.eval(RECT_JS % "'#test1'", timeout=5))
    rp = _j(pg.eval(RECT_JS % "'#test1 > p'", timeout=5))
    rs = _j(pg.eval(RECT_JS % "'#test1 span'", timeout=5))
    loose = _j(pg.eval(LOOSE_RECT_JS % "'#test1'", timeout=5))
    print("#test1 rect:", r1)
    print("  p rect:", rp, "| span rect:", rs, "| 裸文本:", loose)

    points = []
    if loose:
        points.append(("悬停容器里的裸文本 %r" % loose["text"], loose["l"] + 12, (loose["t"] + loose["b"]) / 2))
    points.append(("悬停在 <p> 上", (rp["l"] + rp["r"]) / 2, (rp["t"] + rp["b"]) / 2))
    points.append(("悬停在 <span>jam</span> 上", (rs["l"] + rs["r"]) / 2, (rs["t"] + rs["b"]) / 2))
    points.append(("悬停在 #test1 的空白处", r1["l"] + 5, r1["b"] - 8))

    sel = "'#test1'"
    for label, x, y in points:
        print("\n== %s (x=%d, y=%d) ==" % (label, x, y))
        pg.eval(RESTORE_JS, timeout=6)
        hover_and_translate(pg, int(x), int(y), label)
        state = _j(pg.eval(READ_STATE_JS % sel, timeout=6))
        print("  可见文字: %r" % state["inner"])
        print("  容器裸文本=%r | p 自己的文本=%r done=%s" % (
            state["loose_text"], state["p_direct_text"], state["p_done"]))
        print("  原生 span=%r (%s) | 替换块=%d | 留存原文=%s" % (
            state["span_text"], state["span_display"], state["repl_count"], state["kept_originals"]))
        pg.eval(RESTORE_JS, timeout=6)
        after = _j(pg.eval(READ_STATE_JS % sel, timeout=6))
        print("  还原后: %r | 裸文本=%r | p=%r | span=%r" % (
            after["inner"], after["loose_text"], after["p_direct_text"], after["span_text"]))

    pg.eval(RESTORE_JS, timeout=6)
    pg.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
