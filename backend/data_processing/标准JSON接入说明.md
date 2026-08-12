# 评价 Agent 标准 JSON 接入说明

本说明用于对接最新的评价 Agent 输入格式。当前数据处理模块已经支持把 Word/DOCX、LaTeX、PDF、CAJ、图片等多种来源，在进入评价 Agent 前统一转换为标准 JSON。

## 1. 推荐入口

```python
from data_processor import process_document

agent_input = process_document(
    input_path="paper.pdf",
    output_dir="outputs/paper_assets",
    extract_visuals=False,  # 默认只做文本解析
    max_visual_pages=3,
    schema="full",      # full: 完整版 input.json；minimal: 最小版格式.json
    review_context={
        "target_domain": "教育人工智能",
        "evaluation_purpose": "科研成果影响力分析",
        "selected_frontend_options": ["创新性", "数据合规", "方法可靠性"],
        "retry_feedback": None
    }
)
```

## 2. 命令行用法

### 输出完整版标准 JSON

```powershell
python data_processor.py paper.pdf --output-dir outputs/paper_assets --json-output outputs/agent_input.json
```

### 输出最小版标准 JSON

```powershell
python data_processor.py paper.pdf --schema minimal --json-output outputs/agent_input_min.json
```

### 处理图片来源

```powershell
python data_processor.py figure.png --schema full --json-output outputs/figure_agent_input.json
```

图片来源会通过 QWEN-VL 生成 OCR 文本、图片描述、题注信息，并放入 `full_text`、`figures` 等字段。

### 测试时限制视觉模型处理页数

```powershell
python data_processor.py paper.pdf --max-visual-pages 3 --json-output outputs/agent_input.json
```

### 只做文本标准化，不裁剪视觉内容

```powershell
python data_processor.py paper.docx --json-output outputs/agent_input.json
```

默认不调用视觉模型。需要图表审查时使用 `--visuals`（默认前 5 页）、
`--visual-pages "1,3,5-7"`（指定页面）或 `--full-visuals`（显式开启全文视觉分析）。

## 3. 支持的来源格式

| 来源 | 后缀 | 说明 |
| --- | --- | --- |
| PDF | `.pdf` | 直接提取文本，并可渲染页面后用 QWEN-VL 裁剪图表。 |
| Word | `.docx` | 直接提取段落和表格文本；视觉裁剪需要本机有 LibreOffice。 |
| LaTeX | `.tex` | 提取源码中的正文；视觉裁剪会优先复用同名 PDF，否则尝试用 xelatex/pdflatex 编译。 |
| 知网 CAJ | `.caj` | 需要本机配置 `caj2pdf` 转 PDF。 |
| 图片 | `.png/.jpg/.jpeg/.bmp/.tif/.tiff/.webp` | 用 QWEN-VL 做 OCR、描述和图像信息抽取。 |

## 4. 完整版 JSON 结构

`schema="full"` 时输出结构对齐团队提供的 `input.json`，主要字段如下：

```json
{
  "paper_info": {
    "title": "论文标题",
    "authors": [],
    "abstract": "论文摘要",
    "keywords": []
  },
  "metadata": {
    "journal": "期刊或会议名称",
    "journal_level": "期刊等级",
    "citation_count": 0,
    "publication_year": 2026,
    "open_data": false,
    "open_code": false,
    "external_sources": []
  },
  "content": {
    "full_text": "论文正文纯文本",
    "sections": [],
    "references_text": "参考文献文本",
    "references_anchor_ids": [],
    "text_anchor_schema": "text_anchor_v1",
    "text_anchors": []
  },
  "tables": [],
  "figures": [],
  "data_info": {},
  "method_info": {},
  "ethics_info": {},
  "innovation_info": {},
  "review_context": {},
  "source": {}
}
```

比原始要求多了一个 `source` 字段，用于记录原始文件路径、输入格式、CAJ 转换后的 PDF 路径、图片解析结果、文本锚点版本和锚点数量等。评价 Agent 如果不需要，可以忽略该字段。

## 5. 最小版 JSON 结构

`schema="minimal"` 时输出结构对齐团队提供的 `最小版格式.json`：

```json
{
  "paper_info": {
    "title": "论文标题",
    "authors": [],
    "abstract": "论文摘要",
    "keywords": []
  },
  "metadata": {
    "journal": "",
    "journal_level": "",
    "citation_count": 0,
    "publication_year": 2026,
    "open_data": false,
    "external_sources": []
  },
  "full_text": "论文正文、表格说明、图片说明合并后的文本",
  "text_anchors": [],
  "review_context": {
    "target_domain": "",
    "evaluation_purpose": "",
    "selected_frontend_options": [],
    "retry_feedback": null
  }
}
```

## 6. 字段来源说明

| 标准字段 | 来源 |
| --- | --- |
| `paper_info.title/authors` | 优先由 QWEN 从论文头部文本提取；图片输入时可由 QWEN-VL 补充标题。 |
| `paper_info.abstract/keywords` | 优先用规则从全文中提取“摘要/关键词”；图片输入时使用 QWEN-VL 结果。 |
| `metadata.journal` | 优先 Semantic Scholar 匹配结果，否则使用 QWEN 提取的期刊候选名。 |
| `metadata.journal_level` | 由 QWEN 评估。 |
| `metadata.citation_count/publication_year` | 来自 Semantic Scholar 公开 API。 |
| `metadata.open_data/open_code` | 根据正文关键词和图片解析结果初步判断。 |
| `content.full_text` | 来自 PDF/DOCX/TEX/CAJ 的清洗文本，或图片 OCR+描述。 |
| `content.text_anchors` | PDF/DOCX/CAJ 文本锚点；每个锚点包含 `anchor_id`、`char_start/char_end`、页码或段落序号，PDF 还包含 `bbox/bbox_norm`。 |
| `content.sections[].source_anchor_ids` | 章节文本覆盖到的锚点编号，可用于把章节级风险回指到页面或段落。 |
| `content.references_anchor_ids` | 参考文献区域覆盖到的锚点编号。 |
| `content.sections` | 用轻量规则识别引言、方法、实验、结论等章节。 |
| `tables/figures` | 由 QWEN-VL 识别页面视觉区域后裁剪得到；图片输入会作为 figure。 |
| `data_info/method_info/ethics_info/innovation_info` | 根据全文关键词和章节内容做初步填充，无法识别时填空数组或“未说明”。 |
| `review_context` | 由前端或调用方传入；没有传入时使用空默认值。 |

## 7. 文本锚点与风险定位

PDF、DOCX、CAJ 转 PDF 来源会在 `content.text_anchors` 中保留正文锚点。推荐后续风险 Agent 在生成风险条目时同时保存证据文本或 `anchor_id`：

```json
{
  "anchor_id": "pdf_p0003_b0012",
  "source_type": "pdf_text_block",
  "page": 3,
  "paragraph_index": 12,
  "bbox": [72.12, 315.44, 520.81, 352.03],
  "bbox_norm": [121.15, 374.68, 875.02, 418.14],
  "char_start": 8420,
  "char_end": 8716,
  "text_preview": "用于展示风险条目证据的原文片段"
}
```

- PDF 锚点：按页面文本块生成，包含页码、页面尺寸、PDF point 坐标和 0-1000 归一化坐标。
- Word/DOCX 锚点：按段落和表格顺序生成，包含 `paragraph_index`、`block_index`、表格行列数等逻辑位置；DOCX 原生不稳定提供页码，因此页码默认为 `null`。
- `char_start/char_end` 对齐 `content.full_text`，可用风险证据文本先在全文中定位，再反查覆盖区间内的锚点。
## 8. 环境变量

必须配置：

```powershell
$env:DASHSCOPE_API_KEY="你的 API Key"
```

可选配置：

```powershell
$env:QWEN_BASE_URL="你的百炼 OpenAI 兼容地址"
$env:QWEN_TEXT_MODEL="qwen-plus"
$env:QWEN_FAST_MODEL="qwen-turbo"
$env:QWEN_VISION_MODEL="qwen3-vl-plus"
```

注意：代码中会校验模型名称，只允许 `qwen*` 或 `qvq*` 系列模型。

## 9. 已新增/变更的代码能力

| 文件 | 新增能力 |
| --- | --- |
| `academic_api.py` | 新增 `analyze_image_via_qwen()`，用于图片 OCR 和内容描述。 |
| `pdf_extractor.py` | 新增图片格式支持；新增 `extract_image_to_text()`；新增 PDF/DOCX/CAJ 文本锚点提取，PDF 保留页码和 bbox，DOCX 保留段落/表格坐标。 |
| `data_processor.py` | 新增标准 JSON 构建逻辑；新增 `schema="full/minimal"`；输出字段对齐评价 Agent；在 `content` 中输出 `text_anchors` 并为章节/参考文献关联锚点编号。 |

## 10. 建议对接方式

评价 Agent 前的上游统一使用：

```python
agent_input = process_document(file_path, schema="full")
```

如果评价 Agent 只需要最小字段，则使用：

```python
agent_input = process_document(file_path, schema="minimal")
```

这样后续无论用户上传 Word、LaTeX、PDF 还是图片，进入评价 Agent 的数据格式都是稳定的。
