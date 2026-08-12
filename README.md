# AI 学术审查系统 v5.1

AI 学术审查系统 v5.1 是一个面向学术论文、课程论文、竞赛论文和研究草稿的 AI 辅助审查系统。系统通过多维度评价引擎与最终一致性复核机制，对稿件进行结构化审查、风险提示、修改建议和投稿方向分析。

> 本项目用于 AI 辅助学术审查研究与成果展示，不替代导师判断、同行评审或编辑部决定。

---

## 核心功能

- PDF 论文上传与元数据提取
- 多引擎五维学术审查
  - 数据可靠性
  - 逻辑严密性
  - 创新性
  - 学术影响力
  - 伦理与偏见风险
- AuditAgent 最终一致性复核
- v5.1 学科权重策略
  - 社会科学与人文
  - 理工与实验科学
  - 医学与生命科学
  - 自定义审查
- 投稿期刊方向建议
- Evidence / 风险项展示
- Markdown 报告导出
- PDF 报告导出
- 运维模式与测试工具

---

## v5.1 版本亮点

- 新增四模式学科权重体系
- 支持自定义权重审查
- 修复前端运行时白屏问题
- 修复期刊推荐 `-` 与 `0%` 异常显示
- 策略名中文化展示
- 加强 AuditAgent 降级容错
- 完成最终全方位验收

---

## 项目结构

```text
AI学术审查系统_v5.1_正式发布版/
├─ backend/                  # 后端服务与审查流程
│  ├─ api.py                 # API 入口
│  ├─ main_controller/       # 主审查管线
│  ├─ audit_agent/           # 最终一致性复核
│  └─ .env.example           # 配置模板
├─ frontend/                 # 前端源码与构建产物
│  ├─ src/
│  ├─ dist/                  # 已构建前端静态文件
│  └─ package.json
├─ README_快速启动.md
├─ README_常见问题.md
├─ RELEASE_NOTES_v5.1.md
├─ V5_1_FINAL_FULL_AUDIT_REPORT.md
└─ 启动_AI学术审查系统_v5.1.bat
```

---

## 快速启动

### 1. 克隆仓库

```bash
git clone https://github.com/Yzhyyyyyyy/AI-For-Science-Project.git
cd AI-For-Science-Project
```

### 2. 配置 API Key

复制：

```text
backend/.env.example
```

为：

```text
backend/.env
```

填写你的 OpenAI-compatible API 配置：

```env
DASHSCOPE_API_KEY=YOUR_API_KEY_HERE
QWEN_BASE_URL=https://api.example.com/openai/v1
QWEN_TEXT_MODEL=qwen-plus-latest
```

### 3. 安装后端依赖

```bash
pip install -r backend/requirements.txt
```

### 4. 启动系统

Windows 用户可双击：

```text
启动_AI学术审查系统_v5.1.bat
```

也可手动启动：

```bash
cd backend
python api.py
```

浏览器访问：

```text
http://127.0.0.1:8000/
```

---

## 前端二次开发

如果需要修改前端：

```bash
cd frontend
npm install
npm run dev
```

构建生产版本：

```bash
npm run build
```

---

## 最终验收

v5.1 已完成最终全方位验收：

- 最新成功 report_id：`2ab19623`
- 审查耗时：193.5 秒
- P0：无
- P1：无
- 交付等级：可交付

详见：

```text
V5_1_FINAL_FULL_AUDIT_REPORT.md
```

---

## 安全说明

本仓库不包含：

- 真实 API Key
- 用户上传论文
- 审查缓存
- 运行日志
- 本地数据库
- node_modules
- Python 虚拟环境

请勿将 `.env`、论文原文、日志或缓存提交到公开仓库。

---

## 免责声明

AI 学术审查系统 v5.1 仅作为 AI 辅助学术审查工具，输出内容仅供参考，不构成正式学术评价、同行评审意见或投稿保证。
