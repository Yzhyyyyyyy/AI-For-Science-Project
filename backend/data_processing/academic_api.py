# academic_api.py
"""Academic metadata APIs and OpenAI-compatible AI helpers."""
from __future__ import annotations

import base64
import json
import mimetypes
import os
import re
import threading
import time
from pathlib import Path
from typing import Any

import requests
from openai import OpenAI


DEFAULT_QWEN_BASE_URL = "https://ws-nkoer43fucfrzdgs.cn-beijing.maas.aliyuncs.com/compatible-mode/v1"
_SEMANTIC_CACHE: dict[str, tuple[float, dict]] = {}
_SEMANTIC_CACHE_LOCK = threading.Lock()
_SEMANTIC_POSITIVE_TTL = 86400.0
_SEMANTIC_NEGATIVE_TTL = 600.0


def _assert_qwen_model(model: str) -> str:
    """Backward-compatible name: accept any explicitly configured model."""
    if not model:
        raise ValueError("模型名称不能为空。")
    return model.strip()


def get_qwen_client(api_key: str | None = None, base_url: str | None = None) -> OpenAI:
    """返回 QWEN/OpenAI-compatible 客户端。"""
    effective_api_key = api_key or os.getenv("DASHSCOPE_API_KEY")
    if not effective_api_key:
        raise ValueError("未检测到 API Key，请在设置页面或 backend/.env 中配置。")

    return OpenAI(
        api_key=effective_api_key,
        base_url=base_url or os.getenv("QWEN_BASE_URL") or os.getenv("DASHSCOPE_BASE_URL") or DEFAULT_QWEN_BASE_URL,
        timeout=float(os.getenv("MODEL_TIMEOUT_SECONDS", "120")),
        max_retries=0,
    )


def _load_json_from_model_text(text: str, default: Any) -> Any:
    """兼容模型偶尔包裹 ```json 的情况。"""
    if not text:
        return default
    cleaned = text.strip()
    cleaned = re.sub(r"^```(?:json)?\s*", "", cleaned, flags=re.IGNORECASE)
    cleaned = re.sub(r"\s*```$", "", cleaned)
    try:
        return json.loads(cleaned)
    except json.JSONDecodeError:
        match = re.search(r"(\{.*\}|\[.*\])", cleaned, flags=re.DOTALL)
        if match:
            try:
                return json.loads(match.group(1))
            except json.JSONDecodeError:
                pass
    return default


def _chat_create_compatible(client: OpenAI, **request: Any) -> Any:
    """Retry once without optional JSON controls when a compatible gateway rejects them."""
    try:
        return client.chat.completions.create(**request)
    except Exception as exc:
        status = getattr(exc, "status_code", None)
        text = str(exc).lower()
        markers = (
            "response_format",
            "unsupported parameter",
            "unknown parameter",
            "unrecognized request argument",
        )
        if status not in {400, 422} or not any(marker in text for marker in markers):
            raise
        portable_request = dict(request)
        portable_request.pop("response_format", None)
        portable_request.pop("extra_body", None)
        return client.chat.completions.create(**portable_request)


def _image_to_data_url(image_path: str | Path) -> str:
    image_path = Path(image_path)
    mime_type = mimetypes.guess_type(str(image_path))[0] or "image/png"
    encoded = base64.b64encode(image_path.read_bytes()).decode("ascii")
    return f"data:{mime_type};base64,{encoded}"


def extract_metadata_via_qwen(
    header_text: str,
    model: str | None = None,
    api_key: str | None = None,
    base_url: str | None = None,
) -> dict:
    """
    调用 QWEN 大模型，从论文前几页/头部文本中高精度提取标题、作者列表以及预估期刊。
    """
    client = get_qwen_client(api_key=api_key, base_url=base_url)
    model = _assert_qwen_model(model or os.getenv("QWEN_TEXT_MODEL", "qwen-plus"))

    system_prompt = (
        "You are an expert scientific literature metadata extractor. "
        "Analyze the text extracted from the first pages of a paper and extract the: "
        "1. Paper Title ('title')\n"
        "2. List of Author Names ('authors', as a list of strings)\n"
        "3. Journal/Conference Name if mentioned ('journal_candidate')\n\n"
        "You must respond ONLY with a valid JSON object matching this structure:\n"
        "{\n"
        "  \"title\": \"string\",\n"
        "  \"authors\": [\"string\"],\n"
        "  \"journal_candidate\": \"string\"\n"
        "}"
    )

    user_prompt = f"Here is the text extracted from the header pages:\n\n{header_text[:6000]}"

    try:
        response = _chat_create_compatible(client,
            model=model,
            messages=[
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_prompt},
            ],
            response_format={"type": "json_object"},
            temperature=0.1,
        )

        result_json = _load_json_from_model_text(response.choices[0].message.content, {})
        return {
            "title": result_json.get("title") or "Unknown Title",
            "authors": result_json.get("authors") or [],
            "journal_candidate": result_json.get("journal_candidate") or "",
        }
    except Exception as e:
        print(f"[Warning] 调用 QWEN 提取元数据失败: {str(e)}")
        return {"title": "Unknown Title", "authors": [], "journal_candidate": ""}


def evaluate_journal_level_via_qwen(
    journal_name: str,
    model: str | None = None,
    api_key: str | None = None,
    base_url: str | None = None,
) -> str:
    """
    调用 QWEN 结合学术知识库评估期刊/会议等级。
    """
    if not journal_name or journal_name.lower() == "unknown":
        return "Unknown Level"

    client = get_qwen_client(api_key=api_key, base_url=base_url)
    model = _assert_qwen_model(model or os.getenv("QWEN_FAST_MODEL", "qwen-turbo"))

    system_prompt = (
        "You are an academic evaluation expert. Given a journal or conference name, "
        "determine its academic tier or rating commonly recognized in China, such as:\n"
        "- CCF Category (A, B, C) if it is in computer science.\n"
        "- SCI Quartile / Zone (Q1, Q2, Q3, Q4) / CAS Zone.\n"
        "- Core Journal (中文核心期刊/南大核心) for Chinese publications.\n"
        "Be extremely concise. Return ONLY the JSON object with the key 'level'."
    )

    user_prompt = f"Journal/Conference Name: {journal_name}"

    try:
        response = _chat_create_compatible(client,
            model=model,
            messages=[
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_prompt},
            ],
            response_format={"type": "json_object"},
            temperature=0.1,
        )
        result = _load_json_from_model_text(response.choices[0].message.content, {})
        return result.get("level", "Unknown Level")
    except Exception as e:
        print(f"[Warning] QWEN 评估期刊等级失败: {str(e)}")
        return "Unknown Level"


def detect_visual_regions_via_qwen(
    page_image_path: str | Path,
    page_number: int,
    model: str | None = None,
    max_regions: int = 20,
) -> list[dict]:
    """
    用 QWEN-VL 检测论文页面中的非纯文本视觉对象，并返回归一化裁剪框。

    返回字段示例：
    [
      {
        "type": "figure|table|chart|formula|algorithm|other",
        "label": "Figure 1",
        "caption": "...",
        "bbox": [x1, y1, x2, y2],   # 0~1000 归一化坐标，已要求包含下方一行注解
        "confidence": 0.86
      }
    ]
    """
    client = get_qwen_client()
    model = _assert_qwen_model(model or os.getenv("QWEN_VISION_MODEL", "qwen3-vl-plus"))
    data_url = _image_to_data_url(page_image_path)

    system_prompt = (
        "你是论文版面解析专家。你只能输出 JSON，不要输出解释。"
        "任务：识别页面中的所有非纯文本视觉对象，包括图片、图表、表格、流程图、算法框、重要公式块等。"
        "每个对象的 bbox 必须覆盖视觉对象本体以及其下方紧邻的一行题注/注解；"
        "如果题注在上方，也要一并覆盖。坐标使用页面左上角为原点的 0-1000 归一化坐标。"
    )
    user_prompt = (
        f"请解析这张论文第 {page_number} 页图片。"
        "输出 JSON 格式："
        "{\"regions\":[{\"type\":\"figure/table/chart/formula/algorithm/other\","
        "\"label\":\"图或表编号，没有则为空\","
        "\"caption\":\"题注/注解文本，没有则为空\","
        "\"bbox\":[x1,y1,x2,y2],\"confidence\":0.0}]}。"
        f"最多输出 {max_regions} 个区域；不要把普通段落正文作为区域。"
    )

    try:
        response = _chat_create_compatible(client,
            model=model,
            messages=[
                {"role": "system", "content": system_prompt},
                {
                    "role": "user",
                    "content": [
                        {"type": "text", "text": user_prompt},
                        {"type": "image_url", "image_url": {"url": data_url}},
                    ],
                },
            ],
            response_format={"type": "json_object"},
            temperature=0.0,
            max_tokens=2048,
        )
        payload = _load_json_from_model_text(response.choices[0].message.content, {"regions": []})
        regions = payload.get("regions", []) if isinstance(payload, dict) else []
        return _normalize_regions(regions, max_regions=max_regions)
    except Exception as e:
        print(f"[Warning] QWEN-VL 解析第 {page_number} 页视觉区域失败: {str(e)}")
        return []


def _normalize_regions(regions: Any, max_regions: int = 20) -> list[dict]:
    normalized: list[dict] = []
    if not isinstance(regions, list):
        return normalized

    allowed_types = {"figure", "table", "chart", "formula", "algorithm", "other"}
    for item in regions[:max_regions]:
        if not isinstance(item, dict):
            continue
        bbox = item.get("bbox") or item.get("normalized_bbox") or item.get("box")
        if not isinstance(bbox, list) or len(bbox) != 4:
            continue
        try:
            x1, y1, x2, y2 = [float(v) for v in bbox]
        except (TypeError, ValueError):
            continue
        x1, y1, x2, y2 = [max(0.0, min(1000.0, v)) for v in (x1, y1, x2, y2)]
        if x2 <= x1 or y2 <= y1 or (x2 - x1) < 15 or (y2 - y1) < 15:
            continue
        region_type = str(item.get("type") or "other").lower()
        if region_type not in allowed_types:
            region_type = "other"
        normalized.append(
            {
                "type": region_type,
                "label": str(item.get("label") or "").strip(),
                "caption": str(item.get("caption") or "").strip(),
                "bbox": [round(x1, 2), round(y1, 2), round(x2, 2), round(y2, 2)],
                "confidence": float(item.get("confidence") or 0.0),
            }
        )
    return normalized


def query_semantic_scholar_api(title: str) -> dict:
    """
    请求 Semantic Scholar 官方公开 API，检索论文的引用量和真实发表渠道。
    """
    if not title or title == "Unknown Title":
        return {}
    cache_key = re.sub(r"\s+", " ", title.strip().lower())
    now = time.monotonic()
    with _SEMANTIC_CACHE_LOCK:
        cached = _SEMANTIC_CACHE.get(cache_key)
        if cached and cached[0] > now:
            return dict(cached[1])

    base_url = "https://api.semanticscholar.org/graph/v1/paper/search"
    params = {
        "query": title,
        "limit": 1,
        "fields": "title,citationCount,venue,year",
    }

    headers = {"User-Agent": "ChallengeCup-AgentFlow/1.0"}

    try:
        response = requests.get(base_url, params=params, headers=headers, timeout=8)
        if response.status_code == 200:
            data = response.json()
            if data.get("total", 0) > 0 and len(data.get("data", [])) > 0:
                best_match = data["data"][0]
                result = {
                    "matched_title": best_match.get("title"),
                    "citations": best_match.get("citationCount", 0),
                    "venue": best_match.get("venue") or "Unknown Venue",
                    "year": best_match.get("year"),
                }
                with _SEMANTIC_CACHE_LOCK:
                    _SEMANTIC_CACHE[cache_key] = (now + _SEMANTIC_POSITIVE_TTL, result)
                return dict(result)
        print(f"[Warning] Semantic Scholar 未检索到相符文献, Status Code: {response.status_code}")
    except Exception as e:
        print(f"[Warning] 请求 Semantic Scholar API 发生异常: {str(e)}")

    with _SEMANTIC_CACHE_LOCK:
        _SEMANTIC_CACHE[cache_key] = (now + _SEMANTIC_NEGATIVE_TTL, {})
    return {}


def analyze_image_via_qwen(
    image_path: str | Path,
    model: str | None = None,
) -> dict:
    """
    用 QWEN-VL 对单张图片进行 OCR 与内容描述，用于把图片来源也转成评价 Agent 可用的文本/图像 JSON。
    """
    client = get_qwen_client()
    model = _assert_qwen_model(model or os.getenv("QWEN_VISION_MODEL", "qwen3-vl-plus"))
    data_url = _image_to_data_url(image_path)

    system_prompt = (
        "你是论文/科研材料图片解析专家。你只能输出 JSON，不要输出解释。"
        "请从图片中识别可读文字、标题、关键词、图表说明，并对图片内容做客观描述。"
    )
    user_prompt = (
        "请解析这张作为论文材料输入的图片，输出 JSON："
        "{\"title\":\"可能的标题，没有则为空\","
        "\"abstract\":\"可能的摘要，没有则为空\","
        "\"keywords\":[\"关键词\"],"
        "\"ocr_text\":\"图片中所有可读文字\","
        "\"description\":\"图片内容描述\","
        "\"caption\":\"图片中或图片下方的题注，没有则为空\","
        "\"open_data\":false,\"open_code\":false}。"
    )

    try:
        response = _chat_create_compatible(client,
            model=model,
            messages=[
                {"role": "system", "content": system_prompt},
                {
                    "role": "user",
                    "content": [
                        {"type": "text", "text": user_prompt},
                        {"type": "image_url", "image_url": {"url": data_url}},
                    ],
                },
            ],
            response_format={"type": "json_object"},
            temperature=0.0,
            max_tokens=2048,
        )
        payload = _load_json_from_model_text(response.choices[0].message.content, {})
        if not isinstance(payload, dict):
            payload = {}
        return {
            "title": str(payload.get("title") or "").strip(),
            "abstract": str(payload.get("abstract") or "").strip(),
            "keywords": payload.get("keywords") if isinstance(payload.get("keywords"), list) else [],
            "ocr_text": str(payload.get("ocr_text") or "").strip(),
            "description": str(payload.get("description") or "").strip(),
            "caption": str(payload.get("caption") or "").strip(),
            "open_data": bool(payload.get("open_data", False)),
            "open_code": bool(payload.get("open_code", False)),
        }
    except Exception as e:
        print(f"[Warning] QWEN-VL 图片解析失败: {str(e)}")
        return {
            "title": "",
            "abstract": "",
            "keywords": [],
            "ocr_text": "",
            "description": "",
            "caption": "",
            "open_data": False,
            "open_code": False,
        }
