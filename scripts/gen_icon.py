"""生成 Tauri 应用图标：icon.png (1024) + icon.ico（多尺寸）。

雷达主题：深蓝渐变圆底 + 青色同心圆环 + 扫描线。用 PIL 绘制，无外部依赖。
"""

from __future__ import annotations

from pathlib import Path

from PIL import Image, ImageDraw, ImageFilter

SIZE = 1024
OUT = Path(__file__).resolve().parents[1] / "src-tauri" / "icons"


def lerp(a: tuple[int, int, int], b: tuple[int, int, int], t: float) -> tuple[int, int, int]:
    return tuple(int(a[i] + (b[i] - a[i]) * t) for i in range(3))


def main() -> None:
    OUT.mkdir(parents=True, exist_ok=True)
    img = Image.new("RGBA", (SIZE, SIZE), (0, 0, 0, 0))
    d = ImageDraw.Draw(img)

    # 圆底（深蓝渐变：左上深蓝 → 右下墨蓝）
    mask = Image.new("L", (SIZE, SIZE), 0)
    dm = ImageDraw.Draw(mask)
    dm.ellipse((0, 0, SIZE - 1, SIZE - 1), fill=255)
    base = Image.new("RGBA", (SIZE, SIZE))
    db = ImageDraw.Draw(base)
    for y in range(SIZE):
        t = y / (SIZE - 1)
        db.line(
            [(0, y), (SIZE, y)],
            fill=lerp((10, 28, 82), (6, 16, 48), t),
        )
    img.paste(base, (0, 0), mask)

    cx = cy = SIZE // 2

    # 同心圆环（雷达）
    rings = [330, 235, 150]
    for r in rings:
        d.ellipse(
            (cx - r, cy - r, cx + r, cy + r),
            outline=(0, 210, 190, 200),
            width=14,
        )

    # 中心点
    d.ellipse((cx - 22, cy - 22, cx + 22, cy + 22), fill=(0, 230, 210, 255))

    # 扫描扇形（青色半透明）
    scan = Image.new("RGBA", (SIZE, SIZE), (0, 0, 0, 0))
    ds = ImageDraw.Draw(scan)
    ds.pieslice((cx - 330, cy - 330, cx + 330, cy + 330), 315, 405, fill=(0, 210, 190, 110))
    scan = scan.filter(ImageFilter.GaussianBlur(8))
    img.alpha_composite(scan)

    # 外圈描边
    d.ellipse((cx - 370, cy - 370, cx + 370, cy + 370), outline=(255, 255, 255, 90), width=8)

    png = OUT / "icon.png"
    ico = OUT / "icon.ico"
    img.save(png)
    img.save(
        ico,
        sizes=[(16, 16), (24, 24), (32, 32), (48, 48), (64, 64), (128, 128), (256, 256)],
    )
    print(f"已生成: {png} 和 {ico}")


if __name__ == "__main__":
    main()
