"""真实 API 端到端：生成模拟岗位截图 → parse_jd_screenshots → 打印 JDCard。

运行：python examples/real_e2e.py
"""
from __future__ import annotations

import json
from pathlib import Path

from dotenv import load_dotenv
from PIL import Image, ImageDraw, ImageFont

from coach.core.llm.router import ModelRouter
from coach.tools.jd_parser import parse_jd_screenshots

ROOT = Path(__file__).resolve().parents[1]

LINES = [
    ("大模型应用开发工程师", 34, 30),
    ("深圳智链科技 · 互联网 · D轮及以上", 20, 70),
    ("25-50K·14薪  |  深圳·南山区·科技园  |  本科 3-5年", 22, 105),
    ("", 16, 135),
    ("【岗位职责】", 24, 150),
    ("1. 负责企业知识库 RAG 系统的设计与开发，包含文档解析、向量检索、答案生成；", 20, 188),
    ("2. 基于 LangChain / LlamaIndex 搭建 Agent 工作流，对接业务系统；", 20, 222),
    ("3. 参与大模型微调（LoRA/SFT）与评测，持续优化回答效果。", 20, 256),
    ("", 16, 280),
    ("【任职要求】", 24, 295),
    ("1. 熟练 Python，熟悉 PyTorch，了解 Transformer 原理；", 20, 333),
    ("2. 有 RAG 或 Agent 项目落地经验，熟悉向量数据库（Milvus/Faiss）；", 20, 367),
    ("3. 熟悉 Linux、Docker，能独立部署模型服务；", 20, 401),
    ("4. 良好的沟通能力与自驱力。", 20, 435),
    ("", 16, 459),
    ("【加分项】", 24, 474),
    ("- 有顶会论文或开源项目贡献；有 vLLM/TensorRT 推理优化经验优先。", 20, 512),
]


def make_jd_image(path: Path) -> Path:
    img = Image.new("RGB", (1000, 580), "white")
    draw = ImageDraw.Draw(img)
    font_path = r"C:\Windows\Fonts\msyh.ttc"
    for text, size, y in LINES:
        font = ImageFont.truetype(font_path, size)
        draw.text((40, y), text, fill=(30, 30, 30), font=font)
    img.save(path)
    return path


def main() -> None:
    load_dotenv(ROOT / ".env")
    shot = make_jd_image(ROOT / "data" / "mock_jd.png")
    print(f"模拟截图已生成：{shot}")

    router = ModelRouter.from_yaml(ROOT / "config" / "providers.yaml")
    card = parse_jd_screenshots(router, [str(shot)])
    print(json.dumps(card.model_dump(), ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
