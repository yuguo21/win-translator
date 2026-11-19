"""OCR 纯识别自测：用 PIL 造一张英文图 → Windows.Media.Ocr 识别 → 打印。

不依赖任何窗口，用来单独验证 OCR 链路是否正常。
用法：python tools/test_ocr_image.py
"""
import asyncio
import io
import os
import sys

BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, BASE)

from PIL import Image, ImageDraw, ImageFont

from app.engines.ocr_engine import _make_ocr_engine, _recognize_async

SAMPLES = [
    "Battlemage",
    "The Elder Scrolls II: Daggerfall",
    "Ready weapon",
    "Press any key to continue",
    "Your health is low",
]


def build_image(path: str, scale: int = 2) -> Image.Image:
    w, h = 700, 300
    img = Image.new("RGB", (w, h), (0, 0, 0))
    d = ImageDraw.Draw(img)
    try:
        font = ImageFont.truetype("C:\\Windows\\Fonts\\consola.ttf", 22)
    except Exception:
        font = ImageFont.load_default()
    y = 20
    for s in SAMPLES:
        d.text((24, y), s, fill=(255, 255, 255), font=font)
        y += 48
    img = img.resize((w * scale, h * scale), Image.LANCZOS)
    img.save(path)
    return img


def main() -> int:
    out = os.path.join(BASE, "tools", "ocr_test.png")
    img = build_image(out)
    print(f"测试图: {out}  {img.size}")

    eng = _make_ocr_engine()
    print("OCR 引擎:", "可用" if eng else "不可用")
    if not eng:
        return 1

    buf = io.BytesIO()
    img.save(buf, format="PNG")
    try:
        result = asyncio.run(_recognize_async(eng, buf.getvalue()))
    except Exception as e:
        print("识别异常:", type(e).__name__, e)
        return 1

    print(f"\n识别到 {len(result.lines)} 行：")
    hit = 0
    texts = []
    for line in result.lines:
        texts.append(line.text)
        print("   ", line.text)
    norm = " ".join(texts).lower()
    for s in SAMPLES:
        if s.lower() in norm:
            hit += 1
    print(f"\n命中样本 {hit}/{len(SAMPLES)}")
    return 0 if hit >= 3 else 1


if __name__ == "__main__":
    sys.exit(main())
