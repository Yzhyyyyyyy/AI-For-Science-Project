# 公共管理计量数据库

本目录是数据处理模块维护的公共管理底层计量种子库，只服务于“学术影响力与期刊匹配度评估引擎”。它不会替换或重构普通文科、理科、工科已有数据库结构。

## 文件

- `journals.json`：公共管理 CSSCI/SSCI 期刊分级种子库。
- `classic_literature.json`：公共管理经典文献种子库。
- `citation_edges.csv`：可导入 Neo4j 的经典文献关系种子边。

## 可复现清洗

运行：

```powershell
python public_management_data_cleaner.py --validate
python public_management_data_cleaner.py --export-neo4j outputs/public_management_neo4j
```

清洗脚本会去重、规范化名称、校验引用边端点，并导出 `nodes.csv` / `edges.csv`，供 Neo4j 批量导入。
