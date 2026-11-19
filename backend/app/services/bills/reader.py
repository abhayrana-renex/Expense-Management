"""Turn a bill file into text lines (+ QR payloads).

Order of preference, most to least reliable:
1. QR codes (GST e-invoice IRN QR, B2C dynamic UPI QR) - machine-issued values.
2. The PDF's own text layer (digital/online bills) - exact characters, no guessing.
3. OCR (RapidOCR: PaddleOCR PP-OCRv4 models on ONNX Runtime, CPU) for scans and photos.
"""

import io
import logging
import re
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
    # Prominent text in the top part of page 1 (bigger, coloured or bold than the body),
    # most prominent first - where a bill prints the seller's name. See _rank_headings.
    headings: list[dict] = field(default_factory=list)

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


def _ink_is_coloured(arr: np.ndarray, x0: float, y0: float, x1: float, y1: float) -> bool:
    """True when the text strokes in this box are coloured (not black / grey). The ink is
    whatever differs most from the box's own background, so white-on-colour bands work too."""
    h, w = arr.shape[:2]
    crop = arr[max(0, int(y0)) : min(h, int(y1) + 1), max(0, int(x0)) : min(w, int(x1) + 1)]
    if crop.size == 0:
        return False
    px = crop.reshape(-1, 3).astype(np.int16)
    background = np.median(px, axis=0)
    ink = px[np.abs(px - background).sum(axis=1) > 90]
    if len(ink) < 12:
        return False
    return float((ink.max(axis=1) - ink.min(axis=1)).mean()) > 55


def _ocr_lines(image: Image.Image) -> tuple[list[str], float | None, list[dict]]:
    """OCR an image and rebuild text lines in reading order (top-to-bottom, left-to-right).
    Also returns each text box's size, position and colour for _rank_headings."""
    arr = np.asarray(image.convert("RGB"))
    engine = _ocr_engine()  # load outside the run lock (different lock: no self-deadlock)
    with _run_lock:
        result, _ = engine(arr)
    if not result:
        return [], None, []
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
                "colored": _ink_is_coloured(arr, min(xs), min(ys), max(xs), max(ys)),
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
    segments = [
        {
            "text": b["text"],
            "size": b["h"],
            "top": b["y"] / arr.shape[0],
            "colored": b["colored"],
            "bold": False,
        }
        for b in boxes
    ]
    return [line for line in out if line.strip()], round(conf, 3), segments


# ---------- headings (seller name: biggest / coloured text at the top) ----------

HEADING_ZONE = 0.4  # top 40% of page 1
HEADING_MIN_RATIO = 1.2  # at least 20% bigger than the body text, unless coloured / bold


def _rank_headings(segments: list[dict]) -> list[dict]:
    """Text in the top part of the page that stands out from the body - bigger, coloured or
    bold - most prominent first. Most bills print the seller's name like this, so the parser
    weighs these lines when picking the vendor among the 2-3 names in the header."""
    sizes = sorted(s["size"] for s in segments if s["size"] > 0)
    if not sizes:
        return []
    body = sizes[len(sizes) // 2]
    out = []
    for s in segments:
        text = " ".join(s["text"].split())
        if s["top"] > HEADING_ZONE or sum(ch.isalpha() for ch in text) < 3:
            continue
        ratio = s["size"] / body if body else 1.0
        if ratio < HEADING_MIN_RATIO and not s["colored"] and not s["bold"]:
            continue
        prominence = ratio + (0.35 if s["colored"] else 0) + (0.15 if s["bold"] else 0)
        out.append(
            {
                "text": text,
                "ratio": round(ratio, 2),
                "colored": s["colored"],
                "bold": s["bold"],
                "prominence": round(prominence, 2),
            }
        )
    out.sort(key=lambda h: -h["prominence"])
    return out[:6]


def _is_coloured(color: Any) -> bool:
    """pdfplumber fill colour: grey (1 value), RGB (3) or CMYK (4); anything else = black."""
    if not isinstance(color, list | tuple) or not all(isinstance(c, int | float) for c in color):
        return False
    if len(color) == 3:
        return max(color) - min(color) > 0.2
    if len(color) == 4:
        return max(color[:3]) > 0.2
    return False


def _pdf_segments(page: Any) -> list[dict]:
    """Runs of words on one line of a PDF page, with font size, colour and boldness."""
    words = page.extract_words(
        x_tolerance=1.5, y_tolerance=3, extra_attrs=["size", "fontname", "non_stroking_color"]
    )
    words.sort(key=lambda w: (round(w["top"]), w["x0"]))
    segments: list[dict] = []
    for w in words:
        style = (
            round(w["size"], 1),
            _is_coloured(w["non_stroking_color"]),
            bool(re.search(r"bold|black|heavy", w["fontname"], re.I)),
        )
        last = segments[-1] if segments else None
        # Same line, same style and close together = one run ("PAPERWORKS STATIONERS PVT LTD").
        if (
            last
            and abs(last["_top"] - w["top"]) <= 2
            and last["_style"] == style
            and w["x0"] - last["_x1"] <= w["size"] * 1.5
        ):
            last["text"] += " " + w["text"]
            last["_x1"] = w["x1"]
            continue
        segments.append(
            {
                "text": w["text"],
                "size": style[0],
                "top": w["top"] / page.height,
                "colored": style[1],
                "bold": style[2],
                "_top": w["top"],
                "_x1": w["x1"],
                "_style": style,
            }
        )
    return segments


def _enhance(image: Image.Image) -> Image.Image:
    """Clean-up for faint scans / phone photos: greyscale, auto-contrast, sharpen, enlarge."""
    from PIL import ImageFilter

    img = ImageOps.autocontrast(ImageOps.grayscale(image), cutoff=2)
    if max(img.size) < 1800:
        factor = 1800 / max(img.size)
        img = img.resize((int(img.width * factor), int(img.height * factor)), Image.LANCZOS)
    return img.filter(ImageFilter.UnsharpMask(radius=2, percent=160, threshold=3)).convert("RGB")


def _ocr_best(image: Image.Image) -> tuple[list[str], float | None, list[dict]]:
    """OCR; if the result is weak, retry on an enhanced copy and keep the better reading.
    Headings come from the original image either way: the enhanced copy is greyscale."""
    lines, conf, segments = _ocr_lines(image)
    chars = sum(len(ln) for ln in lines)
    if conf is not None and (conf >= 0.8 and chars >= 40):
        return lines, conf, segments
    lines2, conf2, _ = _ocr_lines(_enhance(image))

    def score(ls: list[str], c: float | None) -> float:
        return (c or 0) * min(1.0, sum(len(x) for x in ls) / 300) ** 0.5

    if score(lines2, conf2) > score(lines, conf):
        return lines2, conf2, segments
    return lines, conf, segments


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


def _pdf_text_lines(data: bytes, max_pages: int) -> tuple[list[list[str]], list[dict]]:
    """Text lines per page, plus the styled text runs of page 1 (for headings)."""
    import pdfplumber

    pages: list[list[str]] = []
    first_page_segments: list[dict] = []
    with pdfplumber.open(io.BytesIO(data)) as pdf:
        for idx, page in enumerate(pdf.pages[:max_pages]):
            text = page.extract_text(x_tolerance=1.5, y_tolerance=3, keep_blank_chars=False) or ""
            pages.append([ln.rstrip() for ln in text.splitlines() if ln.strip()])
            if idx == 0:
                try:
                    first_page_segments = _pdf_segments(page)
                except Exception:  # noqa: BLE001 - styling is a hint; never fail the read
                    log.debug("PDF heading detection failed", exc_info=True)
    return pages, first_page_segments


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
            text_pages, pdf_segments = _pdf_text_lines(data, max_pages)
        except Exception:  # noqa: BLE001
            log.warning("PDF text extraction failed", exc_info=True)
            text_pages, pdf_segments = [], []
            res.warnings.append("Couldn't read the PDF's text layer; used OCR instead")
        images = _pdf_images(data, max_pages)
        res.pages = len(images)
        confs = []
        for idx, image in enumerate(images):
            page_text = text_pages[idx] if idx < len(text_pages) else []
            if sum(len(t) for t in page_text) >= MIN_TEXT_CHARS:
                res.lines.extend(page_text)
                if idx == 0:
                    res.headings = _rank_headings(pdf_segments)
                if "pdf-text" not in res.method:
                    res.method.append("pdf-text")
            else:
                lines, conf, segments = _ocr_best(image)
                res.lines.extend(lines)
                if idx == 0:
                    res.headings = _rank_headings(segments)
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
        res.lines, res.ocr_confidence, segments = _ocr_best(image)
        res.headings = _rank_headings(segments)
        res.method.append("ocr")
    if res.qr_payloads:
        res.method.append("qr")
    if not res.lines:
        res.warnings.append("No readable text found on the bill")
    return res
