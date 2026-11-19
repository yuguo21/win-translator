"""OCR 装饰性文字过滤的离线单测（不截图、不联网）。

验证：窗口按钮 tooltip（Minimize/Maximize/Close 及中文）被丢掉，
正常正文不会被误杀。

用法：python tools/test_ocr_noise.py
"""
import os
import sys

BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, BASE)

from app.engines.ocr_engine import _is_decorative

SKIP = [
    "Minimize", "Maximize", "Restore", "Close",
    "MINIMIZE", "close", " Max ", "Close Window",
    "最小化", "最大化", "还原", "关闭窗口",
    "×", "—", "▢", "...", "|",
]

KEEP = [
    "File", "Edit", "Selection", "View", "Run", "Terminal", "Help",
    "Open File...", "Start New File...",
    "Close all editors",          # 这是菜单项文字，有阅读价值
    "Minimize damage taken",      # 正文里的词，不能被当成按钮
    "Welcome to Visual Studio Code",
    "Restoring the ancient castle",
    "Press Esc to close the dialog",
]


def main() -> int:
    bad = []
    for t in SKIP:
        if not _is_decorative(t):
            bad.append(("应跳过但保留了", t))
    for t in KEEP:
        if _is_decorative(t):
            bad.append(("应保留但被跳过", t))
    print("应跳过 %d 条 / 应保留 %d 条" % (len(SKIP), len(KEEP)))
    if bad:
        for kind, t in bad:
            print("  [失败] %s: %r" % (kind, t))
        print("\n结论: 有 %d 条不符合预期" % len(bad))
        return 1
    print("\n结论: 全部符合预期（按钮提示被过滤，正文不受影响）")
    return 0


if __name__ == "__main__":
    sys.exit(main())
