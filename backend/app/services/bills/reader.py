"""Turn a bill file into text lines (+ QR payloads).

Order of preference, most to least reliable:
1. QR codes (GST e-invoice IRN QR, B2C dynamic UPI QR) - machine-issued values.
2. The PDF's own text layer (digital/online bills) - exact characters, no guessing.
3. OCR (RapidOCR: PaddleOCR PP-OCRv4 models on ONNX Runtime, CPU) for scans and photos.
"""

import io
import logging
import threading
import time
from dataclasses import dataclass, field
from typing import Any

import numpy as np
from PIL import Image, ImageOps

log = logging.getLogger("app.bills")

MAX_PAGES = 4
RENDER_SCALE = 2.5  # ~180 dpi; good for OCR and QR without being slow
MIN_TEXT_CHARS = 40  # below this a PDF page is treated as scanned

PDF_MAGIC = b"%PDF-"
JPEG_MAGIC = b"\xff\xd8\xff"
PNG_MAGIC = b"\x89PNG\r\n\x1a\n"


def sniff(data: bytes) -> str | None:
    if data.startswith(PDF_MAGIC):
        return "application/pdf"
    if data.startswith(JPEG_MAGIC):
        return "image/jpeg"
    if data.startswith(PNG_MAGIC):
        return "image/png"
    return None


@dataclass
class ReadResult:
    lines: list[str] = field(default_factory=list)
    qr_payloads: list[str] = field(default_factory=list)
    method: list[str] = field(default_factory=list)  # e.g. ["pdf-text", "qr"] or ["ocr"]
    ocr_confidence: float | None = None
    pages: int = 0
    warnings: list[str] = field(default_factory=list)

    @property
    def text(self) -> str:
        return "\n".join(self.lines)


# ---------- OCR engine (lazy singleton; thread-safe) ----------

_engine: Any = None
_load_lock = threading.Lock()  # guards one-time model loading
_run_lock = threading.Lock()  # one OCR run at a time (bounded CPU; session not shared)


def _ocr_engine() -> Any:
    global _engine
    if _engine is None:
        with _load_lock:
            if _engine is None:
                from rapidocr_onnxruntime import RapidOCR

                started = time.monotonic()
                _engine = RapidOCR()
                log.info("RapidOCR loaded in %.1fs", time.monotonic() - started)
    return _engine


def _ocr_lines(image: Image.Image) -> tuple[list[str], float | None]:
    """OCR an image and rebuild text lines in reading order (top-to-bottom, left-to-right)."""
    arr = np.asarray(image.convert("RGB"))
    engine = _ocr_engine()  # load outside the run lock (different lock: no self-deadlock)
    with _run_lock:
        result, _ = engine(arr)
    if not result:
        return [], None
    boxes = []
    for box, text, score in result:
        ys = [p[1] for p in box]
        xs = [p[0] for p in box]
        boxes.append(
            {
                "y": (min(ys) + max(ys)) / 2,
                "h": max(ys) - min(ys),
                "x": min(xs),
                "text": str(text).strip(),
                "score": float(score),
            }
        )
    boxes.sort(key=lambda b: (b["y"], b["x"]))
    lines: list[list[dict]] = []
    for b in boxes:
        if lines and abs(lines[-1][0]["y"] - b["y"]) <= max(6.0, 0.5 * b["h"]):
            lines[-1].append(b)
        else:
            lines.append([b])
    out = ["   ".join(s["text"] for s in sorted(line, key=lambda s: s["x"])) for line in lines]
    conf = sum(b["score"] for b in boxes) / len(boxes)
    return [line for line in out if line.strip()], round(conf, 3)


def _enhance(image: Image.Image) -> Image.Image:
    """Clean-up for faint scans / phone photos: greyscale, auto-contrast, sharpen, enlarge."""
    from PIL import ImageFilter

    img = ImageOps.autocontrast(ImageOps.grayscale(image), cutoff=2)
    if max(img.size) < 1800:
        factor = 1800 / max(img.size)
        img = img.resize((int(img.width * factor), int(img.height * factor)), Image.LANCZOS)
    return img.filter(ImageFilter.UnsharpMask(radius=2, percent=160, threshold=3)).convert("RGB")


def _ocr_best(image: Image.Image) -> tuple[list[str], float | None]:
    """OCR; if the result is weak, retry on an enhanced copy and keep the better reading."""
    lines, conf = _ocr_lines(image)
    chars = sum(len(ln) for ln in lines)
    if conf is not None and (conf >= 0.8 and chars >= 40):
        return lines, conf
    lines2, conf2 = _ocr_lines(_enhance(image))

    def score(ls: list[str], c: float | None) -> float:
        return (c or 0) * min(1.0, sum(len(x) for x in ls) / 300) ** 0.5

    return (lines2, conf2) if score(lines2, conf2) > score(lines, conf) else (lines, conf)


# ---------- QR ----------


def _qr_codes(image: Image.Image) -> list[str]:
    try:
        import zxingcpp
    except ImportError:  # pragma: no cover
        return []
    found: list[str] = []
    for candidate in (image, ImageOps.grayscale(image)):
        try:
            for code in zxingcpp.read_barcodes(candidate):
                if code.text and code.text not in found:
                    found.append(code.text)
        except Exception:  # noqa: BLE001 - a QR failure must never fail extraction
            log.debug("QR decode failed", exc_info=True)
        if found:
            break
    return found


# ---------- PDF ----------


def _pdf_text_lines(data: bytes, max_pages: int) -> list[list[str]]:
    import pdfplumber

    pages: list[list[str]] = []
    with pdfplumber.open(io.BytesIO(data)) as pdf:
        for page in pdf.pages[:max_pages]:
            text = page.extract_text(x_tolerance=1.5, y_tolerance=3, keep_blank_chars=False) or ""
            pages.append([ln.rstrip() for ln in text.splitlines() if ln.strip()])
    return pages


def _pdf_images(data: bytes, max_pages: int) -> list[Image.Image]:
    import pypdfium2 as pdfium

    pdf = pdfium.PdfDocument(data)
    try:
        return [pdf[i].render(scale=RENDER_SCALE).to_pil() for i in range(min(len(pdf), max_pages))]
    finally:
        pdf.close()


def read(data: bytes, content_type: str, max_pages: int = MAX_PAGES) -> ReadResult:
    res = ReadResult()
    if content_type == "application/pdf":
        try:
            text_pages = _pdf_text_lines(data, max_pages)
        except Exception:  # noqa: BLE001
            log.warning("PDF text extraction failed", exc_info=True)
            text_pages = []
            res.warnings.append("Couldn't read the PDF's text layer; used OCR instead")
        images = _pdf_images(data, max_pages)
        res.pages = len(images)
        confs = []
        for idx, image in enumerate(images):
            page_text = text_pages[idx] if idx < len(text_pages) else []
            if sum(len(t) for t in page_text) >= MIN_TEXT_CHARS:
                res.lines.extend(page_text)
                if "pdf-text" not in res.method:
                    res.method.append("pdf-text")
            else:
                lines, conf = _ocr_best(image)
                res.lines.extend(lines)
                if conf is not None:
                    confs.append(conf)
                if "ocr" not in res.method:
                    res.method.append("ocr")
            if idx == 0 or not res.qr_payloads:
                res.qr_payloads.extend(q for q in _qr_codes(image) if q not in res.qr_payloads)
        res.ocr_confidence = round(sum(confs) / len(confs), 3) if confs else None
    else:
        image = Image.open(io.BytesIO(data))
        image = ImageOps.exif_transpose(image).convert("RGB")
        if max(image.size) > 3000:  # keep phone photos fast
            image.thumbnail((3000, 3000))
        res.pages = 1
        res.qr_payloads = _qr_codes(image)
        res.lines, res.ocr_confidence = _ocr_best(image)
        res.method.append("ocr")
    if res.qr_payloads:
        res.method.append("qr")
    if not res.lines:
        res.warnings.append("No readable text found on the bill")
    return res
