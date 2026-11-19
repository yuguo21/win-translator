"""验证 UI 模式在浏览器窗口上"读不到 → 自动回退 OCR"的行为（不调用翻译 API）。"""
import os
import sys
import time

BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, BASE)

from app import engines, winutil
from app.config import Config
from app.controller import Controller

BROWSERS = ("msedge.exe", "chrome.exe", "firefox.exe", "brave.exe", "opera.exe")


class DummyTranslator:
    """不请求 API，只返回固定译文，用来验证链路。"""
    stats = {"hits": 0, "misses": 0, "errors": 0}

    def translate_lines(self, lines):
        return {t: "【译文】" + t[:6] for t in lines}


class DummyOverlay:
    class _Root:
        def after(self, ms, fn):
            fn()

    root = _Root()
    last = None

    def place_over(self, rect):
        pass

    def draw(self, pairs, cover=None):
        DummyOverlay.last = pairs

    def hide(self):
        pass


def main() -> int:
    winutil.set_dpi_aware()
    cfg = Config(os.path.join(BASE, "config.json"))
    wins = [w for w in winutil.list_windows() if w.process.lower() in BROWSERS]
    if not wins:
        print("没有浏览器窗口可测")
        return 1
    w = wins[0]
    print("目标窗口:", w.label())

    logs = []
    ctl = Controller(cfg, DummyTranslator(), DummyOverlay(),
                     on_log=lambda m: (logs.append(m), print("  [log]", m)),
                     on_status=lambda m: None)
    ctl.attach_window(w)

    eng = engines.create_engine("uia", cfg)
    eng.prepare(w)
    ctl.start(eng)
    time.sleep(14)
    final_mode = ctl.engine.mode if ctl.engine else "?"
    ctl.stop()
    print("\n结果：当前引擎 =", final_mode)
    print("悬停译文条数 =", len(DummyOverlay.last or []))
    for p in (DummyOverlay.last or [])[:5]:
        print("   ", p[0].text[:50], "->", p[1])
    switched = any("自动切换" in m for m in logs)
    print("是否发生自动回退:", switched)
    print("提示：按用户要求，读不到文本时不再自动切 OCR；模式只能由用户点选。")
    print("      要看降级行为，把 config.json 的 capture.auto_fallback 设成 true。")
    return 0 if not switched else 2


if __name__ == "__main__":
    sys.exit(main())
