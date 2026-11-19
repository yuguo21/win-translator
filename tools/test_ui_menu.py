"""验证：VS Code 下拉菜单里的项能不能被翻译并写回（含截图）。

流程：CDP 点开菜单栏 "Go" → 注入通道读块 → 免费翻译 → 写回 → 校验 → 截图。

用法：python tools/test_ui_menu.py [port] [--keep]
"""
import json
import os
import subprocess
import sys
import tempfile
import time

BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, BASE)

from app import cdp
from app.config import Config
from app.engines.base import TextItem
from app.engines.inject import InjectChannel

FIND_MENU_JS = r"""
(() => {
  const out = [];
  for (const el of document.querySelectorAll('.menubar-menu-title')) {
    const t = (el.innerText || '').trim();
    const r = el.getBoundingClientRect();
    if (r.width < 2) continue;
    out.push({t: t, x: Math.round(r.left + r.width / 2), y: Math.round(r.top + r.height / 2)});
  }
  return JSON.stringify(out);
})()
"""

CHECK_JS = r"""
(() => {
  const done = document.querySelectorAll('[data-ti-done="1"]').length;
  const repl = document.querySelectorAll('[data-ti-repl="1"]').length;
  const menu = document.querySelector('.monaco-menu');
  const items = [];
  if (menu) {
    for (const a of menu.querySelectorAll('.action-label')) {
      items.push((a.innerText || '').trim().slice(0, 30));
    }
  }
  return JSON.stringify({done: done, repl: repl, menu_open: !!menu, labels: items.slice(0, 12)});
})()
"""


class FakeWindow:
    def __init__(self, title):
        self.title = title
        self.hwnd = 0


def main() -> int:
    args = sys.argv[1:]
    port = 9401
    for a in args:
        if a.isdigit():
            port = int(a)
    keep = "--keep" in args

    pages = cdp.list_pages(port)
    page = None
    for p in pages:
        if p.get("type") == "page" and not (p.get("url") or "").startswith("devtools://"):
            page = p
            break
    if not page:
        print("端口 %d 没有 page target" % port)
        return 2
    title = page.get("title") or ""
    pg = cdp.CDPPage(page["webSocketDebuggerUrl"])

    raw = pg.eval(FIND_MENU_JS, timeout=8)
    menus = raw if isinstance(raw, list) else json.loads(raw)
    go = next((m for m in menus if m["t"].strip().lower() == "go"), None)
    if not go:
        print("没找到 Go 菜单")
        return 3
    for typ in ("mousePressed", "mouseReleased"):
        pg.call("Input.dispatchMouseEvent", {
            "type": typ, "x": go["x"], "y": go["y"], "button": "left",
            "buttons": 1 if typ == "mousePressed" else 0, "clickCount": 1}, timeout=6)
    time.sleep(0.4)
    print("菜单已打开。")

    cfg = Config(os.path.join(BASE, "config.json"))
    cfg.set("capture", "inject_ports", [port], False)
    cfg.set("capture", "ui_hover", False, False)   # 这个脚本测整窗扫描（菜单浮层优先）
    ch = InjectChannel(cfg)
    if not ch.attach(FakeWindow(title)):
        print("attach 失败:", ch.last_error)
        return 4

    blocks = ch.read()
    # 菜单打开时读到的块里就包含菜单项，这里不做关键词过滤（不同菜单文字不一样）
    menu_blocks = blocks
    print("读到块:", len(blocks), "| 其中含 Go to:", len([b for b in blocks if "Go to" in b["text"]]))
    for b in menu_blocks[:10]:
        print("   ", b["text"][:44])

    from app.translator import Translator
    real = "--real" in args
    texts = [b["text"] for b in menu_blocks]
    if real:
        # 用临时 work_dir：测试产生的译文不写进真实 tm.json / cache.sqlite3
        tr = Translator(cfg, os.path.join(tempfile.gettempdir(), "tt_tr_menu"))
        print("翻译引擎:", tr.provider_label)
        res = tr.translate_lines(texts) if texts else {}
        pairs = [(TextItem(0, 0, 0, 0, b["text"], 1.0), res.get(b["text"])) for b in menu_blocks]
    else:
        print("用假译文（不联网，测写回链路）")
        pairs = [(TextItem(0, 0, 0, 0, b["text"], 1.0), "【译】" + b["text"][:10]) for b in menu_blocks]
    n = ch.apply(pairs)
    print("写回条数:", n)

    raw = pg.eval(CHECK_JS, timeout=8)
    ck = raw if isinstance(raw, dict) else json.loads(raw)
    print("校验:", json.dumps(ck, ensure_ascii=False)[:400])

    out_png = os.path.join(BASE, "tools", "ui_menu_check.png")
    subprocess.run([sys.executable, os.path.join(BASE, "tools", "shot_by_port.py"),
                    str(port), out_png], capture_output=True, text=True)
    print("截图:", out_png, os.path.exists(out_png))

    if not keep:
        print("还原节点:", ch.restore())
    ch.close()
    pg.close()

    ok = n > 0 and ck.get("repl", 0) > 0
    print("\n结论:", "通过（菜单项已写回中文）" if ok else "写回失败")
    return 0 if ok else 5


if __name__ == "__main__":
    sys.exit(main())
