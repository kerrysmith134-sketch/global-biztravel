"""票据 OCR（ADR-0034：开源自托管 Tesseract）。

零云端依赖：Tesseract（Apache-2.0 开源 OCR）作为可选外部二进制，
未安装时端点显式 503 并给出安装指引，不伪造识别结果。

边界（不变量 I3 精神）：OCR 只产**候选字段**（金额/日期/商户提示），
不自动创建费用记录——人确认后才入库，与客户端"记一笔费用"共用幂等键。
"""

from __future__ import annotations

import re
import shutil
import subprocess
import tempfile
from pathlib import Path
from typing import Any

#: 优先中英双语，中文包缺失时回落 eng。
_OCR_LANG_FALLBACK = ("chi_sim+eng", "eng")

#: 金额候选：¥/$/€ 前缀或 TOTAL/合计 等关键词后的数字；日期候选：常见三种格式。
_AMOUNT_PATTERN = re.compile(
    r"(?:total|amount|合计|金额|实付|小计)[^\d]{0,8}(\d{1,6}(?:[.,]\d{1,2})?)"
    r"|([¥$€£￥]\s*\d{1,6}(?:[.,]\d{1,2})?)"
    r"|(\d{1,6}(?:[.,]\d{2})\s*(?:EUR|USD|CNY|SGD|JPY))",
    re.IGNORECASE,
)
_DATE_PATTERNS = (
    re.compile(r"(\d{4}[-/年.]\s*\d{1,2}[-/月.]\s*\d{1,2}\s*日?)"),
    re.compile(r"(\d{4}-\d{2}-\d{2})"),
)


def ocr_available() -> bool:
    return shutil.which("tesseract") is not None


class OcrUnavailable(RuntimeError):
    pass


def extract_receipt_fields(image_bytes: bytes) -> dict[str, Any]:
    """跑 Tesseract 并抽候选字段。仅提取，不做结论。"""
    if not ocr_available():
        raise OcrUnavailable(
            "tesseract 未安装（开源 OCR）。安装：brew install tesseract tesseract-lang"
        )
    if not image_bytes:
        raise OcrUnavailable("票据图片为空")

    with tempfile.TemporaryDirectory() as tmp:
        image_path = Path(tmp) / "receipt.png"
        image_path.write_bytes(image_bytes)
        text = None
        last_error: Exception | None = None
        for lang in _OCR_LANG_FALLBACK:
            try:
                completed = subprocess.run(
                    ["tesseract", str(image_path), "stdout", "-l", lang, "--psm", "6"],
                    capture_output=True,
                    timeout=30,
                    check=True,
                    text=True,
                )
                text = completed.stdout
                break
            except subprocess.SubprocessError as exc:
                last_error = exc
        if text is None:
            raise OcrUnavailable(f"Tesseract 识别失败：{last_error}")

    return {
        "merchant_hint": _merchant_hint(text),
        "amount_candidates": _amounts(text),
        "date_candidates": _dates(text),
        "text_length": len(text),
        "raw_text_preview": text[:400],
    }


def _lines(text: str) -> list[str]:
    return [line.strip() for line in text.splitlines() if line.strip()]


def _merchant_hint(text: str) -> str | None:
    lines = _lines(text)
    return lines[0][:60] if lines else None


def _amounts(text: str) -> list[str]:
    seen: list[str] = []
    for match in _AMOUNT_PATTERN.finditer(text):
        for group in match.groups():
            if group and group not in seen:
                seen.append(group)
    return seen[:5]


def _dates(text: str) -> list[str]:
    for pattern in _DATE_PATTERNS:
        matches = pattern.findall(text)
        if matches:
            return [m.strip() for m in matches[:3]]
    return []


__all__ = ["OcrUnavailable", "extract_receipt_fields", "ocr_available"]
