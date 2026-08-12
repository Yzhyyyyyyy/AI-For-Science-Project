"""Markdown report generation for business pipeline runs."""

from __future__ import annotations

from datetime import datetime
from typing import Any

from .config import AGENTS
from .orchestrator import AgentRunResult


def build_case_report(case_id: str, case_data: dict[str, Any], results: list[AgentRunResult]) -> str:
    paper_info = case_data.get("paper_info", {})
    title = paper_info.get("title", case_id) if isinstance(paper_info, dict) else case_id
    lines = [
        f"# 五维评价报告：{title}",
        "",
        f"- 生成时间：{datetime.now().isoformat(timespec='seconds')}",
        f"- case_id：`{case_id}`",
        f"- Agent 数量：{len(results)}",
        "",
        "## 运行概览",
        "",
        "| Agent | 维度 | 版本 | 状态 | 尝试次数 | 分数 | 置信度 | 风险 | 错误 |",
        "|---|---|---|---|---:|---:|---:|---|---|",
    ]
    for result in results:
        value = result.validation.normalized_value or {}
        score = value.get("score", "")
        confidence = value.get("confidence", "")
        risk = value.get("risk_level", "")
        error = "；".join(result.validation.errors).replace("|", "\\|")
        lines.append(
            f"| {result.agent} | {AGENTS.get(result.agent, '')} | {result.prompt_version} | "
            f"{result.status} | {result.attempts} | {score} | {confidence} | {risk} | {error} |"
        )

    lines += [
        "",
        "## 主要结论",
        "",
    ]
    for result in results:
        value = result.validation.normalized_value
        lines.append(f"### {AGENTS.get(result.agent, result.agent)}")
        if not value:
            lines.append(f"- 未获得可用结果：{'；'.join(result.validation.errors)}")
            lines.append("")
            continue
        lines.append(f"- 分数：{value.get('score')}，风险：{value.get('risk_level')}，置信度：{value.get('confidence')}")
        lines.append(f"- 摘要：{value.get('summary', '')}")
        issues = value.get("issues", [])
        if issues:
            lines.append("- 核心问题：")
            for item in issues[:5]:
                if not isinstance(item, dict):
                    continue
                lines.append(f"  - `{item.get('issue_type')}`：{item.get('evidence', '')}")
        else:
            lines.append("- 核心问题：未检测到明确问题")
        warnings = result.validation.warnings
        if warnings:
            lines.append("- 质量检查提示：")
            for warning in warnings:
                lines.append(f"  - {warning}")
        lines.append("")

    lines += [
        "## 输出文件",
        "",
        "| Agent | 原始输出 | 标准化输出 | 错误记录 |",
        "|---|---|---|---|",
    ]
    for result in results:
        lines.append(
            f"| {result.agent} | {result.raw_output_path or ''} | "
            f"{result.normalized_output_path or ''} | {result.error_output_path or ''} |"
        )
    lines.append("")
    return "\n".join(lines)
