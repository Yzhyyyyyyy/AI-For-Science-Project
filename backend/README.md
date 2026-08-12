# AI 学术审查系统后端

AI 学术审查系统是一个面向学术论文的智能辅助审查系统。本目录包含可以独立运行的后端程序，负责接收论文、解析正文、执行多维度评价、复核评价结果，并向前端提供报告、反馈和运行监控接口。

> **交付范围说明：本目录只有后端代码，没有制作或集成前端操作页面。**
>
> 目前可以直接使用命令行审查论文；启动 `api.py` 后提供的是供前端调用的接口和接口文档，不是用户操作页面。完整图形界面需要由前端工程单独实现并连接这些接口。

> 系统生成的内容仅用于辅助研究与论文修改，不能替代同行评审、编辑部审稿或正式的学术伦理认定。

## 一、主要功能

### 1. 多格式论文解析

支持以下文件格式：

- PDF
- Word（DOCX）
- LaTeX（TEX）
- CAJ
- PNG、JPG、JPEG、WEBP 等图片

系统会提取论文标题、作者、摘要、关键词、正文、章节、参考文献、图表和基础学术信息，并转换成统一的数据格式。

解析 PDF、Word 和 CAJ 时，会保留页码、段落编号、文本块、字符范围和页面坐标。这些数据可供前端实现“点击风险条目后定位到论文原文”。

### 2. 五维智能评价

系统会以受控并发方式执行五个评价维度（默认并发数为 2，可通过环境变量调整）：

- 数据可靠性
- 伦理与偏见
- 逻辑严密性
- 创新性
- 学术影响力

学术影响力评价还会给出投稿层级建议和当前论文需要补充的内容。投稿建议仅供参考，不代表期刊录用判断。

### 3. 评价结果复核

初步评价完成后，系统会继续检查：

- 评价证据是否确实来自论文原文
- 不同评价维度之间是否存在明显冲突
- 评价结果是否遗漏重要信息

如果部分评价维度调用失败，系统只保留成功结果，不会把失败维度当成 0 分；如果全部评价失败，则直接返回模型服务异常。

### 4. 报告与用户反馈

系统提供：

- 审查报告保存与历史查询
- 用户评分和意见归集
- 无水印报告权益
- 深度诊断权益
- 后续优先处理权益
- 版本更新通知权益

用户可以自愿提交反馈，也可以直接跳过。只有关联具体报告提交反馈后，才会解锁对应的增值权益。

### 5. 运行监控

上传失败、解析异常、模型调用异常和审查超时等问题会自动保存为错误工单，便于后续排查。

相同论文再次上传时默认复用已有报告，保证结果一致并缩短等待时间；传入 `force=true` 可以强制重新评价。系统还会记录成功、失败、缓存命中和平均耗时。

## 二、目录说明

```text
backend/
├── api.py                         Web 接口入口
├── requirements.txt              Python 依赖列表
├── .env.example                  配置文件示例
├── main_controller/              审查流程调度
├── data_processing/              论文解析与文本定位
├── evaluation_agents_delivery/   五维智能评价
├── audit_agent/                  评价结果复核
├── examples/test_paper.pdf       随附的命令行测试论文
└── tests/                        离线自动化测试
```

日常启动只需要使用 `api.py`，一般不需要单独操作其他目录。

## 三、运行环境

- Windows、macOS 或 Linux
- Python 3.10 或更高版本
- 可访问阿里云百炼服务
- 有效的百炼 API Key

推荐使用 Python 虚拟环境，避免与电脑中已有的 Python 包冲突。

## 四、安装步骤

### Windows PowerShell

进入本目录：

```powershell
cd backend
```

创建并启用虚拟环境：

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
```

安装依赖：

```powershell
python -m pip install -r requirements.txt
```

### macOS 或 Linux

```bash
cd backend
python3 -m venv .venv
source .venv/bin/activate
python -m pip install -r requirements.txt
```

## 五、配置模型服务

复制配置示例：

```powershell
Copy-Item .env.example .env
```

macOS 或 Linux：

```bash
cp .env.example .env
```

打开 `.env`，至少填写：

```env
DASHSCOPE_API_KEY=你的百炼APIKey
```

其余模型名称、监听地址、端口和上传大小限制均有默认值，通常无需修改。

请勿把包含真实 API Key 的 `.env` 文件上传到公开仓库或发送给无关人员。

## 六、启动后端

运行：

```powershell
python api.py
```

启动成功后可访问：

- 后端使用提示：<http://127.0.0.1:8000/>
- 接口文档：<http://127.0.0.1:8000/docs>
- 健康检查：<http://127.0.0.1:8000/api/health>

这些地址用于检查和调试后端，不是最终前端页面。

如果需要修改端口，请在 `.env` 中设置：

```env
PORT=8001
```

## 七、命令行审查

当前没有集成前端页面，最直接的使用方式是命令行处理论文：

```powershell
python main_controller/main.py "论文.pdf" -o result.json
```

也可以使用随附的测试论文：

```powershell
python main_controller/main.py "examples/test_paper.pdf" -o result.json
```

以上命令默认只进行文本分析，不调用视觉模型，也不会生成图表裁剪资产。

需要图表审查时，显式开启受限视觉模式；默认只处理前 5 页：

```powershell
python main_controller/main.py "论文.pdf" -o result.json --visuals
```

可以限制处理前 3 页，或者只处理指定页面：

```powershell
python main_controller/main.py "论文.pdf" -o result.json --visuals --max-visual-pages 3
python main_controller/main.py "论文.pdf" -o result.json --visual-pages "1,3,5-7"
```

全文视觉分析必须由用户主动开启：

```powershell
python main_controller/main.py "论文.pdf" -o result.json --full-visuals
```

## 八、前端对接接口

| 请求方式 | 接口 | 说明 |
| --- | --- | --- |
| POST | `/api/review` | 上传论文并流式返回审查进度和结果 |
| POST | `/api/feedback` | 提交用户反馈 |
| GET | `/api/reports` | 查询历史报告 |
| GET | `/api/reports/{report_id}` | 查询报告详情和已解锁权益 |
| GET | `/api/tickets` | 查询自动归档的错误工单 |
| GET | `/api/metrics` | 查询调用状态和平均耗时 |
| GET | `/api/reports/{report_id}/export` | 下载普通或无水印 Markdown 报告 |
| POST | `/api/reports/{report_id}/deep-diagnosis` | 使用反馈权益生成深度诊断 |
| GET | `/api/releases` | 查询版本更新记录 |
| GET | `/api/health` | 查询服务状态 |

### 上传论文

`POST /api/review` 使用表单上传，字段如下：

| 字段 | 是否必填 | 说明 |
| --- | --- | --- |
| `file` | 是 | 论文文件 |
| `domain` | 否 | 论文领域，默认自动识别 |
| `visual_mode` | 否 | `text`、`limited` 或 `full`，默认 `text`（纯文本审查） |
| `max_visual_pages` | 否 | `limited` 模式处理前 N 页，默认 5，范围 1～50 |
| `visual_pages` | 否 | 指定页码，例如 `1,3,5-7`；填写后自动启用受限视觉审查 |
| `force` | 否 | 是否忽略稳定性缓存并重新评价，默认关闭 |
| `priority_token` | 否 | 提交反馈后获得的一次性优先处理令牌 |

稳定性相关环境变量：

| 变量 | 默认值 | 说明 |
| --- | --- | --- |
| `EVALUATION_MAX_WORKERS` | `2` | 五类评价的最大并发数，低额度环境可设为 `1` |
| `EVALUATION_REQUEST_INTERVAL_SECONDS` | `0.5` | 相邻模型请求的最小启动间隔 |
| `MODEL_TIMEOUT_SECONDS` | `120` | 单次模型请求超时秒数 |
| `EVALUATION_MAX_INPUT_CHARS` | `50000` | 每个评价维度最多接收的正文字符数 |

模型服务返回 `insufficient_quota`、密钥无效或无权限时，后端会立即停止该阶段，
不会继续无效重试；普通频率限制仍采用有限退避重试。

系统会在 `runtime/pipeline_cache` 保存成功的解析、评价和审计阶段结果。后续运行相同
文件、参数、模型和提示词时会从最近完成阶段继续。返回结果中的 `stageMetrics`
会标明各阶段耗时、是否命中缓存，以及评价和审计的实际尝试次数。

为降低 Token 消耗，五类评价只接收与各自职责相关的章节和证据锚点；完整原文仍保留
在解析结果中，用于最终证据定位和审计。

接口返回 NDJSON 流。每一行都是一个 JSON 对象：

```json
{"type":"progress","stage":"review","message":"正在执行纯文本审查和五维评价","visualMode":"text"}
{"type":"result","data":{"reportId":"...","evaluationStatus":"success"}}
```

发生异常时会返回：

```json
{
  "type": "error",
  "code": "REVIEW_FAILED",
  "message": "审查失败，问题已自动归档。",
  "ticketId": "错误工单编号"
}
```

### 提交反馈

请求示例：

```json
{
  "report_id": "报告编号",
  "rating": 5,
  "category": "suggestion",
  "content": "希望增加更多评价解释",
  "contact": "可选联系方式"
}
```

`report_id` 必须来自已生成的报告。填写后，接口会返回本次解锁的权益和 `entitlementToken`。该令牌可用于无水印导出、一次深度诊断、一次优先处理和版本更新查询。

## 九、评价状态说明

| 状态 | 含义 |
| --- | --- |
| `success` | 所有评价维度均成功 |
| `partial_failure` | 部分维度失败，只返回和复核成功结果 |
| `failed` | 所有评价维度失败，不继续复核 |

综合分只根据成功返回且具有有效分数的维度计算。

## 十、测试方法

运行全部后端离线测试：

```powershell
python -m unittest discover -s tests -v
python -m unittest evaluation_agents_delivery.test_service -v
```

测试使用模拟模型响应，不消耗 API 额度。真实论文端到端审查需要有效的 API Key 和网络连接。

## 十一、常见问题

### 提示“未配置 DASHSCOPE_API_KEY”

确认本目录中存在 `.env`，并且已正确填写 `DASHSCOPE_API_KEY`。

### 浏览器打不开接口文档

确认命令行中没有启动错误，并检查访问地址是否为：

```text
http://127.0.0.1:8000/docs
```

### 审查速度较慢

完整流程需要解析论文、受控并发执行五维评价并复核结果。系统默认只进行文本分析；受限或全文视觉分析必须通过参数显式开启。实际耗时还会受到论文长度、网络和模型服务负载影响。

### 如何查看后台错误

访问 `/api/tickets`。每条记录包含出错阶段、错误类型、简要信息、上下文和创建时间。

### 定位功能如何对接

报告中的风险条目包含 `evidence_refs`，论文解析结果包含 `textAnchors`。前端可优先使用 `block_id` 或页码匹配文本锚点，再根据段落编号、字符范围或页面坐标滚动到原文位置。
