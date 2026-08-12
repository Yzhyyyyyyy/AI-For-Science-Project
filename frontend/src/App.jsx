import { useState, useCallback, useRef, useMemo, useEffect, Component } from "react";
import { createPortal } from "react-dom";
import {
  Upload, Activity, FileText, Download, RefreshCw, X, Zap,
  CheckCircle, AlertCircle, ChevronRight, Languages, Settings,
  Plus, History, Wrench, AlertTriangle, Info, Eye,
  Lock, Unlock,
} from "lucide-react";
import ReactMarkdown from "react-markdown";
import { PieChart, Pie, Cell, Tooltip as RechartsTooltip } from "recharts";
import { BrowserRouter, Routes, Route } from "react-router-dom";
import html2pdf from "html2pdf.js";
import { marked } from "marked";
import html2canvas from "html2canvas";
import pdfWorkerUrl from "pdfjs-dist/build/pdf.worker.min.mjs?url";

// ============================================================
// API Base URL — Vite env var (dev: localhost, prod: Render URL)
// ============================================================
const API_BASE = "";
const APP_VERSION = "5.1";
const DEFAULT_QWEN_BASE_URL = "https://dashscope.aliyuncs.com/compatible-mode/v1";
const DEFAULT_QWEN_MODEL = "qwen-plus-latest";

function normalizeStoredReviewConfig(value) {
  if (!value || typeof value !== "object") return { config: null, migrated: false };
  const apiKey = String(value.apiKey || "").trim();
  const baseUrl = String(value.baseUrl || "").trim();
  const modelName = String(value.modelName || "").trim();
  if (!apiKey || !baseUrl || !modelName) return { config: null, migrated: false };
  return { config: { apiKey, baseUrl, modelName }, migrated: false };
}

function isCompleteReviewConfig(apiKey, baseUrl, modelName) {
  return Boolean(apiKey.trim() && baseUrl.trim() && modelName.trim());
}

// ============================================================
// Mock data for demo
// ============================================================
const MOCK_RESULT = {
  overallScore: 82.0,
  evaluationStatus: "success",
  biasLevel: "Moderate-Low",
  retryCount: 0,
  auditPassed: true,
  textAnchors: [],
  limitations: [
    "结果由大模型辅助生成，不替代同行评审或编辑部决定。",
    "缺乏同领域最新文献的完整横向对比时，影响力预测存在局限。",
    "纯思辨或政策评论类文章的量化评分不确定性通常更高。",
  ],
  // v5 学科分类与评分策略
  scoringPolicy: {
    policy: "humanities_locked_v1",
    locked: true,
    weights: {
      data_reliability: 0.25,
      logical_rigor: 0.20,
      innovation: 0.25,
      academic_impact: 0.20,
      ethics_bias: 0.10,
    },
    subject_top: "人文学科",
    subject_sub: "公共管理",
    paper_type: "质性",
  },
  researchProfile: {
    research_type: "公共管理质性研究",
    methodology_type: "半结构化访谈 + 政策文本分析",
    evidence_type: "访谈材料、政策文件与案例比较",
  },
  engines: {
    methodology: {
      score: 85, confidence: 0.82, risk_level: "low",
      core_conclusion: "研究方法整体规范，但样本存在显著的 WEIRD 偏差，统计方法选择恰当但效应量报告不完整。",
      evidence: "Section 3.2 Table 1：样本 76% 来自北美本科生（M_age=19.7），性别比 7:3。未提供 G*Power 先验功效分析。Method 部分缺失异常值处理策略。",
      actionable_advice: ["补充分层采样或事后加权方案校正人口学偏差", "在 Method 部分增加 G*Power 功效分析报告", "采用箱线图+IQR 法处理异常值并公开排除标准"],
      strengths: ["实验设计整体清晰，变量定义明确", "统计方法选择合理"],
      issues: [
        { issue_type: "sample_size_insufficient", severity: "high", evidence: "样本 76% 来自北美本科生（M_age=19.7）", suggestion: "补充分层采样或事后加权方案校正人口学偏差" }
      ],
      reasoning_md: "样本代表性不足是主要限制因素...",
      limitations: ["效应量报告不完整"],
      // v5 新增
      bias_explanation: {
        detected_biases: ["selection_bias", "geographic_bias"],
        bias_impact_assessment: "样本集中于少数地区与人群，可能低估人口与文化多样性对结论的影响。",
        debiasing_recommendations: "建议补充不同地区样本，并在局限性中说明样本边界。",
      },
      missing_literature: [
        { title: "Henrich et al. (2010) — The weirdest people in the world?", relevance: "WEIRD 偏差的经典文献", why_missing_is_problematic: "缺少对 WEIRD 偏差的系统性理论与实证依据。" },
      ],
      alternative_theories: [
        { name: "文化心理学视角 (Markus & Kitayama, 1991)", applicability: "可用于解释跨文化样本中的认知差异模式。", potential_insight: "补充文化差异对实验结果的调节效应分析。" },
      ],
    },
    logic: {
      score: 90, confidence: 0.88, risk_level: "low",
      core_conclusion: "论证链条清晰递进，因果推断框架正确，但 Discussion 中存在一处相关到因果的语言跳跃。",
      evidence: "Section 4.1 Discussion：'A significantly predicts B (p<.001), therefore A causes B' — 未控制潜在混杂变量 Z。全文逻辑一致性良好，各章节间无矛盾。",
      actionable_advice: ["将因果性语言改为关联性表述", "在 Limitations 中列出未观测混杂变量", "勘误 p.15 表 3 注释中的符号错误"],
      strengths: ["论证链条清晰递进", "因果推断框架正确"],
      issues: [
        { issue_type: "causality_confusion", severity: "medium", evidence: "A significantly predicts B (p<.001), therefore A causes B", suggestion: "将因果性语言改为关联性表述" }
      ],
      reasoning_md: "整体逻辑一致性良好...",
      limitations: [],
      // v5 新增
      logic_correction_plan: {
        identified_gaps: ["因果推断缺少反事实框架", "交互效应解读处符号印刷错误"],
        correction_strategy: "使用工具变量或DiD进行因果识别，勘误p.15表3注释中的符号错误，并在Limitation段明确列出未观测混杂变量的可能影响。",
        revised_argument_flow: "研究问题 → 理论机制 → 变量操作化 → 相关性分析 → 因果识别(DiD/IV) → 稳健性检验 → 结论与局限",
      },
    },
    ethics: {
      score: 95, confidence: 0.90, risk_level: "low",
      core_conclusion: "伦理合规性优秀，IRB 信息完整可追溯，但作者地域单一性构成轻微的认知多样性不足。",
      evidence: "首页明确标注 IRB#2024-047，可在线验证。利益冲突声明完整。但作者单位均为北美 R1 大学（MIT, Stanford, UC Berkeley），被试招募仅通过英文渠道。",
      actionable_advice: ["在 Discussion 末尾增加研究局限性声明", "建议邀请非西方机构合作者审阅讨论部分", "完善 COI 的机构级披露"],
      strengths: ["伦理合规性优秀", "IRB 信息完整可追溯"],
      issues: [
        { issue_type: "regional_bias", severity: "low", evidence: "作者单位均为北美 R1 大学", suggestion: "在 Discussion 末尾增加研究局限性声明" }
      ],
      reasoning_md: "伦理合规性优秀...",
      limitations: ["作者地域单一性构成轻微的认知多样性不足"],
      // v5 新增
      bias_explanation: {
        detected_biases: ["cultural_bias", "geographic_bias"],
        bias_impact_assessment: "研究只纳入特定文化群体，研究问题的普适性受限于文化语境。",
        debiasing_recommendations: "明确声明研究的文化适用范围，避免将结论推广至非相关文化语境。",
      },
    },
    innovation: {
      score: 75, confidence: 0.75, risk_level: "medium",
      core_conclusion: "XAI 方法交叉引入具有一定新颖性，但理论贡献属验证式扩展而非颠覆性突破，未来工作展望过于宽泛。",
      evidence: "Introduction 明确声称'首次将 SHAP 解释框架引入该细分领域'，但 Discussion 承认'核心框架沿用 [Smith, 2020]'。增量贡献主要体现在 contextual application 层面。",
      actionable_advice: ["精确区分方法论创新与应用创新的边界", "将'未来可结合 fMRI'改为具体实验设计方案", "补充与 [Chen, 2023] 的差异化对比"],
      strengths: ["XAI 方法交叉引入具有一定新颖性"],
      issues: [
        { issue_type: "incremental_improvement", severity: "medium", evidence: "核心框架沿用 [Smith, 2020]", suggestion: "精确区分方法论创新与应用创新的边界" }
      ],
      reasoning_md: "创新性属于验证式扩展...",
      limitations: ["理论贡献属验证式扩展而非颠覆性突破"],
      // v5 新增
      alternative_theories: [
        { name: "制度主义组织理论 (DiMaggio & Powell, 1983)", applicability: "适合解释组织在技术创新压力下的趋同与分化行为。", potential_insight: "可增强对技术创新扩散机制的理论解释力。" },
        { name: "社会技术系统理论 (Trist & Bamforth, 1951)", applicability: "将技术置于社会系统中考察，与本文交叉学科定位匹配。", potential_insight: "帮助理解技术引入对组织结构和人的互动模式的深层影响。" },
      ],
      missing_literature: [
        { title: "Chen (2023) — 同类方法的最新应用", relevance: "直接竞争文献", why_missing_is_problematic: "缺少对最相关竞争工作的差异化对比，削弱了创新声明的说服力。" },
      ],
    },
    academic_impact: {
      score: 78, confidence: 0.65, risk_level: "medium",
      core_conclusion: "论文选题具有明确学术相关性，理论贡献定位清晰，但实证支撑与影响力的论证链条仍有强化空间。",
      evidence: "",
      actionable_advice: ["增加对研究局限性的坦诚陈述", "补充投稿前需完成的理论推导或实验验证事项"],
      strengths: ["选题具有明确的学术相关性", "理论贡献定位清晰"],
      issues: [
        { issue_type: "empirical_evidence_incomplete", severity: "medium", evidence: "实证支撑与影响力的论证链条仍有强化空间", suggestion: "补充投稿前需要完成的实验验证事项" }
      ],
      reasoning_md: "论文在选题新颖度和理论贡献方面表现良好...",
      limitations: ["缺乏同领域最新文献的完整横向对比"],
      journal_recommendation: {
        recommended_tier: "SCI Q2 / CCF-B",
        alternative_tier: "SCI Q3 / 中文核心期刊",
        confidence: 0.60,
        rationale: ["论文选题具有交叉学科新颖性", "方法设计较为严谨"],
        readiness_gaps: ["补充跨领域对比实验", "完善理论贡献的量化分析"],
        basis: ["摘要与引言对研究问题的定位", "方法部分的实验设计"],
      },
      // v5 新增
      missing_literature: [
        { title: "评价体系相关的近三年综述文献", relevance: "提供学术影响力评价方法论参考", why_missing_is_problematic: "缺少对现有评价方法论的系统了解，建议的客观性和全面性受限。" },
      ],
      research_profile: {
        research_type: "交叉学科实证研究",
        methodology_type: "混合方法",
        evidence_type: "实验数据 + 文献对比",
      },
      impact_evidence: {
        audience: "心理学、计算机科学、认知科学交叉领域研究者",
        potential_journals: ["Nature Human Behaviour", "Psychological Science", "Cognitive Psychology"],
        impact_pathway: "通过完善理论对比和实证支撑，论文有潜力进入中高影响力期刊。",
      },
    },
  },
};

// ============================================================
// ErrorBoundary — prevents any child crash from white-screening
// ============================================================
class ErrorBoundary extends Component {
  constructor(props) {
    super(props);
    this.state = { hasError: false, error: null };
  }
  static getDerivedStateFromError(error) {
    return { hasError: true, error };
  }
  componentDidCatch(error, errorInfo) {
    console.error("[AI学术审查系统 ErrorBoundary]", error, errorInfo);
  }
  render() {
    if (this.state.hasError) {
      return (
        <div className="bg-red-50 border border-red-200 rounded-xl p-6 text-sm text-red-800">
          <p className="font-semibold mb-2">界面渲染被中断</p>
          <p className="text-xs text-red-700 mb-2">
            浏览器翻译或扩展可能修改了页面节点，请刷新页面后重试，并关闭当前页面的自动翻译。
          </p>
          <p className="text-xs font-mono text-red-600 whitespace-pre-wrap">
            {String(this.state.error?.message ?? "Unknown error")}
          </p>
          <button
            onClick={() => window.location.reload()}
            className="mt-3 text-xs bg-red-100 hover:bg-red-200 text-red-700 px-3 py-1.5 rounded-lg transition-colors"
          >
            刷新页面恢复
          </button>
        </div>
      );
    }
    return this.props.children;
  }
}

// ============================================================
// i18n Dictionary (unchanged)
// ============================================================
const T = {
  zh: {
    brand: "AI学术审查系统", version: `v${APP_VERSION} 专业版`,
    engineStatus: "引擎状态", reviewEngine: "审查引擎", computePool: "计算池负载",
    arbitrationHub: "仲裁中枢", reviewMode: "审查模式",
    online: "在线", cores4: "5 核并发", idle: "空闲", active: "运行中", standby: "待命",
    concurrent: "并发审查", arbitration: "仲裁打回",
    uploadManuscript: "论文上传", uploadHint: "点击上传 PDF / DOCX / TXT", clearReset: "清除重置",
    appTitle: "AI学术审查系统",
    appSubtitle: "多引擎协同 · 深度学术偏见检测 · 全局一致性仲裁",
    title: "AI学术审查系统 学术偏见审查系统",
    subtitle: "多引擎协作 · 深度学术偏见检测 · 全局一致性仲裁",
    tab0: "社会科学与人文", tab1: "理工与实验科学", tab2: "医学与生命科学",
    focus0: "审查侧重：抽样代表性、文化偏见（WEIRD）、质性编码信度、意识形态渗透检测。适用于社会学、心理学、教育学、人类学及相关领域论文。",
    focus1: "审查侧重：实验可复现性、统计方法恰当性、数据清洗透明度、结果选择性报告（p-hacking）。适用于计算机科学、物理学、工程学及相关领域论文。",
    focus2: "审查侧重：临床试验注册合规性、利益冲突披露、样本纳入/排除标准合理性、基因决定论与生物本质主义风险。适用于医学、公共卫生、生命科学及相关领域论文。",
    uploadPrompt: "请在左侧边栏上传一篇 PDF 论文文件以开始审查。",
    systemOverview: "系统概述",
    overviewDesc: `AI学术审查系统 v${APP_VERSION} 是一款基于多引擎协作的学术论文偏见审查工具，旨在帮助研究者在投稿前自查论文中潜在的认知偏见、方法论缺陷与伦理风险。`,
    reviewPipeline: "审查流程",
    pipeline1: "上传论文 — 四大引擎并发阅读全文。",
    pipeline2: "全局仲裁 — 仲裁中枢交叉校验各引擎评分与 Evidence 的一致性，发现异常立即打回重做。",
    pipeline3: "生成报告 — 结构化输出含雷达图、综合得分及各引擎详细评语。",
    pipeline1Label: "上传论文", pipeline2Label: "全局仲裁", pipeline3Label: "生成报告",
    reconfigure: "重新配置",
    fourEngines: "四大审查引擎", engineCol: "引擎", focusCol: "关注要点",
    initEngines: "正在初始化学术审查引擎...", launching: "启动",
    submitting: "将审查结果提交至 [全局一致性仲裁中枢] 进行交叉比对...",
    toastConflict: "仲裁中枢发现评分-Evidence 维度冲突，已触发强制深度复审！",
    warnMismatch: "检测到高分低质维度冲突：方法论引擎评分偏高，但其 Evidence 明确记录了 WEIRD 抽样偏差与类别不平衡问题。正在进行第 {n} 轮重构审查...",
    reEval: "方法论与实证检验引擎重新评估中 ...",
    crossCheck: "全局一致性仲裁中枢二次校验中 ...",
    finalApproval: "仲裁中枢终审核准：所有引擎评分与 Evidence 一致性达标，结构化报告已生成。",
    processing: "处理中...",
    successBanner: "仲裁中枢终审核准：所有引擎评分与 Evidence 一致性达标，结构化报告已生成。",
    radarTitle: "四维审查雷达图", overallAssessment: "综合评审结果",
    compositeScore: "综合审查得分", biasLevel: "偏见等级",
    moderateLow: "中等偏低", moderateHigh: "中等偏高", crossValidated: "交叉校验通过",
    perEngineScores: "各引擎得分明细", postArbitration: "仲裁修正（第 {n} 轮）",
    readyForReview: "就绪，等待审查", startReview: "开始审查",
    reviewComplete: "审查完成", reRunReview: "重新审查",
    engineReports: "引擎详细报告",
    reportsCaption: "以下为全局一致性仲裁中枢终审通过后的最终报告，各引擎输出均已交叉校验。",
    coreConclusion: "核心结论", evidence: "评价依据（Evidence）",
    actionableAdvice: "修改建议（Actionable Advice）", engineScore: "引擎评分",
    download: "下载完整审查报告（Markdown）",
    demoButton: "查看样例",
    confidenceLabel: "置信度",
    strengthsLabel: "论文亮点",
    issuesLabel: "检测到的问题",
    suggestionLabel: "建议: ",
    viewReasoning: "查看审查引擎推理过程",
    limitationsLabel: "评估局限性: ",
    exportPDFLabel: "导出 PDF",
    exportingLabel: "正在导出...",
    lowRiskLabel: "低风险", mediumRiskLabel: "中风险", highRiskLabel: "高风险",
    highSeverityLabel: "高严重度", mediumSeverityLabel: "中严重度", lowSeverityLabel: "低严重度",
    privacyFooter: "隐私声明：上传论文仅作本地/临时缓存，检测完成 72 小时后自动删除，绝不抓取或传播用户未发表稿件。",
    premiumUnlocked: "已为您解锁深度诊断与无水印报告特权",
    newReview: "新建审查", history: "历史记录", devMode: "运维模式",
    historySoon: "历史记录功能开发中...", settingsSoon: "设置功能开发中...",
    appealTitle: "对 {engine} 的审查结果有异议？",
    appealHint: "请描述您的申诉理由，我们将转交人工专家复核。",
    appealPlaceholder: "请说明您认为大模型误判的具体原因...",
    appealSuccess: "申诉已记录，将转交人工专家复核。",
    submitAppeal: "提交复核", cancel: "取消",
    appealButton: "异议申诉",
    configSaved: "保存并进入审查系统",
    configHint: "凭证仅保存在本地浏览器中，不会上传到服务器",
    configTitle: "配置你的大模型 API 凭证以进入审查系统",
    configApiKey: "API Key", configBaseUrl: "Base URL", configModel: "模型名称",
    methodologyName: "方法论与实证检验引擎", logicName: "论证严密性与逻辑推演引擎",
    ethicsName: "学术伦理与认知偏见检测引擎", innovationName: "理论增量与前瞻性评估引擎",
    academicImpactName: "学术影响力与期刊匹配度评估引擎",
    methodologyShort: "方法论与实证", logicShort: "逻辑推演",
    ethicsShort: "伦理与偏见", innovationShort: "理论增量",
    academicImpactShort: "学术影响力",
    methFocus: "抽样偏差、数据质量、统计方法、可复现性",
    logicFocus: "论证链条、因果推断、谬误检测、统计表述准确性",
    ethicsFocus: "WEIRD 偏见、IRB 合规、利益冲突、认知多样性",
    innovationFocus: "理论贡献、方法论新颖性、应用潜力、未来工作可行性",
    academicImpactFocus: "选题新颖度、理论贡献、论证质量、实证完整性、期刊层级建议",
    methConclusion: "样本存在显著的 WEIRD 偏差与类别不平衡，实验设计整体可复现但部分对照组选择逻辑需补充。",
    methEvidence: "论文第 3.2 节 Table 1：样本男女比 8:2，且 94% 被试来自北美大学本科生群体。Method 部分未提供异常值处理策略与剔除标准。",
    methAdvice: "补充分层采样或事后加权方案以校正性别/地域失衡；在 Method 节增加异常值处理流程图（推荐箱线图+IQR法）；公开数据收集的完整 exclusion criteria。",
    logicConclusion: "整体论证链条清晰、递进合理，但因果关系推断环节存在相关到因果的跳跃，交互效应解读处有一处符号印刷错误。",
    logicEvidence: "论文第 4.1 节 Discussion 段1：'A significantly correlates with B, therefore A causes B' — 未讨论潜在混杂变量 Z。p.15 表3 注释：回归系数符号与正文不符。",
    logicAdvice: "将因果性表述改为关联性表述，或补充工具变量/DID 等因果识别策略；勘误 p.15 表3注释中的符号错误；在 Limitation 段明确列出未观测混杂变量的可能影响。",
    ethicsConclusion: "IRB 合规信息不完整，作者团队地域单一性构成认知多样性缺陷，敏感社会议题的讨论缺少风险声明。",
    ethicsEvidence: "脚注仅写'经伦理审查批准'但无 IRB 编号（参见首页脚注）；作者机构均为北美 R1 大学（Title Page）；被试招募广告仅通过英文渠道发布（Appendix A）。",
    ethicsAdvice: "补充可追溯的 IRB 审批编号；在 Discussion 末尾增加研究局限性声明，明确说明样本文化局限及结论适用范围；建议邀请至少 1 位非西方机构合作者审阅敏感议题论述。",
    innovationConclusion: "XAI 方法交叉引入心理学领域具有新颖性，但理论贡献属验证式扩展而非颠覆性突破，未来工作展望过于宽泛。",
    innovationEvidence: "Introduction 段3 明确声称'首次将 SHAP 解释框架引入该细分领域'；但 Discussion 中承认'核心框架沿用 [Smith, 2020]'，增量贡献主要体现在 contextual application。",
    innovationAdvice: "在 Contribution 段精确区分方法论创新与应用创新的边界；将'未来可结合 fMRI'改为具体的实验设计提案（如在 N=80 的 within-subject 设计中复现 Study 2）；补充与 [Chen, 2023] 的差异化对比分析。",
    academicImpactName: "学术影响力与期刊匹配度评估引擎",
    academicImpactShort: "学术影响力",
    academicImpactFocus: "选题新颖度、理论贡献、论证质量、实证完整性、期刊层级建议",
    academicImpactConclusion: "论文选题具有明确的学术相关性，理论贡献定位清晰，但实证支撑与潜在影响力的论证链条仍有强化空间。",
    academicImpactEvidence: "论文摘要与引言对研究问题的重要性表述充分；结论部分对贡献的定位与现有文献的差异化分析基本到位。",
    academicImpactAdvice: "建议在讨论部分增加对研究局限性更为坦诚的陈述；补充投稿前需要完成的理论推导或实验验证事项。",
    partialFailureWarning: "部分审查维度响应超时，已为您展示已完成的审查结果",
    journalRecommendationLabel: "投稿期刊层级建议",
    recommendedTier: "推荐层级",
    alternativeTier: "备选层级",
    rationale: "建议理由",
    readinessGaps: "投稿前待完善事项",
    basis: "建议依据",
    viewOriginal: "查看原文",
    drawClose: "关闭",
    drawTitle: "原文定位",
  },
  en: {
    brand: "AI学术审查系统", version: `v${APP_VERSION} Professional`,
    engineStatus: "Engine Status", reviewEngine: "Review Engine", computePool: "Compute Pool",
    arbitrationHub: "Arbitration Hub", reviewMode: "Review Mode",
    online: "Online", cores4: "5 Cores", idle: "Idle", active: "Active", standby: "Standby",
    concurrent: "Concurrent", arbitration: "Arbitration",
    uploadManuscript: "Upload Manuscript", uploadHint: "Click to upload PDF", clearReset: "Clear & Reset",
    appTitle: "AI Academic Review System",
    appSubtitle: "Multi-engine collaboration · Deep bias detection · Global arbitration",
    title: "AI学术审查系统 Academic Bias Review System",
    subtitle: "Multi-engine collaboration · Deep academic bias detection · Global consistency arbitration",
    tab0: "Social Sciences & Humanities", tab1: "STEM & Experimental Sciences", tab2: "Medicine & Life Sciences",
    focus0: "Review focus: Sampling representativeness, cultural bias (WEIRD), qualitative coding reliability, ideological penetration detection. Applicable to sociology, psychology, education, anthropology, and related fields.",
    focus1: "Review focus: Experimental reproducibility, statistical method adequacy, data cleaning transparency, selective reporting (p-hacking). Applicable to computer science, physics, engineering, and related fields.",
    focus2: "Review focus: Clinical trial registration compliance, COI disclosure, inclusion/exclusion criteria rationale, genetic determinism and bio-essentialism risk. Applicable to medicine, public health, life sciences, and related fields.",
    uploadPrompt: "Upload a PDF manuscript in the left sidebar to begin the review.",
    systemOverview: "System Overview",
    overviewDesc: `AI学术审查系统 v${APP_VERSION} is a multi-engine academic bias review tool designed to help researchers self-audit papers for potential cognitive biases, methodological flaws, and ethical risks before submission.`,
    reviewPipeline: "Review Pipeline",
    pipeline1: "Upload — Four engines concurrently read the full manuscript.",
    pipeline2: "Arbitration — The Arbitration Hub cross-validates scores against Evidence. Any conflict triggers an immediate re-review.",
    pipeline3: "Report — Structured output: radar chart, composite score, and detailed per-engine reports.",
    pipeline1Label: "Upload Manuscript", pipeline2Label: "Global Arbitration", pipeline3Label: "Generate Report",
    reconfigure: "Reconfigure",
    fourEngines: "Four Review Engines", engineCol: "Engine", focusCol: "Focus Area",
    initEngines: "Initializing academic review engines...", launching: "Launching",
    submitting: "Submitting results to [Global Consistency Arbitration Hub] for cross-validation ...",
    toastConflict: "Arbitration Hub detected dimension conflict — forced deep re-review triggered!",
    warnMismatch: "Score-Evidence mismatch detected: Methodology Engine scored high while its Evidence records WEIRD bias and class imbalance. Re-evaluation round {n} in progress...",
    reEval: "Methodology Engine re-evaluating ...",
    crossCheck: "Arbitration Hub performing secondary cross-check ...",
    finalApproval: "Arbitration Hub final approval: all engine scores consistent with Evidence. Structured report generated.",
    processing: "Processing...",
    successBanner: "Arbitration Hub final approval: all engine scores are consistent with their Evidence. Structured report generated.",
    radarTitle: "4-Dimension Review Radar", overallAssessment: "Overall Assessment",
    compositeScore: "Composite Score", biasLevel: "Bias Level",
    moderateLow: "Moderate-Low", moderateHigh: "Moderate-High", crossValidated: "Cross-validated",
    perEngineScores: "Per-Engine Scores", postArbitration: "Post-arbitration (round {n})",
    readyForReview: "Ready for review", startReview: "Start Review",
    reviewComplete: "Review Complete", reRunReview: "Re-run Review",
    engineReports: "Detailed Engine Reports",
    reportsCaption: "Final output approved by the Global Consistency Arbitration Hub. All entries are cross-validated.",
    coreConclusion: "Core Conclusion", evidence: "Evidence",
    actionableAdvice: "Actionable Advice", engineScore: "Engine Score",
    download: "Download Full Review Report (Markdown)",
    demoButton: "View Sample",
    confidenceLabel: "Confidence",
    strengthsLabel: "Strengths",
    issuesLabel: "Issues",
    suggestionLabel: "Suggestion: ",
    viewReasoning: "View Engine Reasoning Process",
    limitationsLabel: "Limitations: ",
    exportPDFLabel: "Export PDF",
    exportingLabel: "Exporting...",
    lowRiskLabel: "Low Risk", mediumRiskLabel: "Medium Risk", highRiskLabel: "High Risk",
    highSeverityLabel: "High Severity", mediumSeverityLabel: "Medium Severity", lowSeverityLabel: "Low Severity",
    privacyFooter: "Privacy: Uploaded manuscripts are cached locally/temporarily and auto-deleted 72 hours after review. We never crawl or distribute unpublished work.",
    premiumUnlocked: "Deep diagnostics & watermark-free reports unlocked!",
    newReview: "New Review", history: "History", devMode: "Ops Mode",
    historySoon: "History feature coming soon...", settingsSoon: "Settings feature coming soon...",
    appealTitle: "Objection to {engine}'s review?",
    appealHint: "Describe your objection and we will escalate to a human expert for review.",
    appealPlaceholder: "Please explain why you believe the AI made an error...",
    appealSuccess: "Objection recorded — will be escalated to human expert review.",
    submitAppeal: "Submit Appeal", cancel: "Cancel",
    appealButton: "Appeal",
    configSaved: "Save & Enter System",
    configHint: "Credentials are stored only in your browser — never uploaded to the server.",
    configTitle: "Configure your LLM API credentials to enter the review system",
    configApiKey: "API Key", configBaseUrl: "Base URL", configModel: "Model Name",
    methodologyName: "Methodology & Empirical Validation", logicName: "Argument Rigor & Logical Deduction",
    ethicsName: "Academic Ethics & Cognitive Bias Detection", innovationName: "Theoretical Increment & Foresight Assessment",
    methodologyShort: "Methodology", logicShort: "Logic",
    ethicsShort: "Ethics", innovationShort: "Innovation",
    methFocus: "Sampling bias, data quality, statistical methods, reproducibility",
    logicFocus: "Argument chain, causal inference, fallacy detection, reporting accuracy",
    ethicsFocus: "WEIRD bias, IRB compliance, COI, cognitive diversity",
    innovationFocus: "Theoretical contribution, novelty, application potential, future-work feasibility",
    methConclusion: "Significant WEIRD bias and class imbalance detected. Experimental design is reproducible but control-group logic needs supplementation.",
    methEvidence: "Section 3.2, Table 1: M/F ratio 8:2; 94% of subjects are North American undergraduates. The Method section lacks an outlier-handling strategy and exclusion criteria.",
    methAdvice: "Add stratified sampling or post-hoc weighting to correct gender/regional imbalance. Include an outlier-handling flowchart (boxplot + IQR) in the Method section. Publish full exclusion criteria.",
    logicConclusion: "Argument chain is clear and progressive, but a correlation-to-causation leap exists. One typographical sign error in interaction-effect notes.",
    logicEvidence: "Section 4.1, Discussion: 'A significantly correlates with B, therefore A causes B' — confounding variable Z not discussed. Table 3 note on p.15: regression coefficient sign contradicts the main text.",
    logicAdvice: "Replace causal with associative language, or supplement with IV/DiD strategies. Correct the sign error in Table 3 note. List unobserved confounders in Limitations.",
    ethicsConclusion: "IRB compliance is incomplete. Single-region authorship constitutes a cognitive-diversity deficit. Sensitive-topic discussion lacks a risk statement.",
    ethicsEvidence: "Footnote states 'approved by ethics review' but no IRB number. All author affiliations are North American R1 universities. Recruitment ads published only via English channels.",
    ethicsAdvice: "Supply a traceable IRB approval number. Add a Limitations of Generalizability statement. Invite at least one non-Western collaborator to review sensitive passages.",
    innovationConclusion: "Introducing XAI into this sub-field is novel, but the theoretical contribution is confirmatory extension rather than a breakthrough. Future-work outlook is overly broad.",
    innovationEvidence: "Introduction claims 'first application of SHAP to this sub-domain'. Discussion acknowledges 'core framework follows [Smith, 2020]' — primarily contextual application.",
    innovationAdvice: "Distinguish methodological from application innovation. Replace 'future fMRI' with a concrete proposal. Add differentiated comparison with [Chen, 2023].",
    academicImpactName: "Academic Impact & Journal Matching Engine",
    academicImpactShort: "Academic Impact",
    academicImpactFocus: "Topic novelty, theoretical contribution, argument quality, empirical completeness, journal tier recommendation",
    academicImpactConclusion: "The paper topic shows clear academic relevance with well-positioned contributions, but the chain from evidence to impact could be strengthened.",
    academicImpactEvidence: "The abstract and introduction adequately establish the importance of the research question; conclusions appropriately distinguish contributions from existing literature.",
    academicImpactAdvice: "Consider adding more candid limitations in the discussion; itemize theoretical derivations or experimental validations needed before submission.",
    partialFailureWarning: "Some review dimensions timed out. Completed results are shown below.",
    journalRecommendationLabel: "Journal Tier Recommendation",
    recommendedTier: "Recommended Tier",
    alternativeTier: "Alternative Tier",
    rationale: "Rationale",
    readinessGaps: "Readiness Gaps",
    basis: "Basis",
    viewOriginal: "View Original",
    drawClose: "Close",
    drawTitle: "Original Text",
  },
};

const ENGINE_KEYS = ["methodology", "ethics", "logic", "innovation", "academic_impact"];

const PROGRESS_STAGE_LABELS = {
  upload: "文件接收",
  review: "审查准备",
  parsing: "论文解析",
  evaluation: "并行评价",
  audit: "一致性复核",
  report: "报告整理",
  working: "持续处理",
};

const PROGRESS_AGENT_LABELS = {
  data_reliability: "数据可靠性",
  ethics_bias: "伦理与偏见",
  logical_rigor: "逻辑与论证",
  innovation: "理论与创新",
  academic_impact: "学术影响力",
  audit: "一致性复核",
};

function getLogText(entry) {
  if (entry && typeof entry === "object") return String(entry.text ?? "");
  return String(entry ?? "");
}

function comparableTimedMessage(text) {
  return String(text).replace(/\d+(?:\.\d+)?\s*秒/g, "{seconds}秒");
}

function upsertProgressLog(previous, event, text) {
  const progressKey = event.progressKey || null;
  const entry = {
    kind: "progress",
    id: progressKey ? `progress-${progressKey}` : `progress-${Date.now()}-${Math.random()}`,
    progressKey,
    text,
    stageElapsedSeconds: Number.isFinite(Number(event.stageElapsedSeconds))
      ? Number(event.stageElapsedSeconds)
      : null,
  };
  const next = [...previous];

  if (progressKey) {
    const existingIndex = next.findLastIndex(
      (item) => item && typeof item === "object" && item.progressKey === progressKey,
    );
    if (existingIndex >= 0) {
      entry.id = next[existingIndex].id;
      next[existingIndex] = entry;
      return next.slice(-160);
    }
  }

  const lastIndex = next.length - 1;
  const last = next[lastIndex];
  if (
    last && typeof last === "object" && last.kind === "progress"
    && comparableTimedMessage(last.text) === comparableTimedMessage(text)
    && /\d+(?:\.\d+)?\s*秒/.test(text)
  ) {
    entry.id = last.id;
    entry.progressKey = last.progressKey || progressKey;
    next[lastIndex] = entry;
    return next.slice(-160);
  }

  next.push(entry);
  return next.slice(-160);
}

function AnimatedElapsedText({ text, elapsedSeconds, animationKey }) {
  if (!Number.isFinite(elapsedSeconds)) return text;
  const marker = `${elapsedSeconds} 秒`;
  const markerIndex = text.lastIndexOf(marker);
  if (markerIndex < 0) return text;
  return (
    <>
      {text.slice(0, markerIndex)}
      <span
        key={`${animationKey}-${elapsedSeconds}`}
        className="elapsed-number-flip"
        aria-label={`${elapsedSeconds} 秒`}
      >
        {elapsedSeconds}
      </span>
      {text.slice(markerIndex + String(elapsedSeconds).length)}
    </>
  );
}

// Engine weight colors (for donut chart & sliders)
const ENGINE_WEIGHT_COLORS = {
  methodology: { fill: "#3B82F6", label: "方法论", labelEn: "Methodology" },
  logic: { fill: "#8B5CF6", label: "逻辑推演", labelEn: "Logic" },
  ethics: { fill: "#10B981", label: "伦理偏见", labelEn: "Ethics" },
  innovation: { fill: "#F97316", label: "理论创新", labelEn: "Innovation" },
  academic_impact: { fill: "#EC4899", label: "学术影响力", labelEn: "Academic Impact" },
};

// ============================================================
// Issue type mapping: English key -> Chinese academic term
// ============================================================
const ISSUE_TYPE_MAP = {
  uncertainty_not_reported: "未报告不确定性",
  reproducibility_insufficient: "复现性不足",
  related_work_insufficient: "相关工作对比不足",
  methodology_flaw: "方法论缺陷",
  sampling_bias: "抽样偏差",
  statistical_error: "统计方法错误",
  ethics_concern: "学术伦理问题",
  data_quality_issue: "数据质量问题",
  conclusion_overreach: "结论过度推断",
  missing_control_group: "缺少对照组",
  confound_not_controlled: "混杂变量未控制",
  literature_gap: "文献覆盖不足",
  logical_fallacy: "逻辑谬误",
  reporting_incomplete: "报告不完整",
  peer_review_insufficient: "同行评审不充分",
  effect_size_not_reported: "效应量未报告",
  power_analysis_missing: "功效分析缺失",
  selection_bias: "选择偏差",
  measurement_bias: "测量偏差",
  confounding_bias: "混杂偏差",
  publication_bias: "发表偏差",
  funding_bias: "资助偏差",
  cultural_bias: "文化偏差",
  gender_bias: "性别偏差",
  geographic_bias: "地域偏差",
  language_bias: "语言偏差",
  citation_bias: "引用偏差",
};

function translateIssueType(rawType) {
  if (!rawType) return "";
  return ISSUE_TYPE_MAP[rawType] || rawType;
}

// ============================================================
// Shared Markdown report generator (used by both download & PDF)
// ============================================================
function escapeHtml(value) {
  return String(value ?? "")
    .replace(/&/g, "&amp;")
    .replace(/</g, "&lt;")
    .replace(/>/g, "&gt;")
    .replace(/"/g, "&quot;")
    .replace(/'/g, "&#039;");
}

function reportText(value) {
  if (value == null) return "";
  if (typeof value === "string" || typeof value === "number") return String(value);
  if (Array.isArray(value)) return value.map(reportText).filter(Boolean).join("；");
  if (typeof value === "object") {
    return reportText(value.evidence || value.aspect || value.text || value.content || value.summary || "");
  }
  return String(value);
}

function generateMarkdownReport({ engineMeta, engineScores, overallScore, biasLevel, retryCount, forPdf = false, scoringPolicy = null, enginesRaw = null }) {
  const lines = [];
  const scoreValue = typeof overallScore === "number" ? overallScore.toFixed(1) : "0.0";

  if (!forPdf) {
    lines.push("# 论文审查报告");
    lines.push("");
    lines.push(`- **综合得分**：${scoreValue} / 100`);
    lines.push(`- **整体风险等级**：${biasLevel}`);
    lines.push(`- **仲裁轮次**：${retryCount}`);
    lines.push("");
    lines.push("---");
    lines.push("");
  }

  // v5: 学科识别与评分策略
  if (scoringPolicy && scoringPolicy.subject_top) {
    lines.push("## 学科识别与评分策略");
    lines.push("");
    lines.push("| 项目 | 内容 |");
    lines.push("|------|------|");
    lines.push(`| **顶层学科** | ${scoringPolicy.subject_top || "-"} |`);
    if (scoringPolicy.subject_sub) {
      lines.push(`| **二级学科** | ${scoringPolicy.subject_sub} |`);
    }
    if (scoringPolicy.paper_type) {
      const typeLabel = scoringPolicy.paper_type === "质性" ? "质性研究" :
        scoringPolicy.paper_type === "量化" ? "量化研究" :
        scoringPolicy.paper_type === "混合" ? "混合方法" : scoringPolicy.paper_type;
      lines.push(`| **研究范式** | ${typeLabel} |`);
    }
    if (scoringPolicy.policy) {
      const displayName = scoringPolicy.policy_label || (POLICY_LABEL_MAP[scoringPolicy.policy] || scoringPolicy.policy);
      lines.push(`| **评分策略** | ${displayName} |`);
    }
    lines.push(`| **权重锁定** | ${scoringPolicy.locked ? "是" : "否"} |`);
    lines.push("");

    // 权重表
    if (scoringPolicy.weights && typeof scoringPolicy.weights === "object") {
      lines.push("### 权重配置");
      lines.push("");
      lines.push("| 评价维度 | 权重 |");
      lines.push("|----------|-----:|");
      ENGINE_KEYS.forEach((frontendKey) => {
        const backendKey = FRONTEND_TO_BACKEND_WEIGHT_KEY[frontendKey] || frontendKey;
        const raw = scoringPolicy.weights[backendKey];
        const pct = raw != null ? `${Math.round(Number(raw) * 100)}%` : "-";
        const label = (engineMeta[frontendKey] && engineMeta[frontendKey].name) || frontendKey;
        lines.push(`| ${label} | ${pct} |`);
      });
      lines.push("");
    }
    lines.push("---");
    lines.push("");
  }

  ENGINE_KEYS.forEach((key, idx) => {
    const eng = engineMeta[key] || {};
    const score = engineScores[key] ?? 0;
    const engNum = idx + 1;

    lines.push(`## ${engNum}. ${eng.name || key}`);
    lines.push("");

    lines.push("| 项目 | 内容 |");
    lines.push("|------|------|");
    lines.push(`| **引擎名称** | ${eng.name || key} |`);
    lines.push(`| **单项得分** | ${score} / 100 |`);
    if (eng.confidence != null) {
      lines.push(`| **置信度** | ${Math.round(eng.confidence * 100)}% |`);
    }
    if (eng.riskLevel) {
      const riskLabel =
        eng.riskLevel === "high" ? "【高度风险】" :
        eng.riskLevel === "medium" ? "【中度风险】" : "【低度风险】";
      lines.push(`| **风险等级** | ${riskLabel} |`);
    }
    lines.push("");

    lines.push("### 核心结论");
    lines.push("");
    lines.push(eng.conclusion || "（暂无结论）");
    lines.push("");

    const strengths = eng.strengths || [];
    if (strengths.length > 0) {
      lines.push("### 论文亮点");
      lines.push("");
      strengths.forEach((s) => {
        const strengthText = reportText(s);
        if (strengthText) lines.push(`- ${strengthText}`);
      });
      lines.push("");
    }

    const issues = eng.issues || [];
    if (issues.length > 0) {
      lines.push("### 风险与问题");
      lines.push("");
      issues.forEach((issue, i) => {
        const sevLabel =
          issue.severity === "high" ? "**风险等级**：【高度风险】" :
          issue.severity === "medium" ? "**风险等级**：【中度风险】" : "**风险等级**：【低度风险】";
        lines.push(`#### 问题 ${i + 1}：${sevLabel}`);
        lines.push("");
        if (issue.issue_type) {
          const translatedType = translateIssueType(issue.issue_type);
          lines.push(`- **问题类型**：${translatedType}`);
        }
        if (issue.evidence) {
          const evidenceText = String(issue.evidence).replace(/\n+/g, " ").trim();
          lines.push(`- **证据**：`);
          lines.push(`  > ${evidenceText}`);
        }
        if (issue.suggestion) {
          const suggestionText = String(issue.suggestion).replace(/\n+/g, " ").trim();
          lines.push(`- **建议**：`);
          lines.push(`  > ${suggestionText}`);
        }
        lines.push("");
      });
    }

    if (eng.reasoningMd) {
      lines.push("### 审查推理过程");
      lines.push("");
      lines.push(eng.reasoningMd);
      lines.push("");
    }

    const limitations = eng.limitations || [];
    if (limitations.length > 0) {
      lines.push("### 评估局限性");
      lines.push("");
      limitations.forEach((lim) => {
        const limitationText = reportText(lim);
        if (limitationText) lines.push(`- ${limitationText}`);
      });
      lines.push("");
    }

    if (strengths.length === 0 && issues.length === 0 && !eng.reasoningMd) {
      lines.push("### 评价依据（Evidence）");
      lines.push("");
      const ev = eng.evidence;
      if (Array.isArray(ev)) {
        ev.forEach((e) => {
          if (typeof e === "string") lines.push(e);
          else if (e && typeof e === "object") lines.push(e.evidence || e.aspect || String(e));
        });
      } else {
        lines.push(String(ev || "（暂无评价依据）"));
      }
      lines.push("");
      lines.push("### 修改建议（Actionable Advice）");
      lines.push("");
      const adv = eng.advice;
      if (Array.isArray(adv)) {
        adv.forEach((a) => lines.push(`- ${typeof a === "string" ? a : String(a)}`));
      } else {
        lines.push(adv || "（暂无修改建议）");
      }
      lines.push("");
    }

    lines.push("---");
    lines.push("");

    // v5: 新增业务字段
    if (eng.biasExplanation) {
      lines.push("### 偏差解释");
      lines.push("");
      const b = eng.biasExplanation;
      if (b.detected_biases && Array.isArray(b.detected_biases)) {
        lines.push(`- **检测到的偏差**：${b.detected_biases.map(x => ISSUE_TYPE_MAP[x] || x).join("、")}`);
      }
      if (b.bias_impact_assessment) {
        lines.push(`- **影响评估**：${b.bias_impact_assessment}`);
      }
      if (b.debiasing_recommendations) {
        lines.push(`- **纠正建议**：${b.debiasing_recommendations}`);
      }
      lines.push("");
    }

    if (eng.missingLiterature && eng.missingLiterature.length > 0) {
      lines.push("### 缺失文献清单");
      lines.push("");
      eng.missingLiterature.slice(0, 5).forEach((lit, i) => {
        const title = lit.title || lit.name || `文献 ${i + 1}`;
        lines.push(`- **${title}**`);
        if (lit.relevance) lines.push(`  - 相关性：${lit.relevance}`);
        if (lit.why_missing_is_problematic) lines.push(`  - 缺失影响：${lit.why_missing_is_problematic}`);
      });
      lines.push("");
    }

    if (eng.alternativeTheories && eng.alternativeTheories.length > 0) {
      lines.push("### 备选理论与方法");
      lines.push("");
      eng.alternativeTheories.slice(0, 4).forEach((alt, i) => {
        const name = alt.name || alt.theory || `方案 ${i + 1}`;
        lines.push(`- **${name}**`);
        if (alt.applicability) lines.push(`  - 适用性：${alt.applicability}`);
        if (alt.potential_insight) lines.push(`  - 潜在增量：${alt.potential_insight}`);
      });
      lines.push("");
    }

    if (eng.logicCorrectionPlan) {
      lines.push("### 逻辑整改方案");
      lines.push("");
      const l = eng.logicCorrectionPlan;
      if (l.identified_gaps && Array.isArray(l.identified_gaps)) {
        lines.push(`- **识别到的逻辑断层**：${l.identified_gaps.join("；")}`);
      }
      if (l.correction_strategy) {
        lines.push(`- **修正策略**：${l.correction_strategy}`);
      }
      if (l.revised_argument_flow) {
        lines.push(`- **修正后论证流程**：${l.revised_argument_flow}`);
      }
      lines.push("");
    }

    if (eng.impactEvidence) {
      lines.push("### 影响力证据");
      lines.push("");
      const ie = eng.impactEvidence;
      if (ie.audience) lines.push(`- **目标读者**：${ie.audience}`);
      if (ie.potential_journals) {
        const journals = Array.isArray(ie.potential_journals) ? ie.potential_journals.join("、") : String(ie.potential_journals);
        lines.push(`- **候选期刊**：${journals}`);
      }
      if (ie.impact_pathway) lines.push(`- **影响力路径**：${ie.impact_pathway}`);
      lines.push("");
    }

    lines.push("---");
    lines.push("");
  });

  if (!forPdf) lines.push(`*由 AI 学术审查系统 v${APP_VERSION} 生成*`);
  return lines.join("\n");
}

// v5.1: Four review mode presets — three locked + one custom
const REVIEW_MODES = [
  {
    key: "social_sciences", labelZh: "社会科学与人文", labelEn: "Social Sciences & Humanities",
    reviewMode: "preset", domain: "social_sciences", locked: true,
    description: "系统预设权重，后端锁定。适用于社会学、心理学、教育学、公共管理及相关领域论文。",
    weights: { methodology: 25, logic: 20, ethics: 10, innovation: 25, academic_impact: 20 },
    policyId: "humanities_social_science_v1_1",
  },
  {
    key: "stem", labelZh: "理工与实验科学", labelEn: "STEM & Experimental Sciences",
    reviewMode: "preset", domain: "stem", locked: true,
    description: "系统预设权重，强调实验设计、数据可靠性与方法可复现性。",
    weights: { methodology: 30, logic: 25, ethics: 10, innovation: 20, academic_impact: 15 },
    policyId: "stem_experimental_science_v1_1",
  },
  {
    key: "medicine", labelZh: "医学与生命科学", labelEn: "Medicine & Life Sciences",
    reviewMode: "preset", domain: "medicine", locked: true,
    description: "系统预设权重，关注样本统计可靠性、伦理审批与生物安全风险。",
    weights: { methodology: 30, logic: 20, ethics: 20, innovation: 15, academic_impact: 15 },
    policyId: "medical_life_science_v1_1",
  },
  {
    key: "custom", labelZh: "自定义审查", labelEn: "Custom Review",
    reviewMode: "custom", domain: "custom", locked: false,
    description: "运维模式下可调节权重比例。自定义权重将用于本次综合评分。",
    defaultCustomWeights: { methodology: 25, logic: 20, ethics: 20, innovation: 20, academic_impact: 15 },
  },
];

// v5.1: convert frontend percentage weights to backend float format
function frontendWeightsToBackend(pctWeights) {
  const result = {};
  for (const [frontendKey, backendKey] of Object.entries(FRONTEND_TO_BACKEND_WEIGHT_KEY)) {
    result[backendKey] = (pctWeights[frontendKey] || 0) / 100;
  }
  return result;
}

// v5.1: policy ID → Chinese display name mapping
const POLICY_LABEL_MAP = {
  humanities_social_science_v1_1: "社会科学与人文锁定权重",
  stem_experimental_science_v1_1: "理工与实验科学锁定权重",
  medical_life_science_v1_1: "医学与生命科学锁定权重",
  custom_user_defined_v1_1: "用户自定义权重",
  humanities_locked_v1: "社会科学与人文锁定权重",
  science_engineering_legacy_v1: "理工与实验科学锁定权重",
};

const getPolicyDisplayName = (policy) => {
  if (!policy) return "";
  if (policy.policy_label) return policy.policy_label;
  if (policy.policyLabel) return policy.policyLabel;
  if (policy.policy && POLICY_LABEL_MAP[policy.policy]) return POLICY_LABEL_MAP[policy.policy];
  return policy.policy || "";
};

// v5.1: devMode 模拟期刊推荐数据
const MOCK_JOURNAL_RECOMMENDATIONS = [
  { name: "《计算机仿真》", level: "中文科技核心 / 工程仿真方向", match_score: 82,
    reason: "论文包含运动学建模、路径规划与仿真验证内容，主题与工程仿真类期刊较匹配。",
    required_improvements: ["补充与已有路径规划或仿真方法的对比实验", "增加误差分析与鲁棒性测试", "强化理论贡献的抽象表达"] },
  { name: "《系统仿真学报》", level: "中文核心 / 系统建模方向", match_score: 74,
    reason: "稿件涉及多刚体链运动学、碰撞检测与路径规划，可作为系统建模与仿真方向投稿候选。",
    required_improvements: ["增加真实或半真实数据验证", "补充复杂度分析", "明确与现有系统仿真研究的差异"] },
  { name: "《应用数学和力学》", level: "应用数学 / 力学建模方向", match_score: 68,
    reason: "论文包含螺线轨迹、弧长参数化与优化求解，但需要加强严格推导和理论泛化。",
    required_improvements: ["补充定理化表述", "给出更严格的边界条件说明", "增加与经典数值方法的比较"] }
];

function customWeightsTotal(pctWeights) {
  return ENGINE_KEYS.reduce((s, k) => s + (pctWeights[k] || 0), 0);
}

// v5: 前端 engine key → 后端 scoringPolicy.weights 键名映射
const FRONTEND_TO_BACKEND_WEIGHT_KEY = {
  methodology: "data_reliability",
  logic: "logical_rigor",
  ethics: "ethics_bias",
  innovation: "innovation",
  academic_impact: "academic_impact",
};

// v5: 将后端 scoringPolicy.weights (0.0-1.0 float) 转换为前端百分比
function backendWeightsToPercent(scoringPolicy) {
  if (!scoringPolicy?.weights || typeof scoringPolicy.weights !== "object") return null;
  const result = {};
  for (const [frontendKey, backendKey] of Object.entries(FRONTEND_TO_BACKEND_WEIGHT_KEY)) {
    const raw = scoringPolicy.weights[backendKey];
    result[frontendKey] = (raw != null && Number.isFinite(Number(raw)))
      ? Math.round(Number(raw) * 100)
      : null;
  }
  return result;
}

// v5: 安全对象提取 — 排除数组和 null
function asPlainObject(v) {
  return v && typeof v === "object" && !Array.isArray(v) ? v : null;
}
const DOMAIN_THEMES = [
  { tab: "bg-orange-500 border-orange-500", badge: "bg-orange-100 text-orange-700", card: "border-orange-200", bg: "bg-orange-50", accent: "orange", lightBg: "bg-orange-50", lightBorder: "border-orange-200", lightText: "text-orange-800" },
  { tab: "bg-blue-600 border-blue-600", badge: "bg-blue-100 text-blue-700", card: "border-blue-200", bg: "bg-blue-50", accent: "blue", lightBg: "bg-blue-50", lightBorder: "border-blue-200", lightText: "text-blue-800" },
  { tab: "bg-green-600 border-green-600", badge: "bg-green-100 text-green-700", card: "border-green-200", bg: "bg-green-50", accent: "green", lightBg: "bg-green-50", lightBorder: "border-green-200", lightText: "text-green-800" },
  // v5.1: custom review mode
  { tab: "bg-purple-600 border-purple-600", badge: "bg-purple-100 text-purple-700", card: "border-purple-200", bg: "bg-purple-50", accent: "purple", lightBg: "bg-purple-50", lightBorder: "border-purple-200", lightText: "text-purple-800" },
];

// ============================================================
// Academic Term Definitions for TermTooltip
// ============================================================
const ACADEMIC_TERMS = [
  {
    keys: ["置信度"],
    term: "置信度",
    technical: "模型对当前评估结果的确定程度，通常以概率或百分比表示，反映了模型在给出判断时的把握程度。",
    significance: "置信度低说明该建议存在争议，作者可保留个人判断；置信度高则强烈建议作者采纳修改。",
  },
  {
    keys: ["逻辑推演"],
    term: "逻辑推演",
    technical: "验证论文中前提与结论是否存在必然因果关系，通过形式逻辑或非形式逻辑方法检测论证链条中的推理漏洞、逻辑跳跃和谬误。",
    significance: "若推演失败，意味着论文存在「强行得出结论」的漏洞，极易被审稿人攻击。",
  },
  {
    keys: ["样本选择偏差"],
    term: "样本选择偏差",
    technical: "研究选取的样本不具备代表性，即样本特征与目标总体特征存在系统性差异，导致统计推断失真。常见类型包括自选择偏差、便利抽样偏差、幸存者偏差等。",
    significance: "会导致论文结论无法推广到更广泛群体，削弱了研究的普适价值（External Validity）。",
  },
  {
    keys: ["事实幻觉", "幻觉文献"],
    term: "事实幻觉",
    technical: "AI 检测到可能捏造的客观事实或虚构文献——即模型生成的内容在现实中不存在或与已知事实相矛盾，是大型语言模型常见的'幻觉'现象。",
    significance: "这是学术不端的红线，作者必须仔细核对引用的真实性，确保每条引用均可追溯至真实发表的文献。",
  },
  {
    keys: ["学术影响力评级"],
    term: "学术影响力评级",
    technical: "基于论文的理论创新性、方法严谨性、研究问题重要性等多维度，预测论文发表后在学术界的被引潜力和关注度。",
    significance: "代表论文理论贡献或实证价值的稀缺性，可作为投递期刊层级（如核心/普刊）的参考。",
  },
  {
    keys: ["无支撑断言", "Unsupported Claim"],
    term: "无支撑断言",
    technical: "提出了观点或结论但缺乏数据、文献引用或逻辑推理作为支撑，即论点处于'悬空'状态，无法被验证或复现。",
    significance: "这是同行评审中极易被拒稿的致命伤，需补充实证数据或引用文献来支撑相应观点。",
  },
  {
    keys: ["WEIRD 偏见", "WEIRD偏差", "WEIRD"],
    term: "WEIRD 偏见",
    technical: "过度依赖西方（Western）、受过教育（Educated）、工业化（Industrialized）、富裕（Rich）、民主社会（Democratic）的样本进行研究，忽视了全球85%以上人口的文化多样性。该概念由Henrich等人在2010年提出。",
    significance: "提示研究结论可能存在文化局限性，建议在论文局限性部分主动说明样本的文化适用范围。",
  },
  {
    keys: ["过度泛化", "Overgeneralization"],
    term: "过度泛化",
    technical: "基于有限的样本量、特定的实验条件或单一文化背景的数据，得出过于宽泛或普遍性的结论，超出了数据实际能支撑的范围。",
    significance: "容易被审稿人批评为'夸大研究贡献'，建议收窄结论范围，增加限定词（如'在…条件下'、'对于…群体'）。",
  },
  {
    keys: ["交叉验证", "Cross-Validation"],
    term: "交叉验证",
    technical: "通过多源数据或多种算法模型反复比对，以验证研究结论的稳定性和可靠性。常见方法包括K折交叉验证、留一法验证、多中心数据验证等。",
    significance: "增强研究结论的稳健性（Robustness），是高水平期刊常要求的实证标准。",
  },
  {
    keys: ["语义图谱"],
    term: "语义图谱",
    technical: "将文本中的概念、实体及其关系构建成网状结构，通过图论算法分析概念间的关联强度、聚类结构和中心节点。",
    significance: "帮助作者直观发现核心概念之间的关联是否紧密，是否存在逻辑断层或概念孤立。",
  },
];

// Pre-computed sorted term keys (longest first to avoid partial matches)
const ALL_TERM_KEYS = ACADEMIC_TERMS.flatMap((t) => t.keys)
  .sort((a, b) => b.length - a.length);

// ============================================================
// TermTooltip — renders a term with ? icon and hover explanation
// Uses createPortal to render the tooltip into document.body,
// bypassing any parent overflow: hidden/auto clipping.
// ============================================================
function TermTooltip({ termKey }) {
  const triggerRef = useRef(null);
  const [show, setShow] = useState(false);
  const [coords, setCoords] = useState({ x: 0, y: 0 });

  if (!termKey) return null;
  const info = ACADEMIC_TERMS.find(
    (t) => t.keys.includes(termKey) || t.term === termKey,
  );
  if (!info) return <span>{termKey}</span>;

  const updatePosition = () => {
    if (triggerRef.current) {
      const rect = triggerRef.current.getBoundingClientRect();
      const centerX = rect.left + rect.width / 2;
      setCoords({ x: centerX, y: rect.top });
    }
  };

  const handleMouseEnter = () => { updatePosition(); setShow(true); };
  const handleMouseLeave = () => setShow(false);

  const trigger = (
    <span
      ref={triggerRef}
      className="inline-block"
      onMouseEnter={handleMouseEnter}
      onMouseLeave={handleMouseLeave}
    >
      <span className="text-slate-700">{info.term}</span>
      <sup className="text-blue-500 font-bold cursor-help ml-0.5 text-[10px] select-none">?</sup>
    </span>
  );

  const tooltip = show && createPortal(
    <div
      className="fixed z-[9999] w-80 bg-slate-800 text-white text-xs rounded-lg px-3.5 py-3 shadow-2xl whitespace-normal text-left leading-relaxed"
      style={{ left: `${Math.max(8, coords.x - 160)}px`, top: `${coords.y - 12}px`, transform: "translateY(-100%)" }}
      onMouseEnter={() => setShow(true)}
      onMouseLeave={() => setShow(false)}
    >
      <p className="font-semibold text-blue-300 mb-1.5">【技术解释】</p>
      <p className="mb-2">{info.technical}</p>
      <p className="font-semibold text-emerald-300 mb-1.5">【对论文的意义】</p>
      <p className="text-slate-200">{info.significance}</p>
      <div
        className="absolute left-1/2 -translate-x-1/2"
        style={{ top: "100%" }}
      >
        <div className="border-[5px] border-transparent border-t-slate-800" />
      </div>
    </div>,
    document.body,
  );

  return <>{trigger}{tooltip}</>;
}

// ============================================================
// TextWithTerms — scans text for academic terms and inserts tooltips
// ============================================================
function TextWithTerms({ text }) {
  // Guard: null, undefined, or non-renderable
  if (text == null) return null;

  // Guard: array — join string elements, or extract from object elements
  if (Array.isArray(text)) {
    const rendered = text.map((item) => {
      if (typeof item === "string") return item;
      if (item && typeof item === "object") {
        // evidence_refs object shape: { aspect, evidence, evidence_refs }
        return item.evidence || item.aspect || String(item);
      }
      return String(item ?? "");
    }).filter(Boolean);
    if (rendered.length === 0) return null;
    return <>{rendered.join("\n\n")}</>;
  }

  // Guard: non-string — convert safely
  if (typeof text !== "string") {
    if (typeof text === "object") {
      return <>{text.evidence || text.aspect || String(text)}</>;
    }
    return <>{String(text)}</>;
  }

  // Build regex with escaped keys from the pre-computed module-level constant
  const escapedKeys = ALL_TERM_KEYS.map((k) =>
    k.replace(/[.*+?^${}()|[\]\\]/g, "\\$&"),
  );
  const regex = new RegExp(`(${escapedKeys.join("|")})`, "g");
  const parts = String(text).split(regex);

  if (parts.length <= 1) return <>{text}</>;

  return (
    <>
      {parts.map((part, i) => {
        // Find which term definition this part matches (check all definitions whose keys include this part)
        const match = ACADEMIC_TERMS.find((t) => t.keys.includes(part));
        if (match) {
          return <TermTooltip key={i} termKey={match.term} />;
        }
        return <span key={i}>{part}</span>;
      })}
    </>
  );
}

// ============================================================
// AppealModal
// ============================================================
function AppealModal({ engineName, isOpen, onClose, t }) {
  const [reason, setReason] = useState("");
  const [submitted, setSubmitted] = useState(false);

  if (!isOpen) return null;

  const handleSubmit = () => {
    setSubmitted(true);
    setTimeout(() => { setSubmitted(false); setReason(""); onClose(); }, 1500);
  };

  return (
    <div className="fixed inset-0 z-[150] flex items-center justify-center bg-black/40 backdrop-blur-sm">
      <div className="bg-white rounded-2xl shadow-xl w-full max-w-md mx-4 p-6">
        <h3 className="text-lg font-semibold text-slate-800 mb-1">
          {t?.appealTitle?.replace("{engine}", engineName) ?? `对 ${engineName} 的审查结果有异议？`}
        </h3>
        <p className="text-xs text-slate-500 mb-4">{t?.appealHint ?? "请描述您的申诉理由，我们将转交人工专家复核。"}</p>
        {submitted ? (
          <div className="flex items-center gap-2 bg-emerald-50 border border-emerald-200 rounded-xl px-4 py-3 text-emerald-700 text-sm">
            <CheckCircle size={18} />
            {t?.appealSuccess ?? "申诉已记录，将转交人工专家复核。"}
          </div>
        ) : (
          <>
            <textarea
              value={reason}
              onChange={(e) => setReason(e.target.value)}
              rows={4}
              className="w-full border border-slate-200 rounded-lg p-3 text-sm focus:outline-none focus:border-blue-400 resize-none"
              placeholder={t?.appealPlaceholder ?? "请说明您认为大模型误判的具体原因..."}
            />
            <div className="flex justify-end gap-3 mt-4">
              <button onClick={onClose} className="px-4 py-2 text-sm text-slate-500 hover:text-slate-700 transition-colors">
                {t?.cancel ?? "取消"}
              </button>
              <button
                onClick={handleSubmit}
                disabled={!reason.trim()}
                className="px-5 py-2 text-sm bg-blue-600 text-white font-medium rounded-lg hover:bg-blue-700 disabled:opacity-40 disabled:cursor-not-allowed transition-colors"
              >
                {t?.submitAppeal ?? "提交复核"}
              </button>
            </div>
          </>
        )}
      </div>
    </div>
  );
}

// ============================================================
// FeedbackModal — calls POST /api/feedback, unlocks premium entitlements
// ============================================================
function FeedbackModal({ isOpen, onSkip, onSubmit, lang, reportId }) {
  const [feedbackText, setFeedbackText] = useState("");
  const [rating, setRating] = useState(0);
  const [submitted, setSubmitted] = useState(false);
  const [submitting, setSubmitting] = useState(false);
  const [error, setError] = useState(null);

  if (!isOpen) return null;

  const handleSubmit = async () => {
    setSubmitting(true);
    setError(null);
    try {
      const body = {
        rating: rating || null,
        category: "suggestion",
        content: feedbackText.trim() || (lang === "zh" ? "（用户未填写具体反馈内容）" : "(No specific feedback provided)"),
      };
      if (reportId) {
        body.report_id = reportId;
      }
      const res = await fetch(`${API_BASE}/api/feedback`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(body),
      });
      if (!res.ok) {
        const errData = await res.json().catch(() => ({}));
        throw new Error(errData.detail || `HTTP ${res.status}`);
      }
      const data = await res.json();
      setSubmitted(true);
      setTimeout(() => {
        setSubmitted(false);
        setFeedbackText("");
        setRating(0);
        onSubmit(data.entitlementToken, data.unlocked);
      }, 1500);
    } catch (err) {
      setError(String(err.message ?? err));
    } finally {
      setSubmitting(false);
    }
  };

  const handleSkip = () => { onSkip(); };

  return (
    <div className="fixed inset-0 z-[160] flex items-center justify-center bg-black/40 backdrop-blur-sm" onClick={handleSkip}>
      <div className="bg-white rounded-2xl shadow-xl w-full max-w-lg mx-4 p-6" onClick={(e) => e.stopPropagation()}>
        {!submitted ? (
          <>
            <div className="flex items-center gap-2 mb-3">
              <FileText size={20} className="text-blue-500" />
              <h3 className="text-lg font-semibold text-slate-800">
                {lang === "zh" ? "意见反馈" : "Feedback"}
              </h3>
            </div>
            <p className="text-sm text-slate-500 mb-2">
              {lang === "zh"
                ? "感谢您使用 AI 学术审查系统！您的反馈将帮助我们改进审查质量。"
                : "Thank you for using the AI Academic Review System! Your feedback helps us improve."}
            </p>
            <div className="bg-blue-50 border border-blue-200 rounded-lg px-3.5 py-2.5 mb-4 text-xs text-blue-700 leading-relaxed">
              {lang === "zh"
                ? "当前报告默认导出带有普通版水印。提交产品建议后，可为这份报告解锁【无水印高级版】及深度诊断特权。"
                : "Submit your suggestions to unlock the [Watermark-Free Premium] report and deep-diagnosis privileges for free!"}
            </div>
            {/* Rating */}
            <div className="mb-3">
              <p className="text-xs text-slate-500 mb-2">{lang === "zh" ? "评分（可选）" : "Rating (optional)"}</p>
              <div className="flex gap-1.5">
                {[1, 2, 3, 4, 5].map((v) => (
                  <button
                    key={v}
                    type="button"
                    onClick={() => setRating(v === rating ? 0 : v)}
                    className={`w-9 h-9 rounded-lg text-sm font-medium transition-colors ${
                      v <= rating ? "bg-amber-400 text-white" : "bg-slate-100 text-slate-400 hover:bg-slate-200"
                    }`}
                  >
                    {v}
                  </button>
                ))}
              </div>
            </div>
            <textarea
              value={feedbackText}
              onChange={(e) => setFeedbackText(e.target.value)}
              rows={4}
              className="w-full border border-slate-200 rounded-lg p-3 text-sm focus:outline-none focus:border-blue-400 resize-none"
              placeholder={lang === "zh"
                ? "请分享您的使用体验、建议或遇到的问题..."
                : "Share your experience, suggestions, or issues..."}
            />
            {error && (
              <div className="bg-red-50 border border-red-200 rounded-lg p-2.5 mt-3 text-xs text-red-700">
                {error}
              </div>
            )}
            <div className="flex justify-end gap-3 mt-4">
              <button
                onClick={handleSkip}
                className="px-4 py-2 text-sm text-slate-500 hover:text-slate-700 transition-colors"
              >
                {lang === "zh" ? "继续导出带水印版" : "Continue with watermark"}
              </button>
              <button
                onClick={handleSubmit}
                disabled={submitting}
                className="px-5 py-2 text-sm bg-blue-600 text-white font-medium rounded-lg hover:bg-blue-700 disabled:opacity-50 disabled:cursor-not-allowed transition-colors"
              >
                {submitting
                  ? (lang === "zh" ? "提交中..." : "Submitting...")
                  : (lang === "zh" ? "提交反馈并解锁无水印版" : "Submit & Unlock Premium")}
              </button>
            </div>
          </>
        ) : (
          <div className="text-center py-4">
            <div className="flex items-center justify-center gap-2 bg-emerald-50 border border-emerald-200 rounded-xl px-4 py-3 text-emerald-700 text-sm">
              <CheckCircle size={18} />
              {lang === "zh" ? "反馈已提交，感谢您的支持！" : "Feedback submitted — thank you!"}
            </div>
          </div>
        )}
      </div>
    </div>
  );
}

// ============================================================
// HistoryModal — list past reviews, click to reload
// ============================================================
function HistoryModal({ isOpen, onClose, t, lang, onSelect }) {
  const [list, setList] = useState([]);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState(null);

  useEffect(() => {
    if (!isOpen) return;
    setLoading(true);
    setError(null);
    fetch(`${API_BASE}/api/reports`)
      .then((r) => { if (!r.ok) throw new Error(`HTTP ${r.status}`); return r.json(); })
      .then((data) => { setList(Array.isArray(data) ? data : []); setLoading(false); })
      .catch((e) => { setError(String(e)); setLoading(false); });
  }, [isOpen]);

  if (!isOpen) return null;

  const formatDate = (isoStr) => {
    if (!isoStr) return "";
    try {
      const d = new Date(isoStr);
      return d.toLocaleString(lang === "zh" ? "zh-CN" : "en-US", {
        month: "short", day: "numeric", hour: "2-digit", minute: "2-digit",
      });
    } catch { return isoStr; }
  };

  return (
    <div className="fixed inset-0 z-[150] flex items-center justify-center bg-black/40 backdrop-blur-sm" onClick={onClose}>
      <div className="bg-white rounded-2xl shadow-xl w-full max-w-lg mx-4 max-h-[75vh] flex flex-col" onClick={(e) => e.stopPropagation()}>
        <div className="flex items-center justify-between p-5 border-b border-slate-100">
          <h3 className="text-lg font-semibold text-slate-800">
            {lang === "zh" ? "历史审查记录" : "Review History"}
          </h3>
          <button onClick={onClose} className="p-1 rounded-lg hover:bg-slate-100 text-slate-400">
            <X size={18} />
          </button>
        </div>

        <div className="flex-1 overflow-y-auto p-5">
          {loading && (
            <div className="text-center text-sm text-slate-400 py-8">
              <div className="animate-spin inline-block w-5 h-5 border-2 border-slate-200 border-t-blue-500 rounded-full mb-2" />
              <p>{lang === "zh" ? "加载中..." : "Loading..."}</p>
            </div>
          )}

          {error && (
            <div className="bg-red-50 border border-red-200 rounded-lg p-3 text-sm text-red-700">
              {error}
            </div>
          )}

          {!loading && !error && list.length === 0 && (
            <p className="text-sm text-slate-400 text-center py-8">
              {lang === "zh" ? "暂无审查记录" : "No review history"}
            </p>
          )}

          {!loading && !error && list.map((item) => (
            <button
              key={item.id}
              onClick={() => onSelect(item.id)}
              className="w-full text-left px-4 py-3 rounded-xl hover:bg-slate-50 border border-slate-100 mb-2 transition-colors group"
            >
              <div className="flex items-center justify-between">
                <span className="text-sm font-medium text-slate-700 truncate max-w-[300px]">
                  {item.filename || (lang === "zh" ? "未命名" : "Untitled")}
                </span>
                <ChevronRight size={14} className="text-slate-300 group-hover:text-blue-500 transition-colors" />
              </div>
              <div className="flex items-center gap-3 mt-1">
                <span className="text-[10px] text-slate-400">{formatDate(item.created_at)}</span>
                <span className="text-[10px] bg-slate-100 text-slate-500 px-1.5 py-0.5 rounded">{item.subject ?? ""}</span>
              </div>
            </button>
          ))}
        </div>
      </div>
    </div>
  );
}

// ============================================================
// SettingsModal — API key + feedback link
// ============================================================
function SettingsModal({ isOpen, onClose, t, lang, apiKey, baseUrl, modelName, onSave, onApiKeyChange, onBaseUrlChange, onModelNameChange, onClearCache }) {
  if (!isOpen) return null;

  return (
    <div className="fixed inset-0 z-[200] flex items-center justify-center bg-black/40 backdrop-blur-sm" onClick={onClose}>
      <div className="bg-white rounded-2xl shadow-xl w-full max-w-md mx-4 p-6" onClick={(e) => e.stopPropagation()}>
        <div className="flex items-center justify-between mb-5">
          <h3 className="text-lg font-semibold text-slate-800">
            {lang === "zh" ? "系统设置" : "Settings"}
          </h3>
          <button onClick={onClose} className="p-1 rounded-lg hover:bg-slate-100 text-slate-400">
            <X size={18} />
          </button>
        </div>

        <div className="space-y-4">
          {/* API Key */}
          <div>
            <label className="block text-xs font-semibold text-slate-500 uppercase mb-1">
              {t.configApiKey}
            </label>
            <input
              type="password"
              value={apiKey}
              onChange={(e) => onApiKeyChange(e.target.value)}
              placeholder="sk-..."
              className="w-full px-3 py-2 border border-slate-200 rounded-lg text-sm focus:outline-none focus:border-blue-400"
            />
            <p className="text-[10px] text-slate-400 mt-1">
              {lang === "zh" ? "修改后将应用于后续的审查任务" : "Changes apply to future review tasks"}
            </p>
            <p className="text-[10px] text-slate-400 mt-1 leading-relaxed">
              {lang === "zh"
                ? "支持任何 OpenAI 兼容 API。若使用网络代理，请确保当前 API 地址能够稳定访问。"
                : "Any OpenAI-compatible API is supported. If a proxy is enabled, make sure the configured endpoint remains reachable."}
            </p>
          </div>

          {/* Base URL */}
          <div>
            <label className="block text-xs font-semibold text-slate-500 uppercase mb-1">
              {t.configBaseUrl}
            </label>
            <input
              type="text"
              value={baseUrl}
              onChange={(e) => onBaseUrlChange(e.target.value)}
              placeholder={DEFAULT_QWEN_BASE_URL}
              className="w-full px-3 py-2 border border-slate-200 rounded-lg text-sm focus:outline-none focus:border-blue-400"
            />
          </div>

          {/* Model Name */}
          <div>
            <label className="block text-xs font-semibold text-slate-500 uppercase mb-1">
              {t.configModel}
            </label>
            <input
              type="text"
              value={modelName}
              onChange={(e) => onModelNameChange(e.target.value)}
              placeholder={DEFAULT_QWEN_MODEL}
              className="w-full px-3 py-2 border border-slate-200 rounded-lg text-sm focus:outline-none focus:border-blue-400"
            />
          </div>

          {/* Cache management */}
          <div className="border-t border-slate-100 pt-4">
            <button
              onClick={onClearCache}
              className="inline-flex items-center gap-1.5 text-sm text-red-500 hover:text-red-600 font-medium transition-colors"
            >
              {lang === "zh" ? "清除论文审查缓存" : "Clear Review Cache"}
              <RefreshCw size={13} />
            </button>
            <p className="text-[10px] text-slate-400 mt-1">
              {lang === "zh" ? "清除后将重新调用大模型进行审查（而非复用缓存结果）" : "Will re-invoke LLM for review instead of reusing cached results"}
            </p>
          </div>

          {/* Developer tools */}
          <div className="border-t border-slate-100 pt-4">
            <button
              onClick={() => window.open("/dev/logs", "_blank", "noopener,noreferrer")}
              className="inline-flex items-center gap-1.5 text-sm text-slate-600 hover:text-slate-800 font-medium transition-colors"
            >
              {lang === "zh" ? "开发者日志面板" : "Developer Log Panel"}
              <svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round"><path d="M18 13v6a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2V8a2 2 0 0 1 2-2h6"/><polyline points="15 3 21 3 21 9"/><line x1="10" y1="14" x2="21" y2="3"/></svg>
            </button>
            <p className="text-[10px] text-slate-400 mt-1">
              {lang === "zh" ? "在新标签页中打开后端实时运行日志监控面板" : "Open backend real-time log monitor in a new tab"}
            </p>
          </div>

          {/* Feedback link */}
          <div className="border-t border-slate-100 pt-4">
            <a
              href="https://github.com/orgs/AI学术审查系统-team/repositories"
              target="_blank"
              rel="noopener noreferrer"
              className="inline-flex items-center gap-1.5 text-sm text-blue-600 hover:text-blue-700 font-medium transition-colors"
            >
              {lang === "zh" ? "提交建议与反馈" : "Submit Feedback"}
              <svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round"><path d="M18 13v6a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2V8a2 2 0 0 1 2-2h6"/><polyline points="15 3 21 3 21 9"/><line x1="10" y1="14" x2="21" y2="3"/></svg>
            </a>
          </div>
        </div>

        <div className="flex justify-end gap-3 mt-6 pt-4 border-t border-slate-100">
          <button
            onClick={onClose}
            className="px-4 py-2 text-sm text-slate-500 hover:text-slate-700 transition-colors"
          >
            {t.cancel}
          </button>
          <button
            onClick={() => { if (onSave() !== false) onClose(); }}
            disabled={!apiKey.trim() || !baseUrl.trim() || !modelName.trim()}
            className="px-5 py-2 text-sm bg-blue-600 text-white font-medium rounded-lg hover:bg-blue-700 disabled:opacity-40 disabled:cursor-not-allowed transition-colors"
          >
            {lang === "zh" ? "保存配置" : "Save Config"}
          </button>
        </div>
      </div>
    </div>
  );
}

// ============================================================
// TiltCard
// ============================================================
function TiltCard({ children, tiltFactor = 1.5, className = "" }) {
  const ref = useRef(null);
  const [transform, setTransform] = useState("");

  const handleMove = (e) => {
    const el = ref.current;
    if (!el) return;
    const rect = el.getBoundingClientRect();
    const x = e.clientX - rect.left;
    const y = e.clientY - rect.top;
    const cx = rect.width / 2;
    const cy = rect.height / 2;
    const rx = ((y - cy) / cy) * -tiltFactor;
    const ry = ((x - cx) / cx) * tiltFactor;
    setTransform(`perspective(1000px) rotateX(${rx.toFixed(2)}deg) rotateY(${ry.toFixed(2)}deg)`);
  };

  const handleLeave = () => {
    setTransform("perspective(1000px) rotateX(0deg) rotateY(0deg)");
  };

  return (
    <div
      ref={ref}
      className={`tilt-card ${className}`}
      style={{ transform }}
      onMouseMove={handleMove}
      onMouseLeave={handleLeave}
    >
      {children}
    </div>
  );
}

// ============================================================
// Metric
// ============================================================
function Metric({ label, value, delta }) {
  return (
    <div className="bg-slate-50 border border-slate-100 rounded-lg p-3 hover:shadow-sm hover:-translate-y-0.5 transition-all duration-200">
      <p className="text-[10px] text-slate-400 uppercase tracking-wider">{label ?? ""}</p>
      <p className="text-base font-bold text-slate-800 mt-0.5">{value ?? ""}</p>
      {delta && <p className="text-[10px] text-blue-600 mt-0.5">{delta}</p>}
    </div>
  );
}

// ============================================================
// SafeRadarChart — pure SVG, no external chart library
// ============================================================
function SafeRadarChart({ data = [] }) {
  const size = 360;
  const center = size / 2;
  const radius = 125;
  const levels = [20, 40, 60, 80, 100];

  const safeData = Array.isArray(data) && data.length
    ? data.slice(0, 5)
    : [
        { subject: "Methodology", score: 0 },
        { subject: "Logic", score: 0 },
        { subject: "Ethics", score: 0 },
        { subject: "Innovation", score: 0 },
        { subject: "Impact", score: 0 },
      ];

  const points = safeData.map((item, index) => {
    const angle = -Math.PI / 2 + index * ((Math.PI * 2) / safeData.length);
    const rawScore = Number(item?.score);
    const score = Number.isFinite(rawScore) ? Math.max(0, Math.min(100, rawScore)) : 0;
    const r = radius * (score / 100);

    return {
      x: center + Math.cos(angle) * r,
      y: center + Math.sin(angle) * r,
      labelX: center + Math.cos(angle) * (radius + 34),
      labelY: center + Math.sin(angle) * (radius + 34),
      subject: String(item?.subject ?? ""),
    };
  });

  const polygonPoints = points.map((p) => `${p.x},${p.y}`).join(" ");

  const axisPoints = safeData.map((_, index) => {
    const angle = -Math.PI / 2 + index * ((Math.PI * 2) / safeData.length);
    return {
      x: center + Math.cos(angle) * radius,
      y: center + Math.sin(angle) * radius,
    };
  });

  return (
    <div id="radar-chart-container" className="w-full flex justify-center items-center avoid-page-break" style={{ height: "380px" }}>
      <svg width="100%" height="100%" viewBox={`0 0 ${size} ${size}`} role="img">
        {levels.map((level) => {
          const r = radius * (level / 100);
          const gridPoints = safeData.map((_, index) => {
            const angle = -Math.PI / 2 + index * ((Math.PI * 2) / safeData.length);
            return `${center + Math.cos(angle) * r},${center + Math.sin(angle) * r}`;
          }).join(" ");

          return (
            <polygon
              key={level}
              points={gridPoints}
              fill="none"
              stroke="#e2e8f0"
              strokeWidth="1"
            />
          );
        })}

        {axisPoints.map((p, index) => (
          <line
            key={index}
            x1={center}
            y1={center}
            x2={p.x}
            y2={p.y}
            stroke="#e2e8f0"
            strokeWidth="1"
          />
        ))}

        <polygon
          points={polygonPoints}
          fill="#2563EB"
          fillOpacity="0.18"
          stroke="#2563EB"
          strokeWidth="2.5"
        />

        {points.map((p, index) => (
          <circle
            key={index}
            cx={p.x}
            cy={p.y}
            r="4"
            fill="#2563EB"
          />
        ))}

        {points.map((p, index) => (
          <text
            key={index}
            x={p.labelX}
            y={p.labelY}
            textAnchor="middle"
            dominantBaseline="middle"
            fontSize="12"
            fill="#475569"
          >
            {p.subject}
          </text>
        ))}
      </svg>
    </div>
  );
}

// ============================================================
// normalizeBBox — converts various bbox formats to pixel coordinates
// ============================================================
function normalizeBBox(bboxNorm, renderedWidth, renderedHeight) {
  if (!Array.isArray(bboxNorm) || bboxNorm.length !== 4) return null;
  let [x0, y0, x1, y1] = bboxNorm.map(Number);
  if ([x0, y0, x1, y1].some((value) => !Number.isFinite(value))) return null;

  // 后端明确约定 bbox_norm 使用左上角原点的 0-1000 坐标。
  x0 = Math.max(0, Math.min(1000, x0));
  y0 = Math.max(0, Math.min(1000, y0));
  x1 = Math.max(x0, Math.min(1000, x1));
  y1 = Math.max(y0, Math.min(1000, y1));

  const left = (x0 / 1000) * renderedWidth;
  const top = (y0 / 1000) * renderedHeight;
  const width = Math.max(20, ((x1 - x0) / 1000) * renderedWidth);
  const height = Math.max(12, ((y1 - y0) / 1000) * renderedHeight);
  return {
    left,
    top,
    width: Math.min(width, renderedWidth - left),
    height: Math.min(height, renderedHeight - top),
  };
}


// ============================================================
// PdfEvidencePanel — right-side PDF preview with PDF.js canvas rendering & highlight
// ============================================================
function PdfEvidencePanel({ evidenceRef, pdfBlobUrl, viewerKey, onClose, lang }) {
  const page = evidenceRef?.page;
  const coordinates = evidenceRef?.coordinates || {};
  const bboxNorm = evidenceRef?.bbox_norm || coordinates?.bbox_norm || null;
  const bboxRaw = bboxNorm || evidenceRef?.bbox || coordinates?.bbox || null;
  const isValidBbox = bboxRaw && (Array.isArray(bboxRaw) || typeof bboxRaw === "object");

  const canvasRef = useRef(null);
  const containerRef = useRef(null);
  const [pdfDoc, setPdfDoc] = useState(null);
  const [renderError, setRenderError] = useState(null);
  const [canvasDims, setCanvasDims] = useState({ width: 0, height: 0 });
  const [highlightRect, setHighlightRect] = useState(null);

  // Load PDF document
  useEffect(() => {
    if (!pdfBlobUrl) return;
    let cancelled = false;
    setRenderError(null);
    let loadedDoc = null;
    import("pdfjs-dist").then((pdfjs) => {
      pdfjs.GlobalWorkerOptions.workerSrc = pdfWorkerUrl;
      return pdfjs.getDocument({ url: pdfBlobUrl }).promise;
    }).then((doc) => {
      if (cancelled) return;
      loadedDoc = doc;
      setPdfDoc(doc);
    }).catch((err) => {
      if (cancelled) return;
      console.warn("[PdfEvidencePanel] PDF.js failed to load, falling back to iframe:", err.message);
      setRenderError("pdfjs_load_failed");
    });
    return () => {
      cancelled = true;
      loadedDoc?.destroy?.();
    };
  }, [pdfBlobUrl]);

  // Render specific page to canvas
  useEffect(() => {
    if (!pdfDoc || page == null) return;
    let cancelled = false;
    let renderTask = null;
    setHighlightRect(null);
    pdfDoc.getPage(page).then((pdfPage) => {
      if (cancelled) return;
      const canvas = canvasRef.current;
      if (!canvas) return;
      const ctx = canvas.getContext("2d");
      const containerWidth = containerRef.current?.clientWidth || 600;
      const viewport = pdfPage.getViewport({ scale: 1 });
      const scale = containerWidth / viewport.width;
      const scaledViewport = pdfPage.getViewport({ scale: Math.min(scale, 2.5) });
      const width = Math.floor(scaledViewport.width);
      const height = Math.floor(scaledViewport.height);
      canvas.width = width;
      canvas.height = height;
      setCanvasDims({ width, height });

      const renderContext = { canvasContext: ctx, viewport: scaledViewport };
      renderTask = pdfPage.render(renderContext);
      renderTask.promise.then(() => {
        if (cancelled) return;
        setHighlightRect(normalizeBBox(bboxNorm, width, height));
      }).catch(() => {});
    }).catch((err) => {
      console.warn("[PdfEvidencePanel] Page render failed:", err.message);
    });
    return () => {
      cancelled = true;
      renderTask?.cancel?.();
    };
  }, [pdfDoc, page, viewerKey, bboxNorm]);

  // Scroll to highlight after render
  useEffect(() => {
    if (highlightRect && containerRef.current) {
      containerRef.current.scrollTo({
        top: Math.max(0, highlightRect.top - 100),
        behavior: "smooth",
      });
    }
  }, [highlightRect]);

  if (!evidenceRef) return null;

  // Fallback: iframe if PDF.js failed
  if (renderError === "pdfjs_load_failed") {
    return (
      <div className="pdf-panel" style={{
        width: "40%", minWidth: "420px", height: "100vh",
        borderLeft: "2px solid #d1d5db", backgroundColor: "#fff",
        display: "flex", flexDirection: "column", overflow: "hidden",
        boxShadow: "-8px 0 20px rgba(0,0,0,0.06)",
      }}>
        <div className="flex items-center justify-between px-4 py-3 border-b border-slate-200 shrink-0 bg-slate-50">
          <h3 className="text-sm font-semibold text-slate-800">
            {lang === "zh" ? "PDF 原文定位 (降级模式)" : "PDF Source (Fallback)"}
          </h3>
          <button onClick={onClose} className="p-1.5 rounded-lg hover:bg-slate-200 text-slate-500 hover:text-slate-700 transition-colors">
            <X size={16} />
          </button>
        </div>
        {/* Evidence metadata */}
        <div className="shrink-0 px-4 py-3 bg-amber-50 border-b border-amber-100">
          <p className="text-xs text-amber-700">
            {lang === "zh"
              ? "PDF.js 无法加载，已降级为原生 PDF 预览。坐标高亮不可用，请参考下方坐标手动定位。"
              : "PDF.js unavailable. Coordinate highlights are disabled. Use the coordinates below to locate evidence manually."}
          </p>
        </div>
        <div className="shrink-0 px-4 py-2 bg-slate-50 border-b border-slate-100 text-xs">
          <span className="text-slate-400">{lang === "zh" ? "页码" : "Page"}: </span>
          <span className="font-medium text-slate-700">{page ?? "-"}</span>
          {isValidBbox && (
            <span className="ml-4 text-slate-400">{lang === "zh" ? "坐标" : "BBox"}: <span className="font-mono text-slate-600">[{Array.isArray(bboxRaw) ? bboxRaw.join(", ") : JSON.stringify(bboxRaw)}]</span></span>
          )}
        </div>
        {pdfBlobUrl && page != null ? (
          <div className="flex-1" style={{ minHeight: 0 }}>
            <iframe key={`${viewerKey}-${page}`} src={`${pdfBlobUrl}#page=${page}`} width="100%" height="100%" style={{ border: "none" }} title="PDF" />
          </div>
        ) : (
          <div className="flex-1 flex items-center justify-center bg-slate-50">
            <p className="text-sm text-slate-400">{lang === "zh" ? "PDF 不可用" : "PDF unavailable"}</p>
          </div>
        )}
      </div>
    );
  }

  return (
    <div className="pdf-panel" style={{
      width: "40%", minWidth: "420px", height: "100vh",
      borderLeft: "2px solid #d1d5db", backgroundColor: "#fff",
      display: "flex", flexDirection: "column", overflow: "hidden",
      boxShadow: "-8px 0 20px rgba(0,0,0,0.06)",
    }}>
      {/* Header bar */}
      <div className="flex items-center justify-between px-4 py-3 border-b border-slate-200 shrink-0 bg-slate-50">
        <h3 className="text-sm font-semibold text-slate-800">
          {lang === "zh" ? "PDF 原文定位" : "PDF Source Reference"}
        </h3>
        <button
          onClick={onClose}
          className="p-1.5 rounded-lg hover:bg-slate-200 text-slate-500 hover:text-slate-700 transition-colors"
          title={lang === "zh" ? "关闭原文预览" : "Close source preview"}
        >
          <X size={16} />
        </button>
      </div>

      {/* Evidence metadata */}
      <div className="shrink-0 px-4 py-3 bg-slate-50 border-b border-slate-100">
        <div className="grid grid-cols-2 gap-1.5 text-xs">
          <div>
            <span className="text-slate-400">{lang === "zh" ? "页码" : "Page"}: </span>
            <span className="font-medium text-slate-700">{page ?? "-"}</span>
          </div>
          <div>
            <span className="text-slate-400">{lang === "zh" ? "段落" : "Para"}: </span>
            <span className="font-medium text-slate-700">{evidenceRef?.paragraph ?? "-"}</span>
          </div>
          <div className="col-span-2">
            <span className="text-slate-400">{lang === "zh" ? "锚点" : "Anchor"}: </span>
            <span className="font-mono text-[10px] text-slate-600">{evidenceRef?.anchor_id ?? "-"}</span>
          </div>
          {isValidBbox && (
            <div className="col-span-2">
              <span className="text-slate-400">{lang === "zh" ? "坐标" : "BBox"}: </span>
              <span className="font-mono text-[10px] text-slate-600">
                [{Array.isArray(bboxRaw) ? bboxRaw.map((v) => (typeof v === "number" ? v.toFixed(1) : v)).join(", ") : JSON.stringify(bboxRaw)}]
              </span>
            </div>
          )}
          <div className="col-span-2">
            <span className="text-slate-400">{lang === "zh" ? "匹配" : "Match"}: </span>
            <span className="font-medium text-slate-700">{evidenceRef?.match_method ?? "-"}</span>
          </div>
        </div>
      </div>

      {/* PDF canvas with highlight overlay */}
      {pdfBlobUrl && page != null ? (
        <div ref={containerRef} className="flex-1 overflow-auto bg-gray-200 relative" style={{ minHeight: 0 }}>
          <div className="flex justify-center py-2" style={{ minHeight: "100%" }}>
            <div style={{ position: "relative", width: canvasDims.width || "100%", height: canvasDims.height || "auto" }}>
              <canvas
                ref={canvasRef}
                style={{ display: "block", width: canvasDims.width || "100%", height: canvasDims.height || "auto" }}
              />
              {highlightRect && (
                <div
                  aria-label={lang === "zh" ? "原文证据高亮" : "Evidence highlight"}
                  style={{
                    position: "absolute",
                    left: highlightRect.left,
                    top: highlightRect.top,
                    width: highlightRect.width,
                    height: highlightRect.height,
                    background: "rgba(255, 235, 59, 0.38)",
                    border: "3px solid rgba(245, 158, 11, 0.95)",
                    boxShadow: "0 0 0 2px rgba(255,255,255,0.7), 0 0 14px rgba(245,158,11,0.55)",
                    borderRadius: 3,
                    pointerEvents: "none",
                    boxSizing: "border-box",
                    zIndex: 2,
                  }}
                />
              )}
            </div>
          </div>
        </div>
      ) : (
        <div className="flex-1 flex items-center justify-center bg-slate-50">
          <p className="text-sm text-slate-400">
            {!pdfBlobUrl
              ? (lang === "zh" ? "请先上传 PDF 文件" : "Upload a PDF file first")
              : (lang === "zh" ? "正在加载 PDF..." : "Loading PDF...")}
          </p>
        </div>
      )}
    </div>
  );
}


// ============================================================
// ContentArea
// ============================================================
function ContentArea({ t, lang, phase, file, logs, retryCount, overallScore, biasLevel, radarData, engineMeta, engineScores, onStart, onReset, hasError, onDownload, onAppeal, domainTheme, isPremium, onRequestExport, feedbackClosedVersion, evaluationStatus, auditPassed, systemLimitations, engineResults, onEvidenceClick }) {
  const logEndRef = useRef(null);
  const [pdfExporting, setPdfExporting] = useState(false);
  const pendingActionRef = useRef(null);
  // Evidence mode is managed by parent; delegated via onEvidenceClick
  const handleViewOriginal = useCallback((location) => {
    if (onEvidenceClick && location) {
      onEvidenceClick(location);
    }
  }, [onEvidenceClick]);

  // Watch feedbackClosedVersion — when it increments, execute pending export
  const prevFeedbackVerRef = useRef(feedbackClosedVersion);
  useEffect(() => {
    if (feedbackClosedVersion > 0 && feedbackClosedVersion !== prevFeedbackVerRef.current) {
      prevFeedbackVerRef.current = feedbackClosedVersion;
      const action = pendingActionRef.current;
      pendingActionRef.current = null;
      if (action === "pdf") {
        setTimeout(() => handleDirectPDFExportRef.current?.(), 150);
      } else if (action === "markdown") {
        setTimeout(() => onDownload?.(), 150);
      }
    }
  }, [feedbackClosedVersion, onDownload]);

  const handleDirectPDFExportRef = useRef(null);

  // ---- Direct PDF export: polished cover + structured report -> html2pdf ----
  const handleDirectPDFExport = useCallback(async () => {
    if (pdfExporting) return;
    setPdfExporting(true);

    try {
      await new Promise((r) => setTimeout(r, 100));

      // 1. Capture radar chart as a sharp image for the cover.
      let chartHtml = "";
      const chartElement = document.getElementById("radar-chart-container");
      if (chartElement) {
        try {
          const canvas = await html2canvas(chartElement, { scale: 2, backgroundColor: "#ffffff", logging: false });
          const imgData = canvas.toDataURL("image/png");
          chartHtml = `<div class="cover-chart"><div class="cover-chart-title">五维审查雷达图</div><img src="${imgData}" alt="综合审查雷达图" /></div>`;
        } catch (e) {
          console.warn("[AI学术审查系统] Radar chart capture for PDF failed:", e);
        }
      }

      // 2. Build the detailed body without duplicating the cover summary.
      const md = generateMarkdownReport({
        engineMeta,
        engineScores,
        overallScore,
        biasLevel,
        retryCount,
        forPdf: true,
        scoringPolicy: engineResults?.scoringPolicy || null,
      });

      // 3. Convert Markdown to HTML
      const rawHtml = marked.parse(md);

      const paperTitle = engineResults?.paperTitle || file?.name || "未命名论文";
      const journal = engineResults?.paperJournal || "未指定期刊";
      const scoreValue = typeof overallScore === "number" ? overallScore.toFixed(1) : "-";
      const generatedAt = new Date().toLocaleString(lang === "zh" ? "zh-CN" : "en-US", {
        year: "numeric", month: "2-digit", day: "2-digit", hour: "2-digit", minute: "2-digit",
      });
      const issueCount = Object.values(engineMeta || {}).reduce(
        (sum, engine) => sum + (Array.isArray(engine?.issues) ? engine.issues.length : 0),
        0,
      );
      const scoreRows = ENGINE_KEYS.map((key) => {
        const meta = engineMeta[key] || {};
        const score = engineScores[key];
        const numericScore = Number.isFinite(Number(score)) ? Math.round(Number(score)) : 0;
        return `<div class="engine-score-row">
          <div class="engine-score-label">${escapeHtml(meta.name || key)}</div>
          <div class="engine-score-track"><span style="width:${Math.max(0, Math.min(100, numericScore))}%"></span></div>
          <div class="engine-score-value">${Number.isFinite(Number(score)) ? numericScore : "-"}</div>
        </div>`;
      }).join("");
      const editionText = isPremium ? "无水印高级版" : "普通水印版";
      const auditText = engineResults?.auditPassed === true ? "最终一致性复核通过" : "复核状态未知";
      const reportId = engineResults?.reportId ? String(engineResults.reportId).slice(0, 12) : "LOCAL";

      const watermarkCanvas = document.createElement("canvas");
      watermarkCanvas.width = 1200;
      watermarkCanvas.height = 320;
      const watermarkContext = watermarkCanvas.getContext("2d");
      watermarkContext.clearRect(0, 0, watermarkCanvas.width, watermarkCanvas.height);
      watermarkContext.translate(watermarkCanvas.width / 2, watermarkCanvas.height / 2);
      watermarkContext.rotate(-20 * Math.PI / 180);
      watermarkContext.font = "700 92px 'Microsoft YaHei','PingFang SC','Noto Sans CJK SC',sans-serif";
      watermarkContext.textAlign = "center";
      watermarkContext.textBaseline = "middle";
      watermarkContext.fillStyle = "rgba(148, 163, 184, 0.22)";
      watermarkContext.fillText("AI学术审查系统 · 普通版", 0, 0);
      const watermarkImage = watermarkCanvas.toDataURL("image/png");

      const headerCanvas = document.createElement("canvas");
      headerCanvas.width = 900;
      headerCanvas.height = 80;
      const headerContext = headerCanvas.getContext("2d");
      headerContext.clearRect(0, 0, headerCanvas.width, headerCanvas.height);
      headerContext.font = "600 34px 'Microsoft YaHei','PingFang SC','Noto Sans CJK SC',sans-serif";
      headerContext.textAlign = "left";
      headerContext.textBaseline = "middle";
      headerContext.fillStyle = "#64748b";
      headerContext.fillText("AI学术审查系统 · 审查报告", 0, headerCanvas.height / 2);
      const headerImage = headerCanvas.toDataURL("image/png");

      // 4. Compose a print-oriented report instead of printing the webpage directly.
      const styledHtml = `<div class="report-document">
  <style>
    * { box-sizing:border-box; }
    .report-document { font-family:'Microsoft YaHei','PingFang SC','Noto Sans CJK SC','Helvetica Neue',Arial,sans-serif; color:#243044; line-height:1.72; background:#fff; }
    .report-cover { min-height:245mm; padding:15mm 16mm 13mm; position:relative; overflow:hidden; page-break-after:always; background:linear-gradient(155deg,#f8fbff 0%,#fff 48%,#f1f5ff 100%); }
    .report-cover:before { content:''; position:absolute; left:0; top:0; width:100%; height:7mm; background:linear-gradient(90deg,#1d4ed8,#6366f1,#0ea5e9); }
    .cover-orb { position:absolute; width:92mm; height:92mm; right:-36mm; top:15mm; border-radius:50%; background:radial-gradient(circle,rgba(99,102,241,.16),rgba(59,130,246,0)); }
    .cover-brand { font-size:11px; font-weight:800; color:#2563eb; letter-spacing:2.4px; text-transform:uppercase; }
    .cover-version { float:right; padding:4px 10px; border:1px solid #c7d2fe; border-radius:999px; color:#4f46e5; font-size:9px; font-weight:700; background:#eef2ff; }
    .audit-chip { display:inline-block; margin-top:10mm; padding:5px 11px; border-radius:999px; background:#dcfce7; border:1px solid #86efac; color:#15803d; font-size:10px; font-weight:700; }
    .cover-title { margin:5mm 0 2mm; max-width:150mm; color:#12203a; font-size:25px; line-height:1.22; font-weight:800; letter-spacing:-.4px; }
    .cover-subtitle { color:#64748b; font-size:11px; margin-bottom:5mm; }
    .summary-grid { display:grid; grid-template-columns:repeat(3,1fr); gap:4mm; margin:4mm 0 5mm; }
    .summary-card { padding:4mm; min-height:19mm; border:1px solid #dbe4f0; border-radius:10px; background:rgba(255,255,255,.9); box-shadow:0 4px 14px rgba(37,99,235,.06); }
    .summary-label { color:#94a3b8; font-size:9px; font-weight:700; letter-spacing:.8px; }
    .summary-value { margin-top:1mm; color:#1e293b; font-size:17px; font-weight:800; }
    .summary-value small { font-size:10px; color:#64748b; font-weight:500; }
    .score-overview { padding:3.5mm 6mm; border-radius:10px; border:1px solid #dbeafe; background:rgba(239,246,255,.82); }
    .score-overview-title { margin-bottom:2mm; font-size:10px; font-weight:800; color:#1e40af; }
    .engine-score-row { display:grid; grid-template-columns:46mm 1fr 10mm; align-items:center; gap:3mm; margin:1.3mm 0; break-inside:avoid; }
    .engine-score-label { color:#475569; font-size:9.5px; overflow:hidden; white-space:nowrap; text-overflow:ellipsis; }
    .engine-score-track { height:3mm; overflow:hidden; border-radius:999px; background:#dbeafe; }
    .engine-score-track span { display:block; height:100%; border-radius:999px; background:linear-gradient(90deg,#2563eb,#6366f1); }
    .engine-score-value { text-align:right; color:#1e40af; font-size:10px; font-weight:800; }
    .cover-chart { margin-top:4mm; padding:2.5mm 4mm; border:1px solid #e2e8f0; border-radius:10px; background:#fff; text-align:center; break-inside:avoid; }
    .cover-chart-title { color:#475569; font-size:9.5px; font-weight:800; margin-bottom:1mm; }
    .cover-chart img { display:block; max-width:78mm; max-height:44mm; margin:0 auto; object-fit:contain; }
    .cover-meta { position:absolute; left:16mm; right:16mm; bottom:12mm; padding-top:4mm; border-top:1px solid #dbe4f0; color:#94a3b8; font-size:8.5px; display:flex; justify-content:space-between; }
    .report-intro { margin:0 0 8mm; padding:5mm 6mm; border-left:4px solid #2563eb; border-radius:0 8px 8px 0; background:#eff6ff; color:#475569; font-size:10px; }
    .report-body { padding:0; }
    .report-body h2 { page-break-before:always; margin:0 0 6mm; padding:5mm 6mm; border-radius:9px; color:#fff; background:linear-gradient(100deg,#1e3a8a,#2563eb); font-size:18px; line-height:1.35; break-after:avoid; }
    .report-body h2:first-child { page-break-before:avoid; }
    .report-body h3 { margin:7mm 0 3mm; padding-left:3mm; border-left:3px solid #60a5fa; color:#1e3a8a; font-size:13px; break-after:avoid; }
    .report-body h4 { margin:5mm 0 2mm; padding:3mm 4mm; border:1px solid #fde68a; border-radius:7px; color:#92400e; background:#fffbeb; font-size:11px; break-after:avoid; }
    .report-body table { width:100%; margin:0 0 6mm; border-collapse:separate; border-spacing:0; overflow:hidden; border:1px solid #dbe4f0; border-radius:8px; font-size:9.5px; break-inside:avoid; }
    .report-body th,.report-body td { padding:3mm 4mm; border-right:1px solid #e2e8f0; border-bottom:1px solid #e2e8f0; text-align:left; vertical-align:top; }
    .report-body tr:last-child td { border-bottom:none; }
    .report-body th:last-child,.report-body td:last-child { border-right:none; }
    .report-body th { background:#f1f5f9; color:#334155; font-weight:800; }
    .report-body p { margin:2mm 0; color:#475569; font-size:10px; text-align:justify; }
    .report-body ul,.report-body ol { margin:2mm 0 4mm; padding-left:6mm; }
    .report-body li { margin:1.5mm 0; color:#475569; font-size:9.8px; line-height:1.68; }
    .report-body blockquote { margin:2mm 0 4mm; padding:3.5mm 5mm; border-left:3px solid #38bdf8; border-radius:0 7px 7px 0; background:#f0f9ff; color:#334155; font-size:9.6px; break-inside:avoid; }
    .report-body blockquote p { margin:0; }
    .report-body hr { margin:8mm 0; border:0; border-top:1px solid #dbe4f0; }
    .report-body strong { color:#1e293b; }
    .edition-note { margin-top:3mm; padding:2mm 4mm; border-radius:7px; font-size:8.5px; text-align:center; ${isPremium ? "color:#047857;background:#ecfdf5;border:1px solid #a7f3d0;" : "color:#92400e;background:#fffbeb;border:1px solid #fde68a;"} }
  </style>
  <section class="report-cover">
    <div class="cover-orb"></div>
    <div><span class="cover-brand">AI学术审查系统</span><span class="cover-version">v${APP_VERSION} - ${escapeHtml(editionText)}</span></div>
    <div class="audit-chip">✓ ${escapeHtml(auditText)}</div>
    <h1 class="cover-title">${escapeHtml(paperTitle)}</h1>
    <div class="cover-subtitle">AI 学术审查与一致性核验报告 - ${escapeHtml(journal)}</div>
    <div class="summary-grid">
      <div class="summary-card"><div class="summary-label">综合得分</div><div class="summary-value">${scoreValue}<small> / 100</small></div></div>
      <div class="summary-card"><div class="summary-label">整体风险</div><div class="summary-value" style="font-size:15px">${escapeHtml(biasLevel || "-")}</div></div>
      <div class="summary-card"><div class="summary-label">检出问题</div><div class="summary-value">${issueCount}<small> 项</small></div></div>
    </div>
    <div class="score-overview"><div class="score-overview-title">五维评分概览</div>${scoreRows}</div>
    ${chartHtml}
    <div class="edition-note">${isPremium ? "本报告已通过反馈权益解锁，无导出水印。" : "本报告为普通版，页面包含 AI 学术审查系统普通版水印；提交产品评价可解锁当前报告的无水印版本。"}</div>
    <div class="cover-meta"><span>报告编号 ${escapeHtml(reportId)}</span><span>生成时间 ${escapeHtml(generatedAt)}</span></div>
  </section>
  <section class="report-intro"><strong>阅读说明：</strong>本报告由五个专业评价引擎完成初审，并经整体一致性复核 Agent 校验证据与跨维度冲突。评分和建议用于投稿前自查，不替代同行评议或编辑决定。</section>
  <main class="report-body">${rawHtml}</main>
</div>`;

      // 5. Generate A4 PDF and add stable vector headers, footers, page numbers and watermark.
      const safeTitle = String(paperTitle).replace(/[\\/:*?"<>|]/g, "_").slice(0, 60) || "论文";
      const opt = {
        margin: [18, 14, 18, 14],
        filename: `${safeTitle}_AI学术审查报告.pdf`,
        image: { type: "jpeg", quality: 0.94 },
        html2canvas: { scale: 1.6, useCORS: true, logging: false, backgroundColor: "#ffffff" },
        jsPDF: { unit: "mm", format: "a4", orientation: "portrait" },
        pagebreak: { mode: ["css", "legacy"], avoid: ["table", "blockquote", "h3", "h4", ".summary-card", ".engine-score-row", ".cover-chart"] },
      };

      await html2pdf()
        .set(opt)
        .from(styledHtml)
        .toPdf()
        .get("pdf")
        .then((pdf) => {
          const totalPages = pdf.internal.getNumberOfPages();
          const pw = pdf.internal.pageSize.getWidth();
          const ph = pdf.internal.pageSize.getHeight();
          for (let i = 1; i <= totalPages; i++) {
            pdf.setPage(i);
            if (!isPremium) {
              pdf.addImage(watermarkImage, "PNG", 15, ph * 0.27, pw - 30, 48, undefined, "FAST");
              pdf.addImage(watermarkImage, "PNG", 15, ph * 0.61, pw - 30, 48, undefined, "FAST");
            }
            pdf.setDrawColor(220, 226, 236);
            pdf.setLineWidth(0.25);
            pdf.line(14, ph - 12, pw - 14, ph - 12);
            pdf.setTextColor(125, 137, 154);
            pdf.setFontSize(7.5);
            pdf.text(`v${APP_VERSION}`, 14, ph - 7.5);
            pdf.text(`${i} / ${totalPages}`, pw - 14, ph - 7.5, { align: "right" });
            if (i > 1) {
              pdf.line(14, 12, pw - 14, 12);
              pdf.addImage(headerImage, "PNG", 14, 4.7, 68, 6, undefined, "FAST");
            }
          }
        })
        .save()
        .catch((err) => {
          console.error("[AI学术审查系统] PDF export failed:", err);
        });
    } catch (err) {
      console.error("[AI学术审查系统] Direct PDF export failed:", err);
    } finally {
      setPdfExporting(false);
    }
  }, [pdfExporting, engineMeta, engineScores, overallScore, biasLevel, retryCount, isPremium, engineResults, file, lang]);

  // Keep handleDirectPDFExportRef up to date
  handleDirectPDFExportRef.current = handleDirectPDFExport;

  // Auto-scroll to bottom whenever logs change
  useEffect(() => {
    logEndRef.current?.scrollIntoView({ behavior: "smooth" });
  }, [logs]);

  // Parse a log line into agent prefix + message body
  const parseLogLine = (text) => {
    const agentMatch = text.match(/^(\[.+?\]\s)/);
    if (agentMatch) {
      return { prefix: agentMatch[1], body: text.slice(agentMatch[1].length) };
    }
    const errorMatch = text.match(/^(!!! ERROR(?: \[[^\]]+\])?:)\s*/);
    if (errorMatch) {
      return { prefix: errorMatch[1], body: text.slice(errorMatch[0].length) };
    }
    if (text.startsWith("WARNING")) {
      return { prefix: "", body: text };
    }
    return { prefix: "", body: text };
  };

  // Shared Markdown components (compact terminal style)
  const mdComponents = {
    p: ({ children }) => <span className="whitespace-pre-wrap">{children}</span>,
    ul: ({ children }) => <ul className="list-disc list-inside my-0.5 pl-2 whitespace-pre-wrap">{children}</ul>,
    ol: ({ children }) => <ol className="list-decimal list-inside my-0.5 pl-2 whitespace-pre-wrap">{children}</ol>,
    li: ({ children }) => <li className="my-0">{children}</li>,
  };

  // Filter raw JSON junk from log bodies before rendering
  const sanitizeBody = (text) => {
    if (!text) return text;
    let cleaned = text
      .replace(/["']?(score|core_conclusion|evidence|actionable_advice|overall_score|bias_level|reasoning_process|arbitration_note|feedback_to_engines|approved|conflicts)["']?\s*:\s*/g, "")
      .replace(/[\{\}\[\]]/g, "")
      .replace(/^[",\s]+|[",\s]+$/g, "");
    return cleaned.trim() || null;
  };

  // --- idle: always show full overview ---
  if (phase === "idle") {
    return (
      <div>
        <div className={`${domainTheme?.lightBg ?? "bg-blue-50"} border ${domainTheme?.lightBorder ?? "border-blue-100"} ${domainTheme?.lightText ?? "text-blue-800"} text-sm rounded-xl px-5 py-4 mb-6`}>
          <FileText size={18} className="inline mr-2 mb-0.5" />
          {file ? `${"已上传："}${file.name} — ${t.readyForReview}` : t.uploadPrompt}
        </div>
        <TiltCard tiltFactor={1.5} className={`bg-white border ${domainTheme?.lightBorder ?? "border-slate-200"} rounded-xl p-6 text-sm text-slate-600 leading-relaxed space-y-4`}>
          <h3 className="text-lg font-semibold text-slate-800">{t.systemOverview}</h3>
          <p>{t.overviewDesc}</p>
          <div>
            <h4 className="font-semibold text-slate-700 mb-1">{t.reviewPipeline}</h4>
            <ol className="list-decimal list-inside space-y-1">
              <li><strong>{t.pipeline1Label}</strong> — {t.pipeline1.split("—")[1]?.trim() || t.pipeline1}</li>
              <li><strong>{t.pipeline2Label}</strong> — {t.pipeline2.split("—")[1]?.trim() || t.pipeline2}</li>
              <li><strong>{t.pipeline3Label}</strong> — {t.pipeline3.split("—")[1]?.trim() || t.pipeline3}</li>
            </ol>
          </div>
          <div>
            <h4 className="font-semibold text-slate-700 mb-1">{t.fourEngines}</h4>
            <table className="w-full text-xs border-collapse">
              <thead>
                <tr className="border-b border-slate-200">
                  <th className="text-left py-2 pr-4">{t.engineCol}</th>
                  <th className="text-left py-2">{t.focusCol}</th>
                </tr>
              </thead>
              <tbody>
                {ENGINE_KEYS.map((key) => {
                  const meta = engineMeta[key] || {};
                  return (
                    <tr key={key} className="border-b border-slate-100">
                      <td className="py-2 pr-4 font-medium">{meta.name ?? key}</td>
                      <td className="py-2 text-slate-500">{meta.focus ?? ""}</td>
                    </tr>
                  );
                })}
              </tbody>
            </table>
          </div>
        </TiltCard>
      </div>
    );
  }

  // --- reviewing ---
  if (phase === "reviewing") {
    return (
      <div className="surface-opaque flex-1 min-h-0 border border-slate-200 rounded-xl p-5 mt-2 shadow-sm flex flex-col overflow-hidden">
        <div className="flex items-center gap-2 text-sm font-semibold text-slate-700 mb-3">
          <Activity size={18} className={hasError ? "text-red-600" : "text-blue-600"} />
          {hasError ? "引擎异常 — 请查看下方日志" : t.initEngines}
        </div>
        <div className="flex-1 min-h-0 space-y-1 font-mono text-xs text-slate-600 overflow-y-auto pr-1">
          {(logs ?? []).map((msg, i) => {
            const text = getLogText(msg);
            const isError = text.startsWith("!!! ERROR");
            const isWarn = text.startsWith("WARNING");
            const { prefix, body } = parseLogLine(text);
            const isProgressEntry = msg && typeof msg === "object" && msg.kind === "progress";
            const rowKey = msg && typeof msg === "object" ? msg.id : i;

            if (isError) {
              const cleanBody = sanitizeBody(body);
              if (!cleanBody) return null;
              return (
                <div key={rowKey} className="py-1.5 px-2 rounded bg-red-50 border border-red-200 text-red-800">
                  <AlertCircle size={12} className="inline mr-1" />
                  <span className="font-semibold">{prefix}</span>
                  <div className="prose prose-sm max-w-none text-slate-700 dark:text-slate-300">
                    <ReactMarkdown components={mdComponents}>{cleanBody}</ReactMarkdown>
                  </div>
                </div>
              );
            }
            const cleanBody = sanitizeBody(body);
            if (!cleanBody) return null;
            return (
              <div key={rowKey} className={`py-1 px-2 rounded ${isWarn ? "bg-amber-50 text-amber-800" : ""}`}>
                {isWarn && <AlertCircle size={12} className="inline mr-1" />}
                {prefix && <span className="font-semibold text-blue-700">{prefix}</span>}
                <div className="prose prose-sm max-w-none text-slate-700 dark:text-slate-300">
                  {isProgressEntry ? (
                    <span className="whitespace-pre-wrap">
                      <AnimatedElapsedText
                        text={cleanBody}
                        elapsedSeconds={msg.stageElapsedSeconds}
                        animationKey={msg.progressKey || msg.id}
                      />
                    </span>
                  ) : (
                    <ReactMarkdown components={mdComponents}>{cleanBody}</ReactMarkdown>
                  )}
                </div>
              </div>
            );
          })}
          <div ref={logEndRef} />
        </div>
        <div className={`mt-3 self-start inline-flex items-center gap-2 rounded-full border px-3 py-1.5 text-xs ${
          hasError
            ? "border-red-200 bg-red-50 text-red-700"
            : "processing-breath border-blue-200 bg-blue-50 text-blue-700"
        }`}>
          {!hasError && <RefreshCw size={12} className="animate-spin" />}
          {hasError ? "发生错误，请检查 API 配置后重试" : t.processing}
        </div>
        {hasError && (
          <button
            type="button"
            onClick={onReset}
            className="ml-2 mt-3 inline-flex items-center gap-1.5 rounded-full border border-slate-300 bg-white px-3 py-1.5 text-xs text-slate-600 transition-colors hover:border-blue-300 hover:text-blue-700"
          >
            <RefreshCw size={12} />
            返回并重新审查
          </button>
        )}
      </div>
    );
  }

  // --- done: full results ---
  const hasAnyScore = Object.values(engineScores).some((v) => v != null);
  const scoreValue = hasAnyScore ? (typeof overallScore === "number" ? overallScore.toFixed(1) : "0.0") : "-";
  const biasText = biasLevel ?? "Moderate-Low";

  return (
    <ErrorBoundary>
      <div id="report-export-content" className="space-y-6 mt-2">
        <div className={`${auditPassed === false
          ? "bg-amber-50 border-amber-200 text-amber-800"
          : "bg-emerald-50 border-emerald-200 text-emerald-800"
        } border rounded-xl px-5 py-4 text-sm flex items-start gap-2 no-print`}>
          {auditPassed === false
            ? <AlertTriangle size={18} className="shrink-0 mt-0.5" />
            : <CheckCircle size={18} className="shrink-0 mt-0.5" />}
          <span>
            {auditPassed === false
              ? (lang === "zh"
                  ? "五维评价已完成，但一致性复核服务未成功；当前展示的是未经二次复核的原始评价结果。"
                  : "The five-dimension review completed, but consistency auditing did not. The report shows the un-audited evaluation results.")
              : t.successBanner}
          </span>
        </div>

        {/* Partial failure warning */}
        {evaluationStatus === "partial_failure" && (
          <div className="bg-amber-50 border border-amber-200 text-amber-800 rounded-xl px-5 py-4 text-sm flex items-start gap-2.5">
            <AlertTriangle size={18} className="shrink-0 mt-0.5 text-amber-500" />
            <div>
              <p className="font-semibold">{t.partialFailureWarning}</p>
            </div>
          </div>
        )}

        {/* v5 双层学科分类标签栏 */}
        {engineResults?.scoringPolicy && (
          <div className="flex items-center gap-2 px-4 py-2.5 bg-blue-50 border border-blue-200 rounded-xl text-xs flex-wrap">
            <span className="font-semibold text-blue-700">
              {lang === "zh" ? "学科分类" : "Discipline"}
            </span>
            {engineResults.scoringPolicy.subject_top ? (
              <span className="bg-blue-100 text-blue-700 px-2 py-0.5 rounded-full font-medium">
                {engineResults.scoringPolicy.subject_top}
              </span>
            ) : (
              <span className="bg-slate-100 text-slate-400 px-2 py-0.5 rounded-full text-[10px]">
                {lang === "zh" ? "未识别" : "Unknown"}
              </span>
            )}
            {engineResults.scoringPolicy.subject_sub && (
              <>
                <ChevronRight size={12} className="text-blue-400" />
                <span className="bg-indigo-100 text-indigo-700 px-2 py-0.5 rounded-full font-medium">
                  {engineResults.scoringPolicy.subject_sub}
                </span>
              </>
            )}
            {engineResults.scoringPolicy.paper_type && (
              <>
                <ChevronRight size={12} className="text-blue-400" />
                <span className="bg-purple-100 text-purple-700 px-2 py-0.5 rounded-full font-medium">
                  {engineResults.scoringPolicy.paper_type === "质性" ? "质性研究" :
                   engineResults.scoringPolicy.paper_type === "量化" ? "量化研究" :
                   engineResults.scoringPolicy.paper_type === "混合" ? "混合方法" :
                   engineResults.scoringPolicy.paper_type}
                </span>
              </>
            )}
            {engineResults.scoringPolicy.policy && (
              <span className="text-[10px] text-blue-400 ml-auto">
                {engineResults.scoringPolicy.policy_label || (POLICY_LABEL_MAP[engineResults.scoringPolicy.policy] || engineResults.scoringPolicy.policy)}
              </span>
            )}
          </div>
        )}

        {/* Evaluation Limitations — dynamic from API, fallback to static */}
        <div className="bg-amber-50 border border-amber-200 text-amber-800 rounded-xl px-5 py-4 text-sm flex items-start gap-2.5">
          <Info size={18} className="shrink-0 mt-0.5 text-amber-500" />
          <div>
            <p className="font-semibold mb-1">
              {lang === "zh" ? "【评估局限性声明】" : "[Evaluation Limitations Notice]"}
            </p>
            <ul className="list-disc list-inside text-amber-700 leading-relaxed space-y-0.5">
              {(systemLimitations?.length > 0
                ? systemLimitations
                : [lang === "zh"
                    ? "本系统评估基于当前模型能力。缺乏同领域近年文献横向对比时，影响力预测存在局限；对于纯思辨型政策评论类文章，影响力预测的不确定性更高，结果仅供参考。"
                    : "This system's evaluation is based on current model capabilities. In the absence of recent literature cross-comparison within the same field, impact predictions have limitations. For purely speculative policy commentary articles, the uncertainty of impact predictions is higher, and results are for reference only."]
              ).map((lim, i) => (
                <li key={i}>{lim}</li>
              ))}
            </ul>
          </div>
        </div>

        <div className="grid grid-cols-1 lg:grid-cols-5 gap-6">
          <TiltCard tiltFactor={1.2} className="lg:col-span-3 bg-white border border-slate-200 rounded-xl p-5">
            <h3 className="text-lg font-semibold text-slate-800 mb-4">{t.radarTitle}</h3>
            <SafeRadarChart data={radarData} />
          </TiltCard>

          <TiltCard tiltFactor={1.2} className="lg:col-span-2 bg-white border border-slate-200 rounded-xl p-5">
            <h3 className="text-lg font-semibold text-slate-800 mb-4">{t.overallAssessment}</h3>
            <div className="grid grid-cols-2 gap-4">
              <div className="bg-gradient-to-br from-white to-blue-50 border border-blue-100 rounded-xl p-4 avoid-page-break">
                <p className="text-xs text-slate-500 uppercase tracking-wider">{t.compositeScore}</p>
                <p className="text-3xl font-bold text-slate-800 mt-1">{scoreValue}</p>
                <p className="text-xs text-blue-600 mt-1">/ 100</p>
                <p className="text-[11px] text-slate-400 mt-2">{t.postArbitration.replace("{n}", String(retryCount ?? 0))}</p>
              </div>
              <div className="bg-gradient-to-br from-white to-slate-50 border border-slate-200 rounded-xl p-4">
                <p className="text-xs text-slate-500 uppercase tracking-wider">{t.biasLevel}</p>
                <p className="text-3xl font-bold text-slate-800 mt-1">{biasText}</p>
                <p className={`text-xs mt-1 ${auditPassed === false ? "text-amber-600" : "text-emerald-600"}`}>
                  {auditPassed === false
                    ? (lang === "zh" ? "未完成二次一致性复核" : "Consistency audit unavailable")
                    : t.crossValidated}
                </p>
              </div>
            </div>
            <hr className="my-4 border-slate-100" />
            <p className="text-xs font-semibold text-slate-500 uppercase tracking-wider mb-2">{t.perEngineScores}</p>
            <div className="space-y-2.5">
              {ENGINE_KEYS.map((key) => {
                const score = engineScores[key] ?? 0;
                const meta = engineMeta[key] || { name: key };
                return (
                  <div key={key}>
                    <div className="flex justify-between text-xs mb-0.5">
                      <span className="text-slate-700 font-medium">{meta.name ?? key}</span>
                      <span className="text-slate-500">{score != null ? `${score} / 100` : "- / 100"}</span>
                    </div>
                    <div className="w-full h-1.5 bg-slate-100 rounded-full overflow-hidden">
                      <div className="h-full bg-blue-600 rounded-full transition-all duration-500" style={{ width: `${score != null ? score : 0}%` }} />
                    </div>
                  </div>
                );
              })}
            </div>
          </TiltCard>
        </div>

        {/* Engine Reports */}
        <div>
          <h3 className="text-lg font-semibold text-slate-800 mb-3">{t.engineReports}</h3>
          <p className="text-xs text-slate-400 mb-4">
            {auditPassed === false
              ? (lang === "zh"
                  ? "以下为五个评价引擎的原始结果；一致性复核服务未成功，内容尚未经过二次交叉校验。"
                  : "These are the five engines' original results. The consistency audit was unavailable, so they have not been cross-validated.")
              : t.reportsCaption}
          </p>
          <div className="space-y-4">
            {(() => {
              // v5: 基于 subject_top 计算默认展开的引擎
              const subjectTop = engineResults?.scoringPolicy?.subject_top || "";
              const isHumanities = subjectTop === "人文学科";
              return ENGINE_KEYS.map((key) => {
              const eng = engineMeta[key] || {};
              const score = engineScores[key] ?? 0;
              const isEthics = key === "ethics";
              // v5: 方法论始终展开；理工/纯理科→伦理展开；人文→伦理折叠
              const defaultOpen = key === "methodology" || (key === "ethics" && !isHumanities);
              const isHumanitiesEthics = isEthics && isHumanities;
              // v5: 理工/工科的伦理卡片高亮标识
              const isScienceEthics = isEthics && (subjectTop === "纯理科" || subjectTop === "交叉工科");
              return (
                <details key={key} className={`bg-white border rounded-xl group hover:shadow-md hover:-translate-y-0.5 transition-all duration-300 avoid-page-break${isHumanitiesEthics ? " border-amber-300" : isScienceEthics ? " border-blue-300 bg-blue-50/30" : " border-slate-200"}`} open={defaultOpen}>
                  <summary className="px-5 py-4 cursor-pointer list-none flex items-center justify-between select-none">
                    <div className="flex items-center gap-3">
                      <ChevronRight size={16} className="text-slate-400 transition-transform group-open:rotate-90" />
                      <span className="text-sm font-semibold text-slate-700">{eng.name ?? key}</span>
                      <span className="text-xs bg-blue-100 text-blue-700 px-2 py-0.5 rounded-full font-medium">{score != null ? `${score}/100` : "-/100"}</span>
                      {eng.riskLevel && (
                        <span className={`text-[10px] px-1.5 py-0.5 rounded-full font-medium ${
                          eng.riskLevel === "low" ? "bg-emerald-100 text-emerald-700" :
                          eng.riskLevel === "high" ? "bg-red-100 text-red-700" :
                          "bg-amber-100 text-amber-700"
                        }`}>
                          {eng.riskLevel === "low" ? t.lowRiskLabel : eng.riskLevel === "high" ? t.highRiskLabel : t.mediumRiskLabel}
                        </span>
                      )}
                      {eng.confidence != null && (
                        <span className="text-[10px] text-slate-400">
                          {t.confidenceLabel} {Math.round(eng.confidence * 100)}%
                        </span>
                      )}
                      {/* v5: 人文学科伦理卡片权重标签 */}
                      {isHumanitiesEthics && (
                        <span className="text-[10px] bg-amber-100 text-amber-700 px-2 py-0.5 rounded-full font-medium">
                          {lang === "zh" ? "权重 10%" : "Weight 10%"}
                        </span>
                      )}
                      {/* v5: 理工/工科伦理重点审查标签 */}
                      {isScienceEthics && (
                        <span className="text-[10px] bg-blue-100 text-blue-700 px-2 py-0.5 rounded-full font-medium">
                          {lang === "zh" ? "实验伦理与数据诚信重点校验" : "Research Ethics & Integrity Focus"}
                        </span>
                      )}
                    </div>
                    <button
                      onClick={(e) => { e.preventDefault(); e.stopPropagation(); onAppeal?.(key, eng.name ?? key); }}
                      className="text-[10px] text-amber-600 hover:text-amber-700 hover:bg-amber-50 px-2 py-1 rounded-md transition-colors flex items-center gap-1 opacity-0 group-hover:opacity-100 no-print"
                    >
                      <AlertTriangle size={11} />
                      {t.appealButton}
                    </button>
                  </summary>
                  <div className="px-5 pb-5 space-y-4">
                    {/* Core Conclusion */}
                    <div>
                      <p className="text-xs font-semibold text-slate-500 uppercase mb-1">{t.coreConclusion}</p>
                      <p className="text-sm text-slate-700"><TextWithTerms text={eng.conclusion ?? ""} /></p>
                    </div>

                    {/* Strengths */}
                    {(eng.strengths?.length > 0) && (
                      <div className="bg-emerald-50 border border-emerald-100 rounded-lg p-3 avoid-page-break">
                        <p className="text-xs font-semibold text-emerald-700 uppercase mb-2">
                          {t.strengthsLabel}
                        </p>
                        <ul className="space-y-1.5">
                          {eng.strengths.slice(0, 5).map((s, i) => (
                            <li key={i} className="text-sm text-emerald-800 flex gap-2">
                              <span className="text-emerald-400 shrink-0 mt-0.5">+</span>
                              <span><TextWithTerms text={s} /></span>
                            </li>
                          ))}
                        </ul>
                      </div>
                    )}

                    {/* Issues */}
                    {(eng.issues?.length > 0) && (
                      <div className="bg-amber-50 border border-amber-100 rounded-lg p-3 avoid-page-break">
                        <p className="text-xs font-semibold text-amber-700 uppercase mb-2">
                          {t.issuesLabel}
                        </p>
                        <div className="space-y-3">
                          {eng.issues.map((issue, i) => (
                            <div key={i} className="surface-opaque border border-amber-100 rounded-lg p-3 avoid-page-break">
                              <div className="flex items-center gap-2 mb-1.5">
                                <span className={`text-[10px] px-1.5 py-0.5 rounded-full font-medium ${
                                  issue.severity === "high" ? "bg-red-100 text-red-700" :
                                  issue.severity === "medium" ? "bg-amber-100 text-amber-700" :
                                  "bg-slate-100 text-slate-600"
                                }`}>
                                  {issue.severity === "high" ? t.highSeverityLabel : issue.severity === "medium" ? t.mediumSeverityLabel : t.lowSeverityLabel}
                                </span>
                                <span className="text-[10px] text-slate-400">
                                  {issue.issue_type ?? ""}
                                </span>
                                {issue.location?.page != null && (
                                  <button
                                    onClick={(ev) => { ev.preventDefault(); handleViewOriginal(issue.location); }}
                                    className="text-[10px] text-blue-600 hover:text-blue-700 hover:bg-blue-50 px-1.5 py-0.5 rounded transition-colors ml-auto"
                                  >
                                    {t.viewOriginal}
                                  </button>
                                )}
                              </div>
                              {issue.evidence && (
                                <p className="text-xs text-slate-600 mb-1.5 bg-slate-50 rounded p-2 italic">
                                  <TextWithTerms text={String(issue.evidence).slice(0, 400)} />
                                </p>
                              )}
                              {issue.suggestion && (
                                <p className="text-xs text-blue-700">
                                  {t.suggestionLabel}
                                  <TextWithTerms text={String(issue.suggestion).slice(0, 400)} />
                                </p>
                              )}
                            </div>
                          ))}
                        </div>
                      </div>
                    )}

                    {/* Legacy: Evidence & Advice (fallback for old backend format) */}
                    {(!eng.strengths?.length && !eng.issues?.length) && (
                      <>
                        <div className="bg-slate-50 border border-slate-100 rounded-lg p-3 avoid-page-break">
                          <p className="text-xs font-semibold text-slate-500 uppercase mb-1">{t.evidence}</p>
                          <p className="text-sm text-slate-600"><TextWithTerms text={eng.evidence ?? ""} /></p>
                        </div>
                        <div className={`rounded-lg p-3 avoid-page-break ${isEthics ? "bg-amber-50 border border-amber-200" : "bg-blue-50 border border-blue-100"}`}>
                          <p className="text-xs font-semibold text-slate-500 uppercase mb-1">{t.actionableAdvice}</p>
                          <p className={`text-sm ${isEthics ? "text-amber-800" : "text-blue-800"}`}><TextWithTerms text={eng.advice ?? ""} /></p>
                        </div>
                      </>
                    )}

                    {/* Reasoning MD Collapsible */}
                    {eng.reasoningMd && (
                      <details className="group/reasoning border border-slate-200 rounded-lg overflow-hidden">
                        <summary className="px-4 py-2.5 cursor-pointer list-none flex items-center gap-2 text-xs font-medium text-slate-500 hover:bg-slate-50 transition-colors select-none">
                          <ChevronRight size={12} className="text-slate-400 transition-transform group-open/reasoning:rotate-90" />
                          {t.viewReasoning}
                        </summary>
                        <div className="px-4 py-3 bg-slate-50 border-t border-slate-100 prose prose-sm max-w-none text-slate-600">
                          <ReactMarkdown>{String(eng.reasoningMd).slice(0, 8000)}</ReactMarkdown>
                        </div>
                      </details>
                    )}

                    {/* Limitations */}
                    {(eng.limitations?.length > 0) && (
                      <div className="text-[10px] text-slate-400 italic">
                        <span>{t.limitationsLabel}</span>
                        {eng.limitations.slice(0, 3).join("; ")}
                      </div>
                    )}

                    {/* Journal Recommendation (academic_impact engine) */}
                    {eng.journalRecommendation && (
                      <div className="bg-blue-50 border border-blue-200 rounded-lg p-3 mt-3 avoid-page-break">
                        <p className="text-xs font-semibold text-blue-700 uppercase mb-2">
                          {t.journalRecommendationLabel}
                        </p>
                        {(() => {
                          const recTier = eng.journalRecommendation.recommended_tier;
                          const altTier = eng.journalRecommendation.alternative_tier;
                          const hasTiers = (recTier && recTier.trim()) || (altTier && altTier.trim());
                          const hasJournalRecs = Array.isArray(eng.journalRecommendation.journal_recommendations) && eng.journalRecommendation.journal_recommendations.length > 0;
                          if (!hasTiers && !hasJournalRecs) {
                            return (
                              <p className="text-xs text-blue-800 leading-relaxed">
                                {lang === "zh"
                                  ? "暂不推荐直接投稿具体期刊。建议补强理论深度与实验对比后，考虑投递相关领域的综合性学术期刊或专业子刊。"
                                  : "No specific journal recommendation at this stage. Consider strengthening theoretical depth and experimental comparisons before targeting comprehensive academic journals in related fields."}
                              </p>
                            );
                          }
                          return (
                            <>
                            {hasTiers && (
                            <div className="grid grid-cols-2 gap-2 mb-2">
                              <div>
                                <p className="text-[10px] text-blue-500">{t.recommendedTier}</p>
                                <p className="text-sm font-medium text-blue-800">
                                  {recTier || ""}
                                </p>
                              </div>
                              <div>
                                <p className="text-[10px] text-blue-500">{t.alternativeTier}</p>
                                <p className="text-sm text-blue-700">
                                  {altTier || ""}
                                </p>
                              </div>
                            </div>
                            )}
                            {/* Render journal_recommendations array if present */}
                            {hasJournalRecs && eng.journalRecommendation.journal_recommendations.slice(0, 5).map((jr, i) => {
                              const matchScore = jr.match_score ?? jr.matchScore ?? jr.confidence ?? null;
                              const hasScore = matchScore != null && Number.isFinite(Number(matchScore)) && Number(matchScore) > 0;
                              const scorePct = hasScore ? (Number(matchScore) <= 1 ? Math.round(Number(matchScore) * 100) : Math.round(Number(matchScore))) : null;
                              return (
                                <div key={i} className="border border-blue-200 rounded-lg p-2 mb-2 avoid-page-break">
                                  <div className="flex justify-between items-center mb-1">
                                    <span className="text-xs font-medium text-blue-800">{jr.name || `期刊 ${i + 1}`}</span>
                                    {scorePct != null && (
                                      <span className="text-[10px] font-semibold text-blue-600">{scorePct}%</span>
                                    )}
                                  </div>
                                  {jr.level && <p className="text-[10px] text-blue-500 mb-0.5">{jr.level}</p>}
                                  {jr.reason && <p className="text-[10px] text-blue-700 leading-relaxed">{jr.reason}</p>}
                                  {jr.required_improvements?.length > 0 && (
                                    <ul className="list-disc list-inside text-[10px] text-blue-600 mt-1 space-y-0.5">
                                      {jr.required_improvements.map((ri, j) => (
                                        <li key={j}>{ri}</li>
                                      ))}
                                    </ul>
                                  )}
                                </div>
                              );
                            })}
                            </>
                          );
                        })()}
                        {(() => {
                          const confVal = Number(eng.journalRecommendation.confidence);
                          const hasConf = Number.isFinite(confVal) && confVal > 0;
                          if (!hasConf) return null;
                          return (
                          <div className="mb-2">
                            <p className="text-[10px] text-blue-500">{t.confidenceLabel}</p>
                            <div className="w-full h-1.5 bg-blue-200 rounded-full overflow-hidden">
                              <div className="h-full bg-blue-600 rounded-full" style={{ width: `${Math.round(confVal * 100)}%` }} />
                            </div>
                            <p className="text-[10px] text-blue-500 text-right">{Math.round(confVal * 100)}%</p>
                          </div>
                          );
                        })()}
                        {eng.journalRecommendation.rationale?.length > 0 && (
                          <div className="mb-2">
                            <p className="text-[10px] font-semibold text-blue-600 mb-1">{t.rationale}</p>
                            <ul className="list-disc list-inside space-y-0.5 text-xs text-blue-800">
                              {eng.journalRecommendation.rationale.map((r, i) => (
                                <li key={i}>{r}</li>
                              ))}
                            </ul>
                          </div>
                        )}
                        {eng.journalRecommendation.readiness_gaps?.length > 0 && (
                          <div>
                            <p className="text-[10px] font-semibold text-blue-600 mb-1">{t.readinessGaps}</p>
                            <ul className="list-disc list-inside space-y-0.5 text-xs text-blue-800">
                              {eng.journalRecommendation.readiness_gaps.map((g, i) => (
                                <li key={i}>{g}</li>
                              ))}
                            </ul>
                          </div>
                        )}
                      </div>
                    )}

                    {/* v5: 偏差解释 */}
                    {eng.biasExplanation && (
                      <div className="bg-purple-50 border border-purple-200 rounded-lg p-3 avoid-page-break">
                        <p className="text-xs font-semibold text-purple-700 uppercase mb-2">
                          {lang === "zh" ? "偏差解释" : "Bias Explanation"}
                        </p>
                        {eng.biasExplanation.detected_biases?.length > 0 && (
                          <div className="flex flex-wrap gap-1 mb-2">
                            {eng.biasExplanation.detected_biases.map((b, i) => (
                              <span key={i} className="text-[10px] bg-purple-100 text-purple-700 px-1.5 py-0.5 rounded">
                                {ISSUE_TYPE_MAP[b] || b}
                              </span>
                            ))}
                          </div>
                        )}
                        {eng.biasExplanation.bias_impact_assessment && (
                          <p className="text-xs text-purple-800 mb-1">
                            <TextWithTerms text={eng.biasExplanation.bias_impact_assessment} />
                          </p>
                        )}
                        {eng.biasExplanation.debiasing_recommendations && (
                          <p className="text-xs text-purple-700">
                            <TextWithTerms text={eng.biasExplanation.debiasing_recommendations} />
                          </p>
                        )}
                      </div>
                    )}

                    {/* v5: 缺失文献清单 */}
                    {eng.missingLiterature?.length > 0 && (
                      <div className="bg-sky-50 border border-sky-200 rounded-lg p-3 avoid-page-break">
                        <p className="text-xs font-semibold text-sky-700 uppercase mb-2">
                          {lang === "zh" ? "缺失文献清单" : "Missing Literature"}
                        </p>
                        <div className="space-y-2">
                          {eng.missingLiterature.slice(0, 5).map((lit, i) => (
                            <div key={i} className="text-xs">
                              <p className="font-medium text-sky-800">{lit.title || lit.name || `${lang === "zh" ? "文献" : "Reference"} ${i + 1}`}</p>
                              {lit.relevance && <p className="text-sky-600 mt-0.5">{lit.relevance}</p>}
                              {lit.why_missing_is_problematic && <p className="text-sky-500 mt-0.5 italic">{lit.why_missing_is_problematic}</p>}
                            </div>
                          ))}
                        </div>
                      </div>
                    )}

                    {/* v5: 备选理论/方法 */}
                    {eng.alternativeTheories?.length > 0 && (
                      <div className="bg-teal-50 border border-teal-200 rounded-lg p-3 avoid-page-break">
                        <p className="text-xs font-semibold text-teal-700 uppercase mb-2">
                          {lang === "zh" ? "备选理论与方法" : "Alternative Theories"}
                        </p>
                        <div className="space-y-2">
                          {eng.alternativeTheories.slice(0, 4).map((alt, i) => (
                            <div key={i} className="text-xs">
                              <p className="font-medium text-teal-800">{alt.name || alt.theory || `${lang === "zh" ? "方案" : "Option"} ${i + 1}`}</p>
                              {alt.applicability && <p className="text-teal-600 mt-0.5">{alt.applicability}</p>}
                              {alt.potential_insight && <p className="text-teal-500 mt-0.5">{alt.potential_insight}</p>}
                            </div>
                          ))}
                        </div>
                      </div>
                    )}

                    {/* v5: 逻辑整改方案（仅 logic 引擎） */}
                    {eng.logicCorrectionPlan && (
                      <div className="bg-rose-50 border border-rose-200 rounded-lg p-3 avoid-page-break">
                        <p className="text-xs font-semibold text-rose-700 uppercase mb-2">
                          {lang === "zh" ? "逻辑整改方案" : "Logic Correction Plan"}
                        </p>
                        {eng.logicCorrectionPlan.identified_gaps?.length > 0 && (
                          <ul className="list-disc list-inside text-xs text-rose-800 mb-2 space-y-0.5">
                            {eng.logicCorrectionPlan.identified_gaps.map((gap, i) => (
                              <li key={i}><TextWithTerms text={typeof gap === "string" ? gap : String(gap)} /></li>
                            ))}
                          </ul>
                        )}
                        {eng.logicCorrectionPlan.correction_strategy && (
                          <p className="text-xs text-rose-700">
                            <TextWithTerms text={eng.logicCorrectionPlan.correction_strategy} />
                          </p>
                        )}
                        {eng.logicCorrectionPlan.revised_argument_flow && (
                          <p className="text-xs text-rose-600 mt-1 italic">
                            <TextWithTerms text={eng.logicCorrectionPlan.revised_argument_flow} />
                          </p>
                        )}
                      </div>
                    )}
                  </div>
                </details>
              );
              });
            })()}
          </div>
        </div>

        <div className="pb-8 no-print">
          <div className={`mb-3 flex items-center justify-end gap-2 text-xs ${
            isPremium ? "text-emerald-600" : "text-amber-600"
          }`}>
            {isPremium ? <Unlock size={14} /> : <Lock size={14} />}
            <span>
              {isPremium
                ? (lang === "zh" ? "当前报告已解锁无水印导出" : "Watermark-free export unlocked for this report")
                : (lang === "zh" ? "普通导出包含水印；提交评价可为当前报告移除水印" : "Standard export includes a watermark; submit feedback to remove it")}
            </span>
          </div>
          <div className="text-right">
          <button
            onClick={() => {
              if (isPremium) { onDownload?.(); }
              else { pendingActionRef.current = "markdown"; onRequestExport?.("markdown"); }
            }}
            className="inline-flex items-center gap-2 bg-slate-800 text-white text-sm font-medium px-5 py-2.5 rounded-xl hover:bg-slate-700 transition-colors mr-3"
          >
            <Download size={16} />
            {t.download}
          </button>
          <button
            onClick={() => {
              if (isPremium) { handleDirectPDFExport(); }
              else { pendingActionRef.current = "pdf"; onRequestExport?.("pdf"); }
            }}
            disabled={pdfExporting}
            className="inline-flex items-center gap-2 bg-blue-600 text-white text-sm font-medium px-5 py-2.5 rounded-xl hover:bg-blue-700 disabled:opacity-50 disabled:cursor-not-allowed transition-colors"
          >
            {pdfExporting ? (
              <RefreshCw size={16} className="animate-spin" />
            ) : (
              <FileText size={16} />
            )}
            {pdfExporting
              ? t.exportingLabel
              : t.exportPDFLabel}
          </button>
          </div>
        </div>

        {/* PDF Source Text Drawer — replaced by split-panel evidence mode. */}
      </div>
    </ErrorBoundary>
  );
}

// ============================================================
// App
// ============================================================
function InnerApp() {
  const [lang, setLang] = useState("zh");
  const t = T[lang];

  const [file, setFile] = useState(null);
  const [phase, setPhase] = useState("idle");
  const [logs, setLogs] = useState([]);
  const [retryCount, setRetryCount] = useState(0);
  const [toast, setToast] = useState(null);
  const [activeModeIndex, setActiveModeIndex] = useState(0);      // v5.1: 0-3 maps to REVIEW_MODES
  const [engineResults, setEngineResults] = useState(null);
  const [apiKey, setApiKey] = useState("");
  const [baseUrl, setBaseUrl] = useState(DEFAULT_QWEN_BASE_URL);
  const [modelName, setModelName] = useState(DEFAULT_QWEN_MODEL);
  const [isConfigured, setIsConfigured] = useState(false);
  const [hasError, setHasError] = useState(false);
  const [devMode, setDevMode] = useState(false);
  // v5.1: custom-mode weight sliders (user-editable percentages)
  const [customWeights, setCustomWeights] = useState({ methodology: 25, logic: 20, ethics: 20, innovation: 20, academic_impact: 15 });
  const [appealModal, setAppealModal] = useState(null);
  const [showHistory, setShowHistory] = useState(false);
  const [showSettings, setShowSettings] = useState(false);
  const [reviewSessionKey, setReviewSessionKey] = useState(Date.now().toString());
  const [showFeedbackModal, setShowFeedbackModal] = useState(false);
  const [unlockedReportId, setUnlockedReportId] = useState(null);
  const [premiumToast, setPremiumToast] = useState(null);
  const [feedbackClosedVersion, setFeedbackClosedVersion] = useState(0);
  const [pdfBlobUrl, setPdfBlobUrl] = useState(null);
  const [reviewMetadata, setReviewMetadata] = useState(null);
  const [isEvidenceMode, setIsEvidenceMode] = useState(false);
  const [activeEvidenceRef, setActiveEvidenceRef] = useState(null);
  const [pdfViewerKey, setPdfViewerKey] = useState(0);
  const [backendVersion, setBackendVersion] = useState(null);
  const reportScrollRef = useRef(0);
  const reportPanelRef = useRef(null);
  const pendingExportRef = useRef(null);
  const isPremium = Boolean(
    engineResults?.reportId && unlockedReportId === engineResults.reportId,
  );

  const rememberReportScroll = useCallback(() => {
    if (reportPanelRef.current) {
      reportScrollRef.current = reportPanelRef.current.scrollTop;
    }
  }, []);

  const restoreReportScroll = useCallback(() => {
    requestAnimationFrame(() => {
      if (reportPanelRef.current) {
        reportPanelRef.current.scrollTop = reportScrollRef.current;
      }
    });
  }, []);

  // v5.1: Sync custom weights to selected preset mode (presets only)
  useEffect(() => {
    const mode = REVIEW_MODES[activeModeIndex];
    if (mode && mode.locked && mode.weights) {
      setCustomWeights({ ...mode.weights });
    }
  }, [activeModeIndex]);

  // v5.1: devMode → auto switch to custom review mode
  useEffect(() => {
    if (devMode) {
      setActiveModeIndex(3); // REVIEW_MODES[3] = custom
    } else if (activeModeIndex === 3) {
      setActiveModeIndex(0); // fallback to social_sciences
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [devMode]);

  // Detect stale backend processes explicitly instead of relying on browser refresh behavior.
  useEffect(() => {
    let active = true;
    fetch(`${API_BASE}/api/health?client_version=${APP_VERSION}`, { cache: "no-store" })
      .then((response) => response.ok ? response.json() : null)
      .then((data) => {
        if (!active) return;
        if (data?.version) setBackendVersion(String(data.version));
        if (data?.configured) {
          const saved = localStorage.getItem("ai_academic_review_config");
          let hasValidBrowserConfig = false;
          if (saved) {
            try {
              hasValidBrowserConfig = Boolean(normalizeStoredReviewConfig(JSON.parse(saved)).config);
            } catch { /* Invalid storage is handled by the config migration effect. */ }
          }
          if (!hasValidBrowserConfig) setIsConfigured(true);
        }
      })
      .catch(() => { /* Connectivity errors are reported by the review flow itself. */ });
    return () => { active = false; };
  }, []);

  // Load credentials and entitlement token from localStorage on mount
  useEffect(() => {
    const saved = localStorage.getItem("ai_academic_review_config");
    if (saved) {
      try {
        const cfg = JSON.parse(saved);
        const normalized = normalizeStoredReviewConfig(cfg);
        if (normalized.config) {
          setApiKey(normalized.config.apiKey);
          setBaseUrl(normalized.config.baseUrl);
          setModelName(normalized.config.modelName);
          setIsConfigured(true);
        }
      } catch {
        localStorage.removeItem("ai_academic_review_config");
      }
    }
    const savedEntitlement = localStorage.getItem("ai_academic_review_entitlement");
    if (savedEntitlement) {
      try {
        const entitlement = JSON.parse(savedEntitlement);
        if (entitlement?.token && entitlement?.reportId) {
          setUnlockedReportId(entitlement.reportId);
        } else {
          localStorage.removeItem("ai_academic_review_entitlement");
        }
      } catch {
        // v4.6 and earlier stored a global raw token. It cannot safely unlock new reports.
        localStorage.removeItem("ai_academic_review_entitlement");
      }
    }
  }, []);

  const saveConfig = () => {
    if (!isCompleteReviewConfig(apiKey, baseUrl, modelName)) {
      setToast("请完整填写 API Key、OpenAI 兼容地址和模型名称");
      return false;
    }
    const cfg = { apiKey: apiKey.trim(), baseUrl: baseUrl.trim(), modelName: modelName.trim() };
    localStorage.setItem("ai_academic_review_config", JSON.stringify(cfg));
    setIsConfigured(true);
    return true;
  };

  const clearConfig = () => {
    clearAllTimers();
    try { readerRef.current?.cancel(); } catch {}
    readerRef.current = null;
    reviewingRef.current = false;
    if (pdfBlobUrl) { URL.revokeObjectURL(pdfBlobUrl); setPdfBlobUrl(null); }
    localStorage.removeItem("ai_academic_review_config");
    setIsConfigured(false);
    setApiKey("");
    setBaseUrl(DEFAULT_QWEN_BASE_URL);
    setModelName(DEFAULT_QWEN_MODEL);
    setFile(null);
    setPhase("idle");
    setLogs([]);
    setEngineResults(null);
    setRetryCount(0);
    setHasError(false);
    setShowFeedbackModal(false);
    setPremiumToast(null);
    setReviewSessionKey(Date.now().toString());
  };

  // ---- safe timer management ----
  const timersRef = useRef([]);
  const reviewingRef = useRef(false);
  const readerRef = useRef(null);

  const clearAllTimers = useCallback(() => {
    timersRef.current.forEach((id) => clearTimeout(id));
    timersRef.current = [];
  }, []);

  const scheduleTimer = useCallback((fn, delay) => {
    const id = setTimeout(() => {
      timersRef.current = timersRef.current.filter((t) => t !== id);
      fn();
    }, delay);
    timersRef.current.push(id);
    return id;
  }, []);

  // Cleanup on unmount
  useEffect(() => {
    return () => {
      clearAllTimers();
      if (pdfBlobUrl) URL.revokeObjectURL(pdfBlobUrl);
    };
  }, [clearAllTimers]);

  const showToast = useCallback((msg) => {
    setToast(msg);
    scheduleTimer(() => setToast(null), 4000);
  }, [scheduleTimer]);

  // ---- Export with premium check ----
  const handleRequestExport = useCallback((type) => {
    // ContentArea already checks isPremium for direct export.
    // This is only called for non-premium users — just show feedback modal.
    setShowFeedbackModal(true);
  }, []);

  const handleUpload = (e) => {
    const f = e.target.files?.[0];
    if (f) {
      // A previous NDJSON reader can otherwise keep publishing stale state
      // while the new document is being mounted.
      try { readerRef.current?.cancel("new upload selected"); } catch {}
      readerRef.current = null;
      reviewingRef.current = false;
      clearAllTimers();
      // Revoke previous blob URL to free memory
      if (pdfBlobUrl) URL.revokeObjectURL(pdfBlobUrl);
      const blobUrl = URL.createObjectURL(f);
      setPdfBlobUrl(blobUrl);
      setFile(f);
      setPhase("idle");
      setLogs([]);
      setEngineResults(null);
      setReviewMetadata(null);
      setRetryCount(0);
      setToast(null);
      setHasError(false);
      setShowFeedbackModal(false);
      setReviewSessionKey(Date.now().toString());
    }
  };

  // v5.1: Use REVIEW_MODES instead of old DOMAIN_MAP
  const getActiveMode = useCallback(() => REVIEW_MODES[activeModeIndex] || REVIEW_MODES[0], [activeModeIndex]);

  const startReview = useCallback(() => {
    if (reviewingRef.current || !file) return;
    const mode = getActiveMode();
    // v5.1: custom mode requires weights summing to 100%
    if (mode.reviewMode === "custom") {
      const total = customWeightsTotal(customWeights);
      if (Math.abs(total - 100) > 0.1) {
        showToast(lang === "zh"
          ? `权重总和必须为 100%，当前为 ${Math.round(total)}%，请调整后再开始审查。`
          : `Weights must sum to 100%. Current: ${Math.round(total)}%.`);
        return;
      }
    }
    reviewingRef.current = true;
    clearAllTimers();
    setPhase("reviewing");
    setLogs([]);
    setEngineResults(null);
    setReviewMetadata(null);
    setHasError(false);
    setShowFeedbackModal(false);

    // --- streaming API call ---
    const doApiCall = async () => {
      try {
        const formData = new FormData();
        formData.append("file", file);
        formData.append("domain", mode.domain || "social_sciences");
        formData.append("review_mode", mode.reviewMode || "preset");
        formData.append("api_key", apiKey);
        formData.append("base_url", baseUrl);
        formData.append("model_name", modelName);
        formData.append("language", lang);
        formData.append("force_refresh", "true");
        // v5.1: send custom weights in custom mode
        if (mode.reviewMode === "custom") {
          const backendWeights = frontendWeightsToBackend(customWeights);
          formData.append("custom_weights", JSON.stringify(backendWeights));
        }

        const res = await fetch(`${API_BASE}/api/review`, {
          method: "POST",
          body: formData,
        });

        if (!res.ok) {
          throw new Error(`HTTP ${res.status}: ${await res.text()}`);
        }

        // --- stream NDJSON with typewriter support ---
        const reader = res.body.getReader();
        readerRef.current = reader;
        const decoder = new TextDecoder();
        let buffer = "";
        let lastAgent = "";

        while (true) {
          const { done, value } = await reader.read();
          if (done) break;
          if (!reviewingRef.current) { reader.cancel(); return; }

          buffer += decoder.decode(value, { stream: true });
          const lines = buffer.split("\n");
          buffer = lines.pop() || "";

          for (const rawLine of lines) {
            const trimmed = rawLine.trim();
            if (!trimmed) continue;
            try {
              const evt = JSON.parse(trimmed);

              if (evt.type === "chunk") {
                // Typewriter: append to current line, or start new line if agent changed
                const label = evt.agent ? `[${evt.agent}] ` : "";
                setLogs((prev) => {
                  let next = [...prev];
                  if (next.length > 0 && lastAgent === evt.agent && next[next.length - 1].startsWith("[")) {
                    // Append chunk to last line (typewriter effect)
                    next[next.length - 1] += evt.chunk;
                  } else {
                    next.push(label + evt.chunk);
                  }
                  return next;
                });
                lastAgent = evt.agent;
              } else if (evt.type === "metadata") {
                // Fast metadata extracted before main review — drive progress theater
                setReviewMetadata(evt.data);
                lastAgent = "";
              } else if (evt.type === "chunk_end") {
                // Finalize typewriter line with score
                setLogs((prev) => [...prev, `  → 得分 ${evt.score}/100`]);
                lastAgent = "";
              } else if (evt.type === "progress" || evt.type === "thinking") {
                const label = evt.agent
                  ? (PROGRESS_AGENT_LABELS[evt.agent] || evt.agent)
                  : (PROGRESS_STAGE_LABELS[evt.stage] || evt.stage || "系统");
                const statusMark = evt.status === "success" ? "✓ " : evt.status === "failed" ? "⚠ " : "";
                const nextMessage = `[${label}] ${statusMark}${evt.message}`;
                setLogs((prev) => upsertProgressLog(prev, evt, nextMessage));
                lastAgent = "";
              } else if (evt.type === "result") {
                console.log("【API 原始返回数据】:", JSON.stringify(evt.data, null, 2));
                const resultData = evt.data;
                const auditSucceeded = resultData.auditPassed === true;
                if (!auditSucceeded || resultData.evaluationStatus !== "success") {
                  setLogs((prev) => [...prev, "!!! ERROR: 最终复核未完整通过，系统已阻止展示未经复核的评价结果，请重新审查"]);
                  setHasError(true);
                  reviewingRef.current = false;
                  showToast(lang === "zh"
                    ? "最终复核未完成，未发布评价结果"
                    : "Final audit incomplete; results were not published");
                  return;
                }
                setEngineResults(resultData);
                setRetryCount(resultData.retryCount ?? 0);
                setLogs((prev) => [...prev, "[报告整理] ✓ 已接收通过最终复核的结果，正在打开结构化报告"]);
                reviewingRef.current = false;
                scheduleTimer(() => setPhase("done"), 800);
                showToast(t.successBanner);
                // Auto-save to history
                if (file) {
                  const mode = REVIEW_MODES[activeModeIndex] || REVIEW_MODES[0];
                  fetch(`${API_BASE}/api/reports`, {
                    method: "POST",
                    headers: { "Content-Type": "application/json" },
                    body: JSON.stringify({
                      filename: file.name,
                      subject: mode.key,
                      weights: customWeights,
                      report_data: resultData,
                    }),
                  })
                    .then((r) => r.json())
                    .then(() => { /* auto-saved */ })
                    .catch((err) => { console.error("[AI学术审查系统] Auto-save failed:", err); });
                }
              } else if (evt.type === "error") {
                const label = evt.agent ? `[${evt.agent}] ` : "";
                const code = evt.code || "";
                const msg = evt.message || "";
                const fullMsg = code ? `!!! ERROR [${code}]: ${label}${msg}` : `!!! ERROR: ${label}${msg}`;
                setLogs((prev) => [...prev, fullMsg]);
                setHasError(true);
                reviewingRef.current = false;
                readerRef.current = null;
                lastAgent = "";
              }
            } catch {
              // skip malformed
            }
          }
        }
      } catch (err) {
        console.error("[AI学术审查系统] API call failed:", err);
        if (!reviewingRef.current) return;
        try { readerRef.current?.cancel(); } catch {}
        readerRef.current = null;
        setLogs((prev) => [...prev, `!!! ERROR: 连接后端失败 — ${String(err.message ?? err).slice(0, 150)}`]);
        setHasError(true);
        reviewingRef.current = false;
        showToast("后端 API 不可达 — 请检查 uvicorn 是否在 8000 端口运行");
      }
    };
    doApiCall();
  }, [t, scheduleTimer, showToast, clearAllTimers, file, activeModeIndex, customWeights, apiKey, baseUrl, modelName, lang, getActiveMode]);

  const resetReview = useCallback(() => {
    clearAllTimers();
    try { readerRef.current?.cancel(); } catch {}
    readerRef.current = null;
    reviewingRef.current = false;
    setPhase("idle");
    setLogs([]);
    setEngineResults(null);
    setReviewMetadata(null);
    setRetryCount(0);
    setToast(null);
    setHasError(false);
    setShowFeedbackModal(false);
    setPremiumToast(null);
    setIsEvidenceMode(false);
    setActiveEvidenceRef(null);
    setReviewSessionKey(Date.now().toString());
  }, [clearAllTimers]);

  const clearAll = useCallback(() => {
    clearAllTimers();
    try { readerRef.current?.cancel(); } catch {}
    readerRef.current = null;
    reviewingRef.current = false;
    if (pdfBlobUrl) { URL.revokeObjectURL(pdfBlobUrl); setPdfBlobUrl(null); }
    setFile(null);
    setPhase("idle");
    setLogs([]);
    setEngineResults(null);
    setReviewMetadata(null);
    setRetryCount(0);
    setToast(null);
    setHasError(false);
    setShowFeedbackModal(false);
    setPremiumToast(null);
    setIsEvidenceMode(false);
    setActiveEvidenceRef(null);
    setReviewSessionKey(Date.now().toString());
  }, [clearAllTimers, pdfBlobUrl]);

  // ---- Save completed report to history ----
  const saveReportToHistory = useCallback(() => {
    if (!engineResults || !file) return;
    const mode = REVIEW_MODES[activeModeIndex] || REVIEW_MODES[0];
    fetch(`${API_BASE}/api/reports`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        filename: file.name,
        subject: mode.key,
        weights: customWeights,
        report_data: engineResults,
      }),
    })
      .then((r) => r.json())
      .then(() => { /* saved */ })
      .catch((err) => {
        console.error("[AI学术审查系统] Failed to save report:", err);
      });
  }, [engineResults, file, activeModeIndex, customWeights]);

  // ---- Clear pipeline cache via API ----
  const handleClearCache = useCallback(() => {
    fetch(`${API_BASE}/api/cache`, { method: "DELETE" })
      .then((r) => r.json())
      .then((data) => {
        showToast(data.message || (lang === "zh" ? "缓存已清除" : "Cache cleared"));
      })
      .catch((err) => {
        console.error("[AI学术审查系统] Cache clear failed:", err);
        showToast(lang === "zh" ? "清除缓存失败" : "Failed to clear cache");
      });
  }, [lang, showToast]);

  // ---- Load a report from history by ID and inject into current view ----
  const handleHistorySelect = useCallback((reportId) => {
    setShowHistory(false);
    setHasError(false);
    setShowFeedbackModal(false);
    setReviewSessionKey(Date.now().toString());
    // Fetch full report from backend
    fetch(`${API_BASE}/api/reports/${reportId}`)
      .then((r) => { if (!r.ok) throw new Error(`HTTP ${r.status}`); return r.json(); })
      .then((data) => {
        // GET /api/reports/:id returns the report body directly; keep compatibility
        // with older deployments that wrapped it in a report_data field.
        const rd = data.report_data || data;
        setEngineResults(rd);
        setRetryCount(rd.retryCount ?? 0);
        setPhase("done");
        setFile({ name: data.filename || "" });
        setLogs([lang === "zh" ? "已加载历史报告" : "History report loaded"]);
        showToast(lang === "zh" ? "已加载历史审查报告" : "History report loaded");
      })
      .catch((err) => {
        console.error("[AI学术审查系统] Failed to load history report:", err);
        showToast(lang === "zh" ? "加载报告失败" : "Failed to load report");
      });
  }, [lang, showToast]);

  // ---- Demo mode: inject mock data ----
  const showDemo = useCallback(() => {
    clearAllTimers();
    reviewingRef.current = false;
    setPhase("done");
    setLogs(["报告已生成."]);
    setEngineResults(MOCK_RESULT);
    setReviewMetadata(null);
    setRetryCount(0);
    setFile(null);
    setHasError(false);
    setShowFeedbackModal(false);
    setPremiumToast(null);
    setReviewSessionKey(Date.now().toString());
    showToast("已加载样例数据，可自由浏览各引擎报告");
  }, [clearAllTimers, showToast]);

  // ---- derived ----
  const engineScores = useMemo(() => {
    if (engineResults?.engines) {
      const s = {};
      ENGINE_KEYS.forEach((k) => {
        const raw = engineResults.engines[k]?.score;
        s[k] = (raw != null && Number.isFinite(Number(raw))) ? Number(raw) : null;
      });
      return s;
    }
    return { methodology: null, logic: null, ethics: null, innovation: null, academic_impact: null };
  }, [engineResults]);

  // v5: 优先使用后端 scoring_policy 计算出的 overallScore；
  // 如果后端未返回（旧版兼容），则回退到本地算术平均。
  const overallScore = useMemo(() => {
    if (engineResults?.overallScore != null && Number.isFinite(Number(engineResults.overallScore))) {
      return Number(engineResults.overallScore);
    }
    // 旧后端兼容：简单算术平均
    const scores = Object.values(engineScores).filter((v) => v != null && Number.isFinite(Number(v)));
    if (scores.length === 0) return null;
    return Math.round(scores.reduce((a, b) => a + Number(b), 0) / scores.length);
  }, [engineResults?.overallScore, engineScores]);
  const biasLevel = engineResults?.biasLevel || ((overallScore ?? 0) >= 75 ? t.moderateLow : t.moderateHigh);

  const radarData = useMemo(() => [
    { subject: t.methodologyShort, score: engineScores.methodology, fullMark: 100 },
    { subject: t.logicShort, score: engineScores.logic, fullMark: 100 },
    { subject: t.ethicsShort, score: engineScores.ethics, fullMark: 100 },
    { subject: t.innovationShort, score: engineScores.innovation, fullMark: 100 },
    { subject: t.academicImpactShort, score: engineScores.academic_impact, fullMark: 100 },
  ], [lang, engineScores]);

  const domainTabs = [t.tab0, t.tab1, t.tab2];
  const domainFocuses = [t.focus0, t.focus1, t.focus2];

  const engineMeta = useMemo(() => {
    const api = engineResults?.engines || {};
    const mk = (key, defs) => ({
      name: defs.name, focus: defs.focus,
      conclusion: api[key]?.core_conclusion || defs.conclusion,
      evidence: api[key]?.evidence || defs.evidence,
      advice: api[key]?.actionable_advice || defs.advice,
      // Enriched fields from new backend
      riskLevel: api[key]?.risk_level || null,
      confidence: api[key]?.confidence ?? null,
      strengths: api[key]?.strengths || [],
      issues: api[key]?.issues || [],
      reasoningMd: api[key]?.reasoning_md || "",
      limitations: api[key]?.limitations || [],
      journalRecommendation: api[key]?.journal_recommendation || null,
      // v5 新增业务字段 — 带 Fallback，旧后端数据不会导致崩溃
      // v5.0: 使用 asPlainObject 排除数组类型的误入
      biasExplanation: asPlainObject(api[key]?.bias_explanation || api[key]?.biasExplanation),
      missingLiterature: Array.isArray(api[key]?.missing_literature || api[key]?.missingLiterature)
        ? (api[key].missing_literature || api[key].missingLiterature) : [],
      alternativeTheories: Array.isArray(api[key]?.alternative_theories || api[key]?.alternativeTheories)
        ? (api[key].alternative_theories || api[key].alternativeTheories) : [],
      logicCorrectionPlan: asPlainObject(api[key]?.logic_correction_plan || api[key]?.logicCorrectionPlan),
      researchProfile: asPlainObject(api[key]?.research_profile || api[key]?.researchProfile),
      // v5.0: 影响力证据（仅 academic_impact）
      impactEvidence: asPlainObject(api[key]?.impact_evidence || api[key]?.impactEvidence),
    });
    return {
      methodology: mk("methodology", { name: t.methodologyName, focus: t.methFocus, conclusion: t.methConclusion, evidence: t.methEvidence, advice: t.methAdvice }),
      logic: mk("logic", { name: t.logicName, focus: t.logicFocus, conclusion: t.logicConclusion, evidence: t.logicEvidence, advice: t.logicAdvice }),
      ethics: mk("ethics", { name: t.ethicsName, focus: t.ethicsFocus, conclusion: t.ethicsConclusion, evidence: t.ethicsEvidence, advice: t.ethicsAdvice }),
      innovation: mk("innovation", { name: t.innovationName, focus: t.innovationFocus, conclusion: t.innovationConclusion, evidence: t.innovationEvidence, advice: t.innovationAdvice }),
      academic_impact: mk("academic_impact", { name: t.academicImpactName, focus: t.academicImpactFocus, conclusion: t.academicImpactConclusion, evidence: t.academicImpactEvidence, advice: t.academicImpactAdvice }),
    };
  }, [lang, engineResults]);

  const handleEvidenceClick = useCallback((location) => {
    rememberReportScroll();
    setIsEvidenceMode(true);
    setActiveEvidenceRef(location);
    setPdfViewerKey((n) => n + 1);
    restoreReportScroll();
  }, [rememberReportScroll, restoreReportScroll]);

  const handleCloseEvidence = useCallback(() => {
    rememberReportScroll();
    setIsEvidenceMode(false);
    setActiveEvidenceRef(null);
    restoreReportScroll();
  }, [rememberReportScroll, restoreReportScroll]);

  // ---- download Markdown report ----
  const downloadMarkdown = useCallback(async () => {
    if (!engineResults) return;
    try {
      await new Promise((r) => setTimeout(r, 50));

      // Capture radar chart as base64 image (optional, skip on failure)
      let chartMarkdown = "";
      const chartElement = document.getElementById("radar-chart-container");
      if (chartElement) {
        try {
          const canvas = await html2canvas(chartElement, { scale: 2, backgroundColor: "#ffffff", logging: false });
          const imgData = canvas.toDataURL("image/png");
          chartMarkdown = `## 综合审查雷达图\n\n![雷达图](${imgData})\n\n---\n\n`;
        } catch (e) {
          console.warn("[AI学术审查系统] Radar chart capture for markdown failed:", e);
        }
      }

      const textMd = generateMarkdownReport({ engineMeta, engineScores, overallScore, biasLevel, retryCount, scoringPolicy: engineResults?.scoringPolicy || null });
      const finalMd = chartMarkdown + textMd;
      // UTF-8 BOM prefix so Windows Notepad / default editors recognise encoding
      const bom = "﻿";
      const blob = new Blob([bom + finalMd], { type: "text/markdown;charset=utf-8" });
      const url = URL.createObjectURL(blob);
      const a = document.createElement("a");
      a.href = url;
      a.download = "AI学术审查系统_Review_Report.md";
      document.body.appendChild(a);
      a.click();
      document.body.removeChild(a);
      URL.revokeObjectURL(url);
    } catch (err) {
      console.error("[AI学术审查系统] Markdown export failed:", err);
    }
  }, [engineResults, engineMeta, engineScores, overallScore, biasLevel, retryCount]);

  const handleMouseMove = (e) => {
    // Updating CSS variables directly avoids re-rendering the whole report on every mouse move.
    e.currentTarget.style.setProperty("--mouse-x", `${e.clientX}px`);
    e.currentTarget.style.setProperty("--mouse-y", `${e.clientY}px`);
  };
  const versionMismatch = backendVersion != null && backendVersion !== APP_VERSION;

  // ---- render ----
  return (
    <div
      className="app-shell notranslate h-screen flex overflow-hidden relative"
      translate="no"
      style={{ "--mouse-x": "50%", "--mouse-y": "50%" }}
      onMouseMove={isEvidenceMode ? undefined : handleMouseMove}
    >
      {/* Grid magnifier layers */}
      {!isEvidenceMode && (
        <>
          <div className="grid-base" />
          <div className="grid-magnifier" />
        </>
      )}

      {/* ============ CONFIG SCREEN ============ */}
      {!isConfigured && (
        <div className="fixed inset-0 z-[200] bg-white flex items-center justify-center">
          <div className="w-full max-w-md mx-4 bg-white border border-slate-200 rounded-2xl p-8 shadow-xl">
            <h2 className="text-xl font-bold text-slate-800 mb-1">{t.appTitle}</h2>
            <p className="text-sm text-slate-500 mb-6">{t.configTitle}</p>

            <label className="block mb-4">
              <span className="text-xs font-semibold text-slate-500 uppercase">{t.configApiKey}</span>
              <input
                type="password"
                value={apiKey}
                onChange={(e) => setApiKey(e.target.value)}
                placeholder="sk-..."
                className="w-full mt-1 px-3 py-2 border border-slate-200 rounded-lg text-sm focus:outline-none focus:border-blue-400"
              />
              <p className="text-xs text-slate-400 mt-1.5 leading-relaxed">
                {lang === "zh"
                  ? "支持任何 OpenAI 兼容 API。若使用网络代理，请确保当前 API 地址能够稳定访问。"
                  : "Any OpenAI-compatible API is supported. If a proxy is enabled, make sure the configured endpoint remains reachable."}
              </p>
            </label>

            <label className="block mb-4">
              <span className="text-xs font-semibold text-slate-500 uppercase">{t.configBaseUrl}</span>
              <input
                type="text"
                value={baseUrl}
                onChange={(e) => setBaseUrl(e.target.value)}
                placeholder={DEFAULT_QWEN_BASE_URL}
                className="w-full mt-1 px-3 py-2 border border-slate-200 rounded-lg text-sm focus:outline-none focus:border-blue-400"
              />
            </label>

            <label className="block mb-6">
              <span className="text-xs font-semibold text-slate-500 uppercase">{t.configModel}</span>
              <input
                type="text"
                value={modelName}
                onChange={(e) => setModelName(e.target.value)}
                placeholder={DEFAULT_QWEN_MODEL}
                className="w-full mt-1 px-3 py-2 border border-slate-200 rounded-lg text-sm focus:outline-none focus:border-blue-400"
              />
            </label>

            <button
              onClick={saveConfig}
              disabled={!apiKey.trim() || !baseUrl.trim() || !modelName.trim()}
              className="w-full bg-blue-600 text-white font-semibold py-2.5 rounded-xl hover:bg-blue-700 disabled:opacity-40 disabled:cursor-not-allowed transition-colors text-sm"
            >
              {t.configSaved}
            </button>

            <p className="text-[11px] text-slate-400 mt-4 text-center">
              {t.configHint}
            </p>
          </div>
        </div>
      )}

      {/* Regular App UI (only when configured) */}
      {isConfigured && (<>

      {versionMismatch && (
        <div className="fixed top-4 left-1/2 -translate-x-1/2 z-[160] bg-red-50 border border-red-300 text-red-800 px-4 py-2.5 rounded-xl shadow-lg text-sm font-medium no-print">
          {lang === "zh"
            ? `版本不一致：前端 v${APP_VERSION} / 后端 v${backendVersion}。请关闭旧服务并重新运行一键启动脚本。`
            : `Version mismatch: frontend v${APP_VERSION} / backend v${backendVersion}. Restart the local service.`}
        </div>
      )}

      {/* Toast */}
      {toast && (
        <div className="fixed top-5 right-5 z-[100] flex items-center gap-2 bg-amber-50 border border-amber-200 text-amber-800 px-4 py-3 rounded-xl shadow-lg no-print">
          <AlertCircle size={18} />
          <span className="text-sm font-medium">{toast}</span>
        </div>
      )}

      {/* ============ EVIDENCE MODE: Split Panel Layout ============ */}
      {isEvidenceMode ? (
        <>
          {toast && (
            <div className="fixed top-5 right-5 z-[100] flex items-center gap-2 bg-amber-50 border border-amber-200 text-amber-800 px-4 py-3 rounded-xl shadow-lg no-print">
              <AlertCircle size={18} />
              <span className="text-sm font-medium">{toast}</span>
            </div>
          )}
          <div className="evidence-mode" style={{ display: "flex", width: "100vw", height: "100vh", overflow: "hidden" }}>
            {/* Left: Report Panel (60%) */}
            <div ref={reportPanelRef} className="report-panel" style={{
              width: "60%", height: "100vh", overflowY: "auto", filter: "none", opacity: 1, pointerEvents: "auto",
              background: "#fff", borderRight: "1px solid #cbd5e1",
            }}>
              {/* Header */}
              <div className="shrink-0 px-4 pt-4 pb-2 no-print">
                <h1 className="text-xl font-bold text-slate-800 tracking-tight">{t.appTitle}</h1>
                <p className="text-xs text-slate-500 mt-0.5">{t.appSubtitle}</p>
              </div>
              {/* Domain Tabs — v5.1 four modes */}
              <div className="shrink-0 px-4 pb-2 no-print">
                <div className="flex gap-1.5 flex-wrap">
                  {REVIEW_MODES.map((mode, i) => {
                    const theme = DOMAIN_THEMES[i] || DOMAIN_THEMES[0];
                    return (
                      <button key={mode.key} onClick={() => setActiveModeIndex(i)}
                        className={`px-3 py-1 text-[11px] font-medium rounded-lg border transition-all duration-200 ${
                          i === activeModeIndex
                            ? `${theme.tab} text-white shadow-sm font-bold`
                            : "bg-white text-slate-600 border-slate-200 hover:border-slate-300"
                        }`}>
                        {lang === "zh" ? mode.labelZh : mode.labelEn}
                      </button>
                    );
                  })}
                </div>
              </div>
              {/* ContentArea with evidence support */}
              <div className="flex-1 px-4 pb-10">
                <ContentArea
                  key={reviewSessionKey}
                  t={t} lang={lang} phase={phase} file={file} logs={logs}
                  retryCount={retryCount} overallScore={overallScore} biasLevel={biasLevel}
                  radarData={radarData} engineMeta={engineMeta} engineScores={engineScores}
                  onStart={startReview} onReset={resetReview} hasError={hasError}
                  onDownload={downloadMarkdown}
                  onAppeal={(key, name) => setAppealModal({ engineKey: key, engineName: name })}
                  domainTheme={DOMAIN_THEMES[activeModeIndex]}
                  isPremium={isPremium} onRequestExport={handleRequestExport}
                  feedbackClosedVersion={feedbackClosedVersion}
                  evaluationStatus={engineResults?.evaluationStatus}
                  auditPassed={engineResults?.auditPassed}
                  systemLimitations={engineResults?.limitations || []}
                  engineResults={engineResults}
                  onEvidenceClick={handleEvidenceClick}
                />
                <div className="text-center mt-10 pb-10 no-print">
                  <p className="text-[9px] text-slate-300">{t.privacyFooter}</p>
                </div>
              </div>
            </div>
            {/* Right: PDF Evidence Panel (40%) */}
            <PdfEvidencePanel
              evidenceRef={activeEvidenceRef}
              pdfBlobUrl={pdfBlobUrl}
              viewerKey={pdfViewerKey}
              onClose={handleCloseEvidence}
              lang={lang}
            />
          </div>
        </>
      ) : (
        <>
      {/* ============ SIDEBAR ============ */}
      <aside className="surface-opaque w-64 border-r border-slate-200 flex flex-col h-screen overflow-y-auto p-5 gap-5 shrink-0 relative z-10 no-print">
        {/* Brand */}
        <div className="flex items-center justify-between shrink-0">
          <div>
            <h2 className="text-lg font-bold text-slate-800 tracking-tight">{t.brand}</h2>
            <p className="text-[10px] text-slate-400 mt-0.5">{t.version}</p>
          </div>
          <button
            onClick={() => setLang((l) => (l === "zh" ? "en" : "zh"))}
            className="flex items-center gap-1 text-[10px] font-medium text-slate-400 bg-slate-100 hover:bg-slate-200 px-2 py-1 rounded-md transition-colors"
          >
            <Languages size={12} />
            {lang === "zh" ? "En" : "中"}
          </button>
        </div>
        <hr className="border-slate-100 shrink-0" />

        {/* Navigation menu */}
        <nav className="flex flex-col gap-1 shrink-0">
          <button
            onClick={clearAll}
            className={`flex items-center gap-2.5 px-3 py-2 text-sm rounded-lg transition-colors font-medium ${
              phase === "idle" ? "bg-blue-50 text-blue-700" : "text-slate-600 hover:bg-slate-50"
            }`}
          >
            <Plus size={16} />
            {t.newReview}
          </button>
          <button
            onClick={() => setShowHistory(true)}
            className="flex items-center gap-2.5 px-3 py-2 text-sm rounded-lg text-slate-500 hover:bg-slate-50 transition-colors"
          >
            <History size={16} />
            {t.history}
          </button>
          <button
            onClick={() => setShowSettings(true)}
            className="flex items-center gap-2.5 px-3 py-2 text-sm rounded-lg text-slate-500 hover:bg-slate-50 transition-colors"
          >
            <Settings size={16} />
            {lang === "zh" ? "设置" : "Settings"}
          </button>
        </nav>

        <hr className="border-slate-100 shrink-0" />

        {/* Upload area */}
        <div className="shrink-0">
          <p className="text-[10px] font-semibold text-slate-400 uppercase tracking-wider mb-2.5">{t.uploadManuscript}</p>
          <label className="surface-opaque flex flex-col items-center gap-2 p-4 border-2 border-dashed border-slate-200 rounded-xl cursor-pointer hover:border-blue-400 hover:bg-blue-50 transition-colors">
            <Upload size={20} className="text-slate-400" />
            <span className="text-[11px] text-slate-500 text-center leading-tight">
              {file ? file.name : t.uploadHint}
            </span>
            <input type="file" accept=".pdf,.docx,.txt" onChange={handleUpload} className="hidden" />
          </label>
          {/* Demo button */}
          <button
            onClick={showDemo}
            className="w-full mt-2 flex items-center justify-center gap-1.5 text-[11px] text-slate-500 bg-slate-50 hover:bg-slate-100 border border-slate-200 rounded-lg py-2 transition-colors"
          >
            <Eye size={13} />
            {t.demoButton}
          </button>
          {file && (
            <button onClick={clearAll} className="w-full mt-2 flex items-center justify-center gap-1.5 text-[11px] text-slate-400 hover:text-red-500 transition-colors">
              <X size={13} /> {t.clearReset}
            </button>
          )}
        </div>

        {/* Spacer */}
        <div className="flex-1 min-h-0" />

        {/* Dev Mode */}
        <div className="flex flex-col gap-2 shrink-0">
          {/* Ops mode toggle — disabled during review */}
          <label className={`flex items-center justify-between px-2 py-2 rounded-lg transition-colors ${
            phase === "reviewing"
              ? "cursor-not-allowed opacity-50"
              : "cursor-pointer hover:bg-slate-50"
          }`}>
            <span className="text-xs text-slate-500 flex items-center gap-1.5 font-medium">
              <Wrench size={13} />
              {t.devMode}
            </span>
            <div className={`relative w-10 h-5 rounded-full transition-colors scale-110 ${
              phase === "reviewing"
                ? "bg-slate-300"
                : devMode ? "bg-amber-500" : "bg-slate-300"
            }`}>
              <div className={`absolute top-0.5 w-4 h-4 rounded-full bg-white shadow transition-transform ${devMode ? "translate-x-5" : "translate-x-0.5"}`} />
            </div>
            <input
              type="checkbox"
              checked={devMode}
              onChange={(e) => setDevMode(e.target.checked)}
              disabled={phase === "reviewing"}
              className="hidden"
            />
          </label>

          {/* ============ DEV MODE (v5.1: auto custom mode with weight adjuster) ============ */}
          {devMode && (() => {
            const mode = REVIEW_MODES[activeModeIndex] || REVIEW_MODES[0];
            const isCustom = mode.reviewMode === "custom";
            const totalPct = customWeightsTotal(customWeights);
            const totalOk = Math.abs(totalPct - 100) <= 0.1;
            const hasReviewResults = engineResults?.scoringPolicy?.weights;
            // When review is done, show backend-policy info for presets, or user-custom info
            const displayWeights = hasReviewResults && !isCustom
              ? backendWeightsToPercent(engineResults.scoringPolicy) || {}
              : {};
            const displayLocked = hasReviewResults && !isCustom && (engineResults?.scoringPolicy?.locked === true);
            return (
            <div className="border-t border-slate-200 pt-3 mt-1 space-y-3 dev-panel-enter">
              {/* Preset mode + review done: show backend locked weights */}
              {displayLocked && (
                <div className="flex items-center gap-1.5 bg-blue-50 border border-blue-200 rounded-lg px-2.5 py-1.5 text-[10px] text-blue-700">
                  <Lock size={10} />
                  <span>
                    {lang === "zh"
                      ? `后端锁定权重：${engineResults?.scoringPolicy?.policy_label || engineResults?.scoringPolicy?.policy || ""}`
                      : `Backend locked: ${engineResults?.scoringPolicy?.policy_label || engineResults?.scoringPolicy?.policy || ""}`}
                  </span>
                </div>
              )}
              {/* Custom mode: user-editable sliders */}
              {isCustom && (
                <div className="flex items-center gap-1.5 bg-purple-50 border border-purple-200 rounded-lg px-2.5 py-1.5 text-[10px] text-purple-700">
                  <Unlock size={10} />
                  <span>{lang === "zh" ? "自定义权重 — 拖动滑块调整各维度权重比例" : "Custom weights — drag sliders to adjust"}</span>
                </div>
              )}
              <p className="text-[10px] font-semibold text-slate-400 uppercase tracking-wider">
                {lang === "zh" ? "引擎权重调节" : "Engine Weights"}
              </p>

              {ENGINE_KEYS.map((key) => {
                const meta = ENGINE_WEIGHT_COLORS[key];
                const val = isCustom
                  ? Math.round(customWeights[key] ?? 0)
                  : (Object.keys(displayWeights).length > 0 ? (displayWeights[key] ?? 0) : Math.round((mode.weights?.[key]) ?? 20));
                const disabled = !isCustom;
                return (
                  <div key={key} className="space-y-1">
                    <div className="flex justify-between items-center">
                      <span className={`text-[11px] font-medium ${disabled ? "text-slate-400" : "text-slate-600"}`}>
                        {lang === "zh" ? meta.label : meta.labelEn}
                      </span>
                      <span className={`text-[11px] font-mono font-bold`} style={disabled ? { color: "#94a3b8" } : { color: meta.fill }}>
                        {val}%
                      </span>
                    </div>
                    <input
                      type="range"
                      min="0"
                      max="100"
                      value={val}
                      disabled={disabled}
                      onChange={(e) => {
                        if (!isCustom) return;
                        const newVal = Math.round(Number(e.target.value));
                        setCustomWeights((prev) => ({ ...prev, [key]: newVal }));
                      }}
                      className={`weight-slider w-full h-1.5 rounded-full appearance-none ${disabled ? "cursor-not-allowed opacity-40" : "cursor-pointer"}`}
                      style={{
                        background: disabled
                          ? `#e2e8f0`
                          : `linear-gradient(to right, ${meta.fill} 0%, ${meta.fill} ${val}%, #e2e8f0 ${val}%, #e2e8f0 100%)`,
                      }}
                    />
                  </div>
                );
              })}

              {/* Custom mode: total + actions */}
              {isCustom && (
                <div className="space-y-1.5">
                  <div className={`text-[10px] font-semibold ${totalOk ? "text-emerald-600" : "text-red-500"}`}>
                    {lang === "zh" ? "当前总和" : "Current total"}：{Math.round(totalPct)}% {totalOk ? "✅" : "❌"}
                  </div>
                  {!totalOk && (
                    <div className="text-[9px] text-red-500">
                      {lang === "zh" ? "权重总和必须为 100%，请调整后再开始审查。" : "Weights must sum to 100%. Please adjust before starting review."}
                    </div>
                  )}
                  <div className="flex gap-2">
                    <button
                      type="button"
                      onClick={() => {
                        // Normalize to 100% proportionally
                        const current = { ...customWeights };
                        const sum = ENGINE_KEYS.reduce((s, k) => s + (current[k] || 0), 0);
                        if (sum > 0) {
                          const normalized = {};
                          ENGINE_KEYS.forEach((k) => { normalized[k] = Math.round((current[k] || 0) / sum * 100); });
                          setCustomWeights(normalized);
                        }
                      }}
                      className="text-[10px] px-2 py-1 rounded bg-purple-100 text-purple-700 hover:bg-purple-200 transition-colors"
                    >
                      {lang === "zh" ? "一键归一化" : "Normalize to 100%"}
                    </button>
                    <button
                      type="button"
                      onClick={() => {
                        const def = mode.defaultCustomWeights || { methodology: 25, logic: 20, ethics: 20, innovation: 20, academic_impact: 15 };
                        setCustomWeights({ ...def });
                      }}
                      className="text-[10px] px-2 py-1 rounded bg-slate-100 text-slate-600 hover:bg-slate-200 transition-colors"
                    >
                      {lang === "zh" ? "恢复默认" : "Restore defaults"}
                    </button>
                  </div>
                </div>
              )}

              {/* Donut Chart — v5.1: use customWeights or mode.weights */}
              <div className="flex justify-center pt-1">
                {(() => {
                  const chartData = ENGINE_KEYS.map((k) => {
                    const displayVal = isCustom ? (customWeights[k] ?? 0) : ((mode.weights?.[k]) ?? 20);
                    return {
                      name: lang === "zh" ? ENGINE_WEIGHT_COLORS[k].label : ENGINE_WEIGHT_COLORS[k].labelEn,
                      value: Math.round(displayVal),
                    };
                  });
                  return (
                <PieChart width={150} height={150}>
                  <Pie
                    data={chartData}
                    cx={75}
                    cy={75}
                    innerRadius={40}
                    outerRadius={62}
                    paddingAngle={2}
                    dataKey="value"
                    stroke="none"
                    isAnimationActive={false}
                  >
                    {ENGINE_KEYS.map((k) => (
                      <Cell key={k} fill={ENGINE_WEIGHT_COLORS[k].fill} />
                    ))}
                  </Pie>
                  <RechartsTooltip
                    formatter={(value) => `${Math.round(value)}%`}
                    contentStyle={{
                      fontSize: "11px",
                      borderRadius: "8px",
                      border: "1px solid #e2e8f0",
                      padding: "4px 8px",
                    }}
                  />
                </PieChart>
                  );
                })()}
              </div>

              {/* v5.1: devMode journal recommendation test injection */}
              <div className="border-t border-slate-200 pt-2">
                <p className="text-[9px] font-semibold text-slate-400 uppercase mb-1">
                  {lang === "zh" ? "期刊推荐测试" : "Journal Test"}
                </p>
                <div className="flex gap-1.5">
                  <button
                    type="button"
                    onClick={() => {
                      if (!engineResults) return;
                      setEngineResults(prev => ({
                        ...prev,
                        engines: {
                          ...prev.engines,
                          academic_impact: {
                            ...(prev.engines?.academic_impact || {}),
                            journal_recommendations: MOCK_JOURNAL_RECOMMENDATIONS,
                          },
                        },
                      }));
                      showToast(lang === "zh" ? "已注入模拟期刊推荐数据" : "Mock journal data injected");
                    }}
                    className="text-[9px] px-2 py-1 rounded bg-purple-100 text-purple-700 hover:bg-purple-200 transition-colors"
                  >
                    {lang === "zh" ? "[测试] 注入期刊推荐" : "[Test] Inject Journals"}
                  </button>
                  <button
                    type="button"
                    onClick={() => {
                      if (!engineResults) return;
                      setEngineResults(prev => ({
                        ...prev,
                        engines: {
                          ...prev.engines,
                          academic_impact: {
                            ...(prev.engines?.academic_impact || {}),
                            journal_recommendations: [],
                          },
                        },
                      }));
                      showToast(lang === "zh" ? "已清除模拟期刊推荐数据" : "Mock journal data cleared");
                    }}
                    className="text-[9px] px-2 py-1 rounded bg-slate-100 text-slate-600 hover:bg-slate-200 transition-colors"
                  >
                    {lang === "zh" ? "[测试] 清除期刊推荐" : "[Test] Clear Journals"}
                  </button>
                </div>
              </div>

              {/* Live logs when reviewing */}
              {phase === "reviewing" && (
                <div>
                  <p className="text-[9px] font-semibold text-slate-400 uppercase mb-1">
                    {lang === "zh" ? "实时日志" : "Live Log"}
                  </p>
                  <div className="font-mono text-[10px] text-slate-500 max-h-[160px] overflow-y-auto space-y-0.5 bg-slate-50 rounded-md p-1.5">
                    {(logs ?? []).slice(-10).map((msg, i) => (
                      <div
                        key={msg && typeof msg === "object" ? msg.id : i}
                        className="whitespace-pre-wrap leading-tight break-all"
                      >
                        {getLogText(msg).slice(0, 100)}
                      </div>
                    ))}
                  </div>
                </div>
              )}
            </div>
          )})()}
        </div>
      </aside>

      {/* ============ MAIN ============ */}
      <div className="flex-1 flex flex-col overflow-hidden relative z-10">
        {/* Header */}
        <div className="shrink-0 px-8 pt-8 pb-4 no-print">
          <h1 className="text-3xl font-bold text-slate-800 tracking-tight">{t.appTitle}</h1>
          <p className="text-sm text-slate-500 mt-1">{t.appSubtitle}</p>
        </div>

        {/* Domain Tabs — v5.1 four modes */}
        <div className="shrink-0 px-8 pb-4 no-print">
          <div className="flex gap-2">
            {REVIEW_MODES.map((mode, i) => {
              const theme = DOMAIN_THEMES[i] || DOMAIN_THEMES[0];
              const modeLabel = lang === "zh" ? mode.labelZh : mode.labelEn;
              return (
                <button
                  key={mode.key}
                  onClick={() => setActiveModeIndex(i)}
                  className={`px-4 py-2 text-xs font-medium rounded-lg border transition-all duration-200 ${
                    i === activeModeIndex
                      ? `${theme.tab} text-white shadow-md font-bold`
                      : "bg-white text-slate-600 border-slate-200 hover:border-slate-300 hover:shadow-sm"
                  }`}
                >
                  {modeLabel}
                </button>
              );
            })}
          </div>
          <p className={`text-xs mt-3 rounded-lg p-3 border ${
            (DOMAIN_THEMES[activeModeIndex] || DOMAIN_THEMES[0]).bg
          } ${(DOMAIN_THEMES[activeModeIndex] || DOMAIN_THEMES[0]).card}`}>
            {phase !== "done" && (
              <><strong>{lang === "zh" ? "审查模式" : "Review Mode"}</strong>：{lang === "zh" ? (REVIEW_MODES[activeModeIndex] || REVIEW_MODES[0]).description : ((REVIEW_MODES[activeModeIndex] || REVIEW_MODES[0]).description.replace("系统预设权重", "System preset weights").replace("后端锁定", "backend-locked"))}</>
            )}
            {phase === "done" && (
              <><strong>{lang === "zh" ? "审查完成" : "Review complete"}</strong> — {lang === "zh" ? "以下为各引擎审查结果详情" : "Detailed engine reports below"}</>
            )}
          </p>
        </div>

        {/* Content area */}
        <div className={phase === "reviewing"
          ? "flex-1 min-h-0 overflow-hidden px-8 flex flex-col"
          : "flex-1 overflow-y-auto px-8 pb-14"}>
          <ContentArea
            key={reviewSessionKey}
            t={t}
            lang={lang}
            phase={phase}
            file={file}
            logs={logs}
            retryCount={retryCount}
            overallScore={overallScore}
            biasLevel={biasLevel}
            radarData={radarData}
            engineMeta={engineMeta}
            engineScores={engineScores}
            onStart={startReview}
            onReset={resetReview}
            hasError={hasError}
            onDownload={downloadMarkdown}
            onAppeal={(key, name) => setAppealModal({ engineKey: key, engineName: name })}
            domainTheme={DOMAIN_THEMES[activeModeIndex]}
            isPremium={isPremium}
            onRequestExport={handleRequestExport}
            feedbackClosedVersion={feedbackClosedVersion}
            evaluationStatus={engineResults?.evaluationStatus}
            auditPassed={engineResults?.auditPassed}
            systemLimitations={engineResults?.limitations || []}
            engineResults={engineResults}
            onEvidenceClick={handleEvidenceClick}
          />
          {/* Privacy Footer */}
          <div className={phase === "reviewing"
            ? "shrink-0 text-center py-3 no-print"
            : "text-center mt-12 pb-12 no-print"}>
            <p className="text-[11px] text-slate-300">{t.privacyFooter}</p>
          </div>
        </div>

        {/* ============ APPEAL MODAL ============ */}
        <AppealModal
          engineName={appealModal?.engineName ?? ""}
          isOpen={!!appealModal}
          onClose={() => setAppealModal(null)}
          t={t}
        />

        {/* ============ HISTORY MODAL ============ */}
        <HistoryModal
          isOpen={showHistory}
          onClose={() => setShowHistory(false)}
          t={t}
          lang={lang}
          onSelect={handleHistorySelect}
        />

        {/* ============ SETTINGS MODAL ============ */}
        <SettingsModal
          isOpen={showSettings}
          onClose={() => setShowSettings(false)}
          t={t}
          lang={lang}
          apiKey={apiKey}
          baseUrl={baseUrl}
          modelName={modelName}
          onSave={saveConfig}
          onApiKeyChange={setApiKey}
          onBaseUrlChange={setBaseUrl}
          onModelNameChange={setModelName}
          onClearCache={handleClearCache}
        />

        {/* ============ FEEDBACK MODAL ============ */}
        <FeedbackModal
          isOpen={showFeedbackModal}
          onSkip={() => {
            setShowFeedbackModal(false);
            setFeedbackClosedVersion((v) => v + 1);
          }}
          onSubmit={(token, unlocked) => {
            setShowFeedbackModal(false);
            const unlockedId = engineResults?.reportId || null;
            if (token && unlocked && unlockedId) {
              localStorage.setItem("ai_academic_review_entitlement", JSON.stringify({
                token,
                reportId: unlockedId,
              }));
              setUnlockedReportId(unlockedId);
              setPremiumToast(t.premiumUnlocked);
              scheduleTimer(() => setPremiumToast(null), 5000);
            }
            setFeedbackClosedVersion((v) => v + 1);
          }}
          lang={lang}
          reportId={engineResults?.reportId}
        />

        {/* ============ PREMIUM TOAST ============ */}
        {premiumToast && (
          <div className="fixed top-5 right-5 z-[110] flex items-center gap-2 bg-emerald-50 border border-emerald-200 text-emerald-800 px-4 py-3 rounded-xl shadow-lg no-print animate-bounce">
            <CheckCircle size={18} />
            <span className="text-sm font-medium">{premiumToast}</span>
          </div>
        )}

      </div>

      {/* ============ FIXED BOTTOM BAR ============ */}
      {file && phase === "idle" && (
        <div className="fixed bottom-5 left-1/2 -translate-x-1/2 w-[80%] max-w-[800px] z-50 no-print">
          <div className="surface-opaque border border-blue-200 rounded-2xl p-5 shadow-[0_-10px_40px_rgba(37,99,235,0.12)] hover:shadow-[0_-15px_50px_rgba(37,99,235,0.2)] hover:-translate-y-1 transition-all duration-300">
            <div className="flex items-center gap-4">
              <div className="flex-1 text-sm text-slate-700">
                <p className="font-semibold">{file.name}</p>
                <p className="text-xs text-slate-400 mt-0.5">{t.readyForReview}</p>
              </div>
              <button
                onClick={startReview}
                disabled={phase !== "idle"}
                className="flex items-center gap-2 bg-blue-600 text-white font-semibold px-6 py-3 rounded-xl hover:bg-blue-700 hover:scale-[1.02] hover:shadow-lg hover:shadow-blue-600/25 transition-all duration-200 text-sm disabled:opacity-50 disabled:cursor-not-allowed"
              >
                <Zap size={16} />
                {t.startReview}
              </button>
            </div>
          </div>
        </div>
      )}

      {phase === "done" && (
        <div className="fixed bottom-5 left-1/2 -translate-x-1/2 w-[80%] max-w-[800px] z-50 no-print">
          <div className="surface-opaque border border-emerald-200 rounded-2xl p-5 shadow-[0_-10px_40px_rgba(16,185,129,0.12)]">
            <div className="flex items-center gap-4">
              <CheckCircle size={20} className="text-emerald-600" />
              <div className="flex-1">
                <p className="text-sm font-semibold text-slate-800">{t.reviewComplete}</p>
                <p className="text-xs text-slate-400">
                  {t.compositeScore}: {typeof overallScore === "number" ? overallScore.toFixed(1) : "0.0"}/100 · {t.postArbitration.replace("{n}", String(retryCount ?? 0))}
                </p>
              </div>
              <button
                onClick={resetReview}
                className="flex items-center gap-2 bg-slate-100 text-slate-700 font-medium px-5 py-2.5 rounded-xl hover:bg-slate-200 transition-colors text-sm"
              >
                <RefreshCw size={16} />
                {t.reRunReview}
              </button>
            </div>
          </div>
        </div>
      )}

      {/* Close evidence mode else-branch fragment and ternary */}
      </>
      )}

      {/* Close isConfigured fragment */}
      </>
      )}

      {/* Close isEvidenceMode + isConfigured */}
    </div>
  );
}

// ============================================================
// App root — BrowserRouter with routes
// ============================================================
export default function App() {
  return (
    <ErrorBoundary>
      <BrowserRouter>
        <Routes>
          <Route path="*" element={<InnerApp />} />
        </Routes>
      </BrowserRouter>
    </ErrorBoundary>
  );
}
