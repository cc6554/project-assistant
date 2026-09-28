"""图片读取工具：本地截图 → ImageBlock（自动识别格式并 base64 编码）。"""

from __future__ import annotations

import base64
from pathlib import Path

from PIL import Image

from ..core.schema import ImageBlock

_FORMAT_TO_MEDIA_TYPE = {
    "JPEG": "image/jpeg",
    "PNG": "image/png",
    "WEBP": "image/webp",
    "GIF": "image/gif",
}


def load_image_blocks(paths: list[str | Path]) -> list[ImageBlock]:
    blocks: list[ImageBlock] = []
    for p in paths:
        path = Path(p)
        if not path.exists():
            raise FileNotFoundError(f"截图不存在：{path}")
        raw = path.read_bytes()
        with Image.open(path) as im:
            media_type = _FORMAT_TO_MEDIA_TYPE.get(im.format, "image/png")
        blocks.append(
            ImageBlock(
                media_type=media_type,
                data=base64.b64encode(raw).decode("ascii"),
            )
        )
    return blocks
