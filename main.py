"""入口：python main.py

可选参数：
  --mode ocr|uia|memory|gfx   启动时选定的捕获模式（同时写入 config.json）
  --selftest                  不打开界面，自查环境（窗口枚举、OCR、翻译配置、缓存）
"""
import argparse
import os
import sys

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
if BASE_DIR not in sys.path:
    sys.path.insert(0, BASE_DIR)


def run_selftest() -> int:
    from app import engines, winutil
    from app.config import Config
    from app.translator import Translator

    print("=" * 60)
    print("环境自查")
    print("=" * 60)
    winutil.set_dpi_aware()

    wins = winutil.list_windows()
    print(f"[窗口] 可见窗口 {len(wins)} 个")
    for w in wins[:8]:
        print(f"   - {w.title} [{w.process}:{w.pid}] hwnd={w.hwnd}")

    # OCR
    print("[OCR ] 检测系统 OCR 引擎…")
    try:
        from app.engines.ocr_engine import _make_ocr_engine
        eng = _make_ocr_engine()
        print(f"   Windows.Media.Ocr -> {'可用' if eng else '不可用（缺英语语言包）'}")
    except Exception as e:
        print(f"   Windows.Media.Ocr 导入失败: {e}")
    try:
        import paddleocr  # noqa
        print("   paddleocr -> 已安装，将优先使用")
    except Exception:
        print("   paddleocr -> 未安装（不影响，可选用）")

    # 翻译配置
    cfg = Config(os.path.join(BASE_DIR, "config.json"))
    t = Translator(cfg, BASE_DIR)
    key = cfg.get("translator", "api_key"); model = cfg.get("translator", "model")
    print(f"[翻译] api_key={'已配置' if key else '缺失'} model={model or '缺失'} 缓存 {t.db_path}")
    print(f"[术语] term_map.json 条目 {len(t.term_map)} 条")
    print(f"[词库] tm.json 已记 {len(t.tm)} 条（翻过的句子直接查表，不再联网）")

    print("[引擎] 注册模式: " + ", ".join(m['mode'] for m in engines.mode_list()))
    print("=" * 60)
    return 0


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--mode", choices=["ocr", "uia", "memory", "gfx"])
    parser.add_argument("--selftest", action="store_true")
    args = parser.parse_args()

    if args.selftest:
        return run_selftest()

    from app import winutil
    winutil.set_dpi_aware()

    from app.config import Config
    if args.mode:
        cfg = Config(os.path.join(BASE_DIR, "config.json"))
        cfg.set("capture", "mode", args.mode)

    from app.app import App
    App(BASE_DIR).run()
    return 0


if __name__ == "__main__":
    sys.exit(main())
