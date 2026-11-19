"""网页翻译模式端到端：读页面 → 调翻译 → 写回网页 → 截图。

默认使用真实翻译接口（会消耗少量额度，几行短句）。
加 --dummy 参数则用假翻译器，只验证注入链路、不花钱。

用法：python tools\test_web_inject.py [--dummy]
产物：tools/web_inject_check.png
"""
import os
import sys
import tkinter as tk

BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, BASE)

from app import cdp, engines
from app.config import Config
from app.controller import Controller
from app.translator import Translator

SHOT = os.path.join(BASE, "tools", "web_inject_check.png")
LOG = []


class DummyTranslator:
    stats = {"hits": 0, "misses": 0, "errors": 0}

    def translate_lines(self, lines):
        return {t: f"【译】{t[:30]}" for t in lines}


def main() -> int:
    dummy = "--dummy" in sys.argv
    cfg = Config(os.path.join(BASE, "config.json"))
    port = cdp.find_port()
    print("调试端口:", port)
    if not port:
        print("没有调试端口，先运行 uia 里的启动逻辑或手动起浏览器")
        return 1
    pages = cdp.list_pages(port)
    print("页面:", [p.get("title", "")[:40] for p in pages])
    if not pages:
        return 1

    translator = DummyTranslator() if dummy else Translator(cfg, BASE)
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
                     on_log=lambda m: (LOG.append(m), print("  [log]", m)),
                     on_status=lambda m: (LOG.append(m), print("  [status]", m)))
    ctl.window = None
    eng = engines.create_engine("web", cfg)
    eng.prepare(None)
    if eng._page is None:
        print("连接失败:", eng.last_error)
        return 2
    ctl.start(eng)

    def shot():
        try:
            import time
            time.sleep(0.1)
            from PIL import ImageGrab
            img = ImageGrab.grab()
            img.save(SHOT)
            print("截图:", SHOT)
        except Exception as e:
            print("截图失败:", e)
        root.quit()

    root.after(12000, shot)
    root.mainloop()
    ctl.stop()
    print("统计:", translator.stats)
    return 0


if __name__ == "__main__":
    sys.exit(main())
