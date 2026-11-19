"""验证：超长段落分块翻译 + 写回完整 + 还原一字不差。

只连测试专用浏览器（9333 / browser_profile_test）。
用法：python tools\probe_long.py
"""
import json
import os
import sys
import time

BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, BASE)
sys.path.insert(0, os.path.join(BASE, "tools"))

import _testenv
from app import cdp
from app.config import Config
from app.engines import web_engine as we

PAGE = _testenv.page_url("english_test_page3.html")

# 造一个 ~4000 字的段落：40 句 × ~100 字
MAKE_HUGE_JS = r"""
(() => {
  let old = document.getElementById('huge');
  if (old) old.remove();
  const p = document.createElement('p');
  p.id = 'huge';
  const sentence = "A reader who wants to remember the argument must give each claim enough room to be understood, "
                 + "and must notice where the author changes direction without saying so aloud. ";
  p.textContent = sentence.repeat(30);
  document.body.appendChild(p);
  p.scrollIntoView({block: 'start'});
  return (p.innerText || "").length;
})()
"""

MOUSE_ON_HUGE_JS = r"""
(() => {
  const el = document.getElementById('huge');
  const r = el.getBoundingClientRect();
  const s = window.__tt = window.__tt || {inited: true};
  s.inited = true;
  s.mx = Math.round(r.left + 20);
  s.my = Math.round(Math.max(r.top, 0) + 20);
  return JSON.stringify({top: r.top, bottom: r.bottom, h: r.height, y: s.my});
})()
"""

TEXT_JS = r"""
(() => {
  const el = document.getElementById('huge');
  return el ? (el.innerText || "").replace(/\s+/g, " ").trim() : "<没有>";
})()
"""


class Dummy:
    def __init__(self):
        self.stats = {"hits": 0, "misses": 0, "errors": 0}

    def translate_lines(self, lines):
        return {t: "【译】" + t for t in lines}


def main() -> int:
    port = _testenv.ensure(PAGE)
    pg = cdp.connect(port)
    time.sleep(2.0)
    if "Coverage" not in (pg.eval("document.title") or ""):
        print("页面不对，中止")
        return 2
    pg.eval(we.RESET_JS)

    n = pg.eval(MAKE_HUGE_JS)
    print("造好长段落，原文长度:", n)

    cfg = Config(os.path.join(BASE, "config.json"))
    eng = we.WebEngine(cfg)
    eng._port = port          # 指向测试浏览器，不碰用户的翻译浏览器
    eng._page = pg
    eng._ws_url = None

    original = pg.eval(TEXT_JS)
    print("还原前原文长度:", len(original))

    pg.eval(MOUSE_ON_HUGE_JS)
    print("鼠标位置:", pg.eval(MOUSE_ON_HUGE_JS))
    eng._last_check = time.time()      # 别触发 _maybe_follow，避免它把连接换掉
    items = eng.capture_once()
    print("capture_once 返回条目数:", len(items))
    for it in items:
        print("   块长度 %4d: %s..." % (len(it.text), it.text[:45]))

    tr = Dummy()
    res = tr.translate_lines([it.text for it in items])
    pairs = [(it, res.get(it.text)) for it in items]
    written = eng.apply(pairs)
    print("apply 写入:", written)

    after = pg.eval(TEXT_JS)
    expect = "".join(res.get(it.text) or it.text for it in items)
    print("写回后长度:", len(after), " 期望:", len(expect))
    print("前缀:", after[:60])
    print("内容完整:", after == expect)

    # 校验原文没丢：还原后应与原文一致
    restored = eng.restore()
    back = pg.eval(TEXT_JS)
    print("restore 还原节点:", restored)
    print("还原后与原文一致:", back == original)
    if back != original:
        print("  还原后:", back[:80])
        print("  原文  :", original[:80])

    pg.eval("(()=>{const e=document.getElementById('huge'); if(e) e.remove(); return 1})()")
    pg.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
