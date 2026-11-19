"""免费翻译端点实测：验证默认 provider 不花钱、反应快、能翻长段落。

会真的联网（但不花钱）。用法：python tools\test_free_translate.py
"""
import os
import sys
import time

BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, BASE)

from app.config import Config
from app.translator import Translator

SHORT = [
    "Hover any sentence here and it should turn into Chinese.",
    "The goal is attention, not duration.",
    "Third, stop at the end of each section and say aloud what happened.",
]
LONG = ("Reading slowly is not about moving your eyes at a snail's pace. It is about giving each "
        "sentence enough time to land, so that the meaning assembles itself in your head. When you "
        "rush, you are not reading, you are scanning for keywords, and the shape of the argument "
        "disappears. Take the first paragraph twice: once quickly to see where it goes, once slowly "
        "to notice how it gets there.")


def main() -> int:
    cfg = Config(os.path.join(BASE, "config.json"))
    cfg.set("translator", "cache_seconds", 0, False)      # 强制走真实请求，不吃缓存
    tr = Translator(cfg, BASE)
    print("当前翻译引擎:", tr.provider, "->", tr.provider_label)

    t0 = time.time()
    res = tr.translate_lines(SHORT + [LONG])
    cost = time.time() - t0
    print("总耗时 %.2fs，%d 条" % (cost, len(SHORT) + 1))
    ok = 0
    for src in SHORT + [LONG]:
        dst = res.get(src)
        print("  原文(%3d): %s..." % (len(src), src[:40]))
        print("  译文(%3d): %s" % (len(dst or ""), (dst or "<失败>")[:80]))
        if dst:
            ok += 1
    print("成功 %d/%d" % (ok, len(SHORT) + 1))
    return 0 if ok == len(SHORT) + 1 else 3


if __name__ == "__main__":
    sys.exit(main())
