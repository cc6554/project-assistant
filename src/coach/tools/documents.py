"""文档读取：简历 / 工作日志 / 自填文本 → 纯文本。

支持 .txt / .md / .pdf（简历常见格式）。
"""

from __future__ import annotations

from pathlib import Path

from pypdf import PdfReader


class UnsupportedDocumentError(ValueError):
    pass


def read_document(path: str | Path) -> str:
    path = Path(path)
    if not path.exists():
        raise FileNotFoundError(f"文档不存在：{path}")

    suffix = path.suffix.lower()
    if suffix in {".txt", ".md", ".markdown", ".log"}:
        return path.read_text(encoding="utf-8", errors="replace").strip()
    if suffix == ".pdf":
        reader = PdfReader(str(path))
        pages = [(page.extract_text() or "") for page in reader.pages]
        return "\n".join(pages).strip()
    raise UnsupportedDocumentError(
        f"暂不支持 {suffix} 文件，请转为 PDF 或纯文本（{path.name}）"
    )
