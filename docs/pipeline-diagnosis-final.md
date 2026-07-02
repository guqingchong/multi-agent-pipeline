# Multi-Agent Pipeline 会诊诊断报告（终版）

> 生成时间：2026-07-02  
> 审查团队：Qwen(可用性 106行) + CodeWhale(实用性 237行) + Claude(科学性 285+370行) + Hermes(综合)  
> 方法：三Agent独立审查 → 综合去重 → 18项问题 + 新发现补充

---

## 一、总体评级

multi-agent-pipeline是一个**面向Python项目、以greenfield为主、硬编码3个Agent的演示级框架**，不可直接用于生产级多项目多Agent场景。在Python Web、3-Agent、greenfield三种假设同时成立时基本自洽，但任何一项不满足就会系统性失效。

| 维度 | 评分 | 一句话 |
|------|------|--------|
| Python + 3 Agent 可用性 | 🟢 70 | 基础dispatch流程可跑 |
| 多Agent扩展性 | 🔴 15 | 注册表/端点/任务类型全部硬编码 |
| 跨语言通用性 | 🔴 20 | Phase检查/evaluate/delivery写死Python |
| 智能化程度 | 🔴 25 | 静态查表+布尔转发，无动态决策 |
| 科学性 | 🟡 40 | 阈值/权重均为经验值，无实证 |

---

## 二、问题清单（去重后共21项）

### 🔴 P0 — 阻塞级（7项）

| # | 问题 | 发现者 | 证据 |
|---|------|--------|------|
| 1 | **命名体系不统一**：adapters用"claude"，constraint用"claude-code" | Claude | `adapters.py:1663` vs `system_constraint.py:98` |
| 2 | **Phase定义4处不一致**：config 12个/models 7个/workflow 12个/checks 7个 | CodeWhale | 4个文件Phase数量全不同 |
| 3 | **入口不统一**：bridge_cli缺pipeline.py的10个命令 | CodeWhale | bridge_cli→COMMANDS只7个，pipeline→argparse有10个 |
| 4 | **配置系统割裂**：PipelineConfig(pydantic)与ConfigLoader(YAML)互不知 | CodeWhale | config.py vs config_loader.py |
| 5 | **Adapter注册表写死3个**：ADAPTER_REGISTRY只认claude/codewhale/qwen | Claude | `adapters.py:1663` |
| 6 | **accept不验证质量**：只看features.json status=='passed' | Hermes | `phase_checks.py accept_check` |
| 7 | **dispatch无CLI健康检查**：Key失效→静默失败，CLI未装→模糊exit 1 | CodeWhale+Hermes | 本回合qwen全线401+6次dispatch失败 |

### 🟠 P1 — 重要级（8项）

| # | 问题 | 发现者 | 证据 |
|---|------|--------|------|
| 8 | **任务类型白名单三处不一致**：MQ 8个/constraint枚举/daemon 4个 | Claude | `message_queue.py:36`, `agent_daemon.py` |
| 9 | **双模式虚设**：condition_engine/suggestion_engine写死greenfield | Claude | `condition_engine.py:568` 硬编码12个phase |
| 10 | **TASK_ADAPTER_MAP死映射**：无能力建模，无历史表现感知 | Claude | `system_constraint.py:98-118` |
| 11 | **dispatch同步路径绕过MQ**：心跳/取消/超时检测全部失效 | CodeWhale | `_execute_sync()` vs `MCPTransport` |
| 12 | **无进度反馈**：长任务全黑盒等待 | CodeWhale+Qwen | `capture_output=True` |
| 13 | **evaluate._find_file_in_project() stub返回False**：误杀合法引用 | Claude | `evaluate.py`中stub实现 |
| 14 | **gate靠正则启发式**：无语义分析，shell=True检测可被绕过 | Qwen+Claude | `gate.py` |
| 15 | **main()异常捕获过窄**：只抓JSONDecodeError/TypeError | CodeWhale | `bridge_cli.py:286` |

### 🟡 P2 — 改进级（6项）

| # | 问题 | 发现者 |
|---|------|--------|
| 16 | 跨语言通用性为零：phase_checks/evaluate/delivery/repo_map全写死Python | Claude+Qwen |
| 17 | check_hermes键名不匹配：target_agent vs target_adapter | Hermes |
| 18 | feature_count:0 | Hermes |
| 19 | 阈值无科学依据：honesty<5→BLOCK等30+个阈值全为经验值 | Claude |
| 20 | 缺少--help和argparse | CodeWhale |
| 21 | LLM-as-Judge权重无实证校准 | Claude |

---

## 三、根因分析

问题不是随机分布，而是**三个系统性根因**的必然结果：

**根因1：平台层缺失。** pipeline没有Agent/任务/能力的抽象层——所有Agent通过硬编码注册表加入，所有任务类型通过枚举白名单。新增1个Agent需改5+文件。

**根因2：假设层泛滥。** "语言=Python""项目结构=src/tests""测试=pytest""Agent=3个"——这四个假设渗透进phase_checks、evaluate、delivery、repo_map、adapters、sandbox共6个模块，没有任何抽象或配置开关。

**根因3：决策层空缺。** system_constraint是死映射，suggestion_engine是布尔转发——没有一个模块在做"根据当前上下文做决策"这件事。dispatch策略、phase推进、质量判断全部是静态规则。

---

## 四、修复路线图

| 阶段 | 周期 | 项数 | 目标 |
|------|------|------|------|
| **P0 紧急** | 1-2周 | A1(命名统一),A2(Phase统一),A3(入口统一),A4(配置统一),A5(注册表抽象),B1(accept验证),C1(健康检查) | 让pipeline在3-Agent Python场景下可靠运行 |
| **P1 重要** | 3-4周 | B2(任务类型统一),B3(双模式落地),B4(能力建模),C2(MQ统一),C3(进度反馈),D1(stub修复),D3(正则→语义),E1(异常兜底) | 初步智能化和通用性 |
| **P2 改进** | 持续 | 跨语言通用、键名修复、feature_count修复、阈值校准、LLM Judge验证、argparse | 科学性和生产就绪 |

---

## 五、多Agent协作效果评估

**本次三Agent会诊产出对比：**

| Agent | 行数 | 独家发现 | 深度 |
|-------|------|---------|------|
| Qwen | 106 | 安全漏洞、真实环境测试缺失 | 中等 |
| CodeWhale | 237 | Phase 4处不一致、配置割裂、MQ绕过 | 深（代码行号） |
| Claude | 655 | 命名不统一、注册表硬编码、evaluate stub、gate正则局限 | 最深（代码行号+反方案） |

三家视角互补：CodeWhale看架构结构，Claude看扩展性极限，Qwen看用户体验。没有重叠冗余，各发现对方没看到的问题。**三Agent会诊模式验证有效。**

---

*报告完毕。审批后按P0→P1→P2分解为features.json执行。*
