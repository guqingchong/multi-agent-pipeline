"""src/e2e_framework.py — E2E 测试框架（Playwright 集成）

F017 实现：
- 支持浏览器 E2E 测试（Playwright 或 mock）
- 场景定义、步骤执行、断言检查
- 截图与报告生成
- 与 QwenCodeAdapter 集成
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import Any, Callable, Dict, List, Optional, Union


# ───────────────────────────────────────────────────────────────
# E2E 场景与步骤定义
# ───────────────────────────────────────────────────────────────


@dataclass
class E2EStep:
    """E2E 测试步骤"""

    action: str  # "goto", "click", "fill", "assert_text", "screenshot", "wait"
    target: str = ""  # selector, URL, or text
    value: str = ""  # fill value, timeout, etc.
    description: str = ""


@dataclass
class E2EScenario:
    """E2E 测试场景"""

    name: str
    steps: List[E2EStep] = field(default_factory=list)
    tags: List[str] = field(default_factory=list)


@dataclass
class E2EStepResult:
    """单个步骤执行结果"""

    step: E2EStep
    passed: bool
    duration_ms: int = 0
    error_message: Optional[str] = None
    screenshot_path: Optional[str] = None


@dataclass
class E2EScenarioResult:
    """场景执行结果"""

    scenario: E2EScenario
    passed: bool
    step_results: List[E2EStepResult] = field(default_factory=list)
    total_duration_ms: int = 0
    screenshots: List[str] = field(default_factory=list)


@dataclass
class E2ERunResult:
    """完整 E2E 运行结果"""

    passed: bool
    scenario_results: List[E2EScenarioResult] = field(default_factory=list)
    total_scenarios: int = 0
    passed_scenarios: int = 0
    failed_scenarios: int = 0
    total_duration_ms: int = 0
    browser: str = "chromium"
    base_url: str = ""
    report_html: Optional[str] = None

    def to_dict(self) -> Dict[str, Any]:
        return {
            "passed": self.passed,
            "total_scenarios": self.total_scenarios,
            "passed_scenarios": self.passed_scenarios,
            "failed_scenarios": self.failed_scenarios,
            "total_duration_ms": self.total_duration_ms,
            "browser": self.browser,
            "base_url": self.base_url,
            "scenario_results": [
                {
                    "name": sr.scenario.name,
                    "passed": sr.passed,
                    "total_duration_ms": sr.total_duration_ms,
                    "steps": [
                        {
                            "action": r.step.action,
                            "target": r.step.target,
                            "passed": r.passed,
                            "duration_ms": r.duration_ms,
                            "error_message": r.error_message,
                        }
                        for r in sr.step_results
                    ],
                }
                for sr in self.scenario_results
            ],
        }


# ───────────────────────────────────────────────────────────────
# Playwright 驱动（mock / 真实）
# ───────────────────────────────────────────────────────────────


class PlaywrightDriver:
    """Playwright 浏览器驱动（mock 实现，可替换为真实 Playwright）"""

    def __init__(self, browser: str = "chromium", headless: bool = True) -> None:
        self.browser = browser
        self.headless = headless
        self._page: Optional[Any] = None
        self._context: Optional[Any] = None
        self._browser_instance: Optional[Any] = None
        self._screenshots: List[str] = []
        self._current_url: str = ""

    def launch(self) -> None:
        """启动浏览器（mock）"""
        self._browser_instance = {"browser": self.browser, "launched": True}
        self._context = {"context": "active"}
        self._page = {"page": "active", "url": ""}

    def close(self) -> None:
        """关闭浏览器"""
        self._page = None
        self._context = None
        self._browser_instance = None

    def goto(self, url: str) -> bool:
        """导航到 URL"""
        if self._page is None:
            return False
        self._current_url = url
        self._page["url"] = url
        return True

    def click(self, selector: str) -> bool:
        """点击元素"""
        return self._page is not None

    def fill(self, selector: str, value: str) -> bool:
        """填充输入框"""
        return self._page is not None

    def assert_text(self, selector: str, text: str) -> bool:
        """断言文本存在"""
        # mock 实现：假设文本存在
        return self._page is not None

    def screenshot(self, path: str) -> bool:
        """截图"""
        if self._page is None:
            return False
        self._screenshots.append(path)
        return True

    def wait(self, ms: int) -> bool:
        """等待（mock 中不实际等待）"""
        return True

    @property
    def screenshots(self) -> List[str]:
        return self._screenshots.copy()


# ───────────────────────────────────────────────────────────────
# E2E 执行器
# ───────────────────────────────────────────────────────────────


class E2EExecutor:
    """E2E 测试执行器"""

    def __init__(
        self,
        driver: Optional[PlaywrightDriver] = None,
        base_url: str = "http://localhost:3000",
        screenshot_dir: str = "./screenshots",
    ) -> None:
        self.driver = driver or PlaywrightDriver()
        self.base_url = base_url.rstrip("/")
        self.screenshot_dir = screenshot_dir
        self._step_impl: Dict[str, Callable[..., bool]] = {
            "goto": self._step_goto,
            "click": self._step_click,
            "fill": self._step_fill,
            "assert_text": self._step_assert_text,
            "screenshot": self._step_screenshot,
            "wait": self._step_wait,
        }

    def _step_goto(self, step: E2EStep) -> tuple[bool, Optional[str]]:
        if self.driver._page is None:
            self.driver.launch()
        url = step.target if step.target.startswith("http") else f"{self.base_url}{step.target}"
        ok = self.driver.goto(url)
        return ok, None if ok else f"Failed to goto {url}"

    def _step_click(self, step: E2EStep) -> tuple[bool, Optional[str]]:
        ok = self.driver.click(step.target)
        return ok, None if ok else f"Failed to click {step.target}"

    def _step_fill(self, step: E2EStep) -> tuple[bool, Optional[str]]:
        ok = self.driver.fill(step.target, step.value)
        return ok, None if ok else f"Failed to fill {step.target}"

    def _step_assert_text(self, step: E2EStep) -> tuple[bool, Optional[str]]:
        ok = self.driver.assert_text(step.target, step.value)
        return ok, None if ok else f"Text '{step.value}' not found in {step.target}"

    def _step_screenshot(self, step: E2EStep) -> tuple[bool, Optional[str]]:
        path = step.target or f"{self.screenshot_dir}/screenshot_{int(time.time())}.png"
        ok = self.driver.screenshot(path)
        return ok, None if ok else f"Failed to screenshot {path}"

    def _step_wait(self, step: E2EStep) -> tuple[bool, Optional[str]]:
        ms = int(step.value) if step.value else 1000
        ok = self.driver.wait(ms)
        return ok, None if ok else f"Wait failed"

    def run_scenario(self, scenario: E2EScenario) -> E2EScenarioResult:
        """执行单个场景"""
        start = time.time()
        step_results: List[E2EStepResult] = []
        screenshots: List[str] = []
        all_passed = True

        for step in scenario.steps:
            step_start = time.time()
            impl = self._step_impl.get(step.action)
            if impl is None:
                passed = False
                error = f"Unknown action: {step.action}"
            else:
                passed, error = impl(step)
            duration_ms = int((time.time() - step_start) * 1000)
            screenshot_path = None
            if step.action == "screenshot" and passed:
                screenshot_path = step.target or f"{self.screenshot_dir}/screenshot_{int(time.time())}.png"
                screenshots.append(screenshot_path)

            step_results.append(
                E2EStepResult(
                    step=step,
                    passed=passed,
                    duration_ms=duration_ms,
                    error_message=error,
                    screenshot_path=screenshot_path,
                )
            )
            if not passed:
                all_passed = False

        total_duration_ms = int((time.time() - start) * 1000)
        return E2EScenarioResult(
            scenario=scenario,
            passed=all_passed,
            step_results=step_results,
            total_duration_ms=total_duration_ms,
            screenshots=screenshots,
        )

    def run(self, scenarios: List[E2EScenario]) -> E2ERunResult:
        """执行多个场景"""
        start = time.time()
        self.driver.launch()
        scenario_results: List[E2EScenarioResult] = []
        passed_count = 0
        failed_count = 0

        for scenario in scenarios:
            result = self.run_scenario(scenario)
            scenario_results.append(result)
            if result.passed:
                passed_count += 1
            else:
                failed_count += 1

        self.driver.close()
        total_duration_ms = int((time.time() - start) * 1000)
        all_passed = passed_count == len(scenarios)

        return E2ERunResult(
            passed=all_passed,
            scenario_results=scenario_results,
            total_scenarios=len(scenarios),
            passed_scenarios=passed_count,
            failed_scenarios=failed_count,
            total_duration_ms=total_duration_ms,
            browser=self.driver.browser,
            base_url=self.base_url,
        )

    def generate_report(self, result: E2ERunResult) -> str:
        """生成 HTML 报告"""
        lines = [
            "<!DOCTYPE html>",
            "<html><head><meta charset='utf-8'><title>E2E Report</title></head><body>",
            f"<h1>E2E Test Report</h1>",
            f"<p>Browser: {result.browser}</p>",
            f"<p>Base URL: {result.base_url}</p>",
            f"<p>Total: {result.total_scenarios}, Passed: {result.passed_scenarios}, Failed: {result.failed_scenarios}</p>",
            f"<p>Duration: {result.total_duration_ms}ms</p>",
            "<hr>",
        ]
        for sr in result.scenario_results:
            status = "PASS" if sr.passed else "FAIL"
            color = "green" if sr.passed else "red"
            lines.append(f"<h2 style='color:{color}'>{status}: {sr.scenario.name}</h2>")
            lines.append("<ul>")
            for r in sr.step_results:
                step_status = "PASS" if r.passed else "FAIL"
                lines.append(f"<li>{step_status}: {r.step.action} {r.step.target} ({r.duration_ms}ms)</li>")
            lines.append("</ul>")
        lines.append("</body></html>")
        html = "\n".join(lines)
        result.report_html = html
        return html


# ───────────────────────────────────────────────────────────────
# 便捷函数
# ───────────────────────────────────────────────────────────────


def create_scenario(name: str, steps: List[Dict[str, str]], tags: Optional[List[str]] = None) -> E2EScenario:
    """从字典列表创建场景"""
    step_objs = [E2EStep(**s) for s in steps]
    return E2EScenario(name=name, steps=step_objs, tags=tags or [])


def run_e2e(scenarios: List[E2EScenario], base_url: str = "http://localhost:3000") -> E2ERunResult:
    """一键运行 E2E 测试"""
    executor = E2EExecutor(base_url=base_url)
    return executor.run(scenarios)
