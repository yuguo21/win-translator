"""对英雄无敌5（或任意指定进程名）实测内存扫描，看看能扫出什么文本。

用法：python tools\\diag_mem_h5.py            # 默认找 h5_game.exe
      python tools\\diag_mem_h5.py notepad    # 换进程名关键字
"""
import os
import sys
import time

BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, BASE)

from app import winutil
from app.config import Config
from app.engines.memory_engine import MemoryEngine

KEY = sys.argv[1] if len(sys.argv) > 1 else "h5_game"


def main() -> int:
    winutil.set_dpi_aware()
    wins = winutil.list_windows()
    cands = [w for w in wins if KEY.lower() in (w.process or "").lower()]
    if not cands:
        cands = [w for w in wins if KEY.lower() in (w.title or "").lower()]
    if not cands:
        print("没找到 %s 的窗口（程序没开？）" % KEY)
        return 2
    if len(cands) > 1:
        print("有 %d 个候选窗口：" % len(cands))
        for w in cands:
            print("   - %s" % w.label())
    win = cands[0]
    print("选中: %s hwnd=%s pid=%s 最小化=%s"
          % (win.label(), win.hwnd, win.pid, win.minimized))

    cfg = Config(os.path.join(BASE, "config.json"))
    cfg.set("capture", "max_new_per_round", 30, False)
    eng = MemoryEngine(cfg)
    eng.prepare(win)
    print("系统模块区间 %d 个" % len(eng._sys_ranges))

    seen = []
    t0 = time.time()
    rounds = 0
    while time.time() - t0 < 25:
        items = eng.capture_once()
        rounds += 1
        seen.extend(it.text for it in items)
        if rounds <= 6:
            print("  第%d轮 %s" % (rounds, eng.last_stats))
        if len(seen) >= 60:
            break

    print("\n共 %d 轮，%d 条候选（前 40 条）：" % (rounds, len(seen)))
    for i, t in enumerate(seen[:40]):
        print("  %2d. %s" % (i + 1, t[:110]))
    eng.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
