"""src/sandbox.py — Layer 0 最小可行沙箱

实现 6 种 Profile（LOCKDOWN / PIPELINE / ASSISTANT / RESEARCH / FREE / E2E）
+ 命令白名单 + 绕过检测 + 临时授权机制。

E2E_PROFILE: 放行浏览器进程（chromium/chrome/firefox/msedge）和网络白名单域名，
供 Playwright E2E 测试使用。

验收标准：
1. Profile 切换命令工作正常
2. 命令白名单拦截未知命令
3. 绕过检测能识别 base64 编码、分片组合、替代解释器
4. 临时授权到期后自动回退
"""

from __future__ import annotations

import re
import time
from dataclasses import dataclass, field
from enum import Enum, auto
from typing import Callable, Dict, List, Optional, Tuple


# ───────────────────────────────────────────────────────────────
# 交互等级 T0-T3
# ───────────────────────────────────────────────────────────────

class InteractionLevel(Enum):
    T0_SUPERVISED = 0   # 用户实时在场
    T1_DIRECTED = 1     # 用户下达目标，Agent 自主执行
    T2_APPROVAL = 2     # 完整约束 + 审批流
    T3_AUTONOMOUS = 3   # 最严格约束 + 硬熔断


# ───────────────────────────────────────────────────────────────
# 风险等级 L0-L4
# ───────────────────────────────────────────────────────────────

class RiskLevel(Enum):
    L0_READONLY = 0
    L1_PROJECT_WRITE = 1
    L2_EXTERNAL_WRITE = 2
    L3_SYSTEM_MODIFY = 3
    L4_IRREVERSIBLE = 4


# ───────────────────────────────────────────────────────────────
# Profile 定义
# ───────────────────────────────────────────────────────────────

class Profile(Enum):
    LOCKDOWN = "lockdown"
    PIPELINE = "pipeline"
    ASSISTANT = "assistant"
    RESEARCH = "research"
    FREE = "free"
    E2E = "e2e"


@dataclass
class ProfileConfig:
    """Profile 配置"""
    name: str
    description: str
    network: str          # 网络策略描述
    directory: str        # 目录策略描述
    default_interaction: InteractionLevel
    command_whitelist_enabled: bool = True
    audit_only: bool = False


PROFILE_CONFIGS: Dict[Profile, ProfileConfig] = {
    Profile.LOCKDOWN: ProfileConfig(
        name="LOCKDOWN",
        description="保险箱 — 高敏感项目",
        network="仅 API 白名单 + 内容审计",
        directory="仅项目目录",
        default_interaction=InteractionLevel.T2_APPROVAL,
        command_whitelist_enabled=True,
    ),
    Profile.PIPELINE: ProfileConfig(
        name="PIPELINE",
        description="流水线 — 标准多 Agent 项目开发",
        network="API 白名单",
        directory="项目目录",
        default_interaction=InteractionLevel.T2_APPROVAL,
        command_whitelist_enabled=True,
    ),
    Profile.ASSISTANT: ProfileConfig(
        name="ASSISTANT",
        description="助手 — 系统默认",
        network="开放(审计)",
        directory="用户授权",
        default_interaction=InteractionLevel.T0_SUPERVISED,
        command_whitelist_enabled=False,
    ),
    Profile.RESEARCH: ProfileConfig(
        name="RESEARCH",
        description="调研 — 网络调研、数据分析",
        network="完全开放",
        directory="用户指定+临时",
        default_interaction=InteractionLevel.T1_DIRECTED,
        command_whitelist_enabled=False,
    ),
    Profile.FREE: ProfileConfig(
        name="FREE",
        description="自由 — 完全信任 Agent",
        network="完全开放",
        directory="全部",
        default_interaction=InteractionLevel.T1_DIRECTED,
        command_whitelist_enabled=False,
        audit_only=True,
    ),
    Profile.E2E: ProfileConfig(
        name="E2E",
        description="E2E 测试 — 放行浏览器进程和网络白名单域名",
        network="白名单域名（被测服务 + 必要CDN/API）",
        directory="项目目录 + 浏览器临时目录",
        default_interaction=InteractionLevel.T2_APPROVAL,
        command_whitelist_enabled=True,
    ),
}


# ───────────────────────────────────────────────────────────────
# 命令白名单
# ───────────────────────────────────────────────────────────────

class CommandAction(Enum):
    ALLOW = auto()
    ASK = auto()
    DENY = auto()
    UNKNOWN = auto()


@dataclass
class WhitelistRule:
    pattern: re.Pattern
    reason: str
    action: CommandAction


# 白名单：允许的安全命令
_ALLOW_PATTERNS: List[Tuple[str, str]] = [
    (r"^git\s+(status|diff|log|show|branch|stash|checkout|commit|merge|rebase|tag)", "Git 版本控制操作"),
    (r"^(pytest|python\s+-m\s+pytest|npm\s+test|npm\s+run\s+test|python\s+-m\s+unittest|jest|vitest)", "运行测试"),
    (r"^(flake8|black|ruff|eslint|tsc|mypy|pylint|prettier)", "代码静态分析"),
    (r"^python\s+(setup\.py|manage\.py|app\.py|main\.py|run\.py|pipeline\.py|verify-runtime\.py|\S+test\.py|\S+spec\.py)", "执行已知 Python 脚本"),
    (r"^(pip\s+list|pip\s+show|npm\s+list|npm\s+info)", "查询已安装包"),
    (r"^(ls|dir|cat|type|head|tail|find|grep|wc|stat|file)", "只读文件操作"),
    (r"^(mkdir|touch|cp|copy|mv|move|rm\s+\S+|del\s+\S+)", "文件操作"),
    (r"^(echo|printenv|env|set|whoami|hostname|pwd|cd)", "系统信息查询"),
    (r"^(curl\s+--head|curl\s+-I|ping|nslookup|tracert)", "网络诊断"),
    (r"^(playwright\s+install|python\s+-m\s+playwright)", "Playwright 浏览器管理"),
    (r"(chromium|chrome|firefox|msedge|msedgewebview2)(\.exe)?", "浏览器进程（E2E 测试）"),
]

# 需要确认的高风险操作
_ASK_PATTERNS: List[Tuple[str, str]] = [
    (r"^git\s+push", "推送到远程仓库"),
    (r"^git\s+(fetch|pull)", "从远程获取代码"),
    (r"^(pip\s+install|npm\s+install|npm\s+ci|yarn\s+install|pnpm\s+install)", "安装新依赖"),
    (r"^(python\s+.*migrate|alembic|django-admin\s+migrate|npm\s+run\s+migrate)", "数据库迁移"),
    (r"^(docker\s+run|docker\s+build|docker-compose)", "Docker 操作"),
]

# 绝对禁止
_DENY_PATTERNS: List[Tuple[str, str]] = [
    (r"^rm\s+-rf\s+/|^rmdir\s+/s\s+/q", "禁止删除根目录"),
    (r"format\s+C:|format\s+/fs", "禁止格式化磁盘"),
    (r"reg\s+delete\s+HKLM", "禁止删除注册表关键项"),
    (r"shutdown\s+/s|shutdown\s+/r", "禁止关机/重启"),
    (r"net\s+user\s+/delete|net\s+localgroup", "禁止修改系统用户/组"),
    (r"certutil\s+-urlcache|certutil\s+-decode", "禁止 certutil 下载/解码（绕过手段）"),
    (r"mshta\s+javascript:|mshta\s+vbscript:", "禁止 mshta 执行脚本（绕过手段）"),
    (r"rundll32\s+.*,\s*#", "禁止 rundll32 执行任意代码（绕过手段）"),
    (r"wmic\s+process\s+call\s+create", "禁止 WMI 创建进程（绕过手段）"),
    (r"powershell\s+-enc|powershell\s+-encodedcommand", "禁止 PowerShell 编码执行（绕过手段）"),
]


def _build_rules() -> List[WhitelistRule]:
    rules: List[WhitelistRule] = []
    for pat, reason in _DENY_PATTERNS:
        rules.append(WhitelistRule(re.compile(pat, re.IGNORECASE), reason, CommandAction.DENY))
    for pat, reason in _ASK_PATTERNS:
        rules.append(WhitelistRule(re.compile(pat, re.IGNORECASE), reason, CommandAction.ASK))
    for pat, reason in _ALLOW_PATTERNS:
        rules.append(WhitelistRule(re.compile(pat, re.IGNORECASE), reason, CommandAction.ALLOW))
    return rules


DEFAULT_RULES: List[WhitelistRule] = _build_rules()


# ───────────────────────────────────────────────────────────────
# 绕过检测
# ───────────────────────────────────────────────────────────────

class BypassDetector:
    """检测命令绕过尝试"""

    encoded_patterns: List[re.Pattern] = [
        re.compile(r"base64\s+-d\s*\|", re.IGNORECASE),
        re.compile(r"echo\s+['\"][A-Za-z0-9+/=]{20,}['\"]\s*\|", re.IGNORECASE),
        re.compile(r"certutil\s+-decode", re.IGNORECASE),
    ]

    fragmentation_patterns: List[re.Pattern] = [
        re.compile(r"SET\s+\w+\s*=\s*\w+", re.IGNORECASE),
        re.compile(r"\%\w+\%\s+\%\w+\%", re.IGNORECASE),
    ]

    alt_interpreter_patterns: List[re.Pattern] = [
        re.compile(r"cscript\s+.*\.(vbs|js)", re.IGNORECASE),
        re.compile(r"wscript\s+.*\.(vbs|js)", re.IGNORECASE),
        re.compile(r"mshta\s+.*\.(hta|html)", re.IGNORECASE),
    ]

    def detect(self, command: str) -> Tuple[bool, str]:
        """返回 (是否检测到绕过, 原因)"""
        for pat in self.encoded_patterns:
            if pat.search(command):
                return True, f"检测到编码绕过: {pat.pattern}"
        for pat in self.fragmentation_patterns:
            if pat.search(command):
                return True, f"检测到分片绕过: {pat.pattern}"
        for pat in self.alt_interpreter_patterns:
            if pat.search(command):
                return True, f"检测到替代解释器: {pat.pattern}"
        return False, ""


# ───────────────────────────────────────────────────────────────
# 沙箱核心
# ───────────────────────────────────────────────────────────────

@dataclass
class TempAuth:
    """临时授权记录"""
    original_profile: Profile
    temp_profile: Profile
    granted_at: float
    duration_seconds: float

    def is_expired(self) -> bool:
        return time.time() - self.granted_at > self.duration_seconds


class Sandbox:
    """Layer 0 最小可行沙箱"""

    DEFAULT_PROFILE: Profile = Profile.ASSISTANT
    DEFAULT_TEMP_AUTH_SECONDS: float = 1800.0  # 30 分钟

    def __init__(self) -> None:
        self._profile: Profile = self.DEFAULT_PROFILE
        self._config: ProfileConfig = PROFILE_CONFIGS[self._profile]
        self._temp_auth: Optional[TempAuth] = None
        self._bypass_detector = BypassDetector()
        self._rules: List[WhitelistRule] = list(DEFAULT_RULES)
        self._audit_log: List[str] = []

    # ── Profile 管理 ──

    @property
    def profile(self) -> Profile:
        self._check_temp_auth_expiry()
        return self._profile

    @property
    def config(self) -> ProfileConfig:
        self._check_temp_auth_expiry()
        return self._config

    def switch_profile(self, profile: Profile) -> None:
        """切换到指定 Profile"""
        self._profile = profile
        self._config = PROFILE_CONFIGS[profile]
        self._audit(f"Profile switched to {profile.value}")

    def get_available_profiles(self) -> List[str]:
        return [p.value for p in Profile]

    # ── 临时授权 ──

    def grant_temp_auth(self, temp_profile: Profile, duration_seconds: Optional[float] = None) -> None:
        """授予临时授权，到期后自动回退到原 Profile"""
        duration = duration_seconds or self.DEFAULT_TEMP_AUTH_SECONDS
        self._temp_auth = TempAuth(
            original_profile=self._profile,
            temp_profile=temp_profile,
            granted_at=time.time(),
            duration_seconds=duration,
        )
        self._profile = temp_profile
        self._config = PROFILE_CONFIGS[temp_profile]
        self._audit(
            f"Temp auth granted: {temp_profile.value} "
            f"(revert to {self._temp_auth.original_profile.value} after {duration}s)"
        )

    def revoke_temp_auth(self) -> bool:
        """手动撤销临时授权，返回是否成功撤销"""
        if self._temp_auth is None:
            return False
        original = self._temp_auth.original_profile
        self._profile = original
        self._config = PROFILE_CONFIGS[original]
        self._audit(f"Temp auth revoked, reverted to {original.value}")
        self._temp_auth = None
        return True

    def _check_temp_auth_expiry(self) -> None:
        """检查临时授权是否到期，到期自动回退"""
        if self._temp_auth and self._temp_auth.is_expired():
            original = self._temp_auth.original_profile
            self._profile = original
            self._config = PROFILE_CONFIGS[original]
            self._audit(
                f"Temp auth expired, auto-reverted to {original.value}"
            )
            self._temp_auth = None

    def is_temp_active(self) -> bool:
        self._check_temp_auth_expiry()
        return self._temp_auth is not None

    def temp_auth_remaining_seconds(self) -> float:
        if self._temp_auth is None:
            return 0.0
        if self._temp_auth.is_expired():
            self._check_temp_auth_expiry()
            return 0.0
        return self._temp_auth.duration_seconds - (time.time() - self._temp_auth.granted_at)

    # ── 命令门控 ──

    def evaluate_command(self, command: str) -> Tuple[CommandAction, str]:
        """评估命令，返回 (动作, 原因)"""
        # 1. 绕过检测（所有 Profile 都执行）
        bypassed, bypass_reason = self._bypass_detector.detect(command)
        if bypassed:
            self._audit(f"BYPASS DETECTED: {command} -> {bypass_reason}")
            return CommandAction.DENY, bypass_reason

        # 2. 绝对禁止（所有 Profile 都执行）
        for rule in self._rules:
            if rule.action == CommandAction.DENY and rule.pattern.search(command):
                self._audit(f"DENY: {command} -> {rule.reason}")
                return CommandAction.DENY, rule.reason

        # 3. FREE 模式：仅审计不拦截
        if self._config.audit_only:
            self._audit(f"AUDIT (FREE): {command}")
            return CommandAction.ALLOW, "FREE 模式：仅审计"

        # 4. 白名单未启用的 Profile：允许所有非禁止命令
        if not self._config.command_whitelist_enabled:
            self._audit(f"ALLOW (no whitelist): {command}")
            return CommandAction.ALLOW, "白名单未启用，允许非禁止命令"

        # 5. 白名单模式：匹配 allow / ask
        for rule in self._rules:
            if rule.pattern.search(command):
                self._audit(f"{rule.action.name}: {command} -> {rule.reason}")
                return rule.action, rule.reason

        # 6. 默认拦截未知命令
        self._audit(f"UNKNOWN -> ASK: {command}")
        return CommandAction.UNKNOWN, "不在白名单中的命令，需用户确认"

    # ── 审计 ──

    def _audit(self, message: str) -> None:
        self._audit_log.append(message)

    def get_audit_log(self) -> List[str]:
        return list(self._audit_log)

    # ── 状态序列化 ──

    def to_dict(self) -> dict:
        self._check_temp_auth_expiry()
        return {
            "profile": self._profile.value,
            "interaction_level": self._config.default_interaction.name,
            "temp_auth_active": self._temp_auth is not None,
            "temp_auth_original": self._temp_auth.original_profile.value if self._temp_auth else None,
            "temp_auth_remaining_seconds": round(self.temp_auth_remaining_seconds(), 2),
        }


# ───────────────────────────────────────────────────────────────
# CLI 命令映射（供 pipeline.py 或外部调用）
# ───────────────────────────────────────────────────────────────

class SandboxCLI:
    """沙箱 CLI 命令处理"""

    def __init__(self, sandbox: Sandbox) -> None:
        self.sandbox = sandbox

    def cmd_mode(self, profile_name: str) -> str:
        """hermes mode <profile>"""
        try:
            profile = Profile(profile_name.lower())
        except ValueError:
            available = ", ".join(self.sandbox.get_available_profiles())
            return f"Error: Unknown profile '{profile_name}'. Available: {available}"
        self.sandbox.switch_profile(profile)
        return f"Switched to {profile.value.upper()} mode"

    def cmd_elevate(self, duration_minutes: Optional[float] = None) -> str:
        """hermes elevate — 临时提升到 FREE 模式"""
        secs = (duration_minutes or 60) * 60
        self.sandbox.grant_temp_auth(Profile.FREE, secs)
        return f"Elevated to FREE mode for {secs / 60:.0f} minutes"

    def cmd_status(self) -> dict:
        """hermes status — 返回当前沙箱状态"""
        return self.sandbox.to_dict()
