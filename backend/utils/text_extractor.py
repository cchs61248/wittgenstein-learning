"""
本地文件解析模組：將上傳的檔案轉換為純文字。
後端掌控文字抽取，不依賴 LLM Files API 作為主要教材來源。
"""
from pathlib import Path


def extract_text(filename: str, raw_bytes: bytes) -> str:
    """
    從檔案 bytes 抽取純文字。
    優先使用對應格式的 parser；若失敗，fallback 到 utf-8 decode。
    """
    suffix = Path(filename).suffix.lower()
    try:
        if suffix in (".txt", ".md"):
            return _extract_text_plain(raw_bytes)
        elif suffix == ".pdf":
            return _extract_pdf(raw_bytes)
        elif suffix == ".docx":
            return _extract_docx(raw_bytes)
        elif suffix == ".pptx":
            return _extract_pptx(raw_bytes)
        elif suffix in (".html", ".htm"):
            return _extract_html(raw_bytes)
        elif suffix == ".epub":
            return _extract_epub(raw_bytes)
        else:
            return _fallback_decode(raw_bytes)
    except Exception:
        return _fallback_decode(raw_bytes)


def _extract_text_plain(raw: bytes) -> str:
    for enc in ("utf-8", "utf-8-sig", "big5", "gb18030"):
        try:
            return raw.decode(enc)
        except (UnicodeDecodeError, LookupError):
            continue
    return raw.decode("utf-8", errors="replace")


def _extract_pdf(raw: bytes) -> str:
    import io
    try:
        import pdfplumber
        with pdfplumber.open(io.BytesIO(raw)) as pdf:
            pages = []
            for page in pdf.pages:
                text = page.extract_text(x_tolerance=2, y_tolerance=2)
                if text:
                    pages.append(text.strip())
            return "\n\n".join(pages)
    except ImportError:
        pass

    # fallback to pypdf
    import pypdf
    reader = pypdf.PdfReader(io.BytesIO(raw))
    pages = []
    for page in reader.pages:
        text = page.extract_text()
        if text:
            pages.append(text.strip())
    return "\n\n".join(pages)


def _extract_docx(raw: bytes) -> str:
    import io
    from docx import Document
    doc = Document(io.BytesIO(raw))
    parts = []
    for para in doc.paragraphs:
        text = para.text.strip()
        if not text:
            continue
        # 保留標題層級
        if para.style and para.style.name.startswith("Heading"):
            level = para.style.name.replace("Heading ", "").strip()
            try:
                hashes = "#" * int(level)
            except ValueError:
                hashes = "##"
            parts.append(f"{hashes} {text}")
        else:
            parts.append(text)
    return "\n\n".join(parts)


def _extract_pptx(raw: bytes) -> str:
    import io
    from pptx import Presentation
    prs = Presentation(io.BytesIO(raw))
    slides = []
    for i, slide in enumerate(prs.slides, 1):
        texts = []
        for shape in slide.shapes:
            if not shape.has_text_frame:
                continue
            for para in shape.text_frame.paragraphs:
                text = para.text.strip()
                if text:
                    texts.append(text)
        if texts:
            slides.append(f"[Slide {i}]\n" + "\n".join(texts))
    return "\n\n".join(slides)


def _extract_html(raw: bytes) -> str:
    from bs4 import BeautifulSoup
    soup = BeautifulSoup(raw, "lxml")
    for tag in soup(["script", "style", "head", "nav", "footer"]):
        tag.decompose()
    return soup.get_text(separator="\n", strip=True)


def _extract_epub(raw: bytes) -> str:
    import io
    import tempfile
    from pathlib import Path as _Path
    from bs4 import BeautifulSoup
    from ebooklib import epub, ITEM_DOCUMENT

    # ebooklib 只接受路徑（不接受 BytesIO），用臨時檔承接
    with tempfile.NamedTemporaryFile(suffix=".epub", delete=False) as tmp:
        tmp.write(raw)
        tmp_path = tmp.name
    try:
        book = epub.read_epub(tmp_path)
        parts: list[str] = []
        title = book.get_metadata("DC", "title")
        if title:
            parts.append(f"# {title[0][0]}")

        for item in book.get_items_of_type(ITEM_DOCUMENT):
            # 用 get_body_content() 只取 body 內容片段（HTML fragment），
            # 避免完整 XHTML 文件的 XML 宣告觸發 XMLParsedAsHTMLWarning
            body = item.get_body_content()
            if not body:
                continue
            soup = BeautifulSoup(body, "lxml")
            for tag in soup(["script", "style", "nav", "footer"]):
                tag.decompose()
            text = soup.get_text(separator="\n", strip=True)
            if text:
                parts.append(text)
        return "\n\n".join(parts)
    finally:
        try:
            _Path(tmp_path).unlink()
        except OSError:
            pass


def _fallback_decode(raw: bytes) -> str:
    for enc in ("utf-8", "utf-8-sig", "big5", "gb18030", "latin-1"):
        try:
            return raw.decode(enc)
        except (UnicodeDecodeError, LookupError):
            continue
    return raw.decode("utf-8", errors="replace")
