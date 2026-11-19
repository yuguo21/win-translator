"""验证「点链接换页后翻译还继续工作」。

模拟三种情况：
1. 同一标签跳到新页面（location.href 跳转）
2. 新标签打开新页面（window.open）
3. 切回原标签（确认能跟回去）

用法：python tools\\test_web_follow.py [--keep]
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
from app import cdp, engines, winutil
from app.config import Config
from app.controller import Controller

P1 = "file:///" + os.path.join(BASE, "tools", "english_test_page.html").replace("\\", "/")
P2 = "file:///" + os.path.join(BASE, "tools", "english_test_page2.html").replace("\\", "/")

HOVER_AT_JS = r"""
(() => {
  const els = document.querySelectorAll('h1,h2,p,li');
  for (const el of els) {
    const t = (el.innerText || "").trim();
    if (t.length < 8 || !/[A-Za-z]{3}/.test(t)) continue;
    if (el.getAttribute('data-tt-done') === '1') continue;
    const r = el.getBoundingClientRect();
    if (r.height < 5) continue;
    window.__tt = window.__tt || {inited: false};
    window.__tt.mx = Math.round(r.left + 20);
    window.__tt.my = Math.round(r.top + r.height / 2);
    return t.slice(0, 40);
  }
  return "";
})()
"""


class DummyTranslator:
    stats = {"hits": 0, "misses": 0, "errors": 0}

    def translate_lines(self, lines):
        print("    [翻译]", [x[:24] for x in lines])
        return {t: "【译】" + t[:24] for t in lines}


def find_debug_edge_window(port):
    """测试浏览器窗口（按端口占用进程 pid 定位，不会抓到用户自己的 Edge）。"""
    return _testenv.window_for_port(port)


def main() -> int:
    cfg = Config(os.path.join(BASE, "config.json"))
    port = _testenv.ensure(P1)
    time.sleep(1.5)
    root = tk.Tk()
    root.withdraw()

    class NoOverlay:
        root = None
        def place_over(self, *a): pass
        def draw(self, *a, **k): pass
        def hide(self): pass
        def show(self): pass
        def clear(self): pass

    ctl = Controller(cfg, DummyTranslator(), NoOverlay(),
                     on_log=lambda m: print("  [log]", m),
                     on_status=lambda m: print("  [status]", m))
    eng = engines.create_engine("web", cfg)

    win = find_debug_edge_window(port)
    print("测试浏览器窗口:", (win.title[:50] if win else "没找到（不锁窗口运行）"))
    eng._forced_port = port            # 只连测试浏览器，不动用户正在用的那个
    eng.prepare(win)

    def probe(label):
        time.sleep(0.5)
        if eng._page is None:
            print("  [%s] 页面没连上: %s" % (label, eng.last_error))
            return False
        hovered = eng._page.eval(HOVER_AT_JS, timeout=6)
        print("  [%s] 悬停: %r" % (label, hovered))
        time.sleep(1.8)
        got = eng._page.eval(
            "(() => { const d=document.querySelectorAll('[data-tt-done=\"1\"]');"
            " return d.length + '|' + (d[0] ? (d[0].innerText||'').slice(0,20) : ''); })()",
            timeout=6)
        print("  [%s] 已翻段落: %s" % (label, got))
        return got.split("|")[0] not in ("", "0")

    ctl.start(eng)
    time.sleep(1.0)

    ok1 = probe("第1页")
    print("① 第一页可翻:", ok1)

    # 1) 同一标签跳转
    eng._page.eval("location.href = %s; 1" % json.dumps(P2))
    time.sleep(2.0)
    print("   跳转后窗口标题:", (winutil.get_window_title(win.hwnd) if win else "(无窗口)"))
    ok2 = probe("跳转后第2页")
    print("② 跳转后仍可翻:", ok2)

    # 2) 新标签打开
    eng._page.eval("window.open(%s); 1" % json.dumps(P2))
    time.sleep(2.4)
    print("   新标签后窗口标题:", (winutil.get_window_title(win.hwnd) if win else "(无窗口)"))
    print("   当前连接:", (eng._ws_url or "")[-14:])
    ok3 = probe("新标签第2页")
    print("③ 新标签后仍可翻:", ok3)

    # 3) 切回第一个标签
    eng._page.eval("window.history.back(); 1")
    time.sleep(2.0)
    ok4 = probe("切回后")
    print("④ 切回后仍可翻:", ok4)

    if "--keep" not in sys.argv:
        try:
            print("还原节点:", eng.restore())
        except Exception:
            pass
    ctl.stop()
    print("结果:", {"第一页": ok1, "同标签跳转": ok2, "新标签": ok3, "切回": ok4})
    return 0 if all([ok1, ok2, ok3, ok4]) else 3


if __name__ == "__main__":
    sys.exit(main())
