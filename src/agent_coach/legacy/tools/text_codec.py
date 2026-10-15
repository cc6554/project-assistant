"""Bounded, deterministic decoding for user supplied text files."""

from __future__ import annotations


class TextDecodingError(ValueError):
    """Raised when bytes are not valid supported text."""


_BOM_ENCODINGS: tuple[tuple[bytes, str], ...] = (
    # UTF-32 markers must precede UTF-16 markers because the latter are a
    # prefix of the former.
    (b"\xff\xfe\x00\x00", "utf-32"),
    (b"\x00\x00\xfe\xff", "utf-32"),
    (b"\xef\xbb\xbf", "utf-8-sig"),
    (b"\xff\xfe", "utf-16"),
    (b"\xfe\xff", "utf-16"),
)


def decode_text_bytes(data: bytes) -> str:
    """Decode supported text encodings without silently replacing bytes.

    UTF BOMs are authoritative.  For files without a BOM, strict UTF-8 is
    preferred and strict GB18030 is the compatibility fallback used by the
    legacy document importer.  Invalid input raises a typed error instead of
    returning replacement characters that could corrupt a resume.
    """

    for marker, encoding in _BOM_ENCODINGS:
        if data.startswith(marker):
            try:
                return data.decode(encoding)
            except UnicodeDecodeError as exc:
                raise TextDecodingError(
                    f"文件不是有效的 {encoding} 文本"
                ) from exc

    try:
        return data.decode("utf-8")
    except UnicodeDecodeError as utf8_error:
        try:
            return data.decode("gb18030")
        except UnicodeDecodeError as exc:
            raise TextDecodingError("文件不是有效的 UTF-8 或 GB18030 文本") from exc
