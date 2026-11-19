"""词库（tm.json）自测：查表命中、长短分层、网页来源不写 json、落盘、重启命中、损坏备份、瘦身。

用法：python tools\\test_tm.py      （不联网：网络调用被替换成固定译文）
"""
import json
import os
import shutil
import sys
import tempfile

BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, BASE)

from app.config import Config
from app.translator import Translator

SHORT = "Back"                       # 4 字 → 该进词库
SHORT2 = "Go to File..."             # 13 字 → 该进词库
LONG = ("Reading slowly is not about moving your eyes at a snail's pace, it is about giving "
        "each sentence enough time to land.")   # 长句 → 只进缓存


def main() -> int:
    tmp = os.path.join(tempfile.gettempdir(), "tt_tm_test")
    if os.path.isdir(tmp):
        shutil.rmtree(tmp, ignore_errors=True)
    os.makedirs(tmp)
    shutil.copy2(os.path.join(BASE, "config.json"), os.path.join(tmp, "config.json"))
    tm_path = os.path.join(tmp, "tm.json")
    ok = True

    # ---------- 1. 预置词库，验证命中（不联网） ----------
    with open(tm_path, "w", encoding="utf-8") as f:
        json.dump({"Hello World": "你好世界"}, f, ensure_ascii=False)
    cfg = Config(os.path.join(tmp, "config.json"))
    tr = Translator(cfg, tmp)
    tr._free_one = lambda text: "【译】" + text      # 断网也能跑
    got = tr.translate_lines(["Hello World", "hello   world"])
    hit1 = got.get("Hello World") == "你好世界" and got.get("hello   world") == "你好世界"
    print("[1] 词库命中（大小写/空格不敏感）: %s（tm_hits=%d）" % (hit1, tr.stats["tm_hits"]))
    ok = ok and hit1 and tr.stats["tm_hits"] == 2

    # ---------- 2. 界面来源：短词条进 json，长句只进缓存 ----------
    tr.translate_lines([SHORT, SHORT2, LONG], source="uia")
    in_json = dict(tr.tm.items())
    short_ok = in_json.get(SHORT) == "【译】Back" and in_json.get(SHORT2) == "【译】Go to File..."
    long_in_json = any(LONG[:20] in k for k in in_json)
    long_in_cache = tr._db_get(LONG) is not None
    print("[2] 短词条进 json: %s | 长句进 json: %s（应为 False）| 长句在 sqlite 缓存: %s"
          % (short_ok, long_in_json, long_in_cache))
    ok = ok and short_ok and (not long_in_json) and long_in_cache

    # ---------- 3. 网页来源：再短也不写 json ----------
    web_text = "News Title"
    tr.translate_lines([web_text], source="web")
    web_in_json = web_text in tr.tm.items()
    web_in_cache = tr._db_get(web_text) is not None
    print("[3] 网页来源写 json: %s（应为 False）| 在 sqlite 缓存: %s" % (web_in_json, web_in_cache))
    ok = ok and (not web_in_json) and web_in_cache

    # ---------- 4. 落盘 + 重启命中 ----------
    tr.tm.flush()
    saved = json.load(open(tm_path, encoding="utf-8"))
    tr2 = Translator(cfg, tmp)
    tr2._free_one = lambda text: "【译】" + text
    got2 = tr2.translate_lines(["BACK", SHORT2], source="uia")
    restart_ok = got2.get("BACK") == "【译】Back" and got2.get(SHORT2) == "【译】Go to File..."
    print("[4] 落盘 %d 条；重启后命中: %s（%r）" % (len(saved), restart_ok, got2.get("BACK")))
    ok = ok and restart_ok

    # ---------- 5. 瘦身：历史长句挪进 sqlite，json 只留短条 ----------
    with open(tm_path, "w", encoding="utf-8") as f:
        json.dump({"Back": "后退", LONG: "慢读不是让眼睛慢慢移动。"}, f, ensure_ascii=False)
    tr3 = Translator(cfg, tmp)
    after = tr3.tm.items()
    pruned_ok = (LONG not in after) and ("Back" in after) and tr3._db_get(LONG)
    print("[5] 瘦身：迁走 %d 条；json 剩 %d 条；长句仍在缓存: %s"
          % (tr3.pruned, len(after), bool(tr3._db_get(LONG))))
    ok = ok and pruned_ok

    # ---------- 6. 文件损坏不崩 + 留备份 ----------
    with open(tm_path, "w", encoding="utf-8") as f:
        f.write("{坏掉的 json")
    tr4 = Translator(cfg, tmp)
    bad_ok = len(tr4.tm) == 0
    backups = [x for x in os.listdir(tmp) if x.startswith("tm.json.bad-")]
    print("[6] 损坏文件：不崩溃=%s，备份=%s" % (bad_ok, backups))
    ok = ok and bad_ok and bool(backups)

    shutil.rmtree(tmp, ignore_errors=True)
    print("\n结论: " + ("通过" if ok else "不符合预期"))
    return 0 if ok else 6


if __name__ == "__main__":
    sys.exit(main())
