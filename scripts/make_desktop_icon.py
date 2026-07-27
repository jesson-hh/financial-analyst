# -*- coding: utf-8 -*-
"""一次性生成桌面壳图标:朱红印章上一个「觀」字。

**构建期脚本**。pillow 只在跑本脚本时需要,不进运行时依赖 —— 产物 .ico
已提交进仓库,除非要改图标,否则不必再跑。

    pip install pillow
    python scripts/make_desktop_icon.py
"""
from __future__ import annotations

from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

_OUT = Path(__file__).resolve().parents[1] / "guanlan_v2" / "desktop" / "guanlan.ico"
_YIN = (168, 57, 45)        # --yin 印章红
_PAPER = (241, 234, 217)    # --paper 宣纸暖白
_FONT = "C:/Windows/Fonts/msyhbd.ttc"   # 微软雅黑 Bold,Windows 自带
_SIZES = [16, 24, 32, 48, 64, 128, 256]


def _render(size: int) -> Image.Image:
    img = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    d = ImageDraw.Draw(img)
    pad = max(1, size // 16)
    radius = max(1, size // 12)
    d.rounded_rectangle([pad, pad, size - pad - 1, size - pad - 1], radius=radius, fill=_YIN)
    font = ImageFont.truetype(_FONT, int(size * 0.62))
    box = d.textbbox((0, 0), "觀", font=font)
    d.text(((size - (box[2] - box[0])) / 2 - box[0],
            (size - (box[3] - box[1])) / 2 - box[1]), "觀", font=font, fill=_PAPER)
    return img


def main() -> None:
    frames = [_render(s) for s in _SIZES]
    frames[-1].save(_OUT, format="ICO", sizes=[(s, s) for s in _SIZES])
    print(f"wrote {_OUT} ({_OUT.stat().st_size} bytes)")


if __name__ == "__main__":
    main()
