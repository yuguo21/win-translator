"""内存模式"只写日志、不贴图"的端到端验证。

起一个带英文句子的子进程当目标窗口，用假的翻译器（不联网），跑真实 controller 几秒，
检查：日志里出现 "[译] 原文 → 译文"，并且 overlay.draw 一次都没被调用。
"""
import os
import subprocess
import sys
import tempfile
import time

BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, BASE)

from app import winutil
from app.config import Config
from app.controller import Controller
from app.engines.memory_engine import MemoryEngine

CHILD = r'''
import tkinter as tk
root = tk.Tk()
root.title("MEMLOG")
root.geometry("380x200+120+120")
for t in ["Open the eastern gate before the sun rises",
          "Recruit seven archers from the tavern",
          "The castle walls will hold against the siege"]:
    tk.Label(root, text=t, wraplength=360, justify="left").pack(anchor="w")
root.after(30000, root.destroy)
root.mainloop()
'''


class FakeOverlay:
    def __init__(self):
        self.drawn = 0
        self.shown = 0

    def draw(self, pairs, cover=False):
        self.drawn += 1

    def show(self):
        self.shown += 1

    def hide(self):
        pass

    def place_over(self, rect):
        pass

    def clear(self):
        pass


class FakeTranslator:
    def translate_lines(self, lines, source="ui"):
        return {t: "【中】" + t[:10] for t in lines}


def main() -> int:
    winutil.set_dpi_aware()
    py = sys.executable
    tmp = os.path.join(tempfile.gettempdir(), "tt_memlog_child.py")
    with open(tmp, "w", encoding="utf-8") as f:
        f.write(CHILD)
    proc = subprocess.Popen([py, "-u", tmp])
    try:
        win = None
        for _ in range(40):
            for w in winutil.list_windows():
                if w.title == "MEMLOG" and w.pid == proc.pid:
                    win = w
                    break
            if win:
                break
            time.sleep(0.5)
        if not win:
            print("没等到目标窗口")
            return 2

        cfg = Config(os.path.join(BASE, "config.json"))
        cfg.set("capture", "interval_ms", 500, False)
        eng = MemoryEngine(cfg)
        eng.prepare(win)

        logs = []
        overlay = FakeOverlay()
        ctrl = Controller(cfg, FakeTranslator(), overlay,
                          on_log=lambda m: logs.append(m), on_status=lambda m: None)
        ctrl.attach_window(win)
        ctrl.start(eng)
        time.sleep(12)
        ctrl.stop()

        trans = [m for m in logs if m.startswith("[译]")]
        print("日志行数 %d，其中译文 %d 条，overlay.draw 调用 %d 次"
              % (len(logs), len(trans), overlay.drawn))
        for m in trans[:6]:
            print("  " + m)
        ok = bool(trans) and overlay.drawn == 0
        print("\n结论: " + ("通过（译文只进日志，没有贴图）" if ok else "不符合预期"))
        return 0 if ok else 6
    finally:
        try:
            proc.terminate()
            proc.wait(timeout=5)
        except Exception:
            pass
        try:
            os.remove(tmp)
        except Exception:
            pass


if __name__ == "__main__":
    sys.exit(main())
