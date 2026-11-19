"""复现「长段落 / 嵌套 HTML 段落被覆盖、内容显示不全」。

对每个目标点：设鼠标 -> POLL_JS 取段 -> 用假翻译写回 -> 打印前后 innerText，
并统计字符数掉了多少，判断写回是否把内容吃掉。

只连测试专用浏览器（9333 / browser_profile_test），不碰用户正在用的翻译浏览器。
用法：python tools\probe_cover.py
"""
import json
import os
import sys
import time

BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, BASE)
sys.path.insert(0, os.path.join(BASE, "tools"))

import _testenv
from app import cdp
from app.engines.web_engine import APPLY_JS_TPL, POLL_JS, RESET_JS

PAGE = _testenv.page_url("english_test_page3.html")

TARGET_IDS = ["long-para", "nested-card", "nest-p2", "rich-para"]

POINTS_JS = r"""
(() => {
  const ids = %s;
  const out = [];
  for (const id of ids) {
    const el = document.getElementById(id);
    if (!el) continue;
    const r = el.getBoundingClientRect();
    out.push({id: id, x: Math.round(r.left + 12),
              y: Math.round(r.top + Math.min(12, Math.max(4, r.height / 2)))});
  }
  return JSON.stringify(out);
})()
""" % json.dumps(TARGET_IDS)

DUMP_JS = r"""
(() => {
  const ids = %s;
  const o = {};
  for (const id of ids) {
    const el = document.getElementById(id);
    if (el) o[id] = (el.innerText || "").replace(/\s+/g, " ").trim();
    else o[id] = "<不存在>";
  }
  return JSON.stringify(o);
})()
""" % json.dumps(TARGET_IDS)


def main() -> int:
    port = _testenv.ensure(PAGE)
    pg = cdp.connect(port)
    time.sleep(2.2)
    title = pg.eval("document.title") or ""
    print("测试页:", title)
    if "Coverage" not in title:
        print("页面没载入测试页，中止")
        return 2
    pg.eval(RESET_JS)

    before = json.loads(pg.eval(DUMP_JS))
    for k, v in before.items():
        print("[原文] %-12s %4d 字: %s..." % (k, len(v), v[:60]))

    points = json.loads(pg.eval(POINTS_JS))
    for pt in points:
        print()
        print("========== 悬停 %s (x=%d y=%d) ==========" % (pt["id"], pt["x"], pt["y"]))
        pg.eval("(()=>{const s=window.__tt=window.__tt||{};"
                "s.inited=true;s.mx=%d;s.my=%d;return 1})()" % (pt["x"], pt["y"]))
        row = json.loads(pg.eval(POLL_JS) or "{}")
        if not row:
            print("  这段没被识别为可翻段落")
            continue
        print("  命中 id=%s 文本长度=%d" % (row["id"], len(row["text"])))
        print("  送翻首句: %s..." % row["text"][:50])
        trans = "【译】" + row["text"]          # 模拟"译完且内容等长"
        js = APPLY_JS_TPL.replace("__PAYLOAD__", json.dumps(
            {"id": row["id"], "t": trans}, ensure_ascii=False))
        pg.eval(js)
        after = json.loads(pg.eval(DUMP_JS))
        for k, v in after.items():
            was = before.get(k, "")
            if v == was:
                continue
            delta = len(v) - len(was)
            flag = "   <-- 内容变少" if delta < -5 else ""
            print("  写回后 %-12s %4d -> %4d 字 (%+d)%s" % (k, len(was), len(v), delta, flag))
            print("    现在: %s" % v[:100])
        pg.eval("location.reload(); 1")
        time.sleep(1.6)
        pg.eval(RESET_JS)
    pg.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
