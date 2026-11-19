"""UI 模式注入通道自测：对 Electron/Chromium 窗口读 DOM → 翻译 → 写回 → 还原。

前置：目标程序必须带 --remote-debugging-port 启动。VS Code 示例：
    "D:\\WorkSpace\\Microsoft VS Code\\Code.exe" --remote-debugging-port=9400

用法：python tools\test_ui_inject.py [port] [--real]
  不带 --real 用假译文，只验证读写链路；带 --real 走当前翻译引擎（默认免费）。
"""
import json
import os
import sys
import tempfile

BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, BASE)

from app.config import Config
from app.engines.inject import InjectChannel
from app.engines.base import TextItem


class FakeWindow:
    def __init__(self, title):
        self.title = title
        self.hwnd = 0


def main() -> int:
    args = [a for a in sys.argv[1:]]
    real = "--real" in args
    port = None
    for a in args:
        if a.isdigit():
            port = int(a)
    cfg = Config(os.path.join(BASE, "config.json"))
    if port:
        cfg.set("capture", "inject_ports", [port], False)
    # 这个脚本测的是"整窗扫描"，不是悬停
    cfg.set("capture", "ui_hover", False, False)

    # 用调试端口上的目标标题当"窗口标题"，模拟真实锁定场景
    from app import cdp
    ports = cfg.get("capture", "inject_ports", [9400])
    target_title = ""
    for p in ports:
        if not cdp.find_port((p,)):
            continue
        pages = cdp.list_pages(p)
        for pg in pages:
            if pg.get("type") == "page" and not (pg.get("url") or "").startswith("devtools://"):
                target_title = pg.get("title") or ""
                port = p
                break
        if target_title:
            break
    if not target_title:
        print("没找到开着调试端口的目标程序。请先用 --remote-debugging-port 启动 VS Code 等程序。")
        return 2

    print("端口:", port, "| 目标标题:", target_title[:60])
    ch = InjectChannel(cfg)
    if not ch.attach(FakeWindow(target_title)):
        print("attach 失败:", ch.last_error)
        return 3
    print("已连接 ->", "port", ch.port)

    blocks = ch.read()
    print("\n读到文本块:", len(blocks))
    for b in blocks[:10]:
        print("  [%s] %r" % (b["id"][:12], b["text"][:56]))

    if not blocks:
        print("没有可翻内容")
        ch.close()
        return 4

    sample = []
    seen = set()
    for b in blocks:
        if b["text"] in seen:
            continue
        seen.add(b["text"])
        sample.append(b)
        if len(sample) >= 6:
            break
    if real:
        from app.translator import Translator
        # 用临时 work_dir：测试产生的译文不写进真实 tm.json / cache.sqlite3
        tr = Translator(cfg, os.path.join(tempfile.gettempdir(), "tt_tr_inject"))
        print("\n翻译引擎:", tr.provider_label)
        res = tr.translate_lines([b["text"] for b in sample])
        pairs = [(TextItem(0, 0, 0, 0, b["text"], 1.0), res.get(b["text"])) for b in sample]
    else:
        pairs = [(TextItem(0, 0, 0, 0, b["text"], 1.0), "【译】" + b["text"][:8]) for b in sample]

    n = ch.apply(pairs)
    print("\n写回成功:", n)

    # 校验：被翻的元素里是否出现替换块、原文是否还能取回
    verify_js = """
    (() => {
      const done = document.querySelectorAll('[data-ti-done="1"]');
      const repl = document.querySelectorAll('[data-ti-repl="1"]');
      const out = [];
      for (const r of Array.from(repl).slice(0, 4)) {
        out.push({parent: (r.parentElement.innerText || '').trim().slice(0, 40)});
      }
      return JSON.stringify({done: done.length, repl: repl.length, samples: out});
    })()
    """
    raw = ch._page.eval(verify_js, timeout=8)
    v = raw if isinstance(raw, dict) else json.loads(raw)
    print("校验:", json.dumps(v, ensure_ascii=False))

    if "--keep" in args:
        print("\n--keep：保持译文不还原（想看图用这个）")
        ch.close()
        return 0 if v.get("repl", 0) > 0 else 5

    n = ch.restore()
    print("\n还原节点:", n)
    raw = ch._page.eval(verify_js, timeout=8)
    v2 = raw if isinstance(raw, dict) else json.loads(raw)
    print("还原后:", json.dumps(v2, ensure_ascii=False))

    ok = v.get("repl", 0) > 0 and v2.get("repl", 0) == 0 and n > 0
    print("\n结论:", "通过" if ok else "有问题")
    ch.close()
    return 0 if ok else 5


if __name__ == "__main__":
    sys.exit(main())
