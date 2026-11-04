"""Page-level extraction. OCR runs locally; source documents are never rewritten."""
from dataclasses import asdict, dataclass, field
from functools import lru_cache
from pathlib import Path
from statistics import median, mean
import io
import re
import threading
import unicodedata

from pypdf import PdfReader


@dataclass
class ExtractedPage:
    page: int
    text: str
    method: str = 'text'
    warnings: list[str] = field(default_factory=list)
    confidence: float | None = None
    reviewed: bool = False

    def to_dict(self):
        return asdict(self)


_OCR_LOCK = threading.Lock()
_RENDER_LOCK = threading.Lock()
MODELS = Path(__file__).resolve().parent / 'data' / 'ocr-models'


@lru_cache(maxsize=1)
def ocr_engine():
    from rapidocr import RapidOCR, OCRVersion, ModelType, LangRec
    MODELS.mkdir(parents=True, exist_ok=True)
    return RapidOCR(params={
        'Global.model_root_dir': str(MODELS), 'Global.log_level': 'warning',
        'Global.text_score': 0.35,
        'Det.ocr_version': OCRVersion.PPOCRV5, 'Det.model_type': ModelType.MOBILE,
        'Rec.ocr_version': OCRVersion.PPOCRV5, 'Rec.model_type': ModelType.MOBILE,
        'Rec.lang_type': LangRec.EN,
        'EngineConfig.onnxruntime.intra_op_num_threads': 2,
        'EngineConfig.onnxruntime.inter_op_num_threads': 2,
    })


def render_pdf_page(content: bytes, number: int, max_side: int = 2600) -> bytes:
    import pypdfium2 as pdfium
    # PDFium is not thread safe. Close native resources before leaving the lock.
    with _RENDER_LOCK:
        pdf = pdfium.PdfDocument(content)
        try:
            if not 1 <= number <= len(pdf):
                raise ValueError('Page not found.')
            page = pdf[number - 1]
            try:
                width, height = page.get_size()
                if min(width, height) <= 0:
                    raise ValueError('Invalid page dimensions.')
                scale = min(3.1, max_side / max(width, height))
                bitmap = page.render(scale=scale)
                try:
                    image = bitmap.to_pil()
                    output = io.BytesIO()
                    image.save(output, format='PNG')
                    image.close()
                    return output.getvalue()
                finally:
                    bitmap.close()
            finally:
                page.close()
        finally:
            pdf.close()


def clean_text(text: str) -> str:
    text = unicodedata.normalize('NFC', text).replace('\x00', '').replace('\r\n', '\n')
    text = text.replace('\u00ad', '')
    return '\n'.join(line.rstrip() for line in text.splitlines()).strip()


def group_ocr_rows(records: list[dict]) -> list[dict]:
    """Join cells on the same baseline before sorting rows; never sort by x alone."""
    if not records:
        return []
    heights = [max(p[1] for p in item['box']) - min(p[1] for p in item['box']) for item in records]
    tolerance = max(3, median(heights) * 0.48)
    rows = []
    for item in sorted(records, key=lambda item: mean(p[1] for p in item['box'])):
        center = mean(p[1] for p in item['box'])
        if rows and abs(center - rows[-1]['y']) <= tolerance:
            rows[-1]['items'].append(item)
            rows[-1]['y'] = mean(mean(p[1] for p in it['box']) for it in rows[-1]['items'])
        else:
            rows.append({'y': center, 'items': [item]})
    for row in rows:
        row['items'].sort(key=lambda item: min(p[0] for p in item['box']))
    return rows


def ocr_to_markdown(records: list[dict]) -> str:
    rows = group_ocr_rows(records)
    output = []
    index = 0
    while index < len(rows):
        items = rows[index]['items']
        cells = [clean_text(item['text']).replace('|', '\\|') for item in items]
        combined = ' '.join(cells)
        # A numbered table needs both a multi-cell header and a numbered data row.
        next_cells = [it['text'].strip() for it in rows[index + 1]['items']] if index + 1 < len(rows) else []
        is_table = len(cells) >= 3 and next_cells and re.fullmatch(r'\d+[.)]?', next_cells[0]) and len(next_cells) >= 3
        if is_table:
            headers = cells
            table = []
            positions = [min(p[0] for p in item['box']) for item in items]
            cursor = index + 1
            while cursor < len(rows):
                row_items = rows[cursor]['items']
                values = [clean_text(it['text']).replace('|', '\\|') for it in row_items]
                if re.fullmatch(r'\d+[.)]?', values[0]) and len(values) >= 2:
                    aligned = [''] * len(headers)
                    for item, value in zip(row_items, values):
                        x = min(point[0] for point in item['box'])
                        column = min(range(len(positions)), key=lambda col: abs(x - positions[col]))
                        aligned[column] = (aligned[column] + ' ' + value).strip()
                    table.append(aligned)
                elif table and len(values) == 1 and min(p[0] for p in row_items[0]['box']) >= (positions[-2] + positions[-1]) / 2 and rows[cursor]['y'] - rows[cursor - 1]['y'] < 50:
                    table[-1][-1] += ' ' + values[0]
                else:
                    break
                cursor += 1
            output.extend(['', '| ' + ' | '.join(headers) + ' |', '| ' + ' | '.join(['---'] * len(headers)) + ' |'])
            output.extend('| ' + ' | '.join(values) + ' |' for values in table)
            output.append('')
            index = cursor
            continue
        if len(cells) == 1 and 4 < len(combined) < 110 and (combined.endswith(':') or (combined.isupper() and len(re.findall('[A-Z]', combined)) > 8)):
            output.extend(['', '## ' + combined.rstrip(':'), ''])
        elif len(cells) == 2 and re.search(r'\d[\d,./-]*', cells[-1]) and len(cells[-1]) < 32:
            output.append('- ' + cells[0].lstrip('•> ') + ': ' + cells[1].lstrip(': '))
        elif re.match(r'^Step\s*\d+\s*:', combined, re.I):
            match = re.match(r'^Step\s*(\d+)\s*:\s*(.*)', combined, re.I)
            output.append(match[1] + '. ' + match[2])
        else:
            output.append(combined)
        index += 1
    return re.sub(r'\n{3,}', '\n\n', '\n'.join(output)).strip()


def refine_long_lines(image_bytes: bytes, records: list[dict]) -> list[dict]:
    """Recognize long lines in word-boundary crops to avoid squeezed text."""
    import numpy as np
    from PIL import Image
    with Image.open(io.BytesIO(image_bytes)) as image:
        for record in records:
            xs = [point[0] for point in record['box']]
            ys = [point[1] for point in record['box']]
            left, top = max(0, int(min(xs))), max(0, int(min(ys)))
            right, bottom = min(image.width, int(max(xs)) + 1), min(image.height, int(max(ys)) + 1)
            width, height = right - left, bottom - top
            if height <= 0 or width / height < 25:
                continue
            crop = image.crop((left, top, right, bottom))
            gray = np.asarray(crop.convert('L'))
            ink = (gray < 170).sum(axis=0)
            pieces, start = [], 0
            step = max(280, height * 20)
            while width - start > step + height * 6:
                target = start + step
                low = max(start + height * 10, target - height * 3)
                high = min(width, target + height * 3)
                candidates = np.where(ink[low:high] <= max(1, height * .06))[0] + low
                cut = int(min(candidates, key=lambda value: abs(value - target))) if len(candidates) else target
                pieces.append(crop.crop((start, 0, cut, height)))
                start = cut
            pieces.append(crop.crop((start, 0, width, height)))
            texts, scores = [], []
            for piece in pieces:
                result = ocr_engine()(piece, use_det=False, use_cls=False, use_rec=True)
                if result.txts:
                    texts.extend(result.txts)
                    scores.extend(float(score) for score in result.scores)
                piece.close()
            crop.close()
            candidate = ' '.join(text.strip() for text in texts).strip()
            if candidate and len(candidate) >= max(3, len(record['text']) * .6) and scores and mean(scores) > record['score'] + .005:
                record['text'] = candidate
                record['score'] = mean(scores)
    return records


def ocr_page(content: bytes, number: int) -> ExtractedPage:
    image = render_pdf_page(content, number)
    with _OCR_LOCK:
        result = ocr_engine()(image)
        if result.txts is None or not len(result.txts):
            raise ValueError('OCR could not recognize text on this page.')
        records = [{'box': box.tolist(), 'text': text, 'score': float(score)} for box, text, score in zip(result.boxes, result.txts, result.scores)]
        records = refine_long_lines(image, records)
    confidence = mean(item['score'] for item in records)
    warnings = ['OCR text needs comparison with the original, especially amounts, dates, names and eligibility rules.']
    uncertain = sum(item['score'] < 0.85 for item in records)
    if uncertain:
        warnings.append(f'{uncertain} text regions have low recognition scores. The score is not a guarantee of accuracy.')
    warnings.append('English OCR was used. Other scripts and handwriting may be inaccurate.')
    return ExtractedPage(number, ocr_to_markdown(records), 'ocr', warnings, round(confidence, 4))


def has_scan_image(page) -> bool:
    resources = page.get('/Resources', {})
    resources = resources.get_object() if hasattr(resources, 'get_object') else resources
    objects = resources.get('/XObject', {})
    objects = objects.get_object() if hasattr(objects, 'get_object') else objects
    for obj in objects.values():
        obj = obj.get_object()
        if obj.get('/Subtype') == '/Image' and int(obj.get('/Width', 0)) * int(obj.get('/Height', 0)) > 700_000:
            return True
    return False


def extract_pdf(content: bytes, force_ocr: bool = False) -> list[ExtractedPage]:
    reader = PdfReader(io.BytesIO(content))
    if reader.is_encrypted and not reader.decrypt(''):
        raise ValueError('Use an unencrypted PDF.')
    if len(reader.pages) > 300:
        raise ValueError('Maximum 300 pages per document.')
    pages = []
    for number, page in enumerate(reader.pages, 1):
        text = clean_text(page.extract_text(extraction_mode='layout') or '')
        scan = has_scan_image(page)
        if force_ocr or scan:
            try:
                pages.append(ocr_page(content, number))
                continue
            except Exception:
                # Never silently accept a broken scan as trustworthy extracted text.
                if not text:
                    raise ValueError(f'OCR failed on page {number}. Try again after checking OCR dependencies and models.')
                pages.append(ExtractedPage(number, text, 'text-fallback', ['OCR was unavailable. This text layer may contain recognition errors; compare and correct it before approval.']))
        else:
            pages.append(ExtractedPage(number, text))
    if not any(page.text.strip() for page in pages):
        raise ValueError('No readable text was found. The PDF may be blank or unreadable.')
    return pages


def extract_document(content: bytes, suffix: str, force_ocr: bool = False) -> tuple[list[ExtractedPage], bytes, str]:
    if suffix == '.pdf':
        return extract_pdf(content, force_ocr), content, 'application/pdf'
    if suffix == '.docx':
        from docx import Document
        doc = Document(io.BytesIO(content))
        text = '\n\n'.join(p.text for p in doc.paragraphs) + '\n\n' + '\n'.join(' | '.join(c.text for c in row.cells) for table in doc.tables for row in table.rows)
        text = clean_text(text)
        if not text:
            raise ValueError('No readable text was found.')
        return [ExtractedPage(1, text)], text.encode(), 'text/plain'
    text = clean_text(content.decode('utf-8-sig'))
    if not text:
        raise ValueError('No readable text was found.')
    return [ExtractedPage(1, text)], content, 'text/plain'


def search_chunks(text: str, limit: int = 1800) -> list[str]:
    """Overlapping retrieval passages are separate from the full-page review text."""
    lines = []
    for line in text.splitlines():
        while len(line) > limit:
            cut = line.rfind(' ', 0, limit)
            cut = cut if cut > 0 else limit
            lines.append(line[:cut])
            line = line[cut:].lstrip()
        lines.append(line)
    chunks, buffer = [], []
    for line in lines:
        if len('\n'.join(buffer + [line])) > limit and buffer:
            chunks.append('\n'.join(buffer).strip())
            overlap = buffer[-2:]
            buffer = overlap if len('\n'.join(overlap + [line])) <= limit else []
        buffer.append(line)
    if '\n'.join(buffer).strip():
        chunks.append('\n'.join(buffer).strip())
    return chunks
