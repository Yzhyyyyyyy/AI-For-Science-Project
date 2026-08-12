# public_management_database.py
"""Public-management bibliometric seed library and Neo4j export helpers."""
from __future__ import annotations

import csv
import json
import re
from pathlib import Path
from typing import Any


DATA_DIR = Path(__file__).resolve().parent / "data" / "public_management"


def _read_json(path: Path) -> dict[str, Any]:
    with open(path, "r", encoding="utf-8") as f:
        payload = json.load(f)
    return payload if isinstance(payload, dict) else {}


def _normalise_text(value: str) -> str:
    return re.sub(r"[^0-9a-zA-Z\u4e00-\u9fff]+", "", value or "").lower()


def _normalise_author(value: str) -> str:
    return re.sub(r"[^0-9a-zA-Z\u4e00-\u9fff]+", "", value or "").lower()


def load_public_management_resources(data_dir: str | Path | None = None) -> dict[str, Any]:
    """Load public-management journal, classic literature, and graph seed data."""
    root = Path(data_dir) if data_dir else DATA_DIR
    journals = _read_json(root / "journals.json")
    literature = _read_json(root / "classic_literature.json")
    edges: list[dict[str, str]] = []
    with open(root / "citation_edges.csv", "r", encoding="utf-8-sig", newline="") as f:
        for row in csv.DictReader(f):
            edges.append({key: (value or "").strip() for key, value in row.items()})
    return {
        "data_dir": str(root.resolve()),
        "journals": journals.get("journals", []),
        "journal_schema_version": journals.get("schema_version", ""),
        "literature": literature.get("literature", []),
        "literature_schema_version": literature.get("schema_version", ""),
        "citation_edges": edges,
    }


def clean_public_management_sources(data_dir: str | Path | None = None) -> dict[str, Any]:
    """Return deterministic cleaned resources plus validation warnings."""
    resources = load_public_management_resources(data_dir)
    warnings: list[str] = []

    seen_journals: set[str] = set()
    clean_journals: list[dict[str, Any]] = []
    for item in resources["journals"]:
        name = str(item.get("name") or "").strip()
        key = _normalise_text(name)
        if not name:
            warnings.append("发现空期刊名称，已跳过。")
            continue
        if key in seen_journals:
            warnings.append(f"发现重复期刊名称，已保留首次出现项：{name}")
            continue
        seen_journals.add(key)
        next_item = dict(item)
        next_item["normalized_name"] = key
        next_item["aliases"] = sorted({str(alias).strip() for alias in item.get("aliases", []) if str(alias).strip()})
        clean_journals.append(next_item)

    seen_literature: set[str] = set()
    clean_literature: list[dict[str, Any]] = []
    for item in resources["literature"]:
        title = str(item.get("title") or "").strip()
        year = str(item.get("year") or "").strip()
        key = f"{_normalise_text(title)}_{year}"
        if not title:
            warnings.append("发现空经典文献标题，已跳过。")
            continue
        if key in seen_literature:
            warnings.append(f"发现重复经典文献，已保留首次出现项：{title} ({year})")
            continue
        seen_literature.add(key)
        next_item = dict(item)
        next_item["normalized_title"] = _normalise_text(title)
        next_item["normalized_authors"] = [_normalise_author(str(author)) for author in item.get("authors", [])]
        next_item["aliases"] = sorted({str(alias).strip() for alias in item.get("aliases", []) if str(alias).strip()})
        clean_literature.append(next_item)

    valid_node_ids = {item.get("literature_id") for item in clean_literature}
    clean_edges: list[dict[str, str]] = []
    seen_edges: set[tuple[str, str, str]] = set()
    for edge in resources["citation_edges"]:
        source_id = edge.get("source_id", "")
        target_id = edge.get("target_id", "")
        relation = edge.get("relation", "")
        key = (source_id, target_id, relation)
        if source_id not in valid_node_ids or target_id not in valid_node_ids:
            warnings.append(f"引用边端点不存在，已跳过：{source_id}->{target_id}")
            continue
        if key in seen_edges:
            warnings.append(f"发现重复引用边，已保留首次出现项：{source_id}->{target_id}({relation})")
            continue
        seen_edges.add(key)
        clean_edges.append(edge)

    return {
        "schema_version": "public_management_cleaned_v1",
        "data_dir": resources["data_dir"],
        "journals": sorted(clean_journals, key=lambda item: item["normalized_name"]),
        "literature": sorted(clean_literature, key=lambda item: item["normalized_title"]),
        "citation_edges": sorted(clean_edges, key=lambda item: (item.get("source_id", ""), item.get("target_id", ""))),
        "warnings": warnings,
        "counts": {
            "journals": len(clean_journals),
            "literature": len(clean_literature),
            "citation_edges": len(clean_edges),
        },
    }


def match_public_management_journal(
    journal_name: str,
    resources: dict[str, Any] | None = None,
) -> dict[str, Any] | None:
    """Match a journal name against the public-management CSSCI/SSCI seed library."""
    if not journal_name or journal_name.lower() in {"unknown", "unknown venue"}:
        return None
    clean = clean_public_management_sources() if resources is None else resources
    query = _normalise_text(journal_name)
    if not query:
        return None

    best: tuple[float, dict[str, Any]] | None = None
    for journal in clean.get("journals", []):
        candidates = [journal.get("name", ""), *journal.get("aliases", [])]
        for candidate in candidates:
            cand_key = _normalise_text(str(candidate))
            if not cand_key:
                continue
            if query == cand_key:
                score = 1.0
            elif query in cand_key or cand_key in query:
                score = min(len(query), len(cand_key)) / max(len(query), len(cand_key))
            else:
                score = 0.0
            if score and (best is None or score > best[0]):
                best = (score, journal)

    if not best or best[0] < 0.72:
        return None
    score, journal = best
    return {
        "journal_id": journal.get("journal_id"),
        "name": journal.get("name"),
        "tier": journal.get("tier"),
        "source_index": journal.get("source_index", []),
        "field": journal.get("field"),
        "match_score": round(score, 3),
        "source": "public_management_journal_seed_library",
    }


def match_classic_literature(
    reference_entries: list[dict[str, Any]],
    resources: dict[str, Any] | None = None,
    max_matches: int = 20,
) -> list[dict[str, Any]]:
    """Match parsed references against the public-management classic literature seed library."""
    if not reference_entries:
        return []
    clean = clean_public_management_sources() if resources is None else resources
    matches: list[dict[str, Any]] = []
    for reference in reference_entries:
        raw_text = str(reference.get("raw_text") or reference.get("text") or "")
        normalized_reference = _normalise_text(raw_text)
        if not normalized_reference:
            continue
        for item in clean.get("literature", []):
            names = [item.get("title", ""), *item.get("aliases", [])]
            title_hit = any(_normalise_text(str(name)) in normalized_reference for name in names if str(name).strip())
            year_hit = str(item.get("year") or "") in raw_text
            author_hit = any(author and author in normalized_reference for author in item.get("normalized_authors", []))
            if title_hit or (year_hit and author_hit):
                matches.append(
                    {
                        "reference_id": reference.get("reference_id"),
                        "literature_id": item.get("literature_id"),
                        "title": item.get("title"),
                        "year": item.get("year"),
                        "topic_tags": item.get("topic_tags", []),
                        "matched_on": "title_or_alias" if title_hit else "author_year",
                    }
                )
                break
        if len(matches) >= max_matches:
            break
    return matches


def build_public_management_impact_context(
    *,
    journal_name: str,
    reference_entries: list[dict[str, Any]],
    enabled: bool,
) -> dict[str, Any]:
    """Build academic-impact-only context for downstream public-management evaluation."""
    clean = clean_public_management_sources()
    if not enabled:
        return {
            "enabled": False,
            "scope": "academic_impact_only",
            "reason": "subject_sub 不是公共管理，未启用公共管理专属计量库。",
        }
    return {
        "enabled": True,
        "scope": "academic_impact_only",
        "schema_version": clean["schema_version"],
        "journal_match": match_public_management_journal(journal_name, resources=clean),
        "classic_literature_matches": match_classic_literature(reference_entries, resources=clean),
        "data_lineage": {
            "data_dir": clean["data_dir"],
            "journal_count": clean["counts"]["journals"],
            "classic_literature_count": clean["counts"]["literature"],
            "citation_edge_count": clean["counts"]["citation_edges"],
            "cleaning_warnings": clean["warnings"],
        },
        "neo4j_import_hint": "python public_management_data_cleaner.py --export-neo4j outputs/public_management_neo4j",
    }


def export_neo4j_csv(output_dir: str | Path, data_dir: str | Path | None = None) -> dict[str, str]:
    """Export deterministic nodes.csv and edges.csv for Neo4j bulk import."""
    clean = clean_public_management_sources(data_dir)
    root = Path(output_dir)
    root.mkdir(parents=True, exist_ok=True)
    nodes_path = root / "nodes.csv"
    edges_path = root / "edges.csv"

    with open(nodes_path, "w", encoding="utf-8-sig", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=["id:ID", "label:LABEL", "title", "year:int", "authors", "topic_tags"])
        writer.writeheader()
        for item in clean["literature"]:
            writer.writerow(
                {
                    "id:ID": item.get("literature_id", ""),
                    "label:LABEL": "ClassicLiterature",
                    "title": item.get("title", ""),
                    "year:int": item.get("year", ""),
                    "authors": ";".join(item.get("authors", [])),
                    "topic_tags": ";".join(item.get("topic_tags", [])),
                }
            )

    with open(edges_path, "w", encoding="utf-8-sig", newline="") as f:
        writer = csv.DictWriter(
            f,
            fieldnames=[":START_ID", ":END_ID", ":TYPE", "evidence", "source_file"],
        )
        writer.writeheader()
        for edge in clean["citation_edges"]:
            writer.writerow(
                {
                    ":START_ID": edge.get("source_id", ""),
                    ":END_ID": edge.get("target_id", ""),
                    ":TYPE": edge.get("relation", "RELATED_TO"),
                    "evidence": edge.get("evidence", ""),
                    "source_file": edge.get("source_file", ""),
                }
            )

    return {"nodes": str(nodes_path.resolve()), "edges": str(edges_path.resolve())}
