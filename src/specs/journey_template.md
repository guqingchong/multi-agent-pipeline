
# 用户旅程设计模板

> 专业标准参考：NNGroup Journey Mapping 101, Smashing Magazine CJM, Service Design Doing

---

## 使用说明

此模板定义了 pipeline journey phase 的输出标准。
LLM Agent 在 journey phase 必须基于此模板生成 `specs/journey.md`。

**强制要求**：
1. 至少 5 个标准阶段（Awareness→Consideration→Decision→Retention→Advocacy）
2. 每阶段 ≥2 个触点，每个触点标注情感变化（1-5）和渠道
3. 异常路径覆盖率 ≥80%（每阶段至少 1 条异常路径）
4. 至少 1 个触点标记为关键触点（is_critical=true，失败=用户流失）
5. 用户画像完整（姓名、角色、目标、痛点、技术水平）

**可选增强**：
- JTBD（Jobs to Be Done）陈述
- 服务蓝图（frontstage/backstage/support）
- 无障碍需求标注

---

## 标准阶段定义（AIDRA+ 模型）

| 阶段 | 英文 | 用户问题 | 核心指标 |
|------|------|---------|---------|
| 认知 | Awareness | "这是什么？能解决我的问题吗？" | 到达率、跳出率 |
| 考虑 | Consideration | "它比竞品好在哪？" | 停留时间、对比率 |
| 决策 | Decision | "值得付费/注册吗？" | 转化率、注册完成率 |
| 上手 | Onboarding | "怎么开始用？" | 首次任务完成率 |
| 使用 | Usage | "能帮我完成工作吗？" | DAU、任务完成率 |
| 留存 | Retention | "为什么还要继续用？" | 留存率、流失率 |
| 推荐 | Advocacy | "值得推荐给别人吗？" | NPS、邀请率 |
| 退出 | Exit | "为什么不继续了？" | 退出原因、挽回率 |

最少必须覆盖前 5 个阶段（Awareness→Consideration→Decision→Retention→Advocacy）。

---

## 触点情感标注

| 分数 | 表情 | 含义 | 示例 |
|------|------|------|------|
| 1 | 😡 | 极负面 | 报错、崩溃、找不到功能 |
| 2 | 😟 | 负面 | 困惑、等待、信息不清晰 |
| 3 | 😐 | 中性 | 正常操作、浏览 |
| 4 | 😊 | 正面 | 顺利完成、获得帮助 |
| 5 | 😍 | 极正面 | 超出预期、惊喜、主动推荐 |

每个触点标注 `emotion_before` 和 `emotion_after`。

---

## 异常路径分级

| 严重度 | 图标 | 定义 | 必须覆盖 |
|--------|------|------|---------|
| critical | 🔴 | 导致用户流失的系统性故障 | 每阶段≥1 |
| high | 🟠 | 严重影响体验但可恢复 | 每阶段≥1 |
| medium | 🟡 | 影响体验但可绕过 | ≥50%阶段 |
| low | 🟢 | 轻微不便 | 可选 |

---

## 关键触点判定标准

触点满足以下任一条件，应标记 `is_critical=true`：
1. 此触点的失败直接导致用户流失
2. 此触点是转化漏斗的核心节点（注册、支付、首次任务）
3. 此触点承载了 >50% 的用户操作请求
4. 此触点的情感分 <2 且无替代路径

---

## 输出文件

1. `specs/journey.md` — 人类可读的旅程文档（必须）
2. `specs/journey.json` — 结构化旅程数据（建议，便于自动化验证）

使用 `python -m src.journey_designer` 可快速生成模板并验证。
