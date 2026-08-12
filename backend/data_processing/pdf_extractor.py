# pdf_extractor.py
"""多格式论文解析与视觉资产裁剪工具。

支持：PDF、DOCX、TeX、CNKI CAJ（CAJ 需要本机安装 caj2pdf 或同名 CLI）。
视觉资产裁剪依赖 academic_api.detect_visual_regions_via_qwen，即只使用 QWEN-VL 系列模型
识别图片/表格/图表等区域，再由本地代码完成裁剪输出。
"""
from __future__ import annotations

import os
import re
import shutil
import subprocess
import tempfile
import unicodedata
from pathlib import Path
from typing import Iterable

import fitz  # PyMuPDF
from PIL import Image

IMAGE_EXTENSIONS = {".png", ".jpg", ".jpeg", ".bmp", ".tif", ".tiff", ".webp"}
SUPPORTED_EXTENSIONS = {".pdf", ".docx", ".tex", ".caj"} | IMAGE_EXTENSIONS
TEXT_ANCHOR_SCHEMA_VERSION = "text_anchor_v1"


# -----------------------------------------------------------------------------
# 文本清洗
# -----------------------------------------------------------------------------
def clean_extracted_text(text: str) -> str:
    """
    对提取出的论文纯文本进行基础清洗：
    1. 统一换行符与 Unicode 兼容字符；
    2. 去除控制字符、空字节、常见提取乱码；
    3. 修复英文断行连字符、压缩多余空白；
    4. 保留中文、英文、标点和必要段落换行。
    """
    if not text:
        return ""

    text = text.replace("\r\n", "\n").replace("\r", "\n").replace("\ufeff", "")
    text = unicodedata.normalize("NFKC", text)

    # 常见 PDF 连字。
    ligatures = {
        "ﬁ": "fi",
        "ﬂ": "fl",
        "ﬀ": "ff",
        "ﬃ": "ffi",
        "ﬄ": "ffl",
    }
    for old, new in ligatures.items():
        text = text.replace(old, new)

    # 可选使用 ftfy 修复 mojibake；未安装也不影响。
    try:
        from ftfy import fix_text  # type: ignore

        text = fix_text(text)
    except Exception:
        pass

    # 删除控制字符，但保留 \n / \t。
    text = "".join(
        ch
        for ch in text
        if ch in "\n\t" or not unicodedata.category(ch).startswith("C")
    )

    # 英文 PDF 常见断词：informa-\ntion -> information。
    text = re.sub(r"(?<=[A-Za-z])[-‐‑‒–—]\n(?=[A-Za-z])", "", text)
    # 中文段内硬换行：两侧都是中日韩字符时合并。
    text = re.sub(r"(?<=[\u4e00-\u9fff])\n(?=[\u4e00-\u9fff])", "", text)
    # 英文段内单换行转空格，保留空行作为段落分隔。
    text = re.sub(r"(?<!\n)\n(?!\n)", "\n", text)

    text = re.sub(r"[ \t]{2,}", " ", text)
    text = re.sub(r"\n[ \t]+", "\n", text)
    text = re.sub(r"\n{3,}", "\n\n", text)
    lines = [line.strip() for line in text.split("\n")]
    return "\n".join(lines).strip()


def _read_text_file_with_fallback(path: str | Path) -> str:
    path = Path(path)
    for encoding in ("utf-8", "utf-8-sig", "gb18030", "big5", "latin-1"):
        try:
            return path.read_text(encoding=encoding)
        except UnicodeDecodeError:
            continue
    return path.read_text(errors="ignore")


def _normalise_bbox_to_1000(bbox: list[float], width: float, height: float) -> list[float] | None:
    if width <= 0 or height <= 0:
        return None
    x1, y1, x2, y2 = bbox
    return [
        round(max(0.0, min(1000.0, x1 / width * 1000.0)), 2),
        round(max(0.0, min(1000.0, y1 / height * 1000.0)), 2),
        round(max(0.0, min(1000.0, x2 / width * 1000.0)), 2),
        round(max(0.0, min(1000.0, y2 / height * 1000.0)), 2),
    ]


def _build_text_from_anchors(anchors: list[dict], separator: str = "\n\n") -> tuple[str, list[dict]]:
    """
    用锚点文本生成全文，并把每个锚点映射到全文字符区间。

    后续风险条目只要能匹配到 full_text 中的一段证据文本，就可以通过
    char_start/char_end 反查页码、PDF 坐标或 Word 段落位置。
    """
    anchored: list[dict] = []
    parts: list[str] = []
    cursor = 0

    for anchor in anchors:
        text = clean_extracted_text(str(anchor.get("text") or ""))
        if not text:
            continue

        if parts:
            cursor += len(separator)
        start = cursor
        end = start + len(text)

        next_anchor = dict(anchor)
        next_anchor["text"] = text
        next_anchor["char_start"] = start
        next_anchor["char_end"] = end
        next_anchor["text_preview"] = text[:240]
        anchored.append(next_anchor)
        parts.append(text)
        cursor = end

    return separator.join(parts), anchored


# -----------------------------------------------------------------------------
# PDF 解析
# -----------------------------------------------------------------------------
def extract_pdf_with_anchors(pdf_path: str) -> tuple[str, str, list[dict]]:
    """读取 PDF 文本块，并保留页码、页面坐标和全文字符区间。"""
    try:
        raw_anchors: list[dict] = []
        with fitz.open(pdf_path) as doc:
            for page_index, page in enumerate(doc):
                page_number = page_index + 1
                page_width = float(page.rect.width)
                page_height = float(page.rect.height)
                block_number = 0

                for block in page.get_text("blocks", sort=True):
                    block_type = int(block[6]) if len(block) > 6 else 0
                    if block_type != 0:
                        continue

                    text = clean_extracted_text(block[4])
                    if not text:
                        continue

                    block_number += 1
                    bbox = [round(float(v), 2) for v in block[:4]]
                    raw_anchors.append(
                        {
                            "anchor_id": f"pdf_p{page_number:04d}_b{block_number:04d}",
                            "source_type": "pdf_text_block",
                            "page": page_number,
                            "page_index": page_index,
                            "block_index": block_number,
                            "paragraph_index": block_number,
                            "page_width": round(page_width, 2),
                            "page_height": round(page_height, 2),
                            "bbox": bbox,
                            "bbox_units": "pdf_points",
                            "bbox_norm": _normalise_bbox_to_1000(bbox, page_width, page_height),
                            "text": text,
                        }
                    )

        full_text, anchors = _build_text_from_anchors(raw_anchors)
        header_text = clean_extracted_text("\n\n".join(anchor["text"] for anchor in anchors if (anchor.get("page") or 0) <= 2))
        return full_text, header_text, anchors

    except Exception as e:
        raise RuntimeError(f"PDF 文件读取或解析失败: {str(e)}") from e


def extract_pdf_to_text(pdf_path: str) -> tuple[str, str]:
    """
    读取 PDF 文件并提取纯文本。
    返回:
        full_text: 清洗后的全文纯文本
        header_text: 前2页的文本（用于快速提取标题和作者，减少大模型 Token 消耗）
    """
    full_text, header_text, _anchors = extract_pdf_with_anchors(pdf_path)
    return full_text, header_text


# -----------------------------------------------------------------------------
# DOCX 解析
# -----------------------------------------------------------------------------
def _iter_docx_blocks(document) -> Iterable[object]:
    from docx.oxml.table import CT_Tbl
    from docx.oxml.text.paragraph import CT_P
    from docx.table import Table
    from docx.text.paragraph import Paragraph

    parent_elm = document.element.body
    for child in parent_elm.iterchildren():
        if isinstance(child, CT_P):
            yield Paragraph(child, document)
        elif isinstance(child, CT_Tbl):
            yield Table(child, document)


def _docx_table_to_text(table) -> str:
    rows: list[str] = []
    for row in table.rows:
        cells = [clean_extracted_text(cell.text).replace("\n", " ") for cell in row.cells]
        rows.append("\t".join(cells))
    return "\n".join(rows)


def extract_docx_with_anchors(docx_path: str) -> tuple[str, str, list[dict]]:
    """读取 DOCX 段落与表格文本，并保留段落/表格顺序锚点。"""
    try:
        from docx import Document

        document = Document(docx_path)
        raw_anchors: list[dict] = []
        block_index = 0
        paragraph_index = 0
        table_index = 0

        for block in _iter_docx_blocks(document):
            block_index += 1
            if hasattr(block, "text"):
                text = clean_extracted_text(block.text)
                if text:
                    paragraph_index += 1
                    style_name = ""
                    try:
                        style_name = block.style.name or ""
                    except Exception:
                        style_name = ""
                    raw_anchors.append(
                        {
                            "anchor_id": f"docx_p{paragraph_index:04d}",
                            "source_type": "docx_paragraph",
                            "page": None,
                            "block_index": block_index,
                            "paragraph_index": paragraph_index,
                            "style": style_name,
                            "bbox": None,
                            "bbox_units": None,
                            "bbox_norm": None,
                            "text": text,
                        }
                    )
            elif hasattr(block, "rows"):
                table_index += 1
                table_text = _docx_table_to_text(block)
                if table_text.strip():
                    row_count = len(block.rows)
                    col_count = len(block.rows[0].cells) if row_count else 0
                    raw_anchors.append(
                        {
                            "anchor_id": f"docx_t{table_index:04d}",
                            "source_type": "docx_table",
                            "page": None,
                            "block_index": block_index,
                            "table_index": table_index,
                            "row_count": row_count,
                            "column_count": col_count,
                            "bbox": None,
                            "bbox_units": None,
                            "bbox_norm": None,
                            "text": "[TABLE]\n" + table_text,
                        }
                    )

        full_text, anchors = _build_text_from_anchors(raw_anchors)
        header_text = clean_extracted_text("\n\n".join(anchor["text"] for anchor in anchors[:40]))
        return full_text, header_text, anchors
    except Exception as e:
        raise RuntimeError(f"DOCX 文件读取或解析失败: {str(e)}") from e


def extract_docx_to_text(docx_path: str) -> tuple[str, str]:
    """读取 DOCX 段落与表格文本。"""
    full_text, header_text, _anchors = extract_docx_with_anchors(docx_path)
    return full_text, header_text


# -----------------------------------------------------------------------------
# TeX 解析
# -----------------------------------------------------------------------------
def _strip_tex_comments(text: str) -> str:
    # 删除未转义的 % 注释。
    return re.sub(r"(?<!\\)%.*", "", text)


def _tex_to_plain_text(tex: str) -> str:
    tex = _strip_tex_comments(tex)
    tex = re.sub(r"\\begin\{(?:figure|table|equation|align|lstlisting|algorithm).*?\\end\{.*?\}", "\n", tex, flags=re.DOTALL)
    tex = re.sub(r"\\(?:title|author|date|section|subsection|subsubsection|paragraph)\*?\{([^{}]*)\}", r"\n\1\n", tex)
    tex = re.sub(r"\\(?:textbf|textit|emph|underline|caption)\{([^{}]*)\}", r"\1", tex)
    tex = re.sub(r"\\cite\w*\{[^{}]*\}", "", tex)
    tex = re.sub(r"\\ref\{([^{}]*)\}", r"\1", tex)
    tex = re.sub(r"\\url\{([^{}]*)\}", r"\1", tex)
    tex = re.sub(r"\\[a-zA-Z]+\*?(?:\[[^\]]*\])?(?:\{[^{}]*\})?", " ", tex)
    tex = re.sub(r"[{}$]", " ", tex)
    return clean_extracted_text(tex)


def extract_tex_to_text(tex_path: str) -> tuple[str, str]:
    """读取 LaTeX 源文件并提取近似纯文本。"""
    try:
        raw = _read_text_file_with_fallback(tex_path)
        full_text = _tex_to_plain_text(raw)
        header_text = clean_extracted_text("\n".join(full_text.splitlines()[:80]))
        return full_text, header_text
    except Exception as e:
        raise RuntimeError(f"TeX 文件读取或解析失败: {str(e)}") from e


# -----------------------------------------------------------------------------
# 图片解析
# -----------------------------------------------------------------------------
def extract_image_to_text(image_path: str) -> tuple[str, str, dict]:
    """用 QWEN-VL 解析单张图片，返回可合并进标准 JSON 的 OCR 文本和描述。"""
    analysis = {
        "title": "",
        "abstract": "",
        "keywords": [],
        "ocr_text": "",
        "description": "",
        "caption": "",
        "open_data": False,
        "open_code": False,
    }
    if os.getenv("DASHSCOPE_API_KEY"):
        try:
            from academic_api import analyze_image_via_qwen

            analysis = analyze_image_via_qwen(image_path)
        except Exception as e:
            print(f"[Warning] 图片解析失败: {str(e)}")
    else:
        print("[Warning] 未配置 DASHSCOPE_API_KEY，图片来源无法调用 QWEN-VL 做 OCR/描述。")

    chunks = [analysis.get("title", ""), analysis.get("abstract", ""), analysis.get("ocr_text", ""), analysis.get("description", ""), analysis.get("caption", "")]
    full_text = clean_extracted_text("\n\n".join([c for c in chunks if c]))
    header_text = clean_extracted_text("\n\n".join([analysis.get("title", ""), analysis.get("abstract", ""), analysis.get("ocr_text", "")]))
    return full_text, header_text, analysis

# -----------------------------------------------------------------------------
# CAJ / 格式分发
# -----------------------------------------------------------------------------
def convert_caj_to_pdf(caj_path: str | Path, output_dir: str | Path | None = None) -> Path:
    """
    将 CNKI CAJ 转换为 PDF。

    由于 CAJ 是知网专有格式，纯 Python 稳定解析很困难；这里支持本机已安装的
    caj2pdf 命令行工具。常见用法会依次尝试：
      1) caj2pdf convert input.caj -o output.pdf
      2) caj2pdf input.caj output.pdf
    """
    caj_path = Path(caj_path)
    output_root = (Path(output_dir) if output_dir else caj_path.parent).resolve()
    output_root.mkdir(parents=True, exist_ok=True)
    output_pdf = output_root / f"{caj_path.stem}.converted.pdf"

    caj2pdf = shutil.which("caj2pdf")
    if not caj2pdf:
        raise RuntimeError(
            "检测到 CAJ 文件，但未找到 caj2pdf 命令。请先安装/配置 caj2pdf，"
            "或先将 CAJ 手动另存为 PDF 后再处理。"
        )

    commands = [
        [caj2pdf, "convert", str(caj_path), "-o", str(output_pdf)],
        [caj2pdf, str(caj_path), str(output_pdf)],
    ]
    last_error = ""
    for cmd in commands:
        try:
            result = subprocess.run(cmd, capture_output=True, text=True, timeout=120)
            if result.returncode == 0 and output_pdf.exists() and output_pdf.stat().st_size > 0:
                return output_pdf
            last_error = (result.stderr or result.stdout or "").strip()
        except Exception as e:
            last_error = str(e)

    raise RuntimeError(f"CAJ 转 PDF 失败: {last_error}")


def extract_document_to_text(
    input_path: str,
    output_dir: str | Path | None = None,
) -> tuple[str, str, dict]:
    """
    多格式文本入口。返回 full_text, header_text, source_info。
    """
    path = Path(input_path)
    suffix = path.suffix.lower()
    if suffix not in SUPPORTED_EXTENSIONS:
        raise ValueError(f"暂不支持的文件格式: {suffix}，当前支持: {sorted(SUPPORTED_EXTENSIONS)}")

    source_info: dict = {
        "path": str(path.resolve()),
        "format": suffix.lstrip("."),
        "converted_pdf": None,
        "image_analysis": None,
        "text_anchor_schema": TEXT_ANCHOR_SCHEMA_VERSION,
        "text_anchors": [],
    }

    if suffix == ".pdf":
        full_text, header_text, text_anchors = extract_pdf_with_anchors(str(path))
        source_info["text_anchors"] = text_anchors
    elif suffix == ".docx":
        full_text, header_text, text_anchors = extract_docx_with_anchors(str(path))
        source_info["text_anchors"] = text_anchors
    elif suffix == ".tex":
        full_text, header_text = extract_tex_to_text(str(path))
    elif suffix == ".caj":
        pdf_path = convert_caj_to_pdf(path, output_dir=output_dir)
        source_info["converted_pdf"] = str(pdf_path.resolve())
        full_text, header_text, text_anchors = extract_pdf_with_anchors(str(pdf_path))
        source_info["text_anchors"] = text_anchors
    elif suffix in IMAGE_EXTENSIONS:
        full_text, header_text, image_analysis = extract_image_to_text(str(path))
        source_info["image_analysis"] = image_analysis
    else:  # pragma: no cover
        raise ValueError(f"暂不支持的文件格式: {suffix}")

    return full_text, header_text, source_info


# -----------------------------------------------------------------------------
# 页面渲染与 QWEN-VL 裁剪
# -----------------------------------------------------------------------------
def convert_docx_to_pdf(docx_path: str | Path, output_dir: str | Path) -> Path:
    """优先使用 LibreOffice/soffice 将 DOCX 转 PDF，以便进行版面级视觉裁剪。"""
    docx_path = Path(docx_path)
    output_dir = Path(output_dir).resolve()
    output_dir.mkdir(parents=True, exist_ok=True)

    soffice = shutil.which("soffice") or shutil.which("libreoffice")
    if not soffice:
        raise RuntimeError("未找到 LibreOffice/soffice，无法将 DOCX 渲染为 PDF 进行视觉裁剪。")

    cmd = [
        soffice,
        "--headless",
        "--convert-to",
        "pdf",
        "--outdir",
        str(output_dir),
        str(docx_path),
    ]
    result = subprocess.run(cmd, capture_output=True, text=True, timeout=180)
    pdf_path = output_dir / f"{docx_path.stem}.pdf"
    if result.returncode != 0 or not pdf_path.exists():
        raise RuntimeError(f"DOCX 转 PDF 失败: {(result.stderr or result.stdout).strip()}")
    return pdf_path


def compile_tex_to_pdf(tex_path: str | Path, output_dir: str | Path) -> Path:
    """使用本机 xelatex/pdflatex 编译 TeX；若同名 PDF 已存在，优先复用。"""
    tex_path = Path(tex_path)
    existing_pdf = tex_path.with_suffix(".pdf")
    if existing_pdf.exists():
        return existing_pdf

    output_dir = Path(output_dir).resolve()
    output_dir.mkdir(parents=True, exist_ok=True)
    engine = shutil.which("xelatex") or shutil.which("pdflatex")
    if not engine:
        raise RuntimeError("未找到 xelatex/pdflatex，无法将 TeX 编译为 PDF 进行视觉裁剪。")

    cmd = [
        engine,
        "-interaction=nonstopmode",
        "-halt-on-error",
        "-output-directory",
        str(output_dir),
        str(tex_path),
    ]
    # 编译两次，尽量补齐交叉引用。
    for _ in range(2):
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=180, cwd=str(tex_path.parent))
        if result.returncode != 0:
            raise RuntimeError(f"TeX 编译失败: {(result.stderr or result.stdout)[-2000:]}")
    pdf_path = output_dir / f"{tex_path.stem}.pdf"
    if not pdf_path.exists():
        raise RuntimeError("TeX 编译完成但未找到输出 PDF。")
    return pdf_path


def ensure_pdf_for_visual_extraction(input_path: str | Path, output_dir: str | Path) -> Path:
    """为任意支持格式准备可渲染 PDF。"""
    path = Path(input_path)
    suffix = path.suffix.lower()
    if suffix == ".pdf":
        return path
    if suffix == ".caj":
        return convert_caj_to_pdf(path, output_dir=output_dir)
    if suffix == ".docx":
        return convert_docx_to_pdf(path, output_dir=output_dir)
    if suffix == ".tex":
        return compile_tex_to_pdf(path, output_dir=output_dir)
    raise ValueError(f"暂不支持为该格式渲染页面: {suffix}")


def render_pdf_pages(
    pdf_path: str | Path,
    output_dir: str | Path,
    dpi: int = 180,
    max_pages: int | None = None,
    page_numbers: list[int] | None = None,
) -> list[dict]:
    """将 PDF 页面渲染为 PNG，返回 page/page_image/width/height。"""
    pdf_path = Path(pdf_path)
    output_dir = Path(output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)

    pages: list[dict] = []
    zoom = dpi / 72.0
    matrix = fitz.Matrix(zoom, zoom)
    with fitz.open(pdf_path) as doc:
        if page_numbers:
            page_indexes = sorted({number - 1 for number in page_numbers if 1 <= number <= len(doc)})
        else:
            total_pages = len(doc) if max_pages is None else min(len(doc), max_pages)
            page_indexes = range(total_pages)
        for page_index in page_indexes:
            page = doc[page_index]
            pix = page.get_pixmap(matrix=matrix, alpha=False)
            page_image = output_dir / f"page_{page_index + 1:03d}.png"
            pix.save(str(page_image))
            pages.append(
                {
                    "page": page_index + 1,
                    "page_image_path": str(page_image.resolve()),
                    "width": pix.width,
                    "height": pix.height,
                }
            )
    return pages


def _safe_asset_name(value: str, default: str = "asset") -> str:
    value = re.sub(r"[^\w\u4e00-\u9fff.-]+", "_", value.strip(), flags=re.UNICODE)
    value = value.strip("._")
    return value[:60] or default


def crop_regions_from_page(
    page_info: dict,
    regions: list[dict],
    output_dir: str | Path,
    margin_px: int = 8,
) -> list[dict]:
    """根据 QWEN-VL 返回的 0-1000 归一化 bbox 裁剪页面。"""
    output_dir = Path(output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)

    image_path = Path(page_info["page_image_path"])
    with Image.open(image_path) as img:
        width, height = img.size
        cropped_assets: list[dict] = []
        for idx, region in enumerate(regions, start=1):
            x1, y1, x2, y2 = region["bbox"]
            left = max(0, int(width * x1 / 1000) - margin_px)
            top = max(0, int(height * y1 / 1000) - margin_px)
            right = min(width, int(width * x2 / 1000) + margin_px)
            bottom = min(height, int(height * y2 / 1000) + margin_px)
            if right <= left or bottom <= top:
                continue

            label = _safe_asset_name(region.get("label", ""), default=f"region_{idx:02d}")
            asset_id = f"p{page_info['page']:03d}_{idx:02d}_{region.get('type', 'other')}"
            crop_path = output_dir / f"{asset_id}_{label}.png"
            img.crop((left, top, right, bottom)).save(crop_path)

            cropped_assets.append(
                {
                    "id": asset_id,
                    "type": region.get("type", "other"),
                    "page": page_info["page"],
                    "label": region.get("label", ""),
                    "caption": region.get("caption", ""),
                    "confidence": region.get("confidence", 0.0),
                    "bbox_norm": region.get("bbox"),
                    "bbox_pixels": [left, top, right, bottom],
                    "crop_path": str(crop_path.resolve()),
                    "page_image_path": str(image_path.resolve()),
                }
            )
    return cropped_assets


def extract_visual_assets(
    input_path: str,
    output_dir: str | Path | None = None,
    dpi: int = 180,
    max_pages: int | None = None,
    page_numbers: list[int] | None = None,
) -> list[dict]:
    """
    使用 QWEN-VL 检测并裁剪论文中的图片、表格等视觉资产。

    注意：如果没有 DASHSCOPE_API_KEY，函数会跳过并返回空列表。
    """
    if not os.getenv("DASHSCOPE_API_KEY"):
        print("[Warning] 未配置 DASHSCOPE_API_KEY，跳过 QWEN-VL 视觉资产裁剪。")
        return []

    from academic_api import detect_visual_regions_via_qwen

    input_path_obj = Path(input_path)
    root = (Path(output_dir) if output_dir else Path("outputs") / f"{input_path_obj.stem}_assets").resolve()
    root.mkdir(parents=True, exist_ok=True)
    work_dir = root / "_work"
    page_dir = work_dir / "pages"
    crop_dir = root / "crops"
    page_dir.mkdir(parents=True, exist_ok=True)
    crop_dir.mkdir(parents=True, exist_ok=True)

    if input_path_obj.suffix.lower() in IMAGE_EXTENSIONS:
        from academic_api import analyze_image_via_qwen

        analysis = analyze_image_via_qwen(input_path_obj)
        safe_name = _safe_asset_name(input_path_obj.stem, default="image_input")
        crop_path = crop_dir / f"p001_01_figure_{safe_name}{input_path_obj.suffix.lower()}"
        shutil.copy2(input_path_obj, crop_path)
        return [
            {
                "id": "p001_01_figure",
                "type": "figure",
                "page": 1,
                "label": analysis.get("title") or input_path_obj.stem,
                "caption": analysis.get("caption", ""),
                "ocr_text": analysis.get("ocr_text", ""),
                "description": analysis.get("description", ""),
                "confidence": 1.0,
                "bbox_norm": [0, 0, 1000, 1000],
                "bbox_pixels": None,
                "crop_path": str(crop_path.resolve()),
                "page_image_path": str(input_path_obj.resolve()),
            }
        ]

    try:
        pdf_path = ensure_pdf_for_visual_extraction(input_path_obj, work_dir)
        page_infos = render_pdf_pages(
            pdf_path,
            page_dir,
            dpi=dpi,
            max_pages=max_pages,
            page_numbers=page_numbers,
        )
    except Exception as e:
        print(f"[Warning] 无法准备页面渲染，跳过视觉裁剪: {str(e)}")
        return []

    assets: list[dict] = []
    for page_info in page_infos:
        regions = detect_visual_regions_via_qwen(
            page_info["page_image_path"],
            page_number=page_info["page"],
        )
        if not regions:
            continue
        assets.extend(crop_regions_from_page(page_info, regions, crop_dir))

    return assets







