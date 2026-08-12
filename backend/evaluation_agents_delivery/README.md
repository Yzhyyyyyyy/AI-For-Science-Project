# 五维论文评价服务模块

本目录是可由后端直接调用的独立 Python 模块。服务通过阿里云百炼
兼容接口，以受控并发方式调用五个评价 Agent：

- `data_reliability`：数据可靠性
- `ethics_bias`：伦理与偏见
- `logical_rigor`：逻辑严密性
- `innovation`：创新性

## 运行环境与安装

建议使用 Python 3.10 或更高版本：

```bash
python -m pip install -r evaluation_agents_delivery/requirements.txt
```

后端进程必须提供百炼 API Key：

```text
DASHSCOPE_API_KEY=<your-api-key>
```

也可以复制 `.env.example` 为本目录下的 `.env`。已有进程环境变量的优先级
高于 `.env`，不要将真实密钥提交到代码仓库。

## 后端调用入口

后端只推荐通过包入口调用：

```python
from evaluation_agents_delivery import evaluate_paper

result = evaluate_paper(paper_data)
```

可选的审计反馈会被复制到本次评价输入的 `review_context.audit_feedback`，
不会修改调用方传入的字典：

```python
result = evaluate_paper(
    paper_data,
    audit_feedback={"focus": "请重点复核样本代表性"},
)
```

## 标准输入 JSON Schema

推荐输入同时包含论文基本信息、元数据、正文和评价上下文。正文支持两种
现有格式：顶层 `full_text`，或结构化的 `content.full_text`。`data_info`、
`method_info`、`ethics_info`、`innovation_info`、`tables`、`figures` 等结构化
字段可以作为扩展字段一并传入。

以下为后端可采用的 JSON Schema（Draft 2020-12）示例：

```json
{
  "$schema": "https://json-schema.org/draft/2020-12/schema",
  "$id": "https://example.local/schemas/evaluation-paper-input.json",
  "title": "EvaluationPaperInput",
  "type": "object",
  "required": ["paper_info", "metadata", "review_context"],
  "properties": {
    "paper_info": {
      "type": "object",
      "required": ["title", "authors", "abstract", "keywords"],
      "properties": {
        "title": {"type": "string", "minLength": 1},
        "authors": {
          "type": "array",
          "items": {"type": "string"}
        },
        "abstract": {"type": "string"},
        "keywords": {
          "type": "array",
          "items": {"type": "string"}
        }
      },
      "additionalProperties": true
    },
    "metadata": {
      "type": "object",
      "properties": {
        "journal": {"type": "string"},
        "journal_level": {"type": "string"},
        "citation_count": {"type": ["integer", "null"], "minimum": 0},
        "publication_year": {"type": ["integer", "null"]},
        "open_data": {"type": ["boolean", "null"]},
        "open_code": {"type": ["boolean", "null"]},
        "external_sources": {
          "type": "array",
          "items": {"type": ["string", "object"]}
        }
      },
      "additionalProperties": true
    },
    "full_text": {"type": "string"},
    "content": {
      "type": "object",
      "required": ["full_text"],
      "properties": {
        "full_text": {"type": "string"},
        "sections": {
          "type": "array",
          "items": {
            "type": "object",
            "required": ["section_title", "section_text"],
            "properties": {
              "section_title": {"type": "string"},
              "section_text": {"type": "string"}
            },
            "additionalProperties": true
          }
        },
        "references_text": {"type": "string"}
      },
      "additionalProperties": true
    },
    "review_context": {
      "type": "object",
      "required": ["target_domain", "evaluation_purpose"],
      "properties": {
        "target_domain": {"type": "string"},
        "evaluation_purpose": {"type": "string"},
        "paper_type": {"type": "string"},
        "selected_frontend_options": {
          "type": "array",
          "items": {"type": "string"}
        },
        "retry_feedback": {"type": ["object", "null"]},
        "audit_feedback": {"type": ["object", "null"]}
      },
      "additionalProperties": true
    },
    "data_info": {"type": "object"},
    "method_info": {"type": "object"},
    "ethics_info": {"type": "object"},
    "innovation_info": {"type": "object"},
    "tables": {"type": "array"},
    "figures": {"type": "array"}
  },
  "anyOf": [
    {"required": ["full_text"]},
    {
      "required": ["content"],
      "properties": {
        "content": {"required": ["full_text"]}
      }
    }
  ],
  "additionalProperties": true
}
```

当前 `evaluate_paper` 在运行时只检查 `paper_data` 是否为 `dict`。如后端需要
严格拒绝不完整输入，应在调用服务前使用上述 Schema 校验。

## 完整输出 JSON 示例

返回值以五个 Agent 标识为顶层键。每个维度都包含运行状态、Prompt 版本、
尝试次数、标准化评价结果、错误、警告和枚举归一化记录：

```json
{
  "data_reliability": {
    "status": "success",
    "prompt_version": "v1.6",
    "attempts": 1,
    "result": {
      "agent_name": "data_reliability",
      "dimension_name": "数据可靠性",
      "score": 68,
      "confidence": 0.86,
      "risk_level": "medium",
      "summary": "数据来源明确，但样本范围限制了结论的外推能力。",
      "strengths": ["说明了样本数量和训练测试划分"],
      "issues": [
        {
          "issue_type": "sample_representativeness",
          "severity": "medium",
          "evidence": "样本仅来自某高校计算机学院，却推广至全国高校学生。",
          "suggestion": "增加跨学校、跨专业样本并进行外部验证。"
        }
      ],
      "evidence_refs": [
        {
          "location": "full_text",
          "quote": "研究采集某高校计算机学院120名学生的课程成绩"
        }
      ],
      "reasoning_md": "评价综合考虑数据来源、样本覆盖范围、数据处理说明和结论外推边界。当前输入说明了样本数量及训练测试划分，但样本来源单一，且缺失值处理方式未说明，因此数据可靠性仍存在改进空间。",
      "limitations": ["当前输入未提供完整的数据字典和缺失值统计"],
      "normalization_log": []
    },
    "errors": [],
    "warnings": [],
    "normalization_log": []
  },
  "ethics_bias": {
    "status": "success",
    "prompt_version": "v1.5",
    "attempts": 1,
    "result": {
      "agent_name": "ethics_bias",
      "dimension_name": "伦理与偏见",
      "score": 65,
      "confidence": 0.8,
      "risk_level": "medium",
      "summary": "输入未提供知情同意和隐私保护细节，相关伦理合规性暂时无法判断。",
      "strengths": ["未依据作者、学校声誉或引用量进行质量判断"],
      "bias_detected": [
        {
          "bias_type": "sample_selection_bias",
          "severity": "medium",
          "affected_group_or_factor": "非计算机学院及其他高校学生",
          "evidence": "研究对象仅为某高校计算机学院学生，结论却推广至全国高校。",
          "potential_impact": "模型可能对未覆盖学生群体产生不准确评价。",
          "suggestion": "扩大样本覆盖并按学校和专业报告分组表现。"
        }
      ],
      "issues": [],
      "evidence_refs": [
        {
          "location": "full_text",
          "quote": "论文未说明学生是否知情"
        }
      ],
      "reasoning_md": "评价重点检查知情同意、隐私保护、公平影响及声誉偏见。当前输入未提供足够信息证明已经发生明确伦理违规，因此不直接输出伦理问题；但样本覆盖范围与结论适用范围不一致，仍存在潜在群体公平风险。",
      "limitations": ["当前输入未提供伦理审批、知情同意或数据脱敏细节，相关合规性无法判断"],
      "normalization_log": []
    },
    "errors": [],
    "warnings": [],
    "normalization_log": []
  },
  "logical_rigor": {
    "status": "success",
    "prompt_version": "v1.2",
    "attempts": 1,
    "result": {
      "agent_name": "logical_rigor",
      "dimension_name": "逻辑严密性",
      "score": 62,
      "confidence": 0.88,
      "risk_level": "medium",
      "summary": "实验结果支持校内样本表现，但不足以支持全国适用结论。",
      "strengths": ["研究目标、模型训练和准确率结果之间具有基本对应关系"],
      "issues": [
        {
          "issue_type": "overgeneralization",
          "severity": "high",
          "evidence": "单一学院样本的结果被推广为适用于全国高校学生。",
          "suggestion": "将结论限定在当前样本范围内，或补充跨学校外部验证。"
        }
      ],
      "evidence_refs": [
        {
          "location": "paper_info.abstract",
          "quote": "因此证明该模型能够适用于全国高校学生"
        }
      ],
      "reasoning_md": "评价围绕研究问题、方法、证据和结论之间的推理链进行。当前准确率结果只能直接说明现有测试集上的表现，未进行跨学校测试，因此不能充分支持全国高校范围的适用性判断。建议区分样本内预测性能、外部有效性和实际部署效果，避免从局部证据直接推出普遍结论。",
      "limitations": ["当前输入没有提供置信区间或跨域测试结果"],
      "normalization_log": []
    },
    "errors": [],
    "warnings": [],
    "normalization_log": []
  },
  "innovation": {
    "status": "success",
    "prompt_version": "v1.5",
    "attempts": 1,
    "result": {
      "agent_name": "innovation",
      "dimension_name": "创新性",
      "score": 55,
      "confidence": 0.78,
      "risk_level": "high",
      "summary": "应用场景具有价值，但当前证据不足以确认方法创新及性能优势。",
      "strengths": ["面向教育场景开展学生成绩预测"],
      "innovation_types": ["application"],
      "issues": [
        {
          "issue_type": "baseline_insufficient",
          "severity": "high",
          "evidence": "论文没有与其他模型进行对比。",
          "suggestion": "增加传统模型及同类深度学习模型作为基线。"
        }
      ],
      "evidence_refs": [
        {
          "location": "full_text",
          "quote": "论文没有与其他模型进行对比"
        }
      ],
      "reasoning_md": "评价区分应用价值、方法新颖性和性能证据。当前输入能够支持教育预测这一应用方向，但没有给出与已有方法的差异，也缺少基线对比，因此尚不能确认方法层面的创新和相对优势。",
      "limitations": ["当前输入未提供相关工作对比和模型结构细节"],
      "normalization_log": []
    },
    "errors": [],
    "warnings": [],
    "normalization_log": []
  }
}
```

当某个 Agent 最终失败时，该维度仍会保留，通常表现为：

```json
{
  "status": "failed",
  "prompt_version": "v1.6",
  "attempts": 3,
  "result": null,
  "errors": ["API 调用失败：<error message>"],
  "warnings": [],
  "normalization_log": []
}
```

## 异常与失败处理

- `TypeError`：`paper_data` 不是 `dict`，或 `audit_feedback` 既不是 `dict`
  也不是 `None`。
- `RuntimeError`：后端环境中没有配置 `DASHSCOPE_API_KEY`。
- `FileNotFoundError`：交付包中缺少固定生产 Prompt 文件。
- `ModuleNotFoundError`：运行依赖未安装完整，例如缺少 `openai`。
- 单个 Agent 的 API 调用失败、JSON 格式错误或质量校验失败，通常不会直接抛出，
  而是在对应维度返回 `status: "failed"` 并通过 `errors` 给出原因。
- 初始化错误、线程执行中的未预期错误等仍可能向调用方抛出。后端应在接口层捕获
  异常、记录日志，并转换成统一的 HTTP 错误响应。

`evaluate_paper` 是同步函数，只有在五个 Agent 全部完成或结束重试后才会返回。

## 运行最小测试

测试使用 `tests/cases/test_case_01_typical.json` 和离线模拟客户端，不会发送
真实 API 请求：

```bash
cd evaluation_agents_delivery
python -m unittest -v test_service.py
```

## 固定生产 Prompt

- `data_reliability_prompt_v1.6.txt`
- `ethics_bias_prompt_v1.5.txt`
- `logical_rigor_prompt_v1.2.txt`
- `innovation_prompt_v1.5.txt`

模型配置位于 `pipeline/config.py`，输出结构校验与枚举归一化位于
`pipeline/schema_validation.py`。生产代码不依赖 `run_tests.py`。
