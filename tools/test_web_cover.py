"""网页写回完整性测试：长段落 / 容器包段落 / 带链接的富格式段落。

检查三件事：
1. 写回后整段的**内容长度**和原文对得上（不会只剩半截，也不会把别的段落吞掉）；
2. 段落里的 <a>/<strong>/<em> 元素仍然存在（只是被隐藏），文字没被清掉；
3. 还原之后与原文一字不差，元素的 display 也恢复。

只连测试专用浏览器（9333 / browser_profile_test），不碰用户正在用的翻译浏览器。
用法：python tools\test_web_cover.py
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
from app.config import Config
from app.engines import web_engine as we

PAGE = _testenv.page_url("english_test_page3.html")

IDS = ["long-para", "nested-card", "nest-p2", "rich-para"]

DUMP_JS = r"""
(() => {
  const ids = %s;
  const o = {};
  for (const id of ids) {
    const el = document.getElementById(id);
    o[id] = el ? (el.innerText || "").replace(/\s+/g, " ").trim() : "<不存在>";
  }
  return JSON.stringify(o);
})()
""" % json.dumps(IDS)

# 段内内联元素的文字是否还在（隐藏也算在），以及 display 状态
LINKS_JS = r"""
(() => {
  const el = document.getElementById('rich-para');
  if (!el) return "[]";
  const out = [];
  for (const k of el.querySelectorAll('a,strong,em')) {
    out.push({tag: k.tagName, txt: (k.textContent || "").trim().slice(0, 30),
              disp: k.style.display});
  }
  return JSON.stringify(out);
})()
"""


def point_for(pg, elem_id):
    js = r"""
    (() => {
      const el = document.getElementById(%s);
      if (!el) return "null";
      const r = el.getBoundingClientRect();
      const s = window.__tt = window.__tt || {};
      s.inited = true;
      s.mx = Math.round(r.left + 12);
      s.my = Math.round(Math.max(r.top, 0) + Math.min(12, Math.max(4, r.height / 2)));
      return JSON.stringify({x: s.mx, y: s.my, top: r.top, bottom: r.bottom});
    })()
    """ % json.dumps(elem_id)
    return pg.eval(js)


class Dummy:
    """等长假翻译：便于用长度判断内容是否完整。"""
    stats = {"hits": 0, "misses": 0, "errors": 0}

    def translate_lines(self, lines):
        return {t: "【译】" + t for t in lines}


def main() -> int:
    port = _testenv.ensure(PAGE)
    pg = cdp.connect(port)
    time.sleep(2.0)
    if "Coverage" not in (pg.eval("document.title") or ""):
        print("页面不是测试页，中止")
        return 2
    pg.eval(we.RESET_JS)

    cfg = Config(os.path.join(BASE, "config.json"))
    eng = we.WebEngine(cfg)
    eng._port = port
    eng._page = pg

    original = json.loads(pg.eval(DUMP_JS))
    print("原文长度:", {k: len(v) for k, v in original.items()})
    links_before = json.loads(pg.eval(LINKS_JS))
    print("富格式段落里的内联元素:", links_before)

    dummy = Dummy()
    ok_all = True
    for elem_id in IDS:
        print()
        print("=== %s ===" % elem_id)
        pt = point_for(pg, elem_id)
        if pt == "null":
            print("  元素不存在")
            ok_all = False
            continue
        eng._last_check = time.time()          # 跳过 _maybe_follow，别换连接
        eng._last_key = None
        items = eng.capture_once()
        if not items:
            print("  没识别出可翻段落:", eng.last_error)
            ok_all = False
            continue
        print("  送翻 %d 块，共 %d 字" % (len(items), sum(len(i.text) for i in items)))
        res = dummy.translate_lines([i.text for i in items])
        pairs = [(i, res.get(i.text)) for i in items]
        written = eng.apply(pairs)
        print("  写入:", written)

        after = json.loads(pg.eval(DUMP_JS))
        expect = ""
        for i in items:
            expect += res.get(i.text) or i.text
        # 该段所在的区域（可能是一个容器）长度要"够"，不能只剩半截
        for k in IDS:
            was, now = original[k], after[k]
            if was == now and k != elem_id:
                continue
            delta = len(now) - len(was)
            print("  %-12s %4d -> %4d (%+d)" % (k, len(was), len(now), delta))
            if delta < -5:
                print("    [失败] 内容变少")
                ok_all = False
        if elem_id == "rich-para":
            links = json.loads(pg.eval(LINKS_JS))
            print("  内联元素:", links)
            if not links:
                print("    [失败] 链接/加粗元素被删掉了")
                ok_all = False
            # 链接文字必须还在译文里
            for k in links_before:
                if k["txt"] and k["tag"] == "A" and k["txt"] not in after.get("rich-para", ""):
                    print("    [失败] 链接文字没并入译文:", k["txt"])
                    ok_all = False

        eng.restore()
        back = json.loads(pg.eval(DUMP_JS))
        for k in IDS:
            if back[k] != original[k]:
                print("    [失败] %s 还原不一致" % k)
                print("      现在:", back[k][:70])
                print("      原文:", original[k][:70])
                ok_all = False
        links_after = json.loads(pg.eval(LINKS_JS))
        if links_after != links_before:
            print("    [失败] 内联元素还原不一致:", links_after)
            ok_all = False

    print()
    print("结论:", "全部通过" if ok_all else "有失败项，见上面 [失败]")
    pg.close()
    return 0 if ok_all else 3


if __name__ == "__main__":
    sys.exit(main())
