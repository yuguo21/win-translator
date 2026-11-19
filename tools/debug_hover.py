"""调试：单段反复悬停，观察控制器每轮拿到什么、翻译返回什么、写回几条。"""
import os
import sys
import time
import tkinter as tk

BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, BASE)

from app import cdp, engines
from app.config import Config
from app.controller import Controller


class DummyTranslator:
    stats = {"hits": 0, "misses": 0, "errors": 0}

    def translate_lines(self, lines):
        print("    [翻译输入]", [x[:24] for x in lines])
        return {t: "【译】" + t[:26] for t in lines}


def main() -> int:
    cfg = Config(os.path.join(BASE, "config.json"))
    port = cdp.find_port()
    if not port:
        print("无端口")
        return 1
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
    eng.prepare(None)

    orig_cap = eng.capture_once

    def cap():
        r = orig_cap()
        print("    [capture]", [(x.text[:22]) for x in r], "err=", eng.last_error)
        return r

    orig_apply = eng.apply

    def app(pairs):
        print("    [apply 入参]", [(x.text[:18], t[:18]) for x, t in pairs])
        n = orig_apply(pairs)
        print("    [apply 返回]", n)
        return n

    eng.capture_once = cap
    eng.apply = app

    ctl.start(eng)
    time.sleep(0.6)
    eng._page.eval("window.__tt.mx=300; window.__tt.my=122; 1")
    for i in range(10):
        time.sleep(1.0)
        print(f"-- t={i + 1}s")
    ctl.stop()
    return 0


if __name__ == "__main__":
    sys.exit(main())
