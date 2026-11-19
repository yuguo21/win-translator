"""内存扫描自测：起一个子进程（窗口里带英文句子），按窗口扫描它的内存，看能不能找到。

用法：python tools\\test_memory_scan.py
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
from app.engines.memory_engine import MemoryEngine

CHILD = r'''
import tkinter as tk

TARGETS = [
    "The quick brown fox jumps over the lazy dog and never looks back",
    "Open the eastern gate before the sun rises",
    "Recruit seven archers and two paladins from the tavern",
]
root = tk.Tk()
root.title("MEMTEST")
root.geometry("360x220+120+120")
for t in TARGETS:
    tk.Label(root, text=t, wraplength=340, justify="left").pack(anchor="w", pady=4)
tk.Label(root, text="(this window is a memory-scan test target)").pack(anchor="w")
root.after(30000, root.destroy)
root.mainloop()
'''


def main() -> int:
    winutil.set_dpi_aware()
    py = sys.executable
    tmp = os.path.join(tempfile.gettempdir(), "tt_memtest_child.py")
    with open(tmp, "w", encoding="utf-8") as f:
        f.write(CHILD)
    proc = subprocess.Popen([py, "-u", tmp])
    try:
        # 等窗口出现
        win = None
        for _ in range(40):
            for w in winutil.list_windows():
                if w.title == "MEMTEST" and w.pid == proc.pid:
                    win = w
                    break
            if win:
                break
            time.sleep(0.5)
        if not win:
            print("没等到子进程的窗口")
            return 2
        print("目标窗口: %s hwnd=%s pid=%s 最小化=%s"
              % (win.label(), win.hwnd, win.pid, win.minimized))

        cfg = Config(os.path.join(BASE, "config.json"))
        cfg.set("capture", "max_new_per_round", 40, False)
        eng = MemoryEngine(cfg)
        eng.prepare(win)
        print("已附加进程 pid=%s" % eng._pid)
        print("系统模块区间: %d 个" % len(eng._sys_ranges))

        targets = [
            "jumps over the lazy dog",
            "Open the eastern gate",
            "Recruit seven archers",
        ]
        found_all = set()
        t0 = time.time()
        rounds = 0
        while time.time() - t0 < 40:
            items = eng.capture_once()
            rounds += 1
            st = eng.last_stats or {}
            if rounds <= 8 or found_all:
                print("  第%d轮: %s" % (rounds, st))
            for it in items:
                for t in targets:
                    if t.lower() in it.text.lower():
                        found_all.add(t)
            # 引擎内部去重集合里有没有（可能被"每轮输出条数"挡在外面）
            if len(eng._known) > 0 and len(found_all) < len(targets):
                joined = "\n".join(eng._known).lower()
                for t in targets:
                    if t.lower() in joined:
                        found_all.add(t)
            if len(found_all) == len(targets):
                break
            time.sleep(0.4)

        print("扫了 %d 轮，耗时 %.1fs" % (rounds, time.time() - t0))
        for t in targets:
            print("  %-28s %s" % (t, "找到" if t in found_all else "没找到"))
        # 抽查一条输出
        items = eng.capture_once()
        if items:
            print("示例输出: %r（坐标 %d,%d）" % (items[0].text[:50], items[0].x, items[0].y))
        eng.close()

        ok = len(found_all) == len(targets)
        print("\n结论: " + ("通过（按选定窗口的进程扫到了英文原文）" if ok else "不符合预期"))
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
