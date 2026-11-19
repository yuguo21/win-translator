"""翻译分流的离线单测（不需要网络/额度）：验证长段落走"整段单发"、短句走批量。

做法：把 Translator._call_api 换成一个假的，记录它被怎么调用、返回什么，
然后检查 translate_lines 是否按预期分流、拼接、缓存。

用法：python tools\test_translator_split.py
"""
import os
import sys

BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, BASE)

from app.config import Config
from app.translator import LONG_LINE, Translator

LONG = (("A reader who wants to remember the argument must give each claim enough room "
         "to be understood, and must notice where the author changes direction. ") * 3).strip()
SHORT1 = "Hover any sentence here and it should turn into Chinese."
SHORT2 = "The goal is attention, not duration."


def main() -> int:
    cfg = Config(os.path.join(BASE, "config.json"))
    cfg.set("translator", "cache_seconds", 0, False)      # 不吃缓存，纯测分流
    tr = Translator(cfg, BASE)

    calls = []

    def fake_call(lines, single=False):
        calls.append({"lines": list(lines), "single": single})
        if single:
            return ["【整段】" + lines[0][:5] + "…（%d 字）" % len(lines[0])]
        return ["【批量】" + x[:5] for x in lines]

    tr._call_api = fake_call
    res = tr.translate_lines([LONG, SHORT1, SHORT2])

    print("调用次数:", len(calls))
    for c in calls:
        print("  single=%s 行数=%d" % (c["single"], len(c["lines"])))

    ok = True
    long_calls = [c for c in calls if c["single"]]
    short_calls = [c for c in calls if not c["single"]]
    if len(long_calls) != 1 or long_calls[0]["lines"] != [LONG]:
        print("[失败] 长段落没有单独整段发送")
        ok = False
    if len(short_calls) != 1 or len(short_calls[0]["lines"]) != 2:
        print("[失败] 短句没有合并成一批")
        ok = False
    if len(LONG) <= LONG_LINE:
        print("[失败] 测试样本不够长（LONG_LINE=%d）" % LONG_LINE)
        ok = False
    for key, want in ((LONG, "【整段】"), (SHORT1, "【批量】"), (SHORT2, "【批量】")):
        got = res.get(key) or ""
        if not got.startswith(want):
            print("[失败] %s 的结果不对: %r" % (key[:20], got))
            ok = False
    print("结果:", "通过" if ok else "有失败项")
    return 0 if ok else 3


if __name__ == "__main__":
    sys.exit(main())
