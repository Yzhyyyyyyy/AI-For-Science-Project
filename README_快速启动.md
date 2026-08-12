# AI学术审查系统 v5.1 快速启动

## 1. 环境要求

- Windows 10/11
- Python 3.10+
- 可用的 OpenAI-compatible API Key

## 2. 配置 API Key

复制 `backend\.env.example` 为 `backend\.env`：

```env
DASHSCOPE_API_KEY=你的API Key
QWEN_BASE_URL=你的API地址
QWEN_TEXT_MODEL=模型名称
```

## 3. 启动系统

双击 `启动_AI学术审查系统_v5.1.bat`，浏览器自动打开：

> http://127.0.0.1:8000/

## 4. 使用流程

1. 上传 PDF 论文
2. 选择学科模式（社会科学/理工/医学/自定义）
3. 点击"开始审查"
4. 等待多引擎审查完成（通常 2-5 分钟）
5. 查看五维评价报告
6. 导出 Markdown 或 PDF

## 5. 注意事项

- 首次审查耗时较长，请耐心等待
- 如模型服务波动，可点击"重新审查"
- 普通导出含水印，提交反馈后可解锁无水印版
- API Key 请勿泄露
