"""用真实翻译接口，在测试页 3（长段落 / 容器 / 带链接）上翻一遍，并拍干净成品图。

只连测试专用浏览器（9333 / browser_profile_test）。
用法：python tools\shot_web_cover.py
产物：tools/web_cover_clean.png
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
from app import winutil
from app.config import Config
from app.controller import Controller
from app import engines
from app.translator import Translator
from PIL import Image

OUT = os.path.join(BASE, "tools", "web_cover_clean.png")
PAGE = _testenv.page_url("english_test_page3.html")

TARGETS = ["long-para", "nested-card", "rich-para"]

POINT_JS = r"""
(() => {
  const el = document.getElementById(%s);
  if (!el) return "null";
  const r = el.getBoundingClientRect();
  const s = window.__tt = window.__tt || {};
  s.inited = true;
  s.mx = Math.round(r.left + 14);
  s.my = Math.round(Math.max(r.top, 0) + Math.min(14, Math.max(5, r.height / 2)));
  return JSON.stringify({x: s.mx, y: s.my, h: Math.round(r.height)});
})()
"""

TEXT_JS = r"""
(() => {
  const o = {};
  for (const id of ["long-para", "nested-card", "rich-para"]) {
    const el = document.getElementById(id);
    o[id] = el ? (el.innerText || "").replace(/\s+/g, " ").trim() : "";
  }
  return JSON.stringify(o);
})()
"""


class NoOverlay:
    root = None
    def place_over(self, *a): pass
    def draw(self, *a, **k): pass
    def hide(self): pass
    def show(self): pass
    def clear(self): pass


def main() -> int:
    cfg = Config(os.path.join(BASE, "config.json"))
    port = _testenv.ensure(PAGE)
    time.sleep(2.0)

    root = tk.Tk()
    root.withdraw()
    ctl = Controller(cfg, Translator(cfg, BASE), NoOverlay(),
                     on_log=lambda m: print("  [log]", m),
                     on_status=lambda m: print("  [status]", m))
    ctl.window = None
    eng = engines.create_engine("web", cfg)
    eng._forced_port = port
    eng.prepare(None)
    if eng._page is None:
        print("连接失败:", eng.last_error)
        return 2
    ctl.start(eng)
    time.sleep(1.0)

    before = json.loads(eng._page.eval(TEXT_JS))
    for t in TARGETS:
        pt = eng._page.eval(POINT_JS % json.dumps(t))
        if pt == "null":
            print("  跳过（元素不存在）:", t)
            continue
        info = json.loads(pt)
        print("  悬停 %s (y=%d, 高 %d)" % (t, info["y"], info["h"]))
        time.sleep(4.0)          # 等真实翻译返回
        if t == "long-para":
            time.sleep(5.0)      # 长段落要分块，多等一会

    after = json.loads(eng._page.eval(TEXT_JS))
    for k in TARGETS:
        was, now = before.get(k, ""), after.get(k, "")
        print("  %-12s 原文 %4d 字 -> 译文 %4d 字" % (k, len(was), len(now)))
        print("     %s%s" % (now[:70], "..." if len(now) > 70 else ""))

    win = _testenv.window_for_port(port)
    if win is None:
        print("没找到测试浏览器窗口，跳过截图")
        return 0
    winutil.set_topmost(win.hwnd, True)
    time.sleep(0.8)
    data = winutil.capture_client_rgba(win.hwnd)
    if not data:
        print("抓图失败")
        return 3
    w, h, raw = data
    img = Image.frombuffer("RGB", (w, h), raw, "raw", "BGRX", 0, 1)
    img.save(OUT)
    print("截图:", OUT, img.size)

    print("保留译文，不还原（方便看效果）")
    ctl.stop()
    return 0


if __name__ == "__main__":
    sys.exit(main())
