"""FastAPI entry point for the unified AI academic review backend."""

from __future__ import annotations

import asyncio
import hashlib
import heapq
import json
import logging
import os
import re
import sqlite3
import sys
import time
import uuid
from contextlib import asynccontextmanager, contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from fastapi import FastAPI, File, Form, HTTPException, UploadFile, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse, PlainTextResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

BASE_DIR = Path(__file__).resolve().parent
UPLOAD_DIR = BASE_DIR / "runtime" / "uploads"
DB_PATH = BASE_DIR / "runtime" / "academic_review.db"
SYSTEM_LOG_PATH = BASE_DIR / "runtime" / "system.log"
CACHE_ROOT = BASE_DIR / "runtime" / "pipeline_cache"
MAX_UPLOAD_BYTES = int(os.getenv("MAX_UPLOAD_BYTES", str(50 * 1024 * 1024)))
REVIEW_TIMEOUT_SECONDS = float(os.getenv("REVIEW_TIMEOUT_SECONDS", "600"))
PROGRESS_HEARTBEAT_SECONDS = max(
    2.0,
    min(30.0, float(os.getenv("PROGRESS_HEARTBEAT_SECONDS", "4"))),
)
PIPELINE_VERSION = "5.1"
SUPPORTED_SUFFIXES = {".pdf", ".docx", ".tex", ".caj", ".png", ".jpg", ".jpeg", ".webp"}
UPLOAD_DIR.mkdir(parents=True, exist_ok=True)

if str(BASE_DIR / "main_controller") not in sys.path:
    sys.path.insert(0, str(BASE_DIR / "main_controller"))
from main import main_pipeline  # noqa: E402

logger = logging.getLogger("AIReview.API")

# ---------------------------------------------------------------------------
# Dual-channel logging: console + persistent file for developer diagnostics
# ---------------------------------------------------------------------------
SYSTEM_LOG_PATH.parent.mkdir(parents=True, exist_ok=True)
_file_handler = logging.FileHandler(str(SYSTEM_LOG_PATH), encoding="utf-8")
_file_handler.setLevel(logging.INFO)
_file_handler.setFormatter(logging.Formatter("%(asctime)s [%(levelname)s] %(name)s: %(message)s"))
logging.getLogger().addHandler(_file_handler)
logging.getLogger().setLevel(logging.INFO)
# AIReview.Main propagates to the root logger, so adding the same handler there
# would duplicate every pipeline line and traceback in the developer panel.
logging.getLogger("AIReview.Main").setLevel(logging.INFO)
logger.info("System log initialized at %s", SYSTEM_LOG_PATH)


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


@contextmanager
def db():
    connection = sqlite3.connect(DB_PATH, timeout=30)
    connection.row_factory = sqlite3.Row
    connection.execute("PRAGMA journal_mode=WAL")
    try:
        yield connection
        connection.commit()
    except Exception:
        connection.rollback()
        raise
    finally:
        connection.close()


def initialize_database() -> None:
    with db() as connection:
        connection.executescript(
            """
            CREATE TABLE IF NOT EXISTS reports (
                id TEXT PRIMARY KEY, filename TEXT NOT NULL, file_hash TEXT,
                report_data TEXT NOT NULL, created_at TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS feedback (
                id TEXT PRIMARY KEY, report_id TEXT, rating INTEGER,
                category TEXT NOT NULL, content TEXT NOT NULL,
                contact TEXT, created_at TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS entitlements (
                id TEXT PRIMARY KEY, report_id TEXT NOT NULL,
                type TEXT NOT NULL, remaining INTEGER NOT NULL,
                claim_token TEXT NOT NULL, created_at TEXT NOT NULL,
                UNIQUE(report_id, type)
            );
            CREATE TABLE IF NOT EXISTS tickets (
                id TEXT PRIMARY KEY, stage TEXT NOT NULL, error_type TEXT NOT NULL,
                message TEXT NOT NULL, context TEXT NOT NULL,
                status TEXT NOT NULL DEFAULT 'open', created_at TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS metrics (
                id TEXT PRIMARY KEY, operation TEXT NOT NULL, status TEXT NOT NULL,
                duration_seconds REAL NOT NULL, detail TEXT NOT NULL,
                created_at TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS releases (
                version TEXT PRIMARY KEY, notes TEXT NOT NULL, created_at TEXT NOT NULL
            );
            INSERT OR IGNORE INTO releases(version,notes,created_at)
            VALUES('2.1','五维评价、文本定位、反馈权益、错误工单与稳定性缓存。',datetime('now'));
            INSERT OR IGNORE INTO releases(version,notes,created_at)
            VALUES('4.2','修复Base URL拼接错误、移除硬编码假数据、修复ImportError相对导入、修复React对象渲染崩溃。',datetime('now'));
            INSERT OR IGNORE INTO releases(version,notes,created_at)
            VALUES('4.3','强制刷新绕过缓存、PDF原文预览定位、后端极速元数据提取、前端严格阶段状态机进度剧场。',datetime('now'));
            INSERT OR IGNORE INTO releases(version,notes,created_at)
            VALUES('4.4','细化真实流式进度、限制一致性复核重试、修正复核失败状态、隔离PDF证据模式网格并增加前后端版本检测。',datetime('now'));
            INSERT OR IGNORE INTO releases(version,notes,created_at)
            VALUES('4.5','拆分一致性复核的真实处理阶段；相同等待状态改为单行原地更新，并为耗时数字增加翻转动效。',datetime('now'));
            INSERT OR IGNORE INTO releases(version,notes,created_at)
            VALUES('4.6','整体复核改为安全补丁式输出，关闭思考模式、限制输出长度并阻止长超时后的重复完整计算。',datetime('now'));
            INSERT OR IGNORE INTO releases(version,notes,created_at)
            VALUES('4.7','恢复报告级反馈解锁与普通版PDF水印，扩大流式日志区域，并重制PDF封面、评分概览、分页及页眉页脚。',datetime('now'));
            INSERT OR IGNORE INTO releases(version,notes,created_at)
            VALUES('4.8','统一更名为AI学术审查系统，重绘中文PDF水印与报告品牌，并生成双平台发行包。',datetime('now'));
            """
        )
        columns = {row["name"] for row in connection.execute("PRAGMA table_info(entitlements)")}
        if "claim_token" not in columns:
            connection.execute("ALTER TABLE entitlements ADD COLUMN claim_token TEXT NOT NULL DEFAULT ''")


class PriorityScheduler:
    """Admit feedback-reward reviews before ordinary queued reviews."""

    def __init__(self, capacity: int = 2) -> None:
        self.capacity = capacity
        self.running = 0
        self.waiting: list[tuple[int, int, asyncio.Future[None]]] = []
        self.sequence = 0
        self.lock = asyncio.Lock()

    async def acquire(self, priority: bool) -> None:
        async with self.lock:
            if self.running < self.capacity and not self.waiting:
                self.running += 1
                return
            future = asyncio.get_running_loop().create_future()
            self.sequence += 1
            heapq.heappush(self.waiting, (0 if priority else 10, self.sequence, future))
        await future

    async def release(self) -> None:
        async with self.lock:
            if self.waiting:
                _, _, future = heapq.heappop(self.waiting)
                if not future.done():
                    future.set_result(None)
            else:
                self.running = max(0, self.running - 1)


scheduler = PriorityScheduler(int(os.getenv("MAX_CONCURRENT_REVIEWS", "2")))


@asynccontextmanager
async def lifespan(_: FastAPI):
    initialize_database()
    yield


app = FastAPI(title="AI Academic Review System API", version=PIPELINE_VERSION, lifespan=lifespan)
app.add_middleware(
    CORSMiddleware,
    allow_origins=[item.strip() for item in os.getenv("CORS_ORIGINS", "*").split(",")],
    allow_credentials=False,
    allow_methods=["*"],
    allow_headers=["*"],
)


# ---------------------------------------------------------------------------
# Global exception handler — prevents uncaught crashes from breaking the client
# ---------------------------------------------------------------------------
@app.exception_handler(Exception)
async def global_exception_handler(request: Request, exc: Exception):
    logger.exception("Unhandled exception on %s %s", request.method, request.url.path)
    return JSONResponse(
        status_code=500,
        content={
            "error": "Internal Server Error",
            "detail": str(exc)[:1000],
            "path": str(request.url.path),
        },
    )


# ---------------------------------------------------------------------------
# Determine frontend dist path (used after all API routes are registered)
# ---------------------------------------------------------------------------
FRONTEND_DIST = (BASE_DIR / ".." / "frontend" / "dist").resolve()


def ndjson(event: dict[str, Any]) -> str:
    return json.dumps(event, ensure_ascii=False) + "\n"


def archive_ticket(stage: str, exc: Exception, context: dict[str, Any]) -> str:
    ticket_id = str(uuid.uuid4())
    with db() as connection:
        connection.execute(
            "INSERT INTO tickets(id,stage,error_type,message,context,created_at) VALUES(?,?,?,?,?,?)",
            (ticket_id, stage, type(exc).__name__, str(exc)[:2000],
             json.dumps(context, ensure_ascii=False), utc_now()),
        )
    logger.error("Archived ticket %s at %s: %s", ticket_id, stage, exc)
    return ticket_id


def record_metric(operation: str, status: str, duration: float, detail: dict[str, Any]) -> None:
    with db() as connection:
        connection.execute(
            "INSERT INTO metrics(id,operation,status,duration_seconds,detail,created_at) VALUES(?,?,?,?,?,?)",
            (str(uuid.uuid4()), operation, status, round(duration, 3),
             json.dumps(detail, ensure_ascii=False), utc_now()),
        )


def entitlement(report_id: str, kind: str, token: str, *, consume: bool = False) -> bool:
    if not token:
        return False
    with db() as connection:
        row = connection.execute(
            "SELECT id,remaining FROM entitlements WHERE report_id=? AND type=? AND claim_token=?",
            (report_id, kind, token),
        ).fetchone()
        if not row or row["remaining"] <= 0:
            return False
        if consume:
            connection.execute(
                "UPDATE entitlements SET remaining=remaining-1 WHERE id=? AND remaining>0", (row["id"],)
            )
    return True


def priority_entitlement(token: str, *, consume: bool = False) -> bool:
    if not token:
        return False
    with db() as connection:
        row = connection.execute(
            "SELECT id,remaining FROM entitlements WHERE type='priority_queue' AND claim_token=?",
            (token,),
        ).fetchone()
        if not row or row["remaining"] <= 0:
            return False
        if consume:
            connection.execute(
                "UPDATE entitlements SET remaining=remaining-1 WHERE id=? AND remaining>0", (row["id"],)
            )
    return True


# --- Invalid keyword / cover-page blacklist ---
_KEYWORD_BLACKLIST = {
    "打印并签名", "赛区评阅编号", "由赛区组委会填写", "高教社杯",
    "全国大学生数学建模竞赛", "承诺书", "参赛规则", "http", "www",
    "数学建模竞赛", "论文格式规范", "格式规范", "编号专用页",
    "评阅编号", "指导教师", "指导老师", "联系电话", "手机号码",
    "学号", "班级", "学院", "专业", "姓名", "日期", "年月日",
    "填写说明", "注意事项", "目录", "参考文献", "附录",
    "the", "and", "for", "with", "that", "this", "from", "are", "were",
    "have", "been", "would", "could", "should",
}


def _is_noise_page(page_text: str) -> bool:
    """Return True if a page looks like a cover page, commitment letter, TOC, or rules page."""
    lower = page_text.lower()
    noise_markers = [
        "承诺书", "赛区评阅编号", "打印并签名", "编号专用页",
        "由赛区组委会填写", "论文格式规范", "格式规范",
        "高教社杯", "参赛规则", "填写说明", "注意事项",
        "competition rules", "cover page", "declaration",
    ]
    marker_count = sum(1 for m in noise_markers if m in lower)
    # If a page has 2+ noise markers, it's likely a cover/admin page
    if marker_count >= 2:
        return True
    # If it's mostly dots/spaces/numbers (TOC), skip it
    stripped = re.sub(r"[\s\.…\-\d]+", "", page_text)
    if len(stripped) < 20:
        return True
    return False


def _clean_summary(text: str) -> str:
    """Clean summary: remove URLs, competition numbers, commitment text, excessive whitespace."""
    # Remove URLs
    text = re.sub(r"https?://\S+", "", text)
    # Remove competition numbering patterns
    text = re.sub(r"[A-Z]?\d{6,}", "", text)
    text = re.sub(r"第[一二三四五六七八九十\d]+[届期卷]", "", text)
    # Remove "承诺书" lines
    text = re.sub(r"我们郑重承诺.*?(?=[。\.\n]|$)", "", text)
    # Remove "打印并签名" and surrounding template text
    text = re.sub(r"打印并签名.*?(?=[。\.\n]|$)", "", text)
    text = re.sub(r"赛区评阅编号.*?(?=[。\.\n]|$)", "", text)
    text = re.sub(r"由赛区组委会填写.*?(?=[。\.\n]|$)", "", text)
    # Normalize whitespace
    text = re.sub(r"\s+", " ", text).strip()
    # Remove excessive dots (TOC remnants)
    text = re.sub(r"\.{3,}", "", text)
    return text


def _extract_abstract_section(text: str) -> str:
    """Find the abstract section and return text between abstract and keywords/introduction."""
    patterns = [
        r"摘要\s*[：:]\s*(.*?)(?:\n\s*(?:关键词|关键字|引言|1[\.\s]|一[\.\s、]))",
        r"摘要\s*\n+(.*?)(?:\n\s*(?:关键词|关键字|引言|1[\.\s]|一[\.\s、]))",
        r"(?:abstract|Abstract)\s*[：:\-]\s*(.*?)(?:\n\s*(?:keywords|Keywords|Introduction|1[\.\s]|I[\.\s]))",
    ]
    for pat in patterns:
        m = re.search(pat, text, re.DOTALL | re.IGNORECASE)
        if m:
            raw = m.group(1).strip()
            if len(raw) >= 30:
                return raw
    return ""


def _smart_truncate(text: str, max_chars: int = 220) -> str:
    """Truncate text naturally at sentence boundaries (Chinese or English)."""
    if len(text) <= max_chars:
        return text
    # Find the last sentence-ending char before max_chars
    truncated = text[:max_chars]
    for sep in ["。", "；", "，", ".", ";", ","]:
        idx = truncated.rfind(sep)
        if idx > max_chars * 0.6:
            return truncated[:idx + 1]
    return truncated.rstrip() + "..."


def extract_pdf_metadata(file_path: str) -> dict[str, Any]:
    """Smart metadata extraction that skips cover pages, TOC, and commitments.

    Scans up to 12 pages. Prioritizes abstract, introduction, and main body
    pages. Returns clean summary, real topic keywords, and detected methods.
    """
    import fitz
    import re
    from collections import Counter

    doc = fitz.open(file_path)
    pages = doc.page_count

    # --- Phase 1: Collect text from pages, skipping noise pages ---
    all_body_text = ""
    abstract_text = ""
    body_text_pages = []
    keywords_from_abstract = ""

    scan_limit = min(12, pages)
    for i in range(scan_limit):
        page_text = doc[i].get_text() or ""
        if _is_noise_page(page_text):
            continue
        all_body_text += page_text + "\n"
        body_text_pages.append(i)

        # Check if this page has abstract
        lower = page_text.lower()
        if "摘要" in lower or "abstract" in lower:
            # Find abstract section
            abs_text = _extract_abstract_section(page_text)
            if abs_text and len(abs_text) > len(abstract_text):
                abstract_text = abs_text
            # Also grab keywords line (for keyword extraction)
            kw_match = re.search(
                r"(?:关键词|关键字|Keywords|keywords)\s*[：:]\s*(.+)",
                page_text, re.IGNORECASE,
            )
            if kw_match:
                keywords_from_abstract = kw_match.group(1).strip()

        # If no abstract found yet but page has "introduction" or "引言", use it
        if not abstract_text and ("引言" in lower or "introduction" in lower or "问题重述" in lower):
            # Take first substantial paragraph
            paragraphs = [p.strip() for p in page_text.split("\n") if len(p.strip()) > 30]
            if paragraphs:
                abstract_text = "\n".join(paragraphs[:2])

    # If still no abstract, use first substantial page
    if not abstract_text and body_text_pages:
        first_body_text = ""
        for pi in body_text_pages[:3]:
            first_body_text += doc[pi].get_text() or ""
        abstract_text = first_body_text[:600]

    doc.close()

    # --- Phase 2: Clean and extract summary ---
    abstract_text = _clean_summary(abstract_text)
    # Re-extract abstract section from cleaned text
    abs_section = _extract_abstract_section(abstract_text) or abstract_text
    abs_section = _clean_summary(abs_section)
    summary = _smart_truncate(abs_section.strip(), 220)
    if len(summary) < 15:
        # Fallback: take first meaningful sentence from body text
        cleaned_body = _clean_summary(all_body_text[:800])
        summary = _smart_truncate(cleaned_body.strip(), 220)
    if len(summary) < 10:
        summary = "未能提取到论文摘要，将在深度审查中分析主题"

    # --- Phase 3: Estimate word count ---
    words_est = len(all_body_text.replace(" ", "")) // 3 + all_body_text.count(" ") + 1

    # --- Phase 4: Keyword extraction from clean body text ---
    # Prefer keywords from the "关键词" line if available
    if keywords_from_abstract:
        author_kw = re.split(r"[;；,，\s]+", keywords_from_abstract)
        author_kw = [k.strip() for k in author_kw if 2 <= len(k.strip()) <= 15]
        author_kw = [k for k in author_kw if k.lower() not in _KEYWORD_BLACKLIST]
    else:
        author_kw = []

    # Frequency-based keywords from body text (first 8000 chars)
    freq_kw = _extract_keywords_simple(all_body_text[:8000])
    freq_kw = [k for k in freq_kw if k.lower() not in _KEYWORD_BLACKLIST]

    # Merge: author keywords first, then frequency keywords
    merged_kw = []
    for k in author_kw + freq_kw:
        if k not in merged_kw and k.lower() not in _KEYWORD_BLACKLIST:
            merged_kw.append(k)

    core_keywords = merged_kw[:3] if len(merged_kw) >= 3 else (merged_kw + ["核心问题", "研究对象", "关键变量"])[:3]
    sub_keywords = merged_kw[3:6] if len(merged_kw) > 3 else ["数据特征", "约束条件", "影响因素"]

    # --- Phase 5: Method detection from body text ---
    exp_methods = _detect_methods(all_body_text[:8000])
    if not exp_methods:
        exp_methods = ["文本解析", "逻辑推演", "交叉验证"]

    return {
        "pages": pages,
        "words": words_est,
        "summary": summary,
        "core_keywords": core_keywords,
        "sub_keywords": sub_keywords,
        "exp_methods": exp_methods,
    }


# Academic stopwords (Chinese + English)
_STOPWORDS = {
    "the", "a", "an", "is", "are", "was", "were", "be", "been", "being",
    "have", "has", "had", "do", "does", "did", "will", "would", "could",
    "should", "may", "might", "can", "shall", "to", "of", "in", "for",
    "on", "with", "at", "by", "from", "as", "into", "through", "during",
    "before", "after", "above", "below", "between", "under", "again",
    "further", "then", "once", "here", "there", "when", "where", "why",
    "how", "all", "both", "each", "every", "few", "more", "most", "other",
    "some", "such", "no", "nor", "not", "only", "own", "same", "so",
    "than", "too", "very", "just", "because", "but", "and", "or", "if",
    "while", "although", "however", "also", "thus", "therefore", "yet",
    "this", "that", "these", "those", "it", "its", "we", "they", "them",
    "their", "our", "my", "your", "he", "she", "his", "her", "which",
    "who", "whom", "whose", "about", "using", "based", "used", "within",
    "的", "了", "在", "是", "我", "有", "和", "就", "不", "人", "都", "一",
    "一个", "上", "也", "很", "到", "说", "要", "去", "你", "会", "着",
    "没有", "看", "好", "自己", "这", "他", "她", "它", "们", "那", "些",
    "所", "为", "所以", "因为", "但是", "然而", "因此", "如果", "虽然",
    "可以", "可能", "应该", "已经", "并且", "或者", "以及", "还是",
    "与", "中", "等", "对", "将", "被", "把", "从", "以", "及", "其",
    "如", "或", "但", "而", "且", "该", "则", "于", "之", "能", "着",
    "进行", "通过", "使用", "基于", "本文", "研究", "表明", "发现",
    # Blacklist keywords (cover page / competition template text)
    "打印并签名", "赛区评阅编号", "高教社杯", "承诺书", "参赛规则",
    "数学建模竞赛", "格式规范", "编号专用页", "评阅编号", "指导教师",
    "指导老师", "联系电话", "手机号码", "学号", "班级", "学院",
    "专业", "日期", "年月日", "填写说明", "注意事项", "附录",
}


def _extract_keywords_simple(text: str) -> list[str]:
    """Extract keywords from text using simple frequency analysis with stopword removal."""
    import re
    import collections

    # Tokenize: split on non-alphanumeric (keep Chinese chars and Latin words)
    tokens = re.findall(r"[a-zA-Z]{3,}|[一-鿿]{2,}", text)
    filtered = [t.lower() for t in tokens if t.lower() not in _STOPWORDS and len(t) >= 3]
    counter = collections.Counter(filtered)
    return [word for word, _ in counter.most_common(20)]


# Research method keywords for detection
_METHOD_PATTERNS = [
    ("问卷调查", "问卷调查"),
    ("双盲对照", "双盲对照"),
    ("双盲", "双盲实验"),
    ("randomized controlled trial", "随机对照试验(RCT)"),
    ("RCT", "随机对照试验(RCT)"),
    ("质性访谈", "质性访谈"),
    ("半结构化访谈", "半结构化访谈"),
    ("semi-structured interview", "半结构化访谈"),
    ("焦点小组", "焦点小组访谈"),
    ("focus group", "焦点小组访谈"),
    ("元分析", "元分析(Meta-Analysis)"),
    ("meta-analysis", "元分析(Meta-Analysis)"),
    ("系统综述", "系统综述"),
    ("systematic review", "系统综述"),
    ("田野调查", "田野调查"),
    ("field study", "田野调查"),
    ("民族志", "民族志"),
    ("ethnography", "民族志"),
    ("计算建模", "计算建模"),
    ("computational model", "计算建模"),
    ("仿真实验", "仿真实验"),
    ("simulation", "仿真实验"),
    ("纵向追踪", "纵向追踪研究"),
    ("longitudinal", "纵向追踪研究"),
    ("案例研究", "案例研究"),
    ("case study", "案例研究"),
    ("内容分析", "内容分析"),
    ("content analysis", "内容分析"),
    ("话语分析", "话语分析"),
    ("discourse analysis", "话语分析"),
    ("语料库", "语料库分析"),
    ("corpus", "语料库分析"),
    ("脑成像", "脑成像(fMRI/EEG)"),
    ("fMRI", "脑成像(fMRI/EEG)"),
    ("EEG", "脑成像(fMRI/EEG)"),
    ("深度访谈", "深度访谈"),
    ("in-depth interview", "深度访谈"),
    ("混合方法", "混合方法研究"),
    ("mixed method", "混合方法研究"),
    ("ab test", "A/B测试"),
    ("ablation", "消融实验"),
    ("消融", "消融实验"),
    ("回归分析", "回归分析"),
    ("结构方程", "结构方程模型(SEM)"),
    ("深度学习", "深度学习"),
    ("deep learning", "深度学习"),
    ("自然语言处理", "自然语言处理(NLP)"),
    ("NLP", "自然语言处理(NLP)"),
    ("机器学习", "机器学习"),
    ("machine learning", "机器学习"),
]


def _detect_methods(text: str) -> list[str]:
    """Detect research methods mentioned in text using keyword matching."""
    text_lower = text.lower()
    found = []
    for pattern, label in _METHOD_PATTERNS:
        if pattern.lower() in text_lower and label not in found:
            found.append(label)
        if len(found) >= 5:
            break
    return found[:5] if found else ["定量/定性分析"]


def _summarize_text(text: str) -> str:
    """Extract a one-line summary from abstract/intro text."""
    import re
    if not text.strip():
        return "学术论文"
    # Clean up excessive whitespace
    cleaned = re.sub(r"\s+", " ", text.strip())
    # Take first meaningful sentence (ends with period, or first 120 chars)
    sentences = re.split(r"[。.！!？?\n]", cleaned)
    for s in sentences:
        s = s.strip()
        if len(s) > 15 and len(s) < 200:
            return s[:200]
    return cleaned[:200] if cleaned else "学术论文"


def _format_evidence_refs(evidence_refs: list) -> str:
    """Convert evidence_refs objects to a single display-friendly string."""
    if not evidence_refs or not isinstance(evidence_refs, list):
        return ""
    parts = []
    for ref in evidence_refs:
        if not isinstance(ref, dict):
            parts.append(str(ref))
            continue
        aspect = ref.get("aspect", "")
        evidence_text = ref.get("evidence", "")
        if aspect and evidence_text:
            parts.append(f"[{aspect}] {evidence_text}")
        elif evidence_text:
            parts.append(evidence_text)
        elif aspect:
            parts.append(f"[{aspect}]")
    return "\n\n".join(parts)


def map_results(result: dict[str, Any]) -> dict[str, Any]:
    """Keep the native five dimensions and add frontend-compatible engine aliases."""
    aliases = {
        "data_reliability": "methodology",
        "ethics_bias": "ethics",
        "logical_rigor": "logic",
        "innovation": "innovation",
        "academic_impact": "academic_impact",
    }
    engines: dict[str, Any] = {}
    for agent, value in result.get("final_results", {}).items():
        if not isinstance(value, dict):
            continue
        raw_refs = value.get("evidence_refs", [])
        engines[aliases.get(agent, agent)] = {
            **value,
            "core_conclusion": value.get("summary", ""),
            "evidence": _format_evidence_refs(raw_refs) if isinstance(raw_refs, list) else str(raw_refs or ""),
            "evidence_refs": raw_refs,
            "actionable_advice": [
                issue.get("suggestion") for issue in value.get("issues", [])
                if isinstance(issue, dict) and issue.get("suggestion")
            ],
        }
    scores = [
        item["score"] for item in engines.values()
        if isinstance(item.get("score"), (int, float))
    ]
    paper = result.get("paper_data", {})
    return {
        "evaluationStatus": result.get("evaluation_status"),
        "overallScore": result.get("weighted_score") if result.get("weighted_score") is not None else (round(sum(scores) / len(scores), 1) if scores else None),
        "scoringPolicy": result.get("scoring_policy", {}),
        "researchProfile": result.get("agent_results", {}).get("research_profile", {}),
        "auditPassed": result.get("audit_passed", False),
        "processingTimeSeconds": result.get("processing_time_seconds"),
        "stageMetrics": result.get("stage_metrics", {}),
        "paperTitle": paper.get("paper_info", {}).get("title", ""),
        "paperJournal": paper.get("metadata", {}).get("journal", ""),
        "engines": engines,
        "textAnchors": paper.get("content", {}).get("text_anchors", paper.get("text_anchors", [])),
        "limitations": result.get("system_limitations", []),
        "errorSummary": result.get("error_summary", {}),
    }


async def save_upload(upload: UploadFile) -> tuple[Path, str]:
    suffix = Path(upload.filename or "").suffix.lower()
    if suffix not in SUPPORTED_SUFFIXES:
        raise HTTPException(415, f"不支持的文件类型：{suffix or 'unknown'}")
    content = await upload.read(MAX_UPLOAD_BYTES + 1)
    if not content:
        raise HTTPException(400, "上传文件为空")
    if len(content) > MAX_UPLOAD_BYTES:
        raise HTTPException(413, f"文件不能超过 {MAX_UPLOAD_BYTES // 1024 // 1024} MB")
    digest = hashlib.sha256(content).hexdigest()
    path = UPLOAD_DIR / f"{uuid.uuid4().hex}{suffix}"
    path.write_bytes(content)
    return path, digest


@app.post("/api/review")
async def review(
    file: UploadFile = File(...),
    domain: str = Form("auto-detect"),
    visual_mode: str = Form("text"),
    max_visual_pages: int = Form(5),
    visual_pages: str = Form(""),
    force: bool = Form(False),
    force_refresh: str = Form("false"),
    priority_token: str = Form(""),
    api_key: str = Form(""),
    base_url: str = Form(""),
    model_name: str = Form(""),
    language: str = Form("zh"),
    review_mode: str = Form("preset"),
    custom_weights: str = Form(""),
):
    # Normalize force_refresh to boolean
    force_refresh_bool = force_refresh.lower() == "true"
    visual_mode = visual_mode.strip().lower()
    if visual_mode not in {"text", "limited", "full"}:
        raise HTTPException(422, "visual_mode 只能是 text、limited 或 full")
    if max_visual_pages < 1 or max_visual_pages > 50:
        raise HTTPException(422, "max_visual_pages 必须在 1 到 50 之间")
    try:
        from data_processor import parse_visual_pages
        selected_pages = parse_visual_pages(visual_pages)
    except (ValueError, TypeError) as exc:
        raise HTTPException(422, f"visual_pages 格式错误：{exc}") from exc
    if selected_pages and visual_mode == "text":
        visual_mode = "limited"
    extract_visuals = visual_mode != "text"
    effective_max_pages = None if visual_mode == "full" or selected_pages else max_visual_pages

    filename = Path(file.filename or "document").name
    try:
        path, file_digest = await save_upload(file)
    except HTTPException:
        raise
    except Exception as exc:
        ticket_id = archive_ticket("upload", exc, {"filename": filename})
        raise HTTPException(500, f"上传失败，工单编号：{ticket_id}") from exc

    cache_settings = {
        "domain": domain,
        "visual_mode": visual_mode,
        "max_visual_pages": effective_max_pages,
        "visual_pages": selected_pages,
    }
    if cache_settings == {
        "domain": "auto-detect",
        "visual_mode": "text",
        "max_visual_pages": 5,
        "visual_pages": None,
    }:
        digest = file_digest
    else:
        digest = hashlib.sha256(
            (file_digest + json.dumps(cache_settings, sort_keys=True, ensure_ascii=False)).encode("utf-8")
        ).hexdigest()

    async def stream():
        started = time.monotonic()
        logger.info(
            "[REVIEW] force_refresh=%s file_hash=%s filename=%s digest=%s",
            force_refresh_bool, file_digest, filename, digest,
        )
        yield ndjson({"type": "progress", "stage": "upload", "message": "文件接收完成"})

        # --- Fast metadata extraction (before cache check or main review) ---
        pdf_metadata = None
        try:
            pdf_metadata = await asyncio.to_thread(extract_pdf_metadata, str(path))
            logger.info("[METADATA] Extracted: pages=%s words=%s summary=%s core_kw=%s exp_methods=%s",
                        pdf_metadata.get("pages"), pdf_metadata.get("words"),
                        (pdf_metadata.get("summary") or "")[:60],
                        pdf_metadata.get("core_keywords"),
                        pdf_metadata.get("exp_methods"))
            yield ndjson({"type": "metadata", "data": pdf_metadata})
        except Exception as meta_exc:
            logger.warning("Metadata extraction failed (non-fatal): %s", meta_exc)
            pdf_metadata = {
                "pages": 0, "words": 0, "summary": "未能提取到论文摘要",
                "core_keywords": ["核心问题", "研究对象", "关键变量"],
                "sub_keywords": ["数据特征", "约束条件", "影响因素"],
                "exp_methods": ["文本解析", "逻辑推演", "交叉验证"],
            }
            yield ndjson({"type": "metadata", "data": pdf_metadata})

        try:
            cache_hit = False
            if not force and not force_refresh_bool:
                with db() as connection:
                    cached = connection.execute(
                        "SELECT id,report_data FROM reports WHERE file_hash=? ORDER BY created_at DESC LIMIT 1",
                        (digest,),
                    ).fetchone()
                if cached:
                    payload = json.loads(cached["report_data"])
                    if payload.get("auditPassed") is True and payload.get("evaluationStatus") == "success":
                        cache_hit = True
                        logger.info("[CACHE] HIT file_hash=%s report_id=%s", digest[:16], cached["id"][:8])
                        payload.update({"reportId": cached["id"], "cached": True, "elapsedSeconds": 0})
                        record_metric("review", "cache_hit", time.monotonic() - started, {"file_hash": digest})
                        yield ndjson({"type": "result", "data": payload})
                        return
                    logger.info(
                        "[CACHE] Ignoring unaudited or incomplete report file_hash=%s report_id=%s",
                        digest[:16], cached["id"][:8],
                    )
                else:
                    logger.info("[CACHE] MISS file_hash=%s", digest[:16])

            # --- Force refresh: delete cache files and DB records ---
            if force_refresh_bool:
                logger.info("[FORCE_REFRESH] Deleting cache for file_hash=%s", digest[:16])
                # Delete pipeline cache directory
                import shutil
                pipeline_cache_dir = CACHE_ROOT / digest
                if pipeline_cache_dir.exists():
                    shutil.rmtree(pipeline_cache_dir, ignore_errors=True)
                    logger.info("[FORCE_REFRESH] Deleted pipeline cache dir: %s", pipeline_cache_dir)
                # Delete DB cache records
                with db() as connection:
                    deleted = connection.execute(
                        "DELETE FROM reports WHERE file_hash=?",
                        (digest,),
                    )
                    logger.info("[FORCE_REFRESH] Deleted %s DB cache records for file_hash=%s",
                                deleted.rowcount if deleted.rowcount else 0, digest[:16])
                logger.info("[FORCE_REFRESH] Cache cleared, proceeding to full review for file_hash=%s", digest[:16])

            logger.info("[LLM] Starting main review pipeline for file_hash=%s", digest[:16])
            is_priority = priority_entitlement(priority_token)
            await scheduler.acquire(is_priority)
            if is_priority:
                priority_entitlement(priority_token, consume=True)
            # v5.1: parse custom_weights if review_mode is custom
            custom_weights_dict = None
            if review_mode == "custom":
                if not custom_weights or not custom_weights.strip():
                    yield ndjson({
                        "type": "error",
                        "code": "INVALID_CUSTOM_WEIGHTS",
                        "message": "自定义审查模式必须提供 custom_weights",
                    })
                    return
                try:
                    custom_weights_dict = json.loads(custom_weights)
                except json.JSONDecodeError as exc:
                    yield ndjson({
                        "type": "error",
                        "code": "INVALID_CUSTOM_WEIGHTS",
                        "message": f"自定义权重 JSON 解析失败：{exc}",
                    })
                    return
            mode_labels = {"text": "纯文本", "limited": "受限视觉", "full": "全文视觉"}
            yield ndjson({
                "type": "progress",
                "stage": "review",
                "message": f"正在执行{mode_labels[visual_mode]}审查和五维评价",
                "visualMode": visual_mode,
            })
            try:
                event_loop = asyncio.get_running_loop()
                progress_queue: asyncio.Queue[dict[str, Any]] = asyncio.Queue()

                def publish_progress(event: dict[str, Any]) -> None:
                    payload = {"type": "progress", **event}
                    try:
                        event_loop.call_soon_threadsafe(progress_queue.put_nowait, payload)
                    except RuntimeError:
                        # 请求已结束或事件循环已关闭时忽略迟到通知。
                        pass

                review_task = asyncio.create_task(asyncio.to_thread(
                    main_pipeline,
                    str(path),
                    extract_visuals=extract_visuals,
                    max_visual_pages=effective_max_pages,
                    visual_pages=selected_pages,
                    output_dir=str(BASE_DIR / "runtime" / "assets" / digest),
                    review_context={"target_domain": domain},
                    api_key=api_key or None,
                    base_url=base_url or None,
                    model_name=model_name or None,
                    force_refresh=force_refresh_bool,
                    progress_callback=publish_progress,
                    # v5.1: weight mode parameters
                    review_mode=review_mode,
                    custom_weights=custom_weights_dict,
                    domain_hint=domain if domain != "auto-detect" else None,
                ))
                deadline = event_loop.time() + REVIEW_TIMEOUT_SECONDS
                last_progress_stage = "review"
                last_progress_event: dict[str, Any] = {"stage": "review"}
                stage_wait_started = time.monotonic()
                while True:
                    if review_task.done():
                        while not progress_queue.empty():
                            yield ndjson(progress_queue.get_nowait())
                        result = await review_task
                        break
                    remaining = deadline - event_loop.time()
                    if remaining <= 0:
                        review_task.cancel()
                        raise asyncio.TimeoutError("完整审查超过允许时限")
                    try:
                        progress_event = await asyncio.wait_for(
                            progress_queue.get(), timeout=min(PROGRESS_HEARTBEAT_SECONDS, remaining)
                        )
                        next_stage = str(progress_event.get("stage") or last_progress_stage)
                        # Every concrete backend event starts a new observable wait period.
                        # Heartbeats emitted below never enter this queue, so they do not reset it.
                        stage_wait_started = time.monotonic()
                        last_progress_stage = next_stage
                        last_progress_event = progress_event
                        yield ndjson(progress_event)
                    except asyncio.TimeoutError:
                        if event_loop.time() >= deadline:
                            review_task.cancel()
                            raise asyncio.TimeoutError("完整审查超过允许时限")
                        stage_wait_seconds = max(0, int(time.monotonic() - stage_wait_started))
                        if last_progress_stage == "audit":
                            attempt = last_progress_event.get("attempt")
                            max_attempts = last_progress_event.get("maxAttempts")
                            attempt_label = (
                                f"第 {attempt}/{max_attempts} 次"
                                if attempt is not None and max_attempts is not None
                                else "当前"
                            )
                            if stage_wait_seconds < 12:
                                wait_segment = 0
                                heartbeat_message = (
                                    f"一致性复核{attempt_label}请求已发送，正在等待模型返回完整 JSON，"
                                    f"已等待 {stage_wait_seconds} 秒"
                                )
                            elif stage_wait_seconds < 28:
                                wait_segment = 1
                                heartbeat_message = (
                                    "模型服务仍在生成结构化复核响应，当前未收到错误，"
                                    f"已等待 {stage_wait_seconds} 秒"
                                )
                            elif stage_wait_seconds < 48:
                                wait_segment = 2
                                heartbeat_message = (
                                    "尚未收到完整复核响应，系统正保持请求并等待全部字段生成，"
                                    f"已等待 {stage_wait_seconds} 秒"
                                )
                            else:
                                wait_segment = 3
                                heartbeat_message = (
                                    "复核响应生成时间较长，请求仍在进行且尚未触发错误，"
                                    f"已等待 {stage_wait_seconds} 秒"
                                )
                            heartbeat_key = f"audit-wait-{attempt or 0}-{wait_segment}"
                        else:
                            heartbeat_messages = {
                                "parsing": "正文解析与坐标索引仍在进行",
                                "evaluation": "评价引擎仍在分析论文并核对证据",
                                "report": "结构化报告与原文定位信息仍在整理",
                            }
                            stage_message = heartbeat_messages.get(
                                last_progress_stage,
                                "审查任务仍在运行，正在等待下一条阶段结果",
                            )
                            heartbeat_message = f"{stage_message}，本阶段已用时 {stage_wait_seconds} 秒"
                            heartbeat_key = (
                                f"{last_progress_stage}-wait-"
                                f"{last_progress_event.get('agent') or 'stage'}-"
                                f"{last_progress_event.get('phase') or 'running'}"
                            )
                        yield ndjson({
                            "type": "progress",
                            "stage": last_progress_stage,
                            "agent": last_progress_event.get("agent"),
                            "message": heartbeat_message,
                            "heartbeat": True,
                            "phase": "heartbeat",
                            "progressKey": heartbeat_key,
                            "attempt": last_progress_event.get("attempt"),
                            "maxAttempts": last_progress_event.get("maxAttempts"),
                            "elapsedSeconds": int(time.monotonic() - started),
                            "stageElapsedSeconds": stage_wait_seconds,
                        })
            finally:
                await scheduler.release()
            if result.get("evaluation_status") == "partial_failure":
                archive_ticket("evaluation_partial", RuntimeError("部分评价维度失败"),
                               result.get("error_summary", {}))
            # v5.1: 五维评价完成但 audit 未通过 — 降级报告
            if result.get("evaluation_status") == "success" and result.get("audit_passed") is not True:
                logger.warning("[LLM] Audit unavailable — serving degraded report")
                record_metric("review", "degraded", time.monotonic() - started,
                              {"file_hash": digest, "reason": "audit_unavailable"})
                yield ndjson({
                    "type": "warning",
                    "code": "AUDIT_DEGRADED",
                    "message": "最终一致性复核未完成，系统将展示多引擎初审降级报告。建议重新审查以获得终审核准版本。",
                })
                # fall through to normal result mapping
            elif result.get("evaluation_status") != "success" or result.get("audit_passed") is not True:
                evaluation_incomplete = result.get("evaluation_status") != "success"
                pipeline_error = result.get("error") if isinstance(result.get("error"), dict) else {}
                failure_reason = (
                    str(pipeline_error.get("message") or "五维评价未全部完成")
                    if evaluation_incomplete
                    else "最终一致性复核未通过"
                )
                error_code = (
                    str(pipeline_error.get("code") or "EVALUATION_INCOMPLETE")
                    if evaluation_incomplete
                    else "FINAL_AUDIT_INCOMPLETE"
                )
                ticket_id = archive_ticket(
                    "final_review_incomplete",
                    RuntimeError(failure_reason),
                    {
                        "filename": filename,
                        "file_hash": digest,
                        "evaluation_status": result.get("evaluation_status"),
                        "audit_passed": result.get("audit_passed"),
                    },
                )
                record_metric(
                    "review",
                    "failed",
                    time.monotonic() - started,
                    {"ticket_id": ticket_id, "reason": failure_reason},
                )
                yield ndjson({
                    "type": "error",
                    "code": error_code,
                    "message": f"{failure_reason}，系统不会展示或保存未经完整复核的结果。请重新审查。",
                    "ticketId": ticket_id,
                    "failedAgents": result.get("error_summary", {}).get("failed_agents", []),
                })
                return
            payload = map_results(result)
            payload["pipelineVersion"] = PIPELINE_VERSION
            payload["cached"] = False
            report_id = str(uuid.uuid4())
            with db() as connection:
                # On force_refresh, replace old cached records
                if force_refresh_bool:
                    deleted = connection.execute(
                        "DELETE FROM reports WHERE file_hash=?",
                        (digest,),
                    )
                    logger.info("[FORCE_REFRESH] Overwriting cache: deleted %s old records for file_hash=%s",
                                deleted.rowcount if deleted.rowcount else 0, digest[:16])
                connection.execute(
                    "INSERT INTO reports(id,filename,file_hash,report_data,created_at) VALUES(?,?,?,?,?)",
                    (report_id, filename, digest, json.dumps(payload, ensure_ascii=False), utc_now()),
                )
            logger.info("[LLM] Review complete — report_id=%s file_hash=%s elapsed=%.1fs",
                        report_id[:8], digest[:16], time.monotonic() - started)
            payload["reportId"] = report_id
            payload["elapsedSeconds"] = round(time.monotonic() - started, 3)
            record_metric("review", "success", time.monotonic() - started,
                          {"report_id": report_id, "priority": is_priority})
            yield ndjson({"type": "result", "data": payload})
        except Exception as exc:
            ticket_id = archive_ticket("review", exc, {"filename": filename, "file_hash": digest})
            record_metric("review", "failed", time.monotonic() - started, {"ticket_id": ticket_id})
            error_detail = str(exc)[:500]
            # Write full traceback to system log for developer diagnostics
            logging.getLogger("AIReview.API").exception(
                "Review FAILED for %s | ticket_id=%s | %s", filename, ticket_id, error_detail
            )
            yield ndjson({
                "type": "error",
                "code": "REVIEW_FAILED",
                "message": f"审查失败: {error_detail} (问题已归档，工单编号: {ticket_id})",
                "ticketId": ticket_id,
            })
        finally:
            # Windows may keep a freshly inspected PDF handle alive for a few
            # milliseconds after PyMuPDF raises or returns.  Retrying prevents
            # that transient lock from turning an otherwise valid stream into
            # a REVIEW_FAILED response.
            for cleanup_attempt in range(5):
                try:
                    path.unlink(missing_ok=True)
                    break
                except PermissionError:
                    if cleanup_attempt == 4:
                        logger.warning("Temporary upload is still locked; deferred cleanup: %s", path)
                    else:
                        await asyncio.sleep(0.05 * (cleanup_attempt + 1))

    return StreamingResponse(stream(), media_type="application/x-ndjson",
                             headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})


class FeedbackRequest(BaseModel):
    report_id: str | None = None
    rating: int | None = Field(default=None, ge=1, le=5)
    category: str = Field(default="suggestion", max_length=50)
    content: str = Field(min_length=1, max_length=5000)
    contact: str | None = Field(default=None, max_length=200)


@app.post("/api/feedback")
async def submit_feedback(request: FeedbackRequest):
    feedback_id = str(uuid.uuid4())
    claim_token = uuid.uuid4().hex
    with db() as connection:
        if request.report_id:
            exists = connection.execute("SELECT 1 FROM reports WHERE id=?", (request.report_id,)).fetchone()
            if not exists:
                raise HTTPException(404, "报告不存在")
        connection.execute(
            "INSERT INTO feedback(id,report_id,rating,category,content,contact,created_at) VALUES(?,?,?,?,?,?,?)",
            (feedback_id, request.report_id, request.rating, request.category,
             request.content, request.contact, utc_now()),
        )
        if request.report_id:
            for entitlement_type, remaining in (
                ("watermark_free_report", 1),
                ("deep_diagnosis", 1),
                ("priority_queue", 1),
                ("release_updates", 1),
            ):
                connection.execute(
                    "INSERT INTO entitlements"
                    "(id,report_id,type,remaining,claim_token,created_at) VALUES(?,?,?,?,?,?) "
                    "ON CONFLICT(report_id,type) DO UPDATE SET "
                    "remaining=excluded.remaining,claim_token=excluded.claim_token,"
                    "created_at=excluded.created_at",
                    (str(uuid.uuid4()), request.report_id, entitlement_type,
                     remaining, claim_token, utc_now()),
                )
    return {
        "feedbackId": feedback_id,
        "unlocked": bool(request.report_id),
        "entitlementToken": claim_token if request.report_id else None,
        "entitlements": [
            "watermark_free_report", "deep_diagnosis", "priority_queue", "release_updates"
        ] if request.report_id else [],
    }


@app.get("/api/reports")
async def list_reports(limit: int = 50):
    limit = max(1, min(limit, 100))
    with db() as connection:
        rows = connection.execute(
            "SELECT id,filename,created_at FROM reports ORDER BY created_at DESC LIMIT ?", (limit,)
        ).fetchall()
    return [dict(row) for row in rows]


@app.get("/api/reports/{report_id}")
async def get_report(report_id: str):
    with db() as connection:
        row = connection.execute("SELECT * FROM reports WHERE id=?", (report_id,)).fetchone()
        rights = connection.execute(
            "SELECT type,remaining FROM entitlements WHERE report_id=?", (report_id,)
        ).fetchall()
    if not row:
        raise HTTPException(404, "报告不存在")
    result = json.loads(row["report_data"])
    result["reportId"] = report_id
    result["entitlements"] = {item["type"]: item["remaining"] for item in rights}
    return result


@app.get("/api/reports/{report_id}/export")
async def export_report(report_id: str, entitlement_token: str = ""):
    """Generate a Markdown report; a valid reward token removes its watermark."""
    with db() as connection:
        row = connection.execute(
            "SELECT filename,report_data FROM reports WHERE id=?", (report_id,)
        ).fetchone()
    if not row:
        raise HTTPException(404, "报告不存在")
    data = json.loads(row["report_data"])
    no_watermark = entitlement(
        report_id, "watermark_free_report", entitlement_token, consume=True
    )
    lines = [f"# {data.get('paperTitle') or row['filename']} 学术审查报告", ""]
    if not no_watermark:
        lines.extend(["> AI 学术审查系统辅助审查报告（普通版水印）", ""])
    lines.extend([
        f"- 综合分：{data.get('overallScore')}",
        f"- 评价状态：{data.get('evaluationStatus')}",
        "",
        "## 分维度结果",
    ])
    for name, item in data.get("engines", {}).items():
        lines.extend([
            "",
            f"### {name}",
            f"- 分数：{item.get('score')}",
            f"- 置信度：{item.get('confidence')}",
            f"- 结论：{item.get('summary', item.get('core_conclusion', ''))}",
        ])
    return PlainTextResponse(
        "\n".join(lines),
        media_type="text/markdown; charset=utf-8",
        headers={"Content-Disposition": f'attachment; filename="{report_id}.md"'},
    )


@app.post("/api/reports/{report_id}/deep-diagnosis")
async def deep_diagnosis(report_id: str, entitlement_token: str):
    """Consume one reward and build a detailed diagnosis from verified report data."""
    if not entitlement(report_id, "deep_diagnosis", entitlement_token, consume=True):
        raise HTTPException(403, "没有可用的深度诊断权益")
    with db() as connection:
        row = connection.execute("SELECT report_data FROM reports WHERE id=?", (report_id,)).fetchone()
    if not row:
        raise HTTPException(404, "报告不存在")
    data = json.loads(row["report_data"])
    findings = []
    for dimension, result in data.get("engines", {}).items():
        findings.append({
            "dimension": dimension,
            "score": result.get("score"),
            "risks": result.get("issues", []),
            "limitations": result.get("limitations", []),
            "recommended_actions": result.get("actionable_advice", []),
        })
    return {
        "reportId": report_id,
        "type": "deep_diagnosis",
        "findings": findings,
        "priorityActions": [
            action for finding in findings for action in finding["recommended_actions"]
        ][:10],
    }


@app.get("/api/releases")
async def release_notes(entitlement_token: str = ""):
    if entitlement_token:
        with db() as connection:
            allowed = connection.execute(
                "SELECT 1 FROM entitlements "
                "WHERE type='release_updates' AND claim_token=? AND remaining>0",
                (entitlement_token,),
            ).fetchone()
        if not allowed:
            raise HTTPException(403, "版本通知权益无效")
    with db() as connection:
        rows = connection.execute("SELECT * FROM releases ORDER BY created_at DESC").fetchall()
    return [dict(row) for row in rows]


@app.get("/api/tickets")
async def list_tickets(limit: int = 50):
    limit = max(1, min(limit, 100))
    with db() as connection:
        rows = connection.execute(
            "SELECT * FROM tickets ORDER BY created_at DESC LIMIT ?", (limit,)
        ).fetchall()
    return [dict(row) for row in rows]


@app.get("/api/metrics")
async def metrics():
    with db() as connection:
        rows = connection.execute(
            "SELECT operation,status,COUNT(*) count,"
            "ROUND(AVG(duration_seconds),3) average_seconds "
            "FROM metrics GROUP BY operation,status"
        ).fetchall()
    return [dict(row) for row in rows]


@app.get("/api/health")
async def health():
    return {
        "status": "ok",
        "version": PIPELINE_VERSION,
        "database": str(DB_PATH),
        "configured": bool(os.getenv("DASHSCOPE_API_KEY")),
    }


# ---------------------------------------------------------------------------
# Developer log viewer -- standalone HTML page, no React dependency
# ---------------------------------------------------------------------------
LOG_VIEWER_HTML = r"""<!DOCTYPE html>
<html lang="zh-CN">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>AI学术审查系统 - 开发日志面板</title>
<style>
  * { box-sizing:border-box; margin:0; padding:0; }
  body { font-family: 'JetBrains Mono','Fira Code','Consolas',monospace; background:#0d1117; color:#c9d1d9; padding:16px; min-height:100vh; }
  h1 { font-size:15px; color:#58a6ff; margin-bottom:8px; font-weight:600; border-bottom:1px solid #21262d; padding-bottom:10px; }
  .toolbar { display:flex; gap:10px; margin-bottom:12px; align-items:center; flex-wrap:wrap; }
  .toolbar button { background:#21262d; color:#c9d1d9; border:1px solid #30363d; padding:6px 14px; border-radius:6px; cursor:pointer; font-size:12px; font-family:inherit; }
  .toolbar button:hover { background:#30363d; }
  .toolbar .status { font-size:11px; color:#8b949e; margin-left:auto; }
  .log-container { background:#161b22; border:1px solid #21262d; border-radius:8px; padding:14px; height:calc(100vh - 140px); overflow-y:auto; white-space:pre-wrap; word-break:break-all; font-size:12px; line-height:1.65; }
  .log-container .ERROR { color:#f85149; }
  .log-container .WARNING { color:#d2991d; }
  .log-container .INFO { color:#c9d1d9; }
  .log-container .DEBUG { color:#8b949e; }
  ::-webkit-scrollbar { width:8px; }
  ::-webkit-scrollbar-track { background:#0d1117; }
  ::-webkit-scrollbar-thumb { background:#30363d; border-radius:4px; }
</style>
</head>
<body>
<h1>AI学术审查系统 - 开发日志面板 (v""" + PIPELINE_VERSION + """)</h1>
<div class="toolbar">
  <button onclick="fetchLogs()">Refresh</button>
  <button onclick="toggleAuto()" id="autoBtn">Auto-Refresh: ON</button>
  <button onclick="clearDisplay()">Clear Display</button>
  <span class="status" id="status">Idle</span>
</div>
<div class="log-container" id="log"></div>
<script>
  let autoRefresh = true;
  let lastLength = 0;
  const logEl = document.getElementById('log');
  const statusEl = document.getElementById('status');

  async function fetchLogs() {
    try {
      const resp = await fetch('/api/dev/logs/raw?since=' + lastLength);
      if (!resp.ok) throw new Error('HTTP ' + resp.status);
      const data = await resp.json();
      if (data.lines && data.lines.length > 0) {
        for (const line of data.lines) {
          const div = document.createElement('div');
          div.textContent = line;
          if (line.includes('[ERROR]')) div.className = 'ERROR';
          else if (line.includes('[WARNING]')) div.className = 'WARNING';
          else if (line.includes('[DEBUG]')) div.className = 'DEBUG';
          else div.className = 'INFO';
          logEl.appendChild(div);
        }
        lastLength = data.total_length;
        logEl.scrollTop = logEl.scrollHeight;
      }
      statusEl.textContent = new Date().toLocaleTimeString() + ' | ' + data.total_length + ' lines';
    } catch(e) {
      statusEl.textContent = 'Error: ' + e.message;
    }
  }

  function toggleAuto() {
    autoRefresh = !autoRefresh;
    document.getElementById('autoBtn').textContent = 'Auto-Refresh: ' + (autoRefresh ? 'ON' : 'OFF');
  }

  function clearDisplay() {
    logEl.innerHTML = '';
    lastLength = 0;
  }

  fetchLogs();
  setInterval(() => { if (autoRefresh) fetchLogs(); }, 3000);
</script>
</body>
</html>"""


@app.get("/dev/logs", tags=["开发者"], response_class=HTMLResponse)
async def dev_logs_panel():
    """Standalone HTML log viewer -- open in a separate browser tab."""
    return HTMLResponse(content=LOG_VIEWER_HTML)


@app.get("/api/dev/logs/raw", tags=["开发者"])
async def dev_logs_raw(since: int = 0):
    """Return log lines since a given byte offset.  Used by the AJAX poller above."""
    try:
        text = SYSTEM_LOG_PATH.read_text(encoding="utf-8", errors="replace")
    except FileNotFoundError:
        return {"lines": [], "total_length": 0}
    tail = text[since:] if since < len(text) else ""
    lines = tail.splitlines()
    return {"lines": lines[-500:], "total_length": len(text)}


@app.delete("/api/cache", tags=["运维"])
async def clear_cache():
    """Remove the pipeline cache directory and any cached reports."""
    import shutil
    removed_count = 0
    if CACHE_ROOT.exists():
        removed_count = len(list(CACHE_ROOT.rglob("*")))
        shutil.rmtree(CACHE_ROOT, ignore_errors=True)
        logger.info("Cache cleared: %d files removed from %s", removed_count, CACHE_ROOT)
    # Also clear file-hash-based report cache entries from DB
    with db() as connection:
        connection.execute("DELETE FROM reports WHERE file_hash IS NOT NULL")
    return {"cleared": True, "cache_files_removed": removed_count, "message": "缓存已清除"}


# ---------------------------------------------------------------------------
# Frontend static file serving (MUST be registered AFTER all API routes)
# ---------------------------------------------------------------------------
if FRONTEND_DIST.is_dir() and (FRONTEND_DIST / "index.html").is_file():
    @app.middleware("http")
    async def spa_fallback(request: Request, call_next):
        response = await call_next(request)
        if response.status_code == 404 and not request.url.path.startswith("/api/"):
            return FileResponse(
                str(FRONTEND_DIST / "index.html"),
                headers={"Cache-Control": "no-store, no-cache, must-revalidate, max-age=0"},
            )
        if not request.url.path.startswith(("/api/", "/dev/")):
            response.headers["Cache-Control"] = "no-store, no-cache, must-revalidate, max-age=0"
            response.headers["Pragma"] = "no-cache"
            response.headers["Expires"] = "0"
        return response

    app.mount("/", StaticFiles(directory=str(FRONTEND_DIST), html=True), name="static")
    logger.info("Frontend static files mounted from %s", FRONTEND_DIST)
else:
    @app.get("/", tags=["说明"])
    async def root():
        return {
            "name": "AI 学术审查系统后端",
            "status": "running",
            "frontend_included": False,
            "message": "前端静态文件未找到，请先执行 npm run build 构建前端产物。",
            "expected_path": str(FRONTEND_DIST),
            "api_docs": "/docs",
            "health_check": "/api/health",
        }

    logger.warning("Frontend dist not found at %s; serving API-only JSON root", FRONTEND_DIST)


if __name__ == "__main__":
    import uvicorn
    logging.basicConfig(level=logging.INFO)
    uvicorn.run("api:app", host=os.getenv("HOST", "127.0.0.1"),
                port=int(os.getenv("PORT", "8000")), reload=False)
