"""网页翻译模式自测：对真实浏览器窗口读取页面文本，并统计可翻译的英文行。"""
import os
import sys

BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, BASE)

from app import engines, winutil
from app.config import Config

BROWSERS = ("msedge.exe", "chrome.exe", "firefox.exe", "brave.exe", "opera.exe", "vivaldi.exe")


def main() -> int:
    winutil.set_dpi_aware()
    cfg = Config(os.path.join(BASE, "config.json"))
    wins = [w for w in winutil.list_windows() if w.process.lower() in BROWSERS]
    if not wins:
        print("没有浏览器窗口可测")
        return 1
    w = wins[0]
    print("目标:", w.label())
    print("客户区:", winutil.get_client_rect(w.hwnd))

    eng = engines.create_engine("web", cfg)
    eng.prepare(w)
    items = []
    for _ in range(3):
        items = eng.capture_once()
        if items:
            break
    print(f"\n可翻译英文行 {len(items)} 条：")
    for it in items[:20]:
        print(f"  ({it.x:4},{it.y:4},{it.w:4}x{it.h:3}) {it.text[:70]}")
    if eng.last_error:
        print("last_error:", eng.last_error)
    eng.close()
    return 0 if items else 2


if __name__ == "__main__":
    sys.exit(main())
