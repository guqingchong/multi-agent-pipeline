# Pipeline 诊断与修复方案 v1.0

## 诊断摘要

对 multi-agent-pipeline 进行全面审计，发现 6 大类 14 项问题。

---

## 一、入口与参数问题

### P01: bridge_cli dispatch 参数结构缺陷
**现状**：`dispatch <adapter> <task_type> [prompt]`，纯位置参数，无 --feature-id、--work-dir。
**影响**：dispatch结果无法追踪到具体feature；工作目录硬编码
**修复**：改用 argparse，支持 `--feature-id`、`--work-dir` 命名参数

### P02: cmd_dispatch 无 feature_id 传入
**现状**：dispatch_and_wait 的 payload 只有 prompt，不传 feature_id
**影响**：Agent不知道自己在做哪个feature；结果无法回写 features.json
**修复**：payload 中注入 feature_id；result 中回传

---

## 二、约束与流程问题

### P03: accept 阶段只看 status 字段
**现状**：`check_accept` 仅检查 features.json 中所有 status=='passed'，不验证任何质量证据
**影响**：手动改 status 即可绕过所有流程（本轮已验证）
**修复**：accept 要求每个 passed feature 附带 verify_record（审查报告路径、测试结果路径、Agent签名）

### P04: verify 阶段无强制触发
**现状**：brownfield 有 verify phase，但没有任何机制强制执行
**影响**：execute→accept 跳过了 verify
**修复**：在 phase_checks 的 execute 检查中增加"verify 执行标记"；accept 拒绝未验证的 passed feature

### P05: check_hermes 返回 "unknown"
**现状**：`cmd_check_hermes:133` 用 `.get("target_agent")` 但 `route_task` 返回 `target_adapter`
**影响**：错误信息不准确
**修复**：统一为 `target_agent` 或改用 `target_adapter`

---

## 三、代码质量问题

### P06: feature_count: 0
**现状**：`bridge_cli full` 返回 `feature_count: 0` 但实际有26个
**影响**：仪表盘数据缺失
**修复**：排查 JSON 解析路径，修正 count 逻辑

### P07: dispatch 无超时/无健康检查
**现状**：dispatch 不验证 Agent key 有效性；无超时告警
**影响**：key失效 → 静默失败（本轮qwen 401）；卡住无限等待（claude-code 490s）
**修复**：dispatch前检查Agent可用性；增加默认超时和卡住检测

### P08: features.json status 更新无人负责
**现状**：Agent产出文件后，需要人手动改 features.json 的 status
**影响**：手动操作容易遗漏；流程断链
**修复**：bridge_cli dispatch 成功后自动回写 status（需 feature_id 支持）

---

## 四、质量模块问题

### P09: inspector/adversarial 未接入执行流
**现状**：四个质量模块已注册但无自动触发机制
**影响**：一轮都没有跑过
**修复**：在 verify phase 或 accept phase 强制执行 inspector/adversarial review

### P10: 无验证证据链
**现状**：feature passed 不附带任何审查报告、测试结果的路径引用
**影响**：无法追溯"谁审查的、什么结果"
**修复**：features.json 增加 `verify_record` 字段，包含审查报告路径、测试结果、Agent签名

---

## 五、硬线拦截问题

### P11: 约束系统只管身份不管流程
**现状**：`system_constraint.py` 只阻止 Hermes 编码，不阻止跳过 verify
**影响**：流程约束形同虚设
**修复**：增加流程约束：执行前检查前置phase是否完成

### P12: suggest 引擎不提示 verify
**现状**：`bridge_cli suggest` 只提示 feature pending，不提示 verify 未执行
**影响**：用户/Hermes 不知道需要做 verify
**修复**：suggest 引擎在 execute→accept 之间增加 verify 检查

---

## 六、可维护性问题

### P13: 硬编码Key风险
**现状**：之前 dispatch 脚本硬编码了旧 API key
**修复**：已完成 — Key 从 Registry/settings.json/.env 动态读取 + memory 固化

### P14: Agent工作目录错误
**现状**：Agent 产出文件写到 pipeline 目录而非项目目录
**修复**：已完成 — PIPELINE_PROJECT_DIR 环境变量

---

## 修复计划（按优先级排序）

| 优先级 | 编号 | 修复项 | 复杂度 | 预计行数 |
|--------|------|--------|--------|----------|
| P0 | P03+P04 | accept强制verify检查 | 中 | 80-120 |
| P0 | P07 | dispatch健康检查+超时 | 中 | 60-100 |
| P1 | P01+P02 | dispatch参数结构化 | 大 | 150-250 |
| P1 | P08 | status自动回写 | 中 | 80-120 |
| P1 | P05 | check_hermes bug修复 | 小 | 10-20 |
| P1 | P09+P10 | 验证证据链 | 大 | 150-250 |
| P2 | P06 | feature_count修复 | 小 | 20-40 |
| P2 | P11+P12 | 流程约束+suggest | 中 | 60-100 |

---结束---
