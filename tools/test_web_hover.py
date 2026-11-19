"""网页悬停翻译自测：模拟鼠标停在每个段落上，逐段翻译并写回，最后截图。

默认用假翻译器（不花钱）验证链路；加 --real 走真实翻译接口。
只用测试专用浏览器（9333 / browser_profile_test），不碰用户正在用的翻译浏览器。
用法：python tools\test_web_hover.py [--real] [--keep]
产物：tools/web_hover_check.png
"""
import json
import os
import sys
import time
import tkinter as tk

BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, BASE)
sys.path.insert(0, os.path.join(BASE, "tools"))

import _testenv
from app import cdp, engines
from app.config import Config
from app.controller import Controller
from app.translator import Translator

SHOT = os.path.join(BASE, "tools", "web_hover_check.png")

GET_RECTS_JS = r"""
(() => {
  const out = [];
  const sel = "h1,h2,h3,p,li";
  const vh = window.innerHeight, vw = window.innerWidth;
  const els = document.querySelectorAll(sel);
  for (const el of els) {
    const t = (el.innerText || "").trim();
    if (t.length < 4 || !/[A-Za-z]{2}/.test(t)) continue;
    if (el.getAttribute('data-tt-done') === '1') continue;
    const r = el.getBoundingClientRect();
    if (r.width < 5 || r.height < 5) continue;
    // 只取真正在可视区里的段落：视口外的坐标点 elementFromPoint 拿不到元素
    if (r.top < 0 || r.bottom > vh) continue;
    const x = Math.round(Math.min(Math.max(r.left + 10, 0), vw - 5));
    const y = Math.round(r.top + r.height / 2);
    out.push({x: x, y: y, t: t.slice(0, 40)});
  }
  return JSON.stringify(out);
})()
"""

SET_MOUSE_JS = r"""
(() => {
  window.__tt = window.__tt || {inited:false};
  window.__tt.mx = __X__; window.__tt.my = __Y__;
  return 1;
})()
"""


class DummyTranslator:
    stats = {"hits": 0, "misses": 0, "errors": 0}

    def translate_lines(self, lines):
        return {t: "【译】" + t[:26] for t in lines}


def main() -> int:
    real = "--real" in sys.argv
    cfg = Config(os.path.join(BASE, "config.json"))
    page_url = _testenv.page_url("english_test_page.html")
    port = _testenv.ensure(page_url)
    print("测试浏览器端口:", port)
    time.sleep(1.5)
    translator = Translator(cfg, BASE) if real else DummyTranslator()

    root = tk.Tk()
    root.withdraw()

    class NoOverlay:
        root = None
        def place_over(self, *a): pass
        def draw(self, *a, **k): pass
        def hide(self): pass
        def show(self): pass
        def clear(self): pass

    ctl = Controller(cfg, translator, NoOverlay(),
                     on_log=lambda m: print("  [log]", m),
                     on_status=lambda m: print("  [status]", m))
    ctl.window = None
    eng = engines.create_engine("web", cfg)
    eng._forced_port = port           # 只连测试浏览器
    eng.prepare(None)
    if eng._page is None:
        print("连接失败:", eng.last_error)
        return 2
    ctl.start(eng)
    time.sleep(1.0)

    rects = json.loads(eng._page.eval(GET_RECTS_JS, timeout=8) or "[]")
    print(f"可翻译段落 {len(rects)} 个")
    done = 0
    for round_no in range(1, 25):
        # 每轮重新取坐标：前面的段落被翻译后高度会变，旧坐标会漂
        left = json.loads(eng._page.eval(GET_RECTS_JS, timeout=8) or "[]")
        if not left:
            break
        r = left[0]
        js = SET_MOUSE_JS.replace("__X__", str(r["x"])).replace("__Y__", str(r["y"]))
        eng._page.eval(js, timeout=5)
        time.sleep(1.8)          # 等控制器轮询 + 翻译 + 写回
        done += 1
        print(f"  {done}: 指向 y={r['y']} -> {r['t'][:30]}")
    time.sleep(0.8)

    from PIL import ImageGrab
    ImageGrab.grab().save(SHOT)
    print("截图:", SHOT)
    print("统计:", translator.stats)

    if "--keep" in sys.argv:
        print("保留译文（不还原），方便截图")
    else:
        restored = eng.restore()
        print("还原节点:", restored)
    ctl.stop()
    return 0


if __name__ == "__main__":
    sys.exit(main())
