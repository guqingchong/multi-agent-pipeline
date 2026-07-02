# 对抗审查 3/3：通用性与科学性

> 审查对象：`multi-agent-pipeline` v3.0 代码库  
> 审查视角： adversarial / 通用性 / 跨语言可迁移性 / 科学依据  
> 输出时间：2026-07-02  
> 文件路径：`docs/review-adversarial-3.md`

---

## 0. 结论速览（TL;DR）

| 问题 | 结论 | 风险等级 |
|------|------|----------|
| 若项目类型从 Python Web 切换为 **C++ 嵌入式**，有多少模块可复用？ | **编排/持久化/消息层可复用；检查、评估、交付、适配、仓库映射、初始化层大量写死 Python/JS 假设，基本不可直接复用。** | 🔴 高 |
| 双模式（brownfield/greenfield）设计是否真正通用？ | **形式上支持，运行时多处写死 greenfield 流程；brownfield 检测同样依赖 Python 信号，未做到语言/工具链无关。** | 🟠 中高 |
| `phase_checks` 里的路径检查（`test_*.py` 等）是否假设 Python 项目？ | **明确假设。多处使用 `rglob("*.py")`、`rglob("test_*.py")`、`tests/e2e/test_*.py`、`.ps1`、Python import/build 检查。** | 🔴 高 |
| 科学性（阈值、语言无关性、验证证据） | **阈值多为经验拍脑袋；语言无关性声明与实现矛盾；验证数据集局限于 Python 自身。** | 🟠 中高 |

---

## 1. 若切换为 C++ 嵌入式项目，哪些模块硬编码、无法复用？

我们把代码库拆成 7 层讨论。下面所有行号均来自当前 `src/` 实际代码。

### 1.1 Phase 检查层：`phase_checks.py` + `gate.py` —— 几乎全部写死 Python

| 函数 | 硬编码内容 | 对 C++ 的影响 | 行号 |
|------|-----------|--------------|------|
| `check_develop` | 检查 `src/` 下是否存在 `*.py` 文件 | C++ 源码是 `.cpp/.h/.ino` 等，直接被判定“没有代码” | `398`, `405` |
| `check_test` | 检查 `tests/` 下 `test_*.py` / `*_test.py` | C++ 常用 `catch2/test.cpp`、`gtest` 等，找不到测试文件 | `482`, `485` |
| `check_integrate` | 统计 `src/**/*.py` 数量 | 集成阶段直接失败 | `1053` |
| `check_verify` | 检查 `tests/e2e/test_*.py` | 嵌入式 E2E 可能是硬件在环或串口测试，不匹配 | `945` |
| `check_evaluate` | 检查 `tests/e2e/test_*.py` | 同上 | `1069` |
| `check_deploy` | 要求 `setup.ps1/start.ps1/verify-runtime.ps1` 且 `src/**/*.py` 可导入/构建 | C++ 不需要 PowerShell Python 脚本，也不可能用 Python import 验证构建 | `639`, `651-658` |
| `gate._check_feature_lint` | 遍历 `src/**/*.py` | 无法 lint C++ | `275` |
| `gate._check_feature_tests` | 要求每个 `.py` 模块对应 `test_<module>.py` | C++ 测试命名完全不同 | `331-358` |
| `gate._check_integrate` | 用正则解析 Python `from/import` 语句 | C++ `#include` 无法识别 | `482-522` |
| `gate._check_evaluate` | 统计 `.py` 行数、docstring（三引号） | C++ 无 docstring 概念 | `603-644` |

**结论**：Phase 检查层是 Python 项目假设最集中的地方。切换到 C++ 后，除了 `check_init`（目录/文件存在性）和 `check_design`（文本关键词）等少量检查外，**开发→测试→集成→验证→评估→部署链路几乎全部需要重写**。

### 1.2 项目初始化层：`pipeline.py` —— 骨架写死 Python 项目结构

`cmd_init`（`pipeline.py:202`）无条件创建：

```python
(proj_dir / "src").mkdir(exist_ok=True)
(proj_dir / "tests").mkdir(exist_ok=True)
(proj_dir / "specs").mkdir(exist_ok=True)
(proj_dir / ".logs").mkdir(exist_ok=True)
```

并写入 `SOUL.md`、`AGENTS.md`、`progress.md`、`features.json`，然后执行 `git init`、创建 SQLite DB（`pipeline.py:215-250`）。

**问题**：
- `--stack` 参数虽然保存到 `state.stack`，但**完全没有用于调整目录结构或模板**。
- 不调用 `config.detect_mode()` 或 `workflow_registry.detect_project_type()`，对 brownfield/C++ 均视而不见。
- 对 C++ 嵌入式，通常需要 `src/`、`include/`、`CMakeLists.txt`、`platformio.ini`、`sdkconfig` 等，这里全部不会生成。

### 1.3 模式/类型检测层：`config.py` + `workflow_registry.py` —— 用 Python 文件数量判断 brownfield

`config.detect_mode`（`config.py:81-110`）的 brownfield 触发条件：

```python
# 信号2: 已有源代码
src_dir = proj / "src"
if src_dir.is_dir() and list(src_dir.rglob("*.py")):
    return "brownfield"
```

`workflow_registry.detect_project_type`（`workflow_registry.py:192-237`）同样：

```python
src_dir = proj_dir / "src"
if src_dir.exists() and src_dir.is_dir():
    return "brownfield_feature"
py_files = list(proj_dir.glob("*.py"))
if py_files:
    return "brownfield_feature"
```

**问题**：
- 一个 C++ 项目如果把源码放在 `src/`（如 `src/main.cpp`），`src/` 存在即被判为 brownfield_feature，**但源码不是 `.py` 又不会被后续检查承认**。
- 如果 C++ 项目没有 `src/`（例如只有 `main.cpp`、ESP-IDF 组件目录），则回退成 greenfield，**触发 12 阶段新建流程，与 brownfield 语义矛盾**。

### 1.4 流程编排/调度层：`condition_engine.py` + `suggestion_engine.py` + `phase_flow.py` —— 写死 greenfield 顺序

`condition_engine.determine_phase_insertions`（`condition_engine.py:541-588`）内部硬编码：

```python
phase_order = [
    "INIT", "PRD", "RESEARCH", "DESIGN", "DESIGN_REVIEW",
    "DECOMPOSE", "DEVELOP", "CODE_REVIEW", "TEST",
    "FIX_LOOP", "ACCEPT", "DEPLOY",
]
```

该顺序与 `config.AVAILABLE_MODES["greenfield"]` 一致，但与 `brownfield` 的 `discover/benchmark/analyze/...` 完全不同。当实际运行在 brownfield 模式时，`current_phase.upper()` 可能根本不在 `phase_order` 列表中，导致索引失败、动态插入策略失效。

`suggestion_engine.py` 也大量依赖 `PHASE_ORDER`（从 `phase_flow` 导入，`suggestion_engine.py:44`），其 `phase_task_map`（`suggestion_engine.py:382-390`）只覆盖 greenfield 阶段：

```python
phase_task_map = {
    "init": TaskType.ORCHESTRATE,
    "design": TaskType.ANALYZE,
    "decompose": TaskType.ANALYZE,
    "develop": TaskType.CODE,
    "test": TaskType.TEST,
    "accept": TaskType.REVIEW,
    "deploy": TaskType.DEPLOY,
}
```

对 brownfield 的 `discover/benchmark/execute/verify/deliver` 等没有任务映射。

**结论**：所谓“模式透明”只停留在 `config.phase_order` 读取上；真正做动态插入和建议的引擎根本没读模式。

### 1.5 评估/证据层：`evaluate.py` + `repo_map.py` —— 只能理解 Python

`evaluate.py` 的 `EvidenceCollector` 假设项目目录结构为 `src/`、`tests/`、`specs/`，且源码为 `.py`：

| 方法 | 硬编码假设 | 行号 |
|------|-----------|------|
| `_collect_static_analysis` | `src.rglob("*.py")`，用 `py_compile` 检查语法 | `507`, `523` |
| `_check_imports` | 解析 Python `from/import` 语句 | `570-608` |
| `_collect_test_results` | `tests.rglob("test_*.py")`，用正则 `def test_` 计数 | `621`, `626` |
| `_collect_lint` | 只遍历 `.py` 文件 | `659` |
| `_collect_code_review` | 只遍历 `.py` 文件，查找 Python `eval/exec`、bare `except:` | `738`, `748-793` |
| `_collect_dependency_audit` | 查找 `requirements*.txt`、`pyproject.toml`、`Pipfile` | `797-799` |
| `_detect_lies` | 只验证 `.py/.md/.json/.yaml/.toml` 文件引用 | `1091` |

`repo_map.py` 同样只解析 Python：

```python
PYTHON_EXTENSIONS: Set[str] = {".py", ".pyi", ".pyx"}
```

以及 `_module_to_repo_path` 直接把模块名转 `.py`（`repo_map.py:218`）。对 C++ 的头文件/源文件依赖图完全无法建立。

### 1.6 交付/脚本层：`delivery.py` —— 生成的是 Python 项目部署脚本

`DeliveryConfig` 默认：

```python
python_min_version: str = "3.10"
core_packages: ["pyyaml>=6.0", "pytest>=7.0", "pytest-cov", ...]
```

生成的 `setup.ps1`（`delivery.py:112-354`）、`start.ps1`（`357-531`）、`verify-runtime.ps1`（`534-780`）全部：
- 检查 `python --version`
- 用 `pip install` 安装 Python 包
- 用 `python -m pytest tests/...` 跑测试
- 用 `python -c "import ..."` 验证模块

`verify_startup`（`delivery.py:1109-1177`）还会检查 `src/*.py` 是否存在、能否 `importlib.import_module("delivery")`。

对 C++ 嵌入式项目，这些脚本**没有任何复用价值**，需要重新写 CMake/PlatformIO/ESP-IDF 的构建与刷写脚本。

### 1.7 适配器/沙箱层：`adapters.py` + `sandbox.py` —— 默认工具链是 Python/JS

`adapters.py` 的 `OutputParser` 正则：

```python
TEST_STAT_RE = re.compile(r"(\d+) passed(?:,\s+(\d+) failed)?...")
```

只能解析 pytest 风格输出（`adapters.py:112`, `260`）。Qwen/CodeWhale 的 `_build_skill_prompt` 里反复出现：
- “单元测试编写 (pytest/unittest)”
- “TypeScript/Python type hints”
- “Playwright 浏览器自动化”

`sandbox.py` 的默认命令白名单（`sandbox.py:145-156`）允许：
- `pytest`、`python -m pytest`、`npm test`
- `flake8/black/ruff/mypy/pylint/eslint/tsc/prettier`
- `python setup.py/app.py/main.py/...`
- `playwright install`

**没有** `cmake`、`make`、`g++`、`clang`、`idf.py`、`platformio`、`arm-none-eabi-gcc` 等 C++ 嵌入式工具链命令。默认沙箱会直接拒绝或要求确认。

### 1.8 汇总：可复用 vs 不可复用

| 层级 | 代表模块 | 切换 C++ 后的可复用性 | 风险 |
|------|---------|----------------------|------|
| 持久化/状态 | `state_store.py`, `checkpoint` | ✅ 可复用 | 低 |
| 配置/注册表 | `config.py`, `workflow_registry.py` | ⚠️ 框架可复用，检测逻辑需重写 | 中 |
| 流程状态机 | `phase_flow.py`（仅读取 `PHASE_ORDER` 时） | ⚠️ 部分可复用，但 `condition_engine` 写死 greenfield | 中 |
| 编排/约束 | `system_constraint.py`, `suggestion_engine.py` | ⚠️ 约束层可复用，建议映射需扩展 | 中 |
| Phase 检查 | `phase_checks.py`, `gate.py` | ❌ 基本不可用 | 高 |
| 评估/Judge | `evaluate.py` | ❌ 不可用 | 高 |
| 仓库映射 | `repo_map.py` | ❌ 不可用 | 高 |
| 交付脚本 | `delivery.py` | ❌ 不可用 | 高 |
| 适配器 | `adapters.py` | ⚠️ 结构可复用，prompt/解析器需重写 | 中高 |
| 沙箱 | `sandbox.py` | ⚠️ 框架可复用，白名单需增加 C++ 工具链 | 中 |

---

## 2. 双模式（brownfield/greenfield）设计真的通用吗？

### 2.1 结论：半通用，形式大于内容

代码里确实有一个看起来不错的模式注册表：

- `config.AVAILABLE_MODES`（`config.py:46-64`）定义了 greenfield/brownfield 两套 phase 链。
- `workflow_registry.WORKFLOW_TEMPLATES`（`workflow_registry.py:105-166`）提供了 `greenfield`、`brownfield_feature`、`brownfield_fix`、`brownfield_audit` 四种模板。

但**运行时真正消费的组件并没有统一走这套注册表**，导致模式切换只是“换了 phase 顺序”，没有换检查逻辑、没有换工具链、没有换评估方式。

### 2.2 关键反证

1. **`pipeline.py cmd_init` 不参考模式**：无论 `config.pipeline_mode` 或 `detect_mode()` 返回什么，初始化都创建 `src/tests/specs/.logs` 和 Python 项目骨架（`pipeline.py:215-250`）。
2. **`condition_engine` 写死 greenfield 顺序**：`determine_phase_insertions` 里的 `phase_order` 列表（`condition_engine.py:568-571`）直接硬编码 12 个 greenfield 阶段，未读取 `config.phase_order` 或模板。
3. **`suggestion_engine` 只认识 greenfield 阶段**：`phase_task_map`（`suggestion_engine.py:382-390`）没有 `discover`、`benchmark`、`execute`、`verify` 等 brownfield 阶段；`get_next_phase` 依赖全局 `PHASE_ORDER`（`suggestion_engine.py:300`）。
4. **模式检测依赖 Python 文件**：`config.detect_mode` 和 `workflow_registry.detect_project_type` 把“存在 `src/` 或 `*.py`”当作 brownfield 证据。这对 C++、Java、Go、Rust 项目都不成立。

### 2.3 brownfield 检查函数也假设 Python

`phase_checks.py` 确实实现了 brownfield 检查（`check_discover`、`check_benchmark`、...、`check_deliver`，`869-960`），但：

- `check_verify` 和 `check_evaluate` 仍要求 `tests/e2e/test_*.py`（`945`, `1069`）。
- `check_plan` 仍要求 `PRD*.md` / `architecture*.md`（`913-914`），对 legacy C++ 项目可能不存在。
- `check_execute` 只看 `features.json` 状态，算是最通用的一个。

### 2.4 缺少什么才让双模式真正通用？

- **缺少 project-type / toolchain 插件体系**：没有“语言 × 构建系统 × 测试框架”的抽象。
- **缺少 phase 检查策略的多态实现**：所有检查都是 if-else 硬编码文件后缀。
- **缺少模板驱动的条件引擎**：`condition_engine` 应该读取当前模板的 `phases` 列表，而不是内置 greenfield 列表。
- **缺少 brownfield 的交付物模板**：`delivery.py` 只有 Python 项目脚本。

---

## 3. `phase_checks` 里的文件路径检查是否假设了 Python 项目？

**明确回答：是的，而且是系统性、多处的假设。**

直接证据（均来自 `phase_checks.py`）：

```python
# check_develop: 必须用 src/**/*.py 证明“代码已编写”
src_dir = proj_dir / "src"
py_files = list(src_dir.rglob("*.py"))          # line 398
has_code = len(py_files) > 0
...
errors.append("src/ 目录下没有 .py 代码文件")     # line 405

# check_test: 测试文件必须是 pytest 命名风格
test_files = list(tests_dir.rglob("test_*.py")) + list(tests_dir.rglob("*_test.py"))  # line 482
if not test_files:
    errors.append("tests/ 目录下没有测试文件")   # line 485

# check_integrate: 集成阶段 = 存在 .py 文件
py_files = list(src_dir.rglob("*.py"))          # line 1053
if not py_files:
    return {"passed": False, "reason": "no python files", ...}

# check_verify / check_evaluate: E2E 测试也必须是 Python
test_files = list(tests_dir.glob("test_*.py"))  # lines 945, 1069

# check_deploy: 交付物包含 PowerShell Python 脚本，且构建=存在 .py
required_scripts = ["setup.ps1", "start.ps1", "verify-runtime.ps1"]  # line 639
py_files = list(src_dir.rglob("*.py"))          # line 654
can_import = len(py_files) > 0
errors.append("应用无法导入/构建（src/ 下没有 .py 文件）")  # line 658
```

这些检查把“项目 = Python 项目”作为隐含前提，没有参数化文件扩展名、测试框架、构建产物。换成 C++、Rust、Java、Go 都会批量失败。

---

## 4. 科学性审查

### 4.1 阈值校准：多为经验值，缺少实证

代码里大量“红线/阈值”是开发者直接写死的，没有给出校准方法或数据集：

| 阈值 | 位置 | 问题 |
|------|------|------|
| honesty < 5 → BLOCK | `evaluate.py:894` | 5 分怎么来？不同项目/语言是否一致？ |
| accuracy < 4 → BLOCK | `evaluate.py:895` | 4 分的依据是什么？ |
| code_lines > 500 → deep review | `condition_engine.py:82`, `workflow_template.py:55` | 500 行对 C++ 头文件/嵌入式小程序是否合适？ |
| test_failures > 3 → fix loop | `condition_engine.py:87` | 3 次失败是否适用于所有规模？ |
| budget 80% → pause | `condition_engine.py:92` | 80% 只是常见项目管理经验，没有与历史数据拟合 |
| 测试覆盖率 < 80% | `condition_engine.py:101` | 80% 对嵌入式安全关键代码可能太低，对脚本可能太高 |
| docstring 比例、测试/代码行比例 | `gate.py:627-639` | 比例阈值没有解释 |

**建议**：应通过历史项目数据做敏感性分析，或至少文档化阈值来源，并提供按项目类型覆盖的能力。

### 4.2 语言无关性声明与实现矛盾

文档/注释中多次出现“模式透明”“跨平台”“通用 pipeline”等表述，但实现上：

- `repo_map.py` 明确叫 `PYTHON_EXTENSIONS`。
- `evaluate.py` 明确收集 Python static analysis、pytest results、requirements/Pipfile。
- `adapters.py` 的 prompt 和解析器默认 pytest/Python type hints。
- `delivery.py` 直接生成 Python 部署脚本。

这种“接口通用、实现专用”会导致用户产生错误信任——以为 pipeline 是语言无关的，实际只能跑通 Python 项目。

### 4.3 验证证据不足

当前测试集（`tests/test_phase_checks.py` 等）的 fixture 都是创建 `.py` 文件：

```python
# 测试 fixture 典型写法
(src / "app.py").write_text(...)
(tests / "test_app.py").write_text(...)
```

没有看到：
- C++ 项目 fixture（`CMakeLists.txt`、`main.cpp`、`test_main.cpp`）
- Java/Gradle、Rust/Cargo、Go modules 的 fixture
- 跨语言工具链的端到端测试

因此“通用性”只是**在 Python 项目内部自证的通用性**，不是跨语言的通用性。

### 4.4 LLM-as-Judge 的权重与 red-line 缺乏科学依据

`evaluate.py` 的评分公式：

```python
score = 8.0
score -= len(criticals) * 2.0
score -= len(errors) * 0.5
score -= len(warnings) * 0.2
score += min(len(infos), 3) * 0.1
```

- 权重（accuracy 30%、completeness 20%、honesty 25%...）没有引用任何已验证的 LLM 评估论文或实验。
- 扣分/加分幅度（critical -2.0、error -0.5、warning -0.2）是线性经验公式，没有与人工评分做一致性校验。
- `lie detection` 用正则匹配 `\d+ tests? passed`，鲁棒性差，对非 pytest 输出无效。

---

## 5. 可执行建议

### 5.1 短期（保持现有架构，降低硬编码伤害）

1. **把文件后缀/测试模式参数化**：在 `PipelineConfig` 增加 `source_extensions`、`test_patterns`、`build_artifacts`，让 `phase_checks` 读取配置而不是写死 `*.py`。
2. **`cmd_init` 根据 `--stack` 选择模板**：例如 `--stack cpp:embedded` 生成 `src/`、`include/`、`CMakeLists.txt`；`--stack python:web` 保持现有结构。
3. **`condition_engine.determine_phase_insertions` 读取当前模板 phase 列表**，而不是硬编码 greenfield 顺序。
4. **`suggestion_engine.phase_task_map` 支持 brownfield 阶段**，或从模板元数据动态加载。
5. **沙箱白名单增加常见 C++/嵌入式命令**：`cmake`, `make`, `ninja`, `g++`, `clang`, `arm-none-eabi-gcc`, `idf.py`, `pio` 等。

### 5.2 中期（引入项目类型抽象）

1. **定义 `ProjectProfile` / `ToolchainProfile`**：包含语言、构建系统、测试框架、依赖文件、部署方式。
2. **检查器插件化**：把 `phase_checks` 拆成 `BasePhaseChecker` 接口，提供 `PythonChecker`、`CppChecker`、`GenericChecker` 等实现。
3. **评估器插件化**：`EvidenceCollector` 按 toolchain 选择 static analysis 工具（`clang-tidy`、`cppcheck`、`ctest` 等）。
4. **交付模板插件化**：`delivery.py` 根据 toolchain 生成 `CMakeLists.txt`、`flash.sh`、`README.md`，而不是固定 `setup.ps1`。

### 5.3 长期（科学验证）

1. **建立多语言基准数据集**：至少包含 Python、C++、Java、Go、Rust 各若干项目的真实检查/评估结果。
2. **阈值校准实验**：在历史数据上跑 grid search，给出每个阈值对应的 false positive / false negative，并文档化。
3. **LLM-as-Judge 一致性评估**：用人工评分作为 ground truth，计算不同权重/阈值下 Judge 的 Cohen's Kappa 或 Pearson 相关系数，迭代优化。
4. **对抗测试常态化**：每次新增语言/工具链支持时，必须跑对应的 adversarial fixture（如本审查关注的 C++ 嵌入式场景）。

---

## 6. 最终评级

| 维度 | 评级 | 说明 |
|------|------|------|
| Python 项目内可用性 | 🟢 可用 | 当前设计对 Python Web 项目是自洽的 |
| 跨语言通用性 | 🔴 差 | 大量 Python 硬编码，切换 C++ 基本不可用 |
| 双模式设计 | 🟡 形式上有，运行时不足 | 注册表存在，但 condition/suggestion/初始化未跟进 |
| 科学性 | 🟡 中等偏低 | 阈值、权重多为经验值，缺少跨语言验证 |
| 代码质量/可维护性 | 🟢 良好 | 模块化、类型注解、测试覆盖较好 |

**总体结论**：`multi-agent-pipeline` 是一个**面向 Python 项目、以 greenfield 为主轴**的健壮框架，但尚不能称为“语言无关”或“通用双模式”。若要支持 C++ 嵌入式等异构项目，需要从“文件后缀/工具链假设”这一根因入手，做系统性抽象和重构，并用多语言基准数据验证其科学性。
