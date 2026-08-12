# data_processor.py
"""论文数据源头处理主入口。

目标：把 Word/DOCX、LaTeX、PDF、CAJ、图片等多种来源，在进入评价 Agent 前，
统一转换为标准 JSON。标准 JSON 字段对齐团队提供的 input.json / 最小版格式.json。
"""
from __future__ import annotations

import argparse
from collections import defaultdict
import json
import os
import re
import sys
import time
from pathlib import Path
from typing import Any

from academic_api import (
    evaluate_journal_level_via_qwen,
    extract_metadata_via_qwen,
    query_semantic_scholar_api,
)
from pdf_extractor import TEXT_ANCHOR_SCHEMA_VERSION, SUPPORTED_EXTENSIONS, extract_document_to_text, extract_visual_assets
from public_management_database import build_public_management_impact_context

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")
if hasattr(sys.stderr, "reconfigure"):
    sys.stderr.reconfigure(encoding="utf-8")


SECTION_ALIASES = {
    "引言": ["引言", "绪论", "问题提出", "研究背景", "introduction"],
    "文献综述": ["文献综述", "理论基础", "研究述评", "相关研究", "related work", "literature review"],
    "方法": ["方法", "研究方法", "研究设计", "资料来源", "数据来源", "模型", "方法设计", "method", "methodology", "approach"],
    "实验": ["实验", "实验结果", "实证分析", "案例分析", "结果", "评估", "experiment", "experiments", "result", "results", "evaluation"],
    "讨论": ["讨论", "政策建议", "治理启示", "discussion"],
    "结论": ["结论", "总结", "conclusion", "conclusions"],
}

SUBJECT_TOP_VALUES = {"人文学科", "纯理科", "交叉工科"}

PUBLIC_MANAGEMENT_KEYWORDS = [
    "公共管理", "公共行政", "行政管理", "公共政策", "政策执行", "政策工具", "政策评估",
    "政府治理", "国家治理", "基层治理", "社会治理", "城市治理", "应急管理", "公共服务",
    "公共部门", "政府绩效", "电子政务", "政务服务", "行政审批", "公共财政", "地方政府",
    "府际关系", "协同治理", "治理能力", "营商环境", "公共价值", "公共事务",
    "public administration", "public management", "public policy", "governance", "public service",
]

HUMANITIES_KEYWORDS = [
    "社会学", "心理学", "教育学", "法学", "政治学", "经济学", "管理学", "公共管理",
    "人文学科", "社会科学", "政策", "治理", "制度", "文化", "历史", "哲学",
]

SCIENCE_KEYWORDS = [
    "物理", "化学", "数学", "生物", "地理", "天文", "基础科学", "实验室", "分子",
    "protein", "physics", "chemistry", "biology", "mathematics", "theorem",
]

ENGINEERING_KEYWORDS = [
    "工程", "算法", "系统", "控制", "传感", "机器人", "芯片", "电路", "材料", "机械",
    "计算机", "人工智能", "神经网络", "深度学习", "优化算法", "仿真", "architecture",
    "engineering", "algorithm", "system", "sensor", "robot",
]

QUANTITATIVE_INDICATORS = [
    "量化", "定量", "问卷", "样本", "变量", "回归", "计量", "显著性", "p值", "p 值",
    "结构方程", "sem", "did", "psm", "logit", "probit", "tobit", "固定效应", "随机效应",
    "面板数据", "假设检验", "信度", "效度", "方差", "相关分析", "统计检验", "robustness",
    "regression", "survey", "sample size", "quantitative",
]

QUALITATIVE_INDICATORS = [
    "质性", "定性", "访谈", "深度访谈", "半结构化访谈", "案例研究", "单案例", "多案例",
    "田野", "参与观察", "过程追踪", "扎根理论", "编码", "主题分析", "文本分析", "档案资料",
    "政策文本", "内容分析", "qualitative", "interview", "case study", "grounded theory",
]

MIXED_METHODS_INDICATORS = [
    "混合方法", "混合研究", "定量与定性", "量化与质性", "问卷和访谈", "三角互证",
    "顺序解释", "顺序探索", "并行设计", "mixed methods", "triangulation",
]

def _now() -> str:
    return time.strftime("%Y-%m-%d %H:%M:%S")


def _default_review_context(review_context: dict | None = None) -> dict:
    base = {
        "target_domain": "",
        "evaluation_purpose": "",
        "selected_frontend_options": [],
        "retry_feedback": None,
    }
    if review_context:
        base.update(review_context)
    return base


def _safe_qwen_metadata(
    header_text: str,
    source_info: dict,
    api_key: str | None = None,
    base_url: str | None = None,
    model_name: str | None = None,
) -> dict:
    image_analysis = source_info.get("image_analysis") or {}
    if api_key or os.getenv("DASHSCOPE_API_KEY"):
        try:
            meta = extract_metadata_via_qwen(
                header_text,
                model=model_name,
                api_key=api_key,
                base_url=base_url,
            )
        except Exception as e:
            print(f"[Warning] QWEN 元数据提取不可用: {str(e)}")
            meta = {"title": "Unknown Title", "authors": [], "journal_candidate": ""}
    else:
        print("[Warning] 未配置 DASHSCOPE_API_KEY，跳过 QWEN 元数据提取。")
        meta = {"title": "Unknown Title", "authors": [], "journal_candidate": ""}

    if image_analysis.get("title") and meta.get("title") == "Unknown Title":
        meta["title"] = image_analysis.get("title")
    return meta


def _extract_between(text: str, start_patterns: list[str], end_patterns: list[str], max_chars: int = 2500) -> str:
    lowered = text.lower()
    start = -1
    for pat in start_patterns:
        match = re.search(pat, lowered, flags=re.IGNORECASE)
        if match:
            start = match.end()
            break
    if start < 0:
        return ""

    end = len(text)
    tail = lowered[start:]
    for pat in end_patterns:
        match = re.search(pat, tail, flags=re.IGNORECASE)
        if match:
            end = start + match.start()
            break
    return text[start:end].strip()[:max_chars]


def _extract_abstract(full_text: str, source_info: dict) -> str:
    image_analysis = source_info.get("image_analysis") or {}
    if image_analysis.get("abstract"):
        return image_analysis["abstract"]
    return _extract_between(
        full_text,
        [r"摘要\s*[:：]?", r"abstract\s*[:：]?"],
        [r"关键词\s*[:：]?", r"关键字\s*[:：]?", r"keywords?\s*[:：]?", r"\n\s*1[.、\s]", r"\n\s*引言", r"\n\s*introduction"],
        max_chars=1800,
    )


def _extract_keywords(full_text: str, source_info: dict) -> list[str]:
    image_analysis = source_info.get("image_analysis") or {}
    if isinstance(image_analysis.get("keywords"), list) and image_analysis["keywords"]:
        return [str(k).strip() for k in image_analysis["keywords"] if str(k).strip()]

    match = re.search(r"(?:关键词|关键字|keywords?)\s*[:：]\s*([^\n]{1,300})", full_text, flags=re.IGNORECASE)
    if not match:
        return []
    raw = match.group(1)
    parts = re.split(r"[;,，；、]\s*", raw)
    return [p.strip() for p in parts if p.strip()][:12]


def _anchor_ids_for_range(text_anchors: list[dict] | None, start: int | None, end: int | None) -> list[str]:
    if start is None or end is None or not text_anchors:
        return []

    anchor_ids: list[str] = []
    for anchor in text_anchors:
        try:
            anchor_start = int(anchor.get("char_start"))
            anchor_end = int(anchor.get("char_end"))
        except (TypeError, ValueError):
            continue
        if anchor_end > start and anchor_start < end and anchor.get("anchor_id"):
            anchor_ids.append(str(anchor["anchor_id"]))
    return anchor_ids


def _extract_references_info(full_text: str, text_anchors: list[dict] | None = None) -> dict:
    match = re.search(r"(?:参考文献|references)\s*\n?(.+)$", full_text, flags=re.IGNORECASE | re.DOTALL)
    if not match:
        return {"text": "", "char_start": None, "char_end": None, "source_anchor_ids": []}

    raw_references = match.group(1)
    leading_ws = len(raw_references) - len(raw_references.lstrip())
    trailing_ws = len(raw_references) - len(raw_references.rstrip())
    start = match.start(1) + leading_ws
    end = match.end(1) - trailing_ws
    return {
        "text": full_text[start:end].strip()[:12000],
        "char_start": start,
        "char_end": end,
        "source_anchor_ids": _anchor_ids_for_range(text_anchors, start, end),
    }


def _extract_references_text(full_text: str) -> str:
    return _extract_references_info(full_text).get("text", "")


def _guess_section_label(title: str) -> str | None:
    clean = re.sub(
        r"^(?:第\s*[0-9一二三四五六七八九十]+\s*章\s*|"
        r"[0-9IVXivx一二三四五六七八九十]+(?:\.[0-9]+)*[.、\s]*)",
        "",
        title,
    ).strip().lower()
    if any(value in clean for value in ("参考文献", "references")):
        return "参考文献"
    for standard, aliases in SECTION_ALIASES.items():
        if any(alias.lower() in clean for alias in aliases):
            return standard
    return None


def _split_sections(full_text: str, text_anchors: list[dict] | None = None) -> list[dict]:
    """用轻量规则切分章节，并保留章节覆盖到的源文本锚点。"""
    lines = full_text.splitlines()
    heading_candidates: list[tuple[int, str]] = []
    chapter_re = re.compile(
        r"^\s*第\s*[0-9一二三四五六七八九十]{1,3}\s*章\s+.{1,70}\s*$",
        flags=re.IGNORECASE,
    )
    heading_re = re.compile(
        r"^\s*(?:"
        r"第\s*[0-9一二三四五六七八九十]{1,3}\s*章\s+.{1,70}|"
        r"(?:[0-9]{1,2}|[一二三四五六七八九十]{1,3}|[IVXivx]{1,6})(?:\.[0-9]+)*[.、\s]+"
        r"(?:摘要|引言|绪论|问题提出|研究背景|文献综述|理论基础|研究述评|相关研究|"
        r"方法|研究方法|研究设计|资料来源|数据来源|模型|实证分析|案例分析|实验|实验结果|结果|讨论|政策建议|治理启示|结论|参考文献)|"
        r"摘要|引言|绪论|问题提出|研究背景|文献综述|理论基础|研究述评|相关研究|"
        r"方法|研究方法|研究设计|资料来源|数据来源|模型|实证分析|案例分析|实验|实验结果|结果|讨论|政策建议|治理启示|结论|参考文献|"
        r"abstract|introduction|related work|literature review|method|methodology|approach|experiment|experiments|results?|discussion|conclusions?|references"
        r")\s*$",
        flags=re.IGNORECASE,
    )
    has_numbered_chapters = any(
        chapter_re.match(line.strip())
        and line.strip().count(".") < 5
        and len(line.strip()) <= 90
        for line in lines
    )
    document_level_headings = {"摘要", "abstract", "结论", "conclusion", "conclusions", "参考文献", "references"}
    char_pos = 0
    for line in lines:
        stripped = line.strip()
        if has_numbered_chapters:
            is_heading = bool(chapter_re.match(stripped)) or stripped.lower() in document_level_headings
        else:
            is_heading = bool(heading_re.match(stripped))
        if 1 <= len(stripped) <= 90 and stripped.count(".") < 5 and is_heading:
            heading_candidates.append((char_pos, stripped))
        char_pos += len(line) + 1

    sections: list[dict] = []
    for idx, (start, title) in enumerate(heading_candidates):
        label = _guess_section_label(title)
        if not label:
            continue
        next_start = heading_candidates[idx + 1][0] if idx + 1 < len(heading_candidates) else len(full_text)
        raw_section = full_text[start + len(title):next_start]
        leading_ws = len(raw_section) - len(raw_section.lstrip())
        trailing_ws = len(raw_section) - len(raw_section.rstrip())
        section_start = start + len(title) + leading_ws
        section_end = next_start - trailing_ws
        section_text = full_text[section_start:section_end]
        if section_text:
            sections.append(
                {
                    "section_title": f"{label}：{title}" if title != label else label,
                    "section_category": label,
                    "section_text": section_text[:12000],
                    "char_start": section_start,
                    "char_end": section_end,
                    "source_anchor_ids": _anchor_ids_for_range(text_anchors, section_start, section_end),
                }
            )

    dedup: dict[str, dict] = {}
    for item in sections:
        dedup.setdefault(item["section_title"], item)
    return list(dedup.values())


def _contains_any(text: str, patterns: list[str]) -> bool:
    lowered = text.lower()
    return any(p.lower() in lowered for p in patterns)


def _normalise_compact_text(value: str) -> str:
    return re.sub(r"[^0-9a-zA-Z一-鿿]+", "", value or "").lower()


def _rough_similarity(left: str, right: str) -> float:
    left_norm = _normalise_compact_text(left)
    right_norm = _normalise_compact_text(right)
    if not left_norm or not right_norm:
        return 0.0
    if left_norm == right_norm:
        return 1.0
    if left_norm in right_norm or right_norm in left_norm:
        return min(len(left_norm), len(right_norm)) / max(len(left_norm), len(right_norm))
    left_bigrams = {left_norm[i:i + 2] for i in range(max(0, len(left_norm) - 1))}
    right_bigrams = {right_norm[i:i + 2] for i in range(max(0, len(right_norm) - 1))}
    if not left_bigrams or not right_bigrams:
        return 0.0
    return len(left_bigrams & right_bigrams) / len(left_bigrams | right_bigrams)


def _extract_evidence_snippet(text: str, needle: str, window: int = 140) -> str:
    if not text or not needle:
        return ""
    idx = text.lower().find(needle.lower())
    if idx < 0:
        return ""
    start = max(0, idx - window)
    end = min(len(text), idx + len(needle) + window)
    return text[start:end].replace("\n", " ").strip()


def _extract_doi(text: str) -> str:
    match = re.search(r"10\.\d{4,9}/[-._;()/:A-Z0-9]+", text or "", flags=re.IGNORECASE)
    return match.group(0).rstrip(".。;；,") if match else ""


def _extract_author_details(header_text: str, qwen_meta: dict) -> dict:
    raw_authors = qwen_meta.get("authors") if isinstance(qwen_meta.get("authors"), list) else []
    authors: list[str] = []
    for item in raw_authors:
        if isinstance(item, dict):
            name = str(item.get("name") or "").strip()
        else:
            name = str(item).strip()
        if name and name not in authors:
            authors.append(name)

    emails = sorted(set(re.findall(r"[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}", header_text or "")))
    orcids = sorted(set(re.findall(r"\b\d{4}-\d{4}-\d{4}-\d{3}[0-9X]\b", header_text or "")))
    affiliation_keywords = ["大学", "学院", "研究院", "研究所", "中心", "实验室", "University", "College", "School", "Institute", "Department", "Center"]
    affiliations: list[str] = []
    for line in (header_text or "").splitlines()[:90]:
        clean = re.sub(r"[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}", "", line).strip(" ;；,，")
        if 4 <= len(clean) <= 180 and any(keyword.lower() in clean.lower() for keyword in affiliation_keywords):
            if clean not in affiliations:
                affiliations.append(clean)
        if len(affiliations) >= 10:
            break

    return {
        "authors": [
            {
                "name": name,
                "email": emails[index] if index < len(emails) else "",
                "affiliations": affiliations[:3],
            }
            for index, name in enumerate(authors)
        ],
        "emails": emails,
        "orcids": orcids,
        "affiliations": affiliations,
    }


def _extract_journal_info(header_text: str, full_text: str, final_journal: str, journal_level: str, api_meta: dict) -> dict:
    combined = "\n".join([header_text or "", full_text[:3000] or ""])
    issn_match = re.search(r"\b\d{4}-\d{3}[0-9X]\b", combined, flags=re.IGNORECASE)
    volume_issue = ""
    vi_match = re.search(r"(?:Vol\.?|Volume|第)\s*([0-9]{1,4})\s*(?:卷|,)?\s*(?:No\.?|Issue|第)?\s*([0-9]{1,4})?\s*(?:期)?", combined, flags=re.IGNORECASE)
    if vi_match:
        volume_issue = vi_match.group(0).strip()
    year = api_meta.get("year")
    if not year:
        year_match = re.search(r"\b(19|20)\d{2}\b", combined)
        year = int(year_match.group(0)) if year_match else None
    return {
        "journal": final_journal,
        "journal_level": journal_level,
        "doi": _extract_doi(combined),
        "issn": issn_match.group(0) if issn_match else "",
        "volume_issue": volume_issue,
        "publication_year": year,
        "venue_source": "semantic_scholar" if api_meta.get("venue") else "qwen_or_rule",
    }


def _build_open_science_info(full_text: str, image_analysis: dict) -> dict:
    patterns = ["open data", "data availability", "数据可用性", "开放数据", "数据开放", "开放代码", "open code", "github.com", "osf.io", "figshare", "zenodo"]
    evidence = []
    for pattern in patterns:
        snippet = _extract_evidence_snippet(full_text, pattern)
        if snippet:
            evidence.append({"keyword": pattern, "snippet": snippet})
    urls = sorted(set(re.findall(r"https?://[^\s)）\]>]+", full_text or "")))[:20]
    return {
        "open_data": bool(image_analysis.get("open_data", False)) or any(item["keyword"] in {"open data", "data availability", "数据可用性", "开放数据", "数据开放"} for item in evidence),
        "open_code": bool(image_analysis.get("open_code", False)) or any(item["keyword"] in {"开放代码", "open code", "github.com"} for item in evidence),
        "evidence": evidence[:10],
        "urls": urls,
    }


def _split_reference_entries(references_text: str) -> list[str]:
    text = (references_text or "").strip()
    if not text:
        return []
    numbered = re.split(r"\n(?=\s*(?:\[\d{1,4}\]|\d{1,4}[.、])\s*)", text)
    parts = [part.strip() for part in numbered if len(part.strip()) >= 12]
    if len(parts) <= 1:
        parts = [line.strip() for line in text.splitlines() if len(line.strip()) >= 25]
    return parts[:500]


def _reference_key(entry_text: str) -> str:
    doi = _extract_doi(entry_text)
    if doi:
        return f"doi:{doi.lower()}"
    compact = _normalise_compact_text(re.sub(r"^\s*(?:\[\d+\]|\d+[.、])\s*", "", entry_text))
    return compact[:160]


def _extract_reference_entries(references_info: dict) -> list[dict]:
    entries: list[dict] = []
    for index, raw in enumerate(_split_reference_entries(references_info.get("text", "")), start=1):
        number_match = re.match(r"\s*(?:\[(\d{1,4})\]|(\d{1,4})[.、])", raw)
        number = int(number_match.group(1) or number_match.group(2)) if number_match else index
        years = re.findall(r"\b(19|20)\d{2}\b", raw)
        year = None
        year_match = re.search(r"\b(?:19|20)\d{2}\b", raw)
        if year_match:
            year = int(year_match.group(0))
        doi = _extract_doi(raw)
        cleaned = re.sub(r"\s+", " ", raw).strip()
        title_candidate = cleaned
        segments = [seg.strip() for seg in re.split(r"[。.]\s+", cleaned) if seg.strip()]
        if len(segments) >= 2:
            title_candidate = segments[1]
        entries.append(
            {
                "reference_id": f"ref_{index:04d}",
                "number": number,
                "raw_text": cleaned[:2000],
                "title_candidate": title_candidate[:300],
                "year": year,
                "doi": doi,
                "normalized_key": _reference_key(raw),
                "source_anchor_ids": references_info.get("source_anchor_ids", [])[:20],
                "source_anchor_count": len(references_info.get("source_anchor_ids", [])),
            }
        )
    return entries


def _expand_numeric_citation_marker(marker: str) -> list[int]:
    values: list[int] = []
    for part in re.split(r"[,，、]", marker):
        part = part.strip()
        if not part:
            continue
        if "-" in part:
            left, right = part.split("-", 1)
            if left.strip().isdigit() and right.strip().isdigit():
                start, end = int(left), int(right)
                if 0 < start <= end <= start + 20:
                    values.extend(range(start, end + 1))
        elif part.isdigit():
            values.append(int(part))
    return values[:30]


def _extract_inline_citations(full_text: str) -> list[dict]:
    citations: list[dict] = []
    for match in re.finditer(r"\[(\d{1,4}(?:\s*(?:[-,，、])\s*\d{1,4})*)\]", full_text or ""):
        citations.append(
            {
                "marker": match.group(0),
                "numbers": _expand_numeric_citation_marker(match.group(1)),
                "char_start": match.start(),
                "char_end": match.end(),
                "context": full_text[max(0, match.start() - 120): min(len(full_text), match.end() + 120)].replace("\n", " ").strip(),
            }
        )
        if len(citations) >= 500:
            return citations
    author_year_pattern = r"[（(][^()（）]{0,90}(?:19|20)\d{2}[a-z]?[^()（）]{0,40}[)）]"
    for match in re.finditer(author_year_pattern, full_text or "", flags=re.IGNORECASE):
        citations.append(
            {
                "marker": match.group(0),
                "numbers": [],
                "char_start": match.start(),
                "char_end": match.end(),
                "context": full_text[max(0, match.start() - 120): min(len(full_text), match.end() + 120)].replace("\n", " ").strip(),
            }
        )
        if len(citations) >= 500:
            break
    return citations


def _build_citation_network(full_text: str, references_entries: list[dict], paper_title: str) -> dict:
    inline_citations = _extract_inline_citations(full_text)
    ref_by_number = {entry.get("number"): entry for entry in references_entries}
    nodes = [{"node_id": "paper_current", "type": "source_paper", "title": paper_title or "Unknown Title"}]
    nodes.extend(
        {
            "node_id": entry["reference_id"],
            "type": "reference",
            "title": entry.get("title_candidate", ""),
            "year": entry.get("year"),
            "doi": entry.get("doi", ""),
        }
        for entry in references_entries[:300]
    )
    edges: list[dict] = []
    seen_edges: set[tuple[str, str, str, int]] = set()
    for citation in inline_citations:
        targets: list[dict] = []
        for number in citation.get("numbers", []):
            if number in ref_by_number:
                targets.append(ref_by_number[number])
        if not targets and citation.get("marker"):
            marker_norm = _normalise_compact_text(citation["marker"])
            marker_year_match = re.search(r"(19|20)\d{2}", citation["marker"])
            marker_year = marker_year_match.group(0) if marker_year_match else ""
            for entry in references_entries:
                raw_norm = _normalise_compact_text(entry.get("raw_text", ""))
                if marker_year and marker_year in str(entry.get("year") or "") and any(token and token in raw_norm for token in re.findall(r"[a-zA-Z一-鿿]{2,}", marker_norm)):
                    targets.append(entry)
                    break
        for target in targets[:5]:
            key = ("paper_current", target["reference_id"], citation["marker"], citation["char_start"])
            if key in seen_edges:
                continue
            seen_edges.add(key)
            edges.append(
                {
                    "source": "paper_current",
                    "target": target["reference_id"],
                    "relation": "cites",
                    "citation_marker": citation["marker"],
                    "char_start": citation["char_start"],
                    "char_end": citation["char_end"],
                    "context": citation["context"],
                }
            )
        if len(edges) >= 1000:
            break
    return {
        "schema_version": "citation_network_v1",
        "reference_count": len(references_entries),
        "inline_citation_count": len(inline_citations),
        "edge_count": len(edges),
        "nodes": nodes,
        "edges": edges,
        "unresolved_inline_citations": [item for item in inline_citations if not item.get("numbers")][:50],
    }


def _detect_duplicate_references(reference_entries: list[dict]) -> list[dict]:
    groups: dict[str, list[dict]] = defaultdict(list)
    for entry in reference_entries:
        key = entry.get("normalized_key", "")
        if key and len(key) >= 20:
            groups[key].append(entry)
    duplicates: list[dict] = []
    for key, items in groups.items():
        if len(items) > 1:
            duplicates.append(
                {
                    "normalized_key": key,
                    "reference_ids": [item["reference_id"] for item in items],
                    "kept_reference_id": items[0]["reference_id"],
                    "duplicate_reference_ids": [item["reference_id"] for item in items[1:]],
                    "auto_correctable": True,
                }
            )
    return duplicates


def _detect_unit_inconsistencies(full_text: str) -> list[dict]:
    unit_groups = {
        "currency": ["元", "万元", "亿元", "人民币", "RMB", "CNY"],
        "percentage": ["%", "百分比", "百分点"],
        "population": ["人", "万人", "户", "家企业", "个样本"],
        "time": ["年", "月", "季度", "周", "天"],
    }
    issues: list[dict] = []
    for group, units in unit_groups.items():
        found = sorted({unit for unit in units if re.search(rf"\d\s*{re.escape(unit)}", full_text or "", flags=re.IGNORECASE)})
        if len(found) >= 2:
            issues.append(
                {
                    "type": "unit_inconsistency",
                    "unit_group": group,
                    "units": found,
                    "severity": "low",
                    "description": f"同一量纲检测到多个单位：{', '.join(found)}。",
                    "human_prompt": "请核对表格、正文和附录中该量纲是否已统一换算，必要时补充单位说明。",
                }
            )
    return issues


def _extract_tablelike_lines(full_text: str, limit: int = 300) -> list[str]:
    lines: list[str] = []
    for line in (full_text or "").splitlines():
        if "	" in line or "|" in line or re.search(r"\d\s{2,}\d", line):
            if len(re.findall(r"-?\d+(?:\.\d+)?", line)) >= 3:
                lines.append(line.strip())
        if len(lines) >= limit:
            break
    return lines


def _detect_table_numeric_anomalies(full_text: str, tables: list[dict]) -> list[dict]:
    issues: list[dict] = []
    for line in _extract_tablelike_lines(full_text):
        numbers = [float(value) for value in re.findall(r"-?\d+(?:\.\d+)?", line)]
        if "%" in line or "百分比" in line:
            bad_values = [value for value in numbers if value < 0 or value > 100]
            if bad_values:
                issues.append(
                    {
                        "type": "table_percentage_out_of_range",
                        "severity": "medium",
                        "description": "表格样式文本中发现疑似越界百分比。",
                        "evidence": line[:300],
                        "human_prompt": "请核对百分比字段是否误读、漏小数点或单位未换算。",
                    }
                )
        if re.search(r"合计|总计|total", line, flags=re.IGNORECASE) and len(numbers) >= 3:
            expected = sum(numbers[:-1])
            actual = numbers[-1]
            tolerance = max(0.5, abs(expected) * 0.03)
            if abs(expected - actual) > tolerance:
                issues.append(
                    {
                        "type": "table_total_mismatch",
                        "severity": "medium",
                        "description": "疑似表格合计值与前序数值之和不一致。",
                        "evidence": line[:300],
                        "auto_correction_suggestion": round(expected, 4),
                        "human_prompt": "请人工确认该行最后一列是否为合计值，若是请修正合计或说明计算口径。",
                    }
                )
    for table in tables:
        if not table.get("rows") and not table.get("file_path"):
            issues.append(
                {
                    "type": "table_data_unparsed",
                    "severity": "low",
                    "description": f"{table.get('table_id', 'table')} 已识别但未解析出结构化行列。",
                    "human_prompt": "如该表参与评价或计量计算，请补充结构化表格数据。",
                }
            )
    return issues[:100]


def _build_data_quality_report(
    *,
    full_text: str,
    reference_entries: list[dict],
    tables: list[dict],
    qwen_meta: dict,
    api_meta: dict,
    journal_info: dict,
) -> dict:
    issues: list[dict] = []
    auto_corrections: list[dict] = []
    human_review_prompts: list[str] = []

    duplicate_refs = _detect_duplicate_references(reference_entries)
    for duplicate in duplicate_refs:
        issue = {
            "type": "duplicate_reference",
            "severity": "medium",
            "description": "检测到疑似重复参考文献。",
            "reference_ids": duplicate["reference_ids"],
            "auto_correctable": True,
        }
        issues.append(issue)
        auto_corrections.append(
            {
                "type": "deduplicate_references",
                "kept_reference_id": duplicate["kept_reference_id"],
                "removed_reference_ids": duplicate["duplicate_reference_ids"],
            }
        )

    issues.extend(_detect_unit_inconsistencies(full_text))
    issues.extend(_detect_table_numeric_anomalies(full_text, tables))

    matched_title = str(api_meta.get("matched_title") or "")
    qwen_title = str(qwen_meta.get("title") or "")
    if matched_title and qwen_title and qwen_title != "Unknown Title" and _rough_similarity(matched_title, qwen_title) < 0.55:
        issues.append(
            {
                "type": "metadata_title_conflict",
                "severity": "high",
                "description": "QWEN 提取标题与 Semantic Scholar 匹配标题差异较大。",
                "qwen_title": qwen_title,
                "api_matched_title": matched_title,
                "human_prompt": "请人工确认外部 API 是否匹配到同一篇论文，必要时以 DOI 或作者年份重新检索。",
            }
        )
    if api_meta.get("year") and journal_info.get("publication_year") and api_meta.get("year") != journal_info.get("publication_year"):
        issues.append(
            {
                "type": "metadata_year_conflict",
                "severity": "medium",
                "description": "外部 API 年份与本地规则提取年份不一致。",
                "api_year": api_meta.get("year"),
                "local_year": journal_info.get("publication_year"),
                "human_prompt": "请核对正式发表年份、在线发表年份和引用库年份是否存在口径差异。",
            }
        )

    for issue in issues:
        prompt = issue.get("human_prompt")
        if prompt and prompt not in human_review_prompts:
            human_review_prompts.append(prompt)

    return {
        "schema_version": "data_quality_report_v1",
        "issue_count": len(issues),
        "issues": issues,
        "auto_corrections": auto_corrections,
        "human_review_prompts": human_review_prompts,
        "coverage": {
            "duplicate_reference_detection": bool(reference_entries),
            "unit_inconsistency_detection": bool(full_text),
            "table_numeric_detection": bool(full_text or tables),
            "metadata_conflict_detection": bool(qwen_meta or api_meta),
        },
    }


def _score_keywords(text: str, keywords: list[str]) -> tuple[int, list[str]]:
    lowered = (text or "").lower()
    hits = [keyword for keyword in keywords if keyword.lower() in lowered]
    return len(hits), hits[:12]


def _declared_subject_top(review_context: dict | None) -> str:
    if not isinstance(review_context, dict):
        return ""
    for key in ("subject_top", "discipline_top", "top_subject", "学科大类"):
        value = str(review_context.get(key) or "").strip()
        if value in SUBJECT_TOP_VALUES:
            return value
    return ""


def _classify_public_management_paradigm(text: str) -> dict:
    quant_score, quant_hits = _score_keywords(text, QUANTITATIVE_INDICATORS)
    qual_score, qual_hits = _score_keywords(text, QUALITATIVE_INDICATORS)
    mixed_score, mixed_hits = _score_keywords(text, MIXED_METHODS_INDICATORS)
    ambiguities: list[str] = []
    if mixed_score and quant_score and qual_score:
        paper_type = "混合"
        confidence = 0.86
    elif quant_score > qual_score and quant_score >= 2:
        paper_type = "量化"
        confidence = 0.78
    elif qual_score >= 1:
        paper_type = "质性"
        confidence = 0.74
    else:
        paper_type = "质性"
        confidence = 0.42
        ambiguities.append("未发现强量化或强质性方法证据；按公共管理文本/政策分析场景低置信度归为质性，建议人工复核。")
    return {
        "paper_type": paper_type,
        "confidence": confidence,
        "indicators": {
            "quantitative": quant_hits,
            "qualitative": qual_hits,
            "mixed_methods": mixed_hits,
        },
        "ambiguities": ambiguities,
    }


def _build_subject_profile(full_text: str, final_journal: str, review_context: dict | None) -> dict:
    searchable = "\n".join([final_journal or "", full_text[:20000] or ""])
    declared_top = _declared_subject_top(review_context)
    human_score, human_hits = _score_keywords(searchable, HUMANITIES_KEYWORDS)
    science_score, science_hits = _score_keywords(searchable, SCIENCE_KEYWORDS)
    engineering_score, engineering_hits = _score_keywords(searchable, ENGINEERING_KEYWORDS)
    public_score, public_hits = _score_keywords(searchable, PUBLIC_MANAGEMENT_KEYWORDS)

    if declared_top:
        subject_top = declared_top
        status = "declared_preserved"
    elif public_score:
        subject_top = "人文学科"
        status = "public_management_inferred"
    else:
        scores = {"人文学科": human_score, "纯理科": science_score, "交叉工科": engineering_score}
        subject_top = max(scores, key=scores.get)
        status = "keyword_inferred" if scores[subject_top] > 0 else "fallback"

    subject_sub = ""
    paradigm: dict[str, Any] = {}
    if subject_top == "人文学科":
        subject_sub = "公共管理" if public_score >= 1 else "普通文科"
        if subject_sub == "公共管理":
            paradigm = _classify_public_management_paradigm(searchable)
    profile = {
        "subject_top": subject_top,
        "subject_sub": subject_sub,
        "classification_status": status,
        "scores": {
            "public_management": public_score,
            "humanities": human_score,
            "science": science_score,
            "engineering": engineering_score,
        },
        "evidence_keywords": {
            "public_management": public_hits,
            "humanities": human_hits,
            "science": science_hits,
            "engineering": engineering_hits,
        },
        "routing_rule": "仅 subject_sub=公共管理 时生成 paper_type 并触发公共管理专属流程。",
    }
    if paradigm:
        profile["paper_type"] = paradigm["paper_type"]
        profile["paradigm"] = paradigm
    return profile


def _review_context_with_subject(review_context: dict | None, subject_profile: dict) -> dict:
    merged = _default_review_context(review_context)
    merged["subject_top"] = subject_profile.get("subject_top", "")
    if subject_profile.get("subject_sub"):
        merged["subject_sub"] = subject_profile.get("subject_sub")
    if subject_profile.get("subject_sub") == "公共管理" and subject_profile.get("paper_type"):
        merged["paper_type"] = subject_profile.get("paper_type")
    else:
        merged.pop("paper_type", None)
    return merged


def _build_tables_and_figures(visual_assets: list[dict]) -> tuple[list[dict], list[dict]]:
    tables: list[dict] = []
    figures: list[dict] = []
    table_idx = 1
    figure_idx = 1

    for asset in visual_assets:
        asset_type = str(asset.get("type") or "other").lower()
        label = asset.get("label") or ""
        caption = asset.get("caption") or ""
        title = label or caption
        if asset_type == "table":
            tables.append(
                {
                    "table_id": f"table_{table_idx}",
                    "title": title or f"表{table_idx}",
                    "caption": caption,
                    "columns": [],
                    "rows": [],
                    "source_location": f"第{asset.get('page', '')}页" if asset.get("page") else "",
                    "file_path": asset.get("crop_path", ""),
                }
            )
            table_idx += 1
        else:
            figures.append(
                {
                    "figure_id": f"figure_{figure_idx}",
                    "title": title or f"图{figure_idx}",
                    "caption": caption,
                    "ocr_text": asset.get("ocr_text", ""),
                    "description": asset.get("description", "") or caption,
                    "file_path": asset.get("crop_path", ""),
                }
            )
            figure_idx += 1
    return tables, figures


def _extract_named_section(sections: list[dict], names: list[str]) -> str:
    for sec in sections:
        title = sec.get("section_title", "")
        category = sec.get("section_category", "")
        if category in names or title in names or any(str(title).startswith(name) for name in names):
            return sec.get("section_text", "")
    return ""


def _build_data_info(full_text: str) -> dict:
    data_sources: list[str] = []
    for pat in [r"数据来源[为：:]?([^。\n]{1,120})", r"dataset(?:s)?\s*[:：]?\s*([^\.\n]{1,120})"]:
        for m in re.finditer(pat, full_text, flags=re.IGNORECASE):
            data_sources.append(m.group(1).strip())
    data_sources = list(dict.fromkeys(data_sources))[:5]

    return {
        "data_sources": data_sources,
        "sample_size": _extract_between(full_text, [r"样本量\s*[:：]?", r"sample size\s*[:：]?"], [r"\n", r"。", r"\."], 300),
        "sample_scope": _extract_between(full_text, [r"样本范围\s*[:：]?", r"研究对象\s*[:：]?"], [r"\n", r"。"], 300),
        "variables": [],
        "missing_value_handling": "已提及" if _contains_any(full_text, ["缺失值", "missing value"]) else "未说明",
        "preprocessing": "已提及" if _contains_any(full_text, ["预处理", "preprocessing", "pre-process"]) else "未说明",
        "train_test_split": "已提及" if _contains_any(full_text, ["训练集", "测试集", "train", "test split"]) else "未说明",
    }


def _build_method_info(full_text: str, sections: list[dict]) -> dict:
    method_text = _extract_named_section(sections, ["方法"])
    experiment_text = _extract_named_section(sections, ["实验"])
    return {
        "research_question": "未自动识别",
        "hypothesis": "未说明",
        "method_summary": method_text[:1200] if method_text else "未自动识别",
        "baseline_methods": [],
        "ablation_study": "已提及" if _contains_any(full_text, ["消融", "ablation"]) else "未说明",
        "evaluation_metrics": [m for m in ["准确率", "精确率", "召回率", "F1", "AUC", "RMSE", "MAE", "accuracy", "precision", "recall"] if m.lower() in full_text.lower()][:10],
        "main_results": experiment_text[:1200] if experiment_text else "未自动识别",
    }


def _build_ethics_info(full_text: str) -> dict:
    return {
        "ethics_approval": "已提及" if _contains_any(full_text, ["伦理审批", "ethics approval", "irb"]) else "未说明",
        "informed_consent": "已提及" if _contains_any(full_text, ["知情同意", "informed consent"]) else "未说明",
        "privacy_protection": "已提及" if _contains_any(full_text, ["隐私", "privacy", "匿名", "anonym"] ) else "未说明",
        "data_authorization": "已提及" if _contains_any(full_text, ["数据授权", "授权使用", "permission"]) else "未说明",
        "conflict_of_interest": "已提及" if _contains_any(full_text, ["利益冲突", "conflict of interest"]) else "未说明",
        "affected_groups": [],
    }


def _build_innovation_info(full_text: str) -> dict:
    contributions: list[str] = []
    contrib_text = _extract_between(full_text, [r"主要贡献\s*[:：]?", r"contributions?\s*[:：]?"], [r"\n\s*\d", r"相关工作", r"实验", r"method"], 1200)
    if contrib_text:
        contributions = [x.strip(" -•;；。") for x in re.split(r"[\n;；。]", contrib_text) if x.strip()][:5]
    return {
        "claimed_contributions": contributions,
        "related_work_summary": _extract_between(full_text, [r"相关工作\s*[:：]?", r"related work\s*[:：]?"], [r"方法", r"method", r"实验", r"experiment"], 1200) or "未自动识别",
        "novelty_claims": [s for s in contributions if _contains_any(s, ["创新", "首次", "提出", "novel", "new"])],
        "difference_from_prior_work": "未自动识别",
        "practical_value": "已提及" if _contains_any(full_text, ["应用价值", "实践价值", "practical"]) else "未说明",
        "theoretical_value": "已提及" if _contains_any(full_text, ["理论价值", "theoretical"]) else "未说明",
    }


def _source_without_anchor_payload(source_info: dict, text_anchors: list[dict]) -> dict:
    source = dict(source_info)
    source.pop("text_anchors", None)
    source["text_anchor_schema"] = source.get("text_anchor_schema") or TEXT_ANCHOR_SCHEMA_VERSION
    source["text_anchor_count"] = len(text_anchors)
    return source


def _build_standard_json(
    *,
    source_info: dict,
    full_text: str,
    header_text: str,
    qwen_meta: dict,
    api_meta: dict,
    journal_level: str,
    visual_assets: list[dict],
    review_context: dict | None = None,
) -> dict:
    image_analysis = source_info.get("image_analysis") or {}
    text_anchors = source_info.get("text_anchors") if isinstance(source_info.get("text_anchors"), list) else []
    abstract = _extract_abstract(full_text, source_info)
    keywords = _extract_keywords(full_text, source_info)
    sections = _split_sections(full_text, text_anchors)
    references_info = _extract_references_info(full_text, text_anchors)
    reference_entries = _extract_reference_entries(references_info)
    tables, figures = _build_tables_and_figures(visual_assets)

    journal_candidate = qwen_meta.get("journal_candidate", "")
    final_journal = journal_candidate or "Unknown"
    if api_meta.get("venue") and api_meta.get("venue") != "Unknown Venue":
        final_journal = api_meta.get("venue")

    paper_title = qwen_meta.get("title") or image_analysis.get("title") or "Unknown Title"
    author_details = _extract_author_details(header_text, qwen_meta)
    journal_info = _extract_journal_info(header_text, full_text, final_journal, journal_level, api_meta)
    open_science = _build_open_science_info(full_text, image_analysis)
    citation_network = _build_citation_network(full_text, reference_entries, paper_title)
    subject_profile = _build_subject_profile(full_text, final_journal, review_context)
    review_context_payload = _review_context_with_subject(review_context, subject_profile)

    metadata = {
        "journal": final_journal,
        "journal_level": journal_level,
        "citation_count": api_meta.get("citations", 0),
        "publication_year": api_meta.get("year") or journal_info.get("publication_year"),
        "open_data": open_science["open_data"],
        "open_code": open_science["open_code"],
        "subject_top": subject_profile.get("subject_top", ""),
        "subject_sub": subject_profile.get("subject_sub", ""),
        "journal_info": journal_info,
        "open_science": open_science,
        "external_sources": [
            {
                "name": "Semantic Scholar",
                "matched_title": api_meta.get("matched_title"),
                "venue": api_meta.get("venue"),
                "year": api_meta.get("year"),
                "paper_id": api_meta.get("paper_id"),
                "url": api_meta.get("url"),
            }
        ] if api_meta else [],
    }
    if subject_profile.get("subject_sub") == "公共管理" and subject_profile.get("paper_type"):
        metadata["paper_type"] = subject_profile["paper_type"]

    academic_impact_data = build_public_management_impact_context(
        journal_name=final_journal,
        reference_entries=reference_entries,
        enabled=subject_profile.get("subject_sub") == "公共管理",
    )
    data_quality = _build_data_quality_report(
        full_text=full_text,
        reference_entries=reference_entries,
        tables=tables,
        qwen_meta=qwen_meta,
        api_meta=api_meta,
        journal_info=journal_info,
    )

    if source_info.get("format") in {"png", "jpg", "jpeg", "bmp", "tif", "tiff", "webp"} and not figures:
        figures.append(
            {
                "figure_id": "figure_1",
                "title": image_analysis.get("title") or Path(source_info.get("path", "image")).stem,
                "caption": image_analysis.get("caption", ""),
                "ocr_text": image_analysis.get("ocr_text", ""),
                "description": image_analysis.get("description", ""),
                "file_path": source_info.get("path", ""),
            }
        )

    result = {
        "paper_info": {
            "title": paper_title,
            "authors": qwen_meta.get("authors") or [],
            "author_details": author_details,
            "abstract": abstract,
            "keywords": keywords,
        },
        "metadata": metadata,
        "content": {
            "full_text": full_text,
            "sections": sections,
            "references_text": references_info["text"],
            "references_anchor_ids": references_info["source_anchor_ids"],
            "reference_entries": reference_entries,
            "citation_network": citation_network,
            "text_anchor_schema": source_info.get("text_anchor_schema") or TEXT_ANCHOR_SCHEMA_VERSION,
            "text_anchors": text_anchors,
        },
        "tables": tables,
        "figures": figures,
        "data_info": _build_data_info(full_text),
        "method_info": _build_method_info(full_text, sections),
        "ethics_info": _build_ethics_info(full_text),
        "innovation_info": _build_innovation_info(full_text),
        "discipline": subject_profile,
        "subject_top": subject_profile.get("subject_top", ""),
        "subject_sub": subject_profile.get("subject_sub", ""),
        "citation_network": citation_network,
        "data_quality": data_quality,
        "academic_impact_data": academic_impact_data,
        "review_context": review_context_payload,
        "source": _source_without_anchor_payload(source_info, text_anchors),
    }
    if subject_profile.get("subject_sub") == "公共管理" and subject_profile.get("paper_type"):
        result["paper_type"] = subject_profile["paper_type"]
    return result


def to_minimal_agent_json(standard_json: dict) -> dict:
    """转换为团队提供的“最小版格式.json”。"""
    metadata = {
        "journal": standard_json.get("metadata", {}).get("journal", ""),
        "journal_level": standard_json.get("metadata", {}).get("journal_level", ""),
        "citation_count": standard_json.get("metadata", {}).get("citation_count", 0),
        "publication_year": standard_json.get("metadata", {}).get("publication_year"),
        "open_data": standard_json.get("metadata", {}).get("open_data", False),
        "subject_top": standard_json.get("subject_top", ""),
        "subject_sub": standard_json.get("subject_sub", ""),
        "external_sources": standard_json.get("metadata", {}).get("external_sources", []),
    }
    if standard_json.get("paper_type"):
        metadata["paper_type"] = standard_json.get("paper_type")

    payload = {
        "paper_info": standard_json.get("paper_info", {}),
        "metadata": metadata,
        "full_text": standard_json.get("content", {}).get("full_text", ""),
        "text_anchors": standard_json.get("content", {}).get("text_anchors", []),
        "subject_top": standard_json.get("subject_top", ""),
        "subject_sub": standard_json.get("subject_sub", ""),
        "discipline": standard_json.get("discipline", {}),
        "citation_network": {
            "schema_version": standard_json.get("citation_network", {}).get("schema_version", "citation_network_v1"),
            "reference_count": standard_json.get("citation_network", {}).get("reference_count", 0),
            "inline_citation_count": standard_json.get("citation_network", {}).get("inline_citation_count", 0),
            "edge_count": standard_json.get("citation_network", {}).get("edge_count", 0),
        },
        "data_quality": {
            "schema_version": standard_json.get("data_quality", {}).get("schema_version", "data_quality_report_v1"),
            "issue_count": standard_json.get("data_quality", {}).get("issue_count", 0),
            "human_review_prompts": standard_json.get("data_quality", {}).get("human_review_prompts", []),
        },
        "review_context": standard_json.get("review_context", _default_review_context()),
    }
    if standard_json.get("paper_type"):
        payload["paper_type"] = standard_json.get("paper_type")
    return payload


def process_document(
    input_path: str,
    output_dir: str | None = None,
    extract_visuals: bool = False,
    max_visual_pages: int | None = None,
    visual_pages: list[int] | None = None,
    review_context: dict | None = None,
    schema: str = "full",
    api_key: str | None = None,
    base_url: str | None = None,
    model_name: str | None = None,
) -> dict:
    """
    统一入口：把 PDF/DOCX/TEX/CAJ/图片 转换为评价 Agent 标准 JSON。

    schema="full" 返回完整 input.json 结构；schema="minimal" 返回最小版格式。
    """
    path = Path(input_path)
    if not path.exists():
        raise FileNotFoundError(f"文件不存在: {input_path}")
    if path.suffix.lower() not in SUPPORTED_EXTENSIONS:
        raise ValueError(f"暂不支持的格式: {path.suffix}，当前支持: {sorted(SUPPORTED_EXTENSIONS)}")

    asset_dir = Path(output_dir) if output_dir else Path("outputs") / f"{path.stem}_assets"
    asset_dir.mkdir(parents=True, exist_ok=True)

    print(f"[{_now()}] 开始解析并标准化论文/材料: {path}")

    full_text, header_text, source_info = extract_document_to_text(str(path), output_dir=asset_dir / "_work")

    print(f"[{_now()}] 正在提取基础元数据...")
    qwen_meta = _safe_qwen_metadata(
        header_text,
        source_info,
        api_key=api_key,
        base_url=base_url,
        model_name=model_name,
    )
    paper_title = qwen_meta.get("title", "Unknown Title")

    print(f"[{_now()}] 正在请求外部学术 API 获取引用量...")
    api_meta = query_semantic_scholar_api(paper_title)
    if api_meta:
        print(f"-> 外部 API 匹配成功! 引用量: {api_meta.get('citations', 0)}, 会议/期刊: {api_meta.get('venue')}")
    else:
        print("-> 外部 API 匹配未成功或标题未知，使用本地/QWEN 提取结果。")

    final_journal = api_meta.get("venue") if api_meta.get("venue") and api_meta.get("venue") != "Unknown Venue" else qwen_meta.get("journal_candidate", "")
    if (api_key or os.getenv("DASHSCOPE_API_KEY")) and final_journal:
        print(f"[{_now()}] 正在通过 QWEN 评估期刊等级...")
        journal_level = evaluate_journal_level_via_qwen(
            final_journal,
            model=model_name,
            api_key=api_key,
            base_url=base_url,
        )
    else:
        journal_level = "Unknown Level"

    visual_assets: list[dict] = []
    if extract_visuals:
        print(f"[{_now()}] 正在通过 QWEN-VL 识别并裁剪图片/表格/图表及其题注...")
        visual_assets = extract_visual_assets(
            str(path),
            output_dir=asset_dir,
            max_pages=max_visual_pages,
            page_numbers=visual_pages,
        )
        print(f"-> 已输出视觉资产 {len(visual_assets)} 个，目录: {asset_dir.resolve()}")

    standard = _build_standard_json(
        source_info=source_info,
        full_text=full_text,
        header_text=header_text,
        qwen_meta=qwen_meta,
        api_meta=api_meta,
        journal_level=journal_level,
        visual_assets=visual_assets,
        review_context=review_context,
    )

    print(f"[{_now()}] 标准 JSON 构建完成！")
    if schema.lower() in {"minimal", "min", "最小"}:
        return to_minimal_agent_json(standard)
    return standard


def process_pdf(pdf_path: str) -> dict:
    """兼容旧接口。"""
    return process_document(pdf_path)


def _load_review_context(path: str | None) -> dict | None:
    if not path:
        return None
    with open(path, "r", encoding="utf-8") as f:
        payload = json.load(f)
    return payload.get("review_context", payload)


def parse_visual_pages(value: str | None) -> list[int] | None:
    """解析 1,3,5-7 格式的页码；页码从 1 开始。"""
    if not value:
        return None
    pages: set[int] = set()
    for item in value.split(","):
        item = item.strip()
        if not item:
            continue
        if "-" in item:
            start_text, end_text = item.split("-", 1)
            start, end = int(start_text), int(end_text)
            if start < 1 or end < start:
                raise ValueError(f"无效页码范围：{item}")
            pages.update(range(start, end + 1))
        else:
            page = int(item)
            if page < 1:
                raise ValueError(f"页码必须从 1 开始：{item}")
            pages.add(page)
    return sorted(pages) or None


def _build_arg_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="多来源论文材料 -> 评价 Agent 标准 JSON")
    parser.add_argument("input", nargs="?", help="论文/材料路径，支持 .pdf/.docx/.tex/.caj/.png/.jpg/.jpeg/.webp 等")
    parser.add_argument("--output-dir", help="图片/表格裁剪结果与中间文件输出目录")
    visual_group = parser.add_mutually_exclusive_group()
    visual_group.add_argument("--visuals", action="store_true", help="开启受限视觉审查，默认只处理前 5 页")
    visual_group.add_argument("--full-visuals", action="store_true", help="显式开启全文视觉审查")
    visual_group.add_argument("--no-visuals", action="store_true", help=argparse.SUPPRESS)
    parser.add_argument("--max-visual-pages", type=int, default=5, help="受限视觉审查处理前 N 页，默认 5")
    parser.add_argument("--visual-pages", help="指定视觉审查页码，例如 1,3,5-7；使用后自动开启视觉审查")
    parser.add_argument("--schema", choices=["full", "minimal"], default="full", help="输出完整标准 JSON 或最小版 JSON")
    parser.add_argument("--review-context", help="可选，包含 review_context 的 JSON 文件")
    parser.add_argument("--json-output", help="将最终标准 JSON 写入指定文件")
    return parser


if __name__ == "__main__":
    parser = _build_arg_parser()
    args = parser.parse_args()

    input_path = args.input or "paper.pdf"
    if not Path(input_path).exists():
        print(
            f"未找到 {input_path}。请传入真实论文文件，例如：\n"
            "  python data_processor.py paper.pdf --output-dir outputs/paper_assets --json-output outputs/paper.json\n"
            "  python data_processor.py paper.docx --schema minimal\n"
            "  python data_processor.py figure.png --schema full"
        )
    else:
        try:
            selected_pages = parse_visual_pages(args.visual_pages)
            extract_visuals = bool(args.visuals or args.full_visuals or selected_pages)
            max_visual_pages = None if args.full_visuals or selected_pages else args.max_visual_pages
            result = process_document(
                input_path,
                output_dir=args.output_dir,
                extract_visuals=extract_visuals,
                max_visual_pages=max_visual_pages,
                visual_pages=selected_pages,
                review_context=_load_review_context(args.review_context),
                schema=args.schema,
            )
            print("\n" + "=" * 20 + " 标准 JSON 输出 " + "=" * 20)
            print(json.dumps(result, indent=2, ensure_ascii=False))
            if args.json_output:
                json_path = Path(args.json_output)
                json_path.parent.mkdir(parents=True, exist_ok=True)
                json_path.write_text(json.dumps(result, indent=2, ensure_ascii=False), encoding="utf-8")
                print(f"JSON 已写入: {json_path.resolve()}")
        except Exception as e:
            print(f"处理失败: {e}")


