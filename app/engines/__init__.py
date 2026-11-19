"""引擎注册表：统一创建、切换、释放各抓取模式。"""
from typing import Dict, List, Type

from .base import BaseEngine
from .gfx_engine import GfxEngine
from .memory_engine import MemoryEngine
from .ocr_engine import OcrEngine
from .uia_engine import UiaEngine
from .web_engine import WebEngine

REGISTRY: Dict[str, Type[BaseEngine]] = {
    "ocr": OcrEngine,
    "web": WebEngine,
    "uia": UiaEngine,
    "memory": MemoryEngine,
    "gfx": GfxEngine,
}

ORDER = ["web", "ocr", "uia", "memory", "gfx"]


def mode_list() -> List[dict]:
    out = []
    for key in ORDER:
        cls = REGISTRY[key]
        out.append({"mode": key, "name": cls.display_name, "desc": cls.description})
    return out


def create_engine(mode: str, cfg) -> BaseEngine:
    cls = REGISTRY.get(mode)
    if cls is None:
        raise ValueError(f"未知抓取模式: {mode}")
    return cls(cfg)
