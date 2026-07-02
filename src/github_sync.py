"""src/github_sync.py — GitHub Bidirectional Sync (Q07)

Implements:
- Bidirectional sync: Feature ↔ GitHub Issue
- Git remote auto-configuration
- Auto commit + push per wave
- Integration with state_store (github_issue_number, sync_status columns)

Design:
  GitHubSyncManager orchestrates:
    1. FeatureToIssueSync: Push project features → GitHub Issues
       - Creates issues for unsynced features
       - Updates existing issues when features change
       - Maps feature status to GitHub labels
    2. IssueToFeatureSync: Pull GitHub Issues → project features
       - Fetches open/updated issues
       - Maps issue labels/state back to feature status
       - Creates or updates local features
    3. GitRemoteAutoConfig: Auto-configure git remote from config
    4. WaveCommitPush: Commit + push all changes for a completed wave

Dependencies:
  - urllib (stdlib) for GitHub API calls (no external deps required)
  - Optional: requests if available (falls back to urllib)
"""

from __future__ import annotations

import json
import logging
import os
import subprocess
import time
from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Tuple
from urllib.error import URLError
from urllib.request import Request, urlopen

try:
    from config import get_config, PipelineConfig
except ImportError:
    from src.config import get_config, PipelineConfig

try:
    from state_store import FeatureRecord, StateStore
except ImportError:
    from src.state_store import FeatureRecord, StateStore

logger = logging.getLogger(__name__)


# ───────────────────────────────────────────────────────────────
# Sync Status Enum
# ───────────────────────────────────────────────────────────────

class SyncStatus(Enum):
    """Sync status for a feature-to-issue mapping."""
    UNSYNCED = "unsynced"
    SYNCING = "syncing"
    SYNCED = "synced"
    FAILED = "failed"


class SyncDirection(Enum):
    """Direction of sync operation."""
    TO_GITHUB = "to_github"     # Feature → Issue
    FROM_GITHUB = "from_github" # Issue → Feature
    BIDIRECTIONAL = "bidirectional"


# ───────────────────────────────────────────────────────────────
# GitHub Sync Error
# ───────────────────────────────────────────────────────────────

class GitHubSyncError(Exception):
    """Raised when a GitHub sync operation fails."""
    def __init__(self, message: str, status_code: Optional[int] = None, response: str = ""):
        self.status_code = status_code
        self.response = response
        super().__init__(message)


# ───────────────────────────────────────────────────────────────
# GitHub API Client (stdlib-only, no external deps)
# ───────────────────────────────────────────────────────────────

@dataclass
class GitHubClient:
    """Minimal GitHub REST API client using only stdlib.

    Uses urllib to avoid external dependencies.  Supports:
      - List / create / update issues
      - List / add labels
      - Token-based auth via Authorization header
    """

    token: str = ""
    repo: str = ""          # e.g. "owner/repo"
    api_base: str = field(default="https://api.github.com", repr=False)

    def __post_init__(self) -> None:
        if not self.token or not self.repo:
            cfg = get_config()
            self.token = self.token or cfg.github_token
            self.repo = self.repo or cfg.github_repo

    @property
    def _headers(self) -> Dict[str, str]:
        h = {
            "Accept": "application/vnd.github+json",
            "User-Agent": "Hermes-Pipeline/1.0",
        }
        if self.token:
            h["Authorization"] = f"Bearer {self.token}"
        return h

    def _api_url(self, path: str) -> str:
        return f"{self.api_base}/repos/{self.repo}/{path.lstrip('/')}"

    def _request(
        self,
        method: str,
        path: str,
        data: Optional[Dict[str, Any]] = None,
    ) -> Tuple[int, Dict[str, Any], str]:
        """Make an HTTP request to GitHub API.

        Returns (status_code, parsed_json, raw_text).
        """
        url = self._api_url(path)
        body = json.dumps(data).encode("utf-8") if data else None

        req = Request(url, data=body, headers=self._headers, method=method)

        try:
            with urlopen(req, timeout=30) as resp:
                raw = resp.read().decode("utf-8")
                status = resp.getcode()
                parsed = json.loads(raw) if raw else {}
                return status, parsed, raw
        except URLError as e:
            raise GitHubSyncError(f"GitHub API request failed: {e}")

    def list_issues(
        self,
        state: str = "open",
        labels: Optional[str] = None,
        per_page: int = 100,
        page: int = 1,
    ) -> List[Dict[str, Any]]:
        """List repository issues."""
        params = f"?state={state}&per_page={per_page}&page={page}"
        if labels:
            params += f"&labels={labels}"
        status, data, _ = self._request("GET", f"issues{params}")
        if status != 200:
            raise GitHubSyncError(f"Failed to list issues: HTTP {status}")
        if not isinstance(data, list):
            return []
        return data

    def get_issue(self, issue_number: int) -> Dict[str, Any]:
        """Get a single issue by number."""
        status, data, _ = self._request("GET", f"issues/{issue_number}")
        if status != 200:
            raise GitHubSyncError(
                f"Failed to get issue #{issue_number}: HTTP {status}",
                status_code=status,
            )
        return data

    def create_issue(
        self,
        title: str,
        body: str = "",
        labels: Optional[List[str]] = None,
        assignees: Optional[List[str]] = None,
    ) -> Dict[str, Any]:
        """Create a new GitHub issue. Returns the created issue dict."""
        payload: Dict[str, Any] = {"title": title, "body": body}
        if labels:
            payload["labels"] = labels
        if assignees:
            payload["assignees"] = assignees

        status, data, raw = self._request("POST", "issues", data=payload)
        if status not in (200, 201):
            raise GitHubSyncError(
                f"Failed to create issue: HTTP {status} — {raw[:200]}",
                status_code=status,
                response=raw,
            )
        return data

    def update_issue(
        self,
        issue_number: int,
        title: Optional[str] = None,
        body: Optional[str] = None,
        state: Optional[str] = None,
        labels: Optional[List[str]] = None,
    ) -> Dict[str, Any]:
        """Update an existing GitHub issue."""
        payload: Dict[str, Any] = {}
        if title is not None:
            payload["title"] = title
        if body is not None:
            payload["body"] = body
        if state is not None:
            payload["state"] = state
        if labels is not None:
            payload["labels"] = labels

        status, data, raw = self._request(
            "PATCH", f"issues/{issue_number}", data=payload
        )
        if status != 200:
            raise GitHubSyncError(
                f"Failed to update issue #{issue_number}: HTTP {status} — {raw[:200]}",
                status_code=status,
                response=raw,
            )
        return data

    def add_labels(self, issue_number: int, labels: List[str]) -> List[Dict[str, Any]]:
        """Add labels to an issue."""
        status, data, raw = self._request(
            "POST", f"issues/{issue_number}/labels", data={"labels": labels}
        )
        if status != 200:
            raise GitHubSyncError(
                f"Failed to add labels to issue #{issue_number}: HTTP {status}",
                status_code=status,
            )
        return data

    def set_labels(self, issue_number: int, labels: List[str]) -> List[Dict[str, Any]]:
        """Replace all labels on an issue."""
        status, data, raw = self._request(
            "PUT", f"issues/{issue_number}/labels", data={"labels": labels}
        )
        if status != 200:
            raise GitHubSyncError(
                f"Failed to set labels on issue #{issue_number}: HTTP {status}",
                status_code=status,
            )
        return data

    def close_issue(self, issue_number: int) -> Dict[str, Any]:
        """Close an issue."""
        return self.update_issue(issue_number, state="closed")

    def reopen_issue(self, issue_number: int) -> Dict[str, Any]:
        """Reopen a closed issue."""
        return self.update_issue(issue_number, state="open")


# ───────────────────────────────────────────────────────────────
# Feature ↔ Issue Mapping
# ───────────────────────────────────────────────────────────────

# Map feature status → GitHub labels
STATUS_TO_LABELS: Dict[str, List[str]] = {
    "pending": ["status:pending"],
    "in_progress": ["status:in-progress"],
    "review": ["status:review"],
    "test": ["status:test"],
    "passed": ["status:passed"],
    "failed": ["status:failed"],
    "needs_rework": ["status:needs-rework"],
}

# Map GitHub labels → feature status (reverse lookup)
LABEL_TO_STATUS: Dict[str, str] = {
    "status:pending": "pending",
    "status:in-progress": "in_progress",
    "status:review": "review",
    "status:test": "test",
    "status:passed": "passed",
    "status:failed": "failed",
    "status:needs-rework": "needs_rework",
}


def _feature_labels(feature: FeatureRecord) -> List[str]:
    """Get GitHub labels for a feature based on its status."""
    return STATUS_TO_LABELS.get(feature.status, ["status:pending"])


def _extract_status_from_labels(labels: List[Dict[str, Any]]) -> Optional[str]:
    """Extract feature status from GitHub issue labels."""
    for lbl in labels:
        name = lbl.get("name", "")
        if name in LABEL_TO_STATUS:
            return LABEL_TO_STATUS[name]
    return None


# ───────────────────────────────────────────────────────────────
# Feature → Issue Sync
# ───────────────────────────────────────────────────────────────

@dataclass
class FeatureToIssueSync:
    """Sync project features → GitHub Issues.

    Creates issues for unsynced features, updates existing issues
    when features change.  Maps feature status to GitHub labels.
    """

    client: GitHubClient
    store: Optional[StateStore] = None
    project_id: str = ""

    # Wave label prefix: e.g. "wave:1", "wave:2"
    WAVE_LABEL_PREFIX: str = field(default="wave:", repr=False)

    def sync_feature(self, feature: FeatureRecord) -> Optional[int]:
        """Sync a single feature to GitHub.

        If feature already has github_issue_number, update it.
        Otherwise, create a new issue.

        Returns the GitHub issue number, or None on failure.
        """
        if self.store:
            self.store.update_feature_sync(feature.id, SyncStatus.SYNCING.value)

        try:
            if feature.github_issue_number:
                issue = self._update_issue(feature)
            else:
                issue = self._create_issue(feature)

            issue_number = issue["number"]
            if self.store:
                self.store.update_feature_sync(
                    feature.id,
                    SyncStatus.SYNCED.value,
                    github_issue_number=issue_number,
                )
            return issue_number

        except GitHubSyncError as e:
            logger.error("Failed to sync feature %s: %s", feature.id, e)
            if self.store:
                self.store.update_feature_sync(feature.id, SyncStatus.FAILED.value)
            return None

    def _create_issue(self, feature: FeatureRecord) -> Dict[str, Any]:
        """Create a new GitHub issue for a feature."""
        body = self._build_issue_body(feature)
        labels = _feature_labels(feature)
        if feature.wave > 0:
            labels.append(f"{self.WAVE_LABEL_PREFIX}{feature.wave}")

        logger.info(
            "Creating GitHub issue for feature %s: %s",
            feature.id, feature.title,
        )
        return self.client.create_issue(
            title=feature.title,
            body=body,
            labels=labels,
        )

    def _update_issue(self, feature: FeatureRecord) -> Dict[str, Any]:
        """Update an existing GitHub issue."""
        if not feature.github_issue_number:
            raise GitHubSyncError("Feature has no github_issue_number")

        body = self._build_issue_body(feature)
        labels = _feature_labels(feature)
        if feature.wave > 0:
            labels.append(f"{self.WAVE_LABEL_PREFIX}{feature.wave}")

        # Map status to open/closed
        state = "closed" if feature.status in ("passed",) else "open"

        logger.info(
            "Updating GitHub issue #%d for feature %s",
            feature.github_issue_number, feature.id,
        )
        return self.client.update_issue(
            issue_number=feature.github_issue_number,
            title=feature.title,
            body=body,
            state=state,
            labels=labels,
        )

    def _build_issue_body(self, feature: FeatureRecord) -> str:
        """Build a rich issue body from feature data."""
        parts = [
            f"## Feature: {feature.title}",
            "",
            f"**Feature ID**: `{feature.id}`",
            f"**Status**: {feature.status}",
            f"**Wave**: {feature.wave}",
            "",
        ]
        if feature.description:
            parts.append("### Description")
            parts.append(feature.description)
            parts.append("")

        if feature.dependencies:
            parts.append("### Dependencies")
            for dep in feature.dependencies:
                parts.append(f"- {dep}")
            parts.append("")

        if feature.acceptance_criteria:
            parts.append("### Acceptance Criteria")
            for i, ac in enumerate(feature.acceptance_criteria, 1):
                parts.append(f"{i}. {ac}")
            parts.append("")

        parts.append("---")
        parts.append(f"*Synced by Hermes Pipeline*")
        return "\n".join(parts)

    def sync_all(self, features: List[FeatureRecord]) -> Dict[str, Any]:
        """Sync all features to GitHub Issues.

        Returns summary dict with counts.
        """
        created = 0
        updated = 0
        failed = 0

        for feature in features:
            issue_number = self.sync_feature(feature)
            if issue_number is None:
                failed += 1
            elif feature.github_issue_number:
                updated += 1
            else:
                created += 1

        return {
            "created": created,
            "updated": updated,
            "failed": failed,
            "total": len(features),
        }


# ───────────────────────────────────────────────────────────────
# Issue → Feature Sync
# ───────────────────────────────────────────────────────────────

@dataclass
class IssueToFeatureSync:
    """Sync GitHub Issues → project features.

    Pulls issues from GitHub and creates or updates local features.
    Maps issue labels back to feature status.
    """

    client: GitHubClient
    store: Optional[StateStore] = None
    project_id: str = ""

    def fetch_issues(
        self,
        state: str = "open",
        labels: Optional[str] = None,
    ) -> List[Dict[str, Any]]:
        """Fetch issues from GitHub."""
        return self.client.list_issues(state=state, labels=labels)

    def issue_to_feature_data(self, issue: Dict[str, Any]) -> Dict[str, Any]:
        """Convert a GitHub issue dict to feature data."""
        labels = issue.get("labels", [])
        status = _extract_status_from_labels(labels) or "pending"

        # Try to extract feature_id from body
        body = issue.get("body", "") or ""
        feature_id = ""
        for line in body.split("\n"):
            if "**Feature ID**:" in line:
                feature_id = line.split("`")[1] if "`" in line else ""
                break

        # Extract wave from labels
        wave = 0
        for lbl in labels:
            name = lbl.get("name", "")
            if name.startswith("wave:"):
                try:
                    wave = int(name.split(":")[1])
                except (ValueError, IndexError):
                    pass

        return {
            "id": feature_id or f"gh-{issue['number']}",
            "title": issue.get("title", ""),
            "description": body,
            "status": status,
            "github_issue_number": issue["number"],
            "wave": wave,
        }

    def sync_issue(self, issue: Dict[str, Any]) -> Optional[FeatureRecord]:
        """Sync a single GitHub issue to a local feature.

        If a feature with this issue number exists, update it.
        Otherwise, create a new feature.

        Returns the FeatureRecord or None on failure.
        """
        data = self.issue_to_feature_data(issue)

        if self.store is None:
            logger.warning("No StateStore configured, cannot persist feature")
            return None

        # Check if feature already exists
        existing = self.store.get_feature(data["id"])
        if existing:
            # Update existing feature's sync-related fields
            self.store.update_feature_sync(
                data["id"],
                SyncStatus.SYNCED.value,
                github_issue_number=data["github_issue_number"],
            )
            return existing

        # Create new
        try:
            feature = FeatureRecord(
                id=data["id"],
                project_id=self.project_id,
                title=data["title"],
                description=data["description"],
                status=data["status"],
                wave=data["wave"],
                github_issue_number=data["github_issue_number"],
                sync_status=SyncStatus.SYNCED.value,
            )
            self.store.create_feature(feature)
            return feature
        except (ValueError, TypeError, KeyError, RuntimeError, OSError, ConnectionError, TimeoutError, ImportError, AttributeError) as e:
            logger.error("Failed to create feature from issue #%d: %s", data["github_issue_number"], e)
            return None

    def sync_all(
        self,
        state: str = "all",
        labels: Optional[str] = None,
    ) -> Dict[str, Any]:
        """Pull all matching issues from GitHub and sync to local features.

        Returns summary dict.
        """
        issues = self.fetch_issues(state=state, labels=labels)
        created = 0
        updated = 0
        failed = 0

        for issue in issues:
            result = self.sync_issue(issue)
            if result is None:
                failed += 1
            elif result.id.startswith("gh-"):
                created += 1
            else:
                updated += 1

        return {
            "created": created,
            "updated": updated,
            "failed": failed,
            "total": len(issues),
        }


# ───────────────────────────────────────────────────────────────
# Git Remote Auto-Config
# ───────────────────────────────────────────────────────────────

@dataclass
class GitRemoteAutoConfig:
    """Auto-configure git remote for GitHub sync.

    Detects the current repo, configures 'origin' remote from
    GitHub config (or github_repo/github_token from PipelineConfig).
    """

    repo_path: Path = field(default_factory=Path.cwd)
    remote_name: str = "origin"

    def _run_git(self, *args: str) -> Tuple[int, str, str]:
        """Run a git command. Returns (returncode, stdout, stderr)."""
        try:
            result = subprocess.run(
                ["git"] + list(args),
                cwd=str(self.repo_path),
                capture_output=True,
                text=True,
                timeout=30,
            )
            return result.returncode, result.stdout.strip(), result.stderr.strip()
        except FileNotFoundError:
            return -1, "", "git not found"
        except subprocess.TimeoutExpired:
            return -1, "", "git command timed out"

    def is_git_repo(self) -> bool:
        """Check if repo_path is a git repository."""
        rc, _, _ = self._run_git("rev-parse", "--git-dir")
        return rc == 0

    def has_remote(self) -> bool:
        """Check if the remote is already configured."""
        rc, stdout, _ = self._run_git("remote", "get-url", self.remote_name)
        return rc == 0 and bool(stdout)

    def get_remote_url(self) -> Optional[str]:
        """Get the current remote URL."""
        rc, stdout, _ = self._run_git("remote", "get-url", self.remote_name)
        if rc == 0:
            return stdout
        return None

    def configure_remote(self) -> bool:
        """Configure the git remote from config.

        Builds remote URL from github_repo and github_token config.
        Uses HTTPS with token auth: https://<token>@github.com/<repo>.git
        """
        cfg = get_config()
        repo = cfg.github_repo
        token = cfg.github_token

        if not repo:
            logger.warning("No github_repo configured, cannot set remote")
            return False

        if token:
            url = f"https://{token}@github.com/{repo}.git"
        else:
            url = f"https://github.com/{repo}.git"

        if self.has_remote():
            # Update existing remote
            rc, _, stderr = self._run_git("remote", "set-url", self.remote_name, url)
            if rc == 0:
                logger.info("Updated git remote '%s' → %s", self.remote_name, repo)
                return True
            logger.error("Failed to update remote: %s", stderr)
            return False
        else:
            # Add new remote
            rc, _, stderr = self._run_git("remote", "add", self.remote_name, url)
            if rc == 0:
                logger.info("Added git remote '%s' → %s", self.remote_name, repo)
                return True
            logger.error("Failed to add remote: %s", stderr)
            return False

    def verify_remote(self) -> bool:
        """Verify the remote is accessible by fetching."""
        if not self.has_remote():
            return False
        rc, _, stderr = self._run_git("ls-remote", "--heads", self.remote_name)
        if rc == 0:
            return True
        logger.warning("Remote verification failed: %s", stderr)
        return False

    def get_current_branch(self) -> str:
        """Get the current branch name."""
        rc, stdout, _ = self._run_git("rev-parse", "--abbrev-ref", "HEAD")
        if rc == 0:
            return stdout
        return "main"

    def get_status(self) -> Dict[str, Any]:
        """Get repository status summary."""
        current_branch = self.get_current_branch()
        remote_url = self.get_remote_url()

        rc, changed, _ = self._run_git("status", "--porcelain")
        changed_files = changed.split("\n") if changed else []

        rc, log, _ = self._run_git("log", "--oneline", "-5")
        recent_commits = log.split("\n") if log else []

        return {
            "is_git_repo": self.is_git_repo(),
            "branch": current_branch,
            "remote": remote_url,
            "has_remote": self.has_remote(),
            "changed_files": len(changed_files),
            "recent_commits": recent_commits[:5],
        }


# ───────────────────────────────────────────────────────────────
# Wave Commit + Push
# ───────────────────────────────────────────────────────────────

@dataclass
class WaveCommitPush:
    """Auto commit + push changes for a completed wave.

    After all features in a wave are complete, commits all changes
    with a descriptive message and pushes to the remote.
    """

    repo_path: Path = field(default_factory=Path.cwd)
    remote_name: str = "origin"
    _git: Optional[GitRemoteAutoConfig] = field(default=None, repr=False)

    def __post_init__(self) -> None:
        self._git = GitRemoteAutoConfig(
            repo_path=self.repo_path,
            remote_name=self.remote_name,
        )

    def _run_git(self, *args: str) -> Tuple[int, str, str]:
        return self._git._run_git(*args)

    def has_changes(self) -> bool:
        """Check if there are uncommitted changes."""
        rc, stdout, _ = self._run_git("status", "--porcelain")
        return rc == 0 and bool(stdout.strip())

    def commit_wave(
        self,
        wave_number: int,
        features: List[str],
        message: Optional[str] = None,
    ) -> bool:
        """Commit all changes for a wave.

        Args:
            wave_number: The wave number being committed.
            features: List of feature IDs in this wave.
            message: Custom commit message (auto-generated if None).

        Returns True if commit succeeded.
        """
        if not self.has_changes():
            logger.info("Wave %d: no changes to commit", wave_number)
            return True  # nothing to commit is not a failure

        if message is None:
            feature_list = ", ".join(features[:5])
            if len(features) > 5:
                feature_list += f" (+{len(features) - 5} more)"
            message = (
                f"wave({wave_number}): complete — {len(features)} features\n\n"
                f"Features: {feature_list}\n"
                f"Auto-committed by Hermes Pipeline"
            )

        # Stage all changes
        rc, _, stderr = self._run_git("add", "-A")
        if rc != 0:
            logger.error("git add failed: %s", stderr)
            return False

        # Commit
        rc, stdout, stderr = self._run_git("commit", "-m", message)
        if rc != 0:
            logger.error("git commit failed: %s", stderr)
            return False

        logger.info("Wave %d committed: %s", wave_number, stdout.split("\n")[0] if stdout else "ok")
        return True

    def push_wave(self, branch: Optional[str] = None) -> bool:
        """Push committed changes to remote.

        Args:
            branch: Branch to push (defaults to current branch).

        Returns True if push succeeded.
        """
        if branch is None:
            branch = self._git.get_current_branch()

        rc, stdout, stderr = self._run_git("push", self.remote_name, branch)
        if rc != 0:
            logger.error("git push failed: %s", stderr)
            return False

        logger.info("Wave pushed to %s/%s: %s", self.remote_name, branch, stdout)
        return True

    def commit_and_push_wave(
        self,
        wave_number: int,
        features: List[str],
        branch: Optional[str] = None,
        message: Optional[str] = None,
    ) -> Dict[str, Any]:
        """Commit all changes and push for a wave.

        Returns summary dict.
        """
        result: Dict[str, Any] = {
            "wave": wave_number,
            "feature_count": len(features),
            "committed": False,
            "pushed": False,
            "branch": branch or self._git.get_current_branch(),
        }

        # Ensure remote is configured
        if not self._git.has_remote():
            logger.info("No git remote configured, attempting auto-config")
            if not self._git.configure_remote():
                result["error"] = "Failed to configure git remote"
                return result

        # Commit
        if self.commit_wave(wave_number, features, message):
            result["committed"] = True
        else:
            result["error"] = "Commit failed"
            return result

        # Push
        if self.push_wave(result["branch"]):
            result["pushed"] = True
        else:
            result["error"] = "Push failed"

        return result

    def tag_wave(self, wave_number: int, tag_message: Optional[str] = None) -> bool:
        """Create an annotated tag for a wave."""
        tag_name = f"wave-{wave_number}"
        if tag_message is None:
            tag_message = f"Wave {wave_number} completed"

        rc, _, stderr = self._run_git("tag", "-a", tag_name, "-m", tag_message)
        if rc != 0:
            logger.error("git tag failed: %s", stderr)
            return False

        # Push tag
        rc, _, stderr = self._run_git("push", self.remote_name, tag_name)
        if rc != 0:
            logger.warning("git push tag failed (non-fatal): %s", stderr)

        logger.info("Wave %d tagged as '%s'", wave_number, tag_name)
        return True


# ───────────────────────────────────────────────────────────────
# GitHubSyncManager — Top-Level Orchestrator
# ───────────────────────────────────────────────────────────────

@dataclass
class GitHubSyncManager:
    """Top-level GitHub sync orchestrator.

    Combines FeatureToIssueSync, IssueToFeatureSync, GitRemoteAutoConfig,
    and WaveCommitPush into a single manager.

    Usage:
        mgr = GitHubSyncManager(project_id="my-project")
        mgr.configure_remote()
        mgr.sync_to_github(features)
        mgr.commit_and_push_wave(1, feature_ids)
    """

    project_id: str = ""
    client: Optional[GitHubClient] = None
    store: Optional[StateStore] = None
    repo_path: Path = field(default_factory=Path.cwd)

    def __post_init__(self) -> None:
        if self.client is None:
            self.client = GitHubClient()
        if self.store is None:
            try:
                self.store = StateStore()
            except (ValueError, TypeError, KeyError, RuntimeError, OSError, ConnectionError, TimeoutError, ImportError, AttributeError):
                pass

        self._to_github = FeatureToIssueSync(
            client=self.client,
            store=self.store,
            project_id=self.project_id,
        )
        self._from_github = IssueToFeatureSync(
            client=self.client,
            store=self.store,
            project_id=self.project_id,
        )
        self._git_remote = GitRemoteAutoConfig(repo_path=self.repo_path)
        self._wave_push = WaveCommitPush(repo_path=self.repo_path)

    # ── Remote config ───────────────────────────────────────

    def configure_remote(self) -> bool:
        """Auto-configure git remote for this project."""
        return self._git_remote.configure_remote()

    def remote_status(self) -> Dict[str, Any]:
        """Get git remote configuration status."""
        return self._git_remote.get_status()

    # ── Sync to GitHub ──────────────────────────────────────

    def sync_to_github(self, features: List[FeatureRecord]) -> Dict[str, Any]:
        """Push features to GitHub Issues."""
        return self._to_github.sync_all(features)

    def sync_feature_to_github(self, feature: FeatureRecord) -> Optional[int]:
        """Push a single feature to GitHub."""
        return self._to_github.sync_feature(feature)

    # ── Sync from GitHub ────────────────────────────────────

    def sync_from_github(
        self,
        state: str = "all",
        labels: Optional[str] = None,
    ) -> Dict[str, Any]:
        """Pull GitHub Issues to local features."""
        return self._from_github.sync_all(state=state, labels=labels)

    # ── Bidirectional sync ──────────────────────────────────

    def bidirectional_sync(
        self,
        features: Optional[List[FeatureRecord]] = None,
    ) -> Dict[str, Any]:
        """Run bidirectional sync.

        First pushes local features to GitHub, then pulls
        any remote changes back.

        Returns combined summary.
        """
        result: Dict[str, Any] = {
            "to_github": {},
            "from_github": {},
        }

        # Push local → remote
        if features:
            result["to_github"] = self.sync_to_github(features)

        # Pull remote → local
        result["from_github"] = self.sync_from_github(state="all")

        return result

    # ── Wave commit + push ──────────────────────────────────

    def commit_and_push_wave(
        self,
        wave_number: int,
        features: List[str],
        branch: Optional[str] = None,
        message: Optional[str] = None,
    ) -> Dict[str, Any]:
        """Commit and push all changes for a completed wave."""
        return self._wave_push.commit_and_push_wave(
            wave_number=wave_number,
            features=features,
            branch=branch,
            message=message,
        )

    def tag_wave(self, wave_number: int, tag_message: Optional[str] = None) -> bool:
        """Tag a completed wave."""
        return self._wave_push.tag_wave(wave_number, tag_message)

    # ── Pull latest ─────────────────────────────────────────

    def pull_latest(self, branch: Optional[str] = None) -> bool:
        """Pull latest changes from remote."""
        if branch is None:
            branch = self._git_remote.get_current_branch()

        rc, stdout, stderr = self._git_remote._run_git(
            "pull", self._wave_push.remote_name, branch
        )
        if rc != 0:
            logger.error("git pull failed: %s", stderr)
            return False
        logger.info("Pulled latest: %s", stdout)
        return True


# ───────────────────────────────────────────────────────────────
# Convenience Functions
# ───────────────────────────────────────────────────────────────


def create_github_sync_manager(
    project_id: str,
    repo_path: Optional[str] = None,
    github_repo: str = "",
    github_token: str = "",
) -> GitHubSyncManager:
    """Create a GitHubSyncManager with the given configuration."""
    client = GitHubClient(repo=github_repo, token=github_token)
    return GitHubSyncManager(
        project_id=project_id,
        client=client,
        repo_path=Path(repo_path) if repo_path else Path.cwd(),
    )


def sync_wave_to_github(
    project_id: str,
    wave_number: int,
    features: List[FeatureRecord],
    commit_message: Optional[str] = None,
) -> Dict[str, Any]:
    """Convenience: sync a wave's features to GitHub + commit + push.

    Returns combined result dict.
    """
    mgr = create_github_sync_manager(project_id=project_id)

    # Ensure remote is configured
    if not mgr._git_remote.has_remote():
        mgr.configure_remote()

    # Sync features to issues
    sync_result = mgr.sync_to_github(features)

    # Commit and push
    feature_ids = [f.id for f in features]
    push_result = mgr.commit_and_push_wave(
        wave_number=wave_number,
        features=feature_ids,
        message=commit_message,
    )

    # Tag the wave
    tag_ok = mgr.tag_wave(wave_number)

    return {
        "sync": sync_result,
        "push": push_result,
        "tagged": tag_ok,
    }
