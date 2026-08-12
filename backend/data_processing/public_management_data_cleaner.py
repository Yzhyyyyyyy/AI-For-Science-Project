# public_management_data_cleaner.py
"""Validate and export the public-management bibliometric seed database."""
from __future__ import annotations

import argparse
import json

from public_management_database import clean_public_management_sources, export_neo4j_csv


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="公共管理计量数据库清洗与 Neo4j 导出")
    parser.add_argument("--validate", action="store_true", help="清洗并校验公共管理种子库")
    parser.add_argument("--export-neo4j", help="导出 Neo4j nodes.csv / edges.csv 到指定目录")
    return parser


def main() -> int:
    parser = _build_parser()
    args = parser.parse_args()
    if not args.validate and not args.export_neo4j:
        args.validate = True

    if args.validate:
        clean = clean_public_management_sources()
        print(json.dumps({"counts": clean["counts"], "warnings": clean["warnings"]}, ensure_ascii=False, indent=2))

    if args.export_neo4j:
        exported = export_neo4j_csv(args.export_neo4j)
        print(json.dumps(exported, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
