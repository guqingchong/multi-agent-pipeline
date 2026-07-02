# Deep Code Review: multi-agent-pipeline v2.0

**Total files reviewed:** 24 source + 25 test = 49 files  
**Total test cases:** 1,065  
**Date:** 2026-06-30

---

## Summary

| Priority | Count | Description |
|----------|-------|-------------|
| **P0 BLOCKER** | 7 | Would cause data loss, security breaches, or complete failure |
| **P1 MAJOR** | 14 | Would cause incorrect behavior or missing critical functionality |
| **P2 MINOR** | 19 | Quality/correctness/design issues |

**Overall Health: FAIR — Redundant imports, circular dependencies, missing persistence, hardcoded secrets, no input sanitization.**

---

## Per-File Findings

### 1. `pipeline.py` (616 lines)

| ID | Priority | Category | Finding |
|----|----------|----------|---------|
| PIPE-01 | **P0** | Security — Command Injection | Line 228: `os.system(f"cd {proj_dir} && git init -q")` — unsanitized `project_name` from user input spliced into shell command. If `project_name` contains `;`, `|`, `` ` ``, or `$(...)`, arbitrary code executes. Use `subprocess.run(["git", "init", "-q"], cwd=proj_dir)` instead. |
| PIPE-02 | **P1** | Design — Dual Code Path | Lines 32/54 dual import patterns (`try: from models... except ModuleNotFoundError: from src.models...`) appear in 5 files. This is fragile and duplicates logic. Use a single `sys.path` setup at application entry point. |
| PIPE-03 | **P1** | Bug — Mark-tests flag handling | Lines 591–592: `--failed` sets `args.passed = False`, but `--passed` is a boolean flag — if neither is passed, `args.passed` defaults to `False` (argparse `store_true`), so `cmd_mark_tests` always marks tests as failed when `--passed` is omitted, even if the user just called `mark-tests project`. |
| PIPE-04 | **P1** | Design — Dual legacy/new path | Lines 298–348: `cmd_advance` tries `phase_advance()` first, then falls back to legacy check. Two incompatible advancement paths produce different errors for the same condition. |
| PIPE-05 | **P2** | Code Quality — Duplicate check functions | Lines 110–151 define `check_init`, `check_develop`, `check_review`, `check_test` that are almost-duplicates of `phase_checks.py`. Changes in one must be manually synced. |
| PIPE-06 | **P2** | Bug — String formatting in dash | Line 522: `t.agent or '-'` — if `t.agent` is empty string `""`, `or` won't replace it. Should be `t.agent if t.agent else '-'`. Same pattern in trace table. |
| PIPE-07 | **P2** | Missing — No dry-run flag | `cmd_init` creates directories/files/git/DB without a `--dry-run` preview option. |

---

### 2. `bridge_cli.py` (222 lines)

| ID | Priority | Category | Finding |
|----|----------|----------|---------|
| BRDG-01 | **P2** | Bug — Missing error for `route` without args | Line 209: `if cmd == "route" and len(args) < 1` — but `len(args) < 1` is equivalent to `len(sys.argv[2:]) < 1`, which means the check for `cmd == "route"` happens AFTER the check for `len(args) < 1` at line 201. But wait — `cmd in ("load", "suggest", "full")` does NOT include `"route"`. So `route` with no args gets past the args-length guard and calls `cmd_route(args[0])` which will IndexError. |
| BRDG-02 | **P2** | Design — Tight coupling | Imports `entry`, `system_constraint`, `suggestion_engine` directly from `src/`. If these modules have uncaught import failures, the CLI crashes with generic JSON error. |

---

### 3. `models.py` (166 lines)

| ID | Priority | Category | Finding |
|----|----------|----------|---------|
| MOD-01 | **P1** | Bug — `ProjectState.phase` string serialization | Line 139: `"phase": str(self.phase)` — for `Phase.REVIEW`, `__str__` returns `"review"`, but `from_name("review")` returns `Phase.REVIEW` (value=7). No other names collide, but future additions could. |
| MOD-02 | **P1** | Design — Legacy `REVIEW` pollutes enum | `Phase.REVIEW = 7` exists only for backward compat. This is a design flaw: the phase value 7 sits outside the `init(0)→deploy(6)` order, making `next()` / `prev()` methods return inconsistent results for REVIEW. |
| MOD-03 | **P2** | Bug — `from_dict` KeyError | Line 155: `Phase.from_name(data["phase"])` — raises uncaught `ValueError` if phase string is malformed. No default/fallback. |

---

### 4. `entry.py` (469 lines)

| ID | Priority | Category | Finding |
|----|----------|----------|---------|
| ENTR-01 | **P1** | Bug — `auto_load` double-reads legacy state | Lines 129–154: reads project record first, then reads legacy state. If project record exists (v2), `ctx.current_phase` is set from it. Then legacy state is read separately and overrides `ctx.state`. The `project_record.current_phase` and `state.phase` can diverge. |
| ENTR-02 | **P1** | Bug — Thread safety | Line 89: `_config` global singleton with no lock. Multiple concurrent calls to `get_config()` during init could create duplicate instances. |
| ENTR-03 | **P2** | Bug — `identify_intent` confidence calculation | Lines 344–358: scoring formula `score / (matched_keywords + 1) + (matched_keywords * 0.1)` — after normalization back to 0-1 range by dividing by 2.0. This arbitrary formula makes a single matched keyword with score 2 give `2/(2) + 0.1 = 1.1`, then `1.1/2 = 0.55` confidence. Two matched keywords each getting score 1 gives `2/(3) + 0.2 = 0.87`, then `0.87/2 = 0.44` — LESS confidence for MORE matches. Formula is broken. |
| ENTR-04 | **P2** | Missing — No version check | No guard against loading state from a newer/incompatible schema version. |

---

### 5. `state_store.py` (697 lines)

| ID | Priority | Category | Finding |
|----|----------|----------|---------|
| STOR-01 | **P0** | Security — SQL injection via project_id | Line 34: `projects.id TEXT PRIMARY KEY` — but `id` is used directly in parameterized queries. The `project_id` comes from user CLI input. While parameterized queries prevent injection, the ID is used in string formatting in `pipeline.py` and other callers, not always parameterized. Specifically, `_run_git` in worktree.py passes it to subprocess. |
| STOR-02 | **P1** | Bug — `trace.cache_hit` boolean mismatch | Lines 80: `cache_hit BOOLEAN DEFAULT FALSE` — but `TraceRecord` uses `bool`, and SQLite stores booleans as 0/1 integers. Consistent, but `list_traces` at line 580 does `bool(r["cache_hit"])` — if column is NULL, `bool(None)` = False, silently masking data issues. |
| STOR-03 | **P1** | Bug — `_conn()` creates new connection each call | Every `_conn()` creates a brand new SQLite connection. Used in `_ensure_tables()` → `_migrate_v1_to_v2()` sequentially, each with separate connections. The migration triggers may not fire correctly across separate connections. Should use a single shared connection or WAL mode. |
| STOR-04 | **P1** | Design — `load()` ignores `name` parameter | Lines 672–678: `def load(self, name: str)` — the `name` parameter is completely unused. Always loads the single "state" key. Multiple projects sharing the same DB would all load the same state. |
| STOR-05 | **P2** | Bug — `_migrate_v1_to_v2` no version gate | Migration runs unconditionally every init. Column existence checks are cheap but wasteful. Should check a `schema_version` pragma or saved value. |
| STOR-06 | **P2** | Missing — No WAL mode, no connection pooling | SQLite is used in its default rollback journal mode. For the multi-agent concurrent access scenario described, this will cause `database is locked` errors. |

---

### 6. `phase_checks.py` (840 lines)

| ID | Priority | Category | Finding |
|----|----------|----------|---------|
| PHCK-01 | **P0** | Security — Remote Command Execution | Lines 364–368, 507–518, 537–540, 733–735: `subprocess.run(["git", "-C", str(proj_dir), ...])` and `subprocess.run(["python", str(script)], ...)` — `proj_dir` and `script` paths derived from unsanitized `project_name`. While safer than os.system, if project_name contains path separators like `../../etc`, this walks outside the intended directory. No path validation. |
| PHCK-02 | **P1** | Bug — `_detect_cycle` graph mutation | Line 291–292: `for node in all_nodes: if node not in graph: graph[node] = []` — this mutates the caller's `dependency_graph` dict, adding nodes with empty lists. Later use of the graph outside would see these phantom nodes. |
| PHCK-03 | **P1** | Bug — `check_accept` E2E score parsing | Lines 551–567: tries to parse stdout as JSON. If stdout contains non-JSON content AND exit code is 0, `json.loads` raises `JSONDecodeError` which is caught, but no fallback. Also, `proc.stdout` may be empty even if exit code is 0. |
| PHCK-04 | **P2** | Bug — `check_deploy` deploy scripts | Line 670: requires `setup.ps1`, `start.ps1`, `verify-runtime.ps1` — these are Windows-only (PowerShell). No cross-platform alternatives. |
| PHCK-05 | **P2** | Bug — `audit_e2e` file mutation | Lines 770–777: modifies `e2e-benchmark.json` in place, appending audit log. If concurrent audits run, they'll race on the same file. |
| PHCK-06 | **P1** | Design — `_load_state` duplicates `state_store` | Lines 790–807: reads directly from SQLite via raw `sqlite3.connect`, bypassing `StateStore`. This completely circumvents the schema migration and connection management in state_store.py. |

---

### 7. `phase_flow.py` (245 lines)

| ID | Priority | Category | Finding |
|----|----------|----------|---------|
| PHFL-01 | **P1** | Bug — `_load_state` swallows all exceptions | Lines 79–81: `except Exception: pass` — silently returns None on any error, including disk full, permission denied, corrupted JSON. Caller cannot distinguish "no state" from "error reading state." |
| PHFL-02 | **P2** | Design — `PHASE_ORDER` duplication | Line 44 duplicates `PHASE_ORDER` from `models.py` `PHASE_NAMES` and `suggestion_engine.py`. Three copies of the same list in different files. Any change must be triple-synced. |
| PHFL-03 | **P2** | Bug — `advance()` on empty state | Line 141: `state = self._load_state() or {}` — if no state exists, advances from `"init"` to `"design"` into an empty dict, meaning all previous phase data is lost. |

---

### 8. `suggestion_engine.py` (470 lines)

| ID | Priority | Category | Finding |
|----|----------|----------|---------|
| SUGG-01 | **P1** | Bug — `_check_constraints` accesses private method | Line 393: `self.constraint.get_agent_for_task(task_type.value)` — `_check_constraints` returns False if agent is None. But `get_agent_for_task` only returns None for invalid task types, not for missing agents. This means constraint check always passes for known phases regardless of actual agent availability. |
| SUGG-02 | **P1** | Bug — `check_blockers` duplicates errors | Lines 269–275: parses `reason.split(" | ")` to extract blockers, then at line 275 calls `_check_state_blockers` which may add the same blockers again (design_approved, accept_approved, tests_passed). The `if blocker not in blockers` guard at line 407/413/419 only protects within `_check_state_blockers`, not against duplicates from the reason-based parsing. |
| SUGG-03 | **P2** | Missing — No caching of check results | `suggest_next_phase` calls `check_phase_complete` → `run_check`, then `check_blockers` → `run_check` again. Same `run_check` called twice for the same phase. Expensive for phases like `check_accept` that run E2E scripts. |
| SUGG-04 | **P2** | Design — `suggest_all_phases` returns fake INFO | Lines 340–350 generate mock Suggestion objects with `reason=f"后续 phase: {phase}"` — these are NOT real suggestions and will mislead callers. |

---

### 9. `system_constraint.py` (578 lines)

| ID | Priority | Category | Finding |
|----|----------|----------|---------|
| SYSC-01 | **P0** | Security — Hardcoded password hash | Lines 137–138: `hashlib.sha256(password.encode()).hexdigest()[:16]` — uses SHA-256 truncated to 16 hex chars (64 bits). SHA-256 is not a password hashing function. Use `hashlib.pbkdf2_hmac` or `bcrypt`. Also, no salt. |
| SYSC-02 | **P1** | Bug — `hermes_only_orchestration` false positives | Line 339: `if forbidden in action_lower` — "develop" contains "deploy"? No. But "check_code" → "check" is in forbidden. But "check" is an allowed orchestration action (line 310). The substring check would flag "check" as forbidden because it's in `forbidden_actions` but `allowed_actions` check at line 339 saves it. However: `"architect"` contains no forbidden word but is not in `allowed_actions`, so in strict mode it's rejected. False positive for "architect" which is actually orchestration. |
| SYSC-03 | **P2** | Bug — `emergency_until` default is 0.0 | Lines 174, 194: `self._emergency_until: float = 0.0` — `is_emergency_active` checks `time.time() > self._emergency_until`, which is immediately True at time() > 0.0. Emergency mode never activates by default. This is correct (must explicitly activate), but the initial state could be confusing. |
| SYSC-04 | **P2** | Design — `ConstraintConfig` has callbacks but no serialization | `violation_callbacks` and `route_callbacks` are `List[Callable]` — not serializable in `to_dict`. All configuration can't be round-tripped. |

---

### 10. `config.py` (96 lines)

| ID | Priority | Category | Finding |
|----|----------|----------|---------|
| CNFG-01 | **P1** | Security — Hardcoded token in config | Line 68: `github_token: str = Field(default="")` — if set via `.env`, token is loaded. But the `.env` loading has no `.env.example` validation, and token could accidentally be committed. |
| CNFG-02 | **P2** | Missing — No env var validation | No validation that `phase_order` is a valid phase list, or that `adapter_timeout > 0`, etc. Invalid config values silently propagate. |
| CNFG-03 | **P2** | Missing — `github_token` and `mcp_endpoint` have no masking | When config is printed/logged, sensitive values are exposed. |

---

### 11. `config_loader.py` (135 lines)

| ID | Priority | Category | Finding |
|----|----------|----------|---------|
| CLDR-01 | **P2** | Bug — `_merge_defaults` missing nested merge | Lines 66–75: only merges one level deep. If `loaded["prompt_cache"]` is a dict, it shallow-merges with defaults. Any missing subkeys within prompt_cache get defaults, but dict-type values (like `cache_layers`) use `copy.deepcopy`. This is correct for current config shape but fragile if config grows deeper. |

---

### 12. `context_manager.py` (538 lines)

| ID | Priority | Category | Finding |
|----|----------|----------|---------|
| CTXM-01 | **P1** | Bug — `_search_index` type mismatch | Line 220–224: `self._search_index[doc_id] = {"content": content, "tags": tags}` — stores as dict. But line 244: `for doc_id, doc in self._search_index.items()` iterates as dict items. Lines 245–246: `content = doc["content"]; tags = doc.get("tags", [])` — this works but the typing says `Dict[str, List[str]]`. The actual value is `Dict[str, Any]`. Mypy would flag this. |
| CTXM-02 | **P1** | Bug — `compress` token math wrong | Line 387: `"before_tokens": total + sum(d["tokens"] for d in dropped)` — this adds `total` (which is already reduced by deletions) + sum of already-subtracted tokens. The actual before should be recorded BEFORE the loop. |
| CTXM-03 | **P2** | Bug — `search` Chinese character splitting | Lines 237–239: splits Chinese tokens into individual characters. "搜索" becomes `["搜", "索"]`. Matching individual characters against content will give high false positive scores. |
| CTXM-04 | **P2** | Missing — Token estimation inaccurate | `TOKEN_ESTIMATE_FACTOR = 0.5` treats all chars equally. Chinese chars are ~1.5-2 tokens in most models. This underestimates Chinese token usage by 3-4x, causing unnecessary compression or overflow. |

---

### 13. `sandbox.py` (409 lines)

| ID | Priority | Category | Finding |
|----|----------|----------|---------|
| SNDB-01 | **P1** | Bug — `evaluate_command` rule ordering | Lines 335–338: DENY rules checked FIRST. Then lines 351–354: allow/ask rules checked by iteration order. Since rules are built as DENY → ASK → ALLOW (in `_build_rules`), a command matching both ALLOW and ASK patterns will be ASK'd (since ASK patterns come before ALLOW in the list). But what about a command like `rm dangerous_file`? It matches the ALLOW pattern `rm\s+\S+` first (since ALLOW rules are last in list), NOT the DENY patterns. So `rm important_file` is ALLOWED. This is a bypass of the DENY rules. |
| SNDB-02 | **P1** | Bug — `encoded_patterns` regex too narrow | Line 191: `base64\s+-d\s*\|` — requires a pipe after `base64 -d`. Attackers can use `base64 -d file.b64 > out` without pipe. |
| SNDB-03 | **P2** | Bug — `evaluate_command` allows `rm -rf /` if not root | Line 156: `^rm\s+-rf\s+/` — on Windows (git-bash), `rm -rf /` maps to `C:\` which the regex won't match because it's not a literal `/` in the OS sense. Also, `rm -rf --no-preserve-root /` bypasses this pattern. |
| SNDB-04 | **P2** | Missing — No mitigation for prompt injection | Sandbox checks commands, not the multi-turn conversational attacks mentioned in `architecture_review.py`. |

---

### 14. `e2e_framework.py` (342 lines)

| ID | Priority | Category | Finding |
|----|----------|----------|---------|
| E2EF-01 | **P1** | Bug — `PlaywrightDriver` is entirely mock | Lines 112–162: all methods return `True` unconditionally. Real browser tests cannot be run. The entire E2E framework is a stub that always passes. |
| E2EF-02 | **P2** | Bug — `screenshot` path never created | Line 219: path uses `/` separators but on Windows needs `\`. No directory creation. Screenshots destined for a dir like `./screenshots` will fail if the dir doesn't exist. |
| E2EF-03 | **P2** | Missing — No async support | Playwright requires async. The entire framework is synchronous. Can never integrate with real Playwright. |

---

### 15. `circuit_breaker.py` (395 lines)

| ID | Priority | Category | Finding |
|----|----------|----------|---------|
| CBRK-01 | **P1** | Bug — `can_execute` race condition | Lines 69–81: `can_execute()` checks `_half_open_calls < half_open_max_calls` but doesn't increment it. The increment happens in `record_success`. Between `can_execute()` returning True and `record_success()` running, another caller could pass the check too, causing more than `half_open_max_calls` to execute. |
| CBRK-02 | **P1** | Design — `circuit_breaker.CircuitBreaker.call` catches all exceptions | Line 125: `except Exception:` — catches `KeyboardInterrupt`, `SystemExit`, and `GeneratorExit` even though only operational failures should trigger breakers. Use `except Exception` with explicit exclusions. |
| CBRK-03 | **P2** | Bug — `ResilienceManager.check_and_degrade` creates temp breakers | Line 355: `self.breakers.get(n, CircuitBreaker())` — if the key doesn't exist, creates a new `CircuitBreaker` (always CLOSED), which is then discarded. Reviewer check never actually works for unregistered agents. |

---

### 16. `fallback_manager.py` (350 lines)

| ID | Priority | Category | Finding |
|----|----------|----------|---------|
| FALL-01 | **P1** | Bug — `get_active_adapter` vs `execute_with_fallback` inconsistent | Lines 131–180: `get_active_adapter()` doesn't do `execute_with_fallback`'s try/except around adapter creation. And `execute_with_fallback` at line 234 checks `if name not in (self.config.fallback_chain[:1])` — this tuple comparison `name not in ("qwen",)` (a tuple with one element) to decide whether to increment `_fallback_count`. Unclear logic. |
| FALL-02 | **P2** | Bug — `execute_with_fallback` mutates `attempted` list | Lines 198–206: `adapters_to_try` starts with `self.config.primary` then extends with `fallback_chain`. If primary is also in fallback_chain, it'll be attempted twice. The `if name in attempted` guard prevents re-execution but it's wasteful to attempt checking. |
| FALL-03 | **P2** | Missing — No timeout enforcement | `execute_with_fallback` doesn't enforce a total execution timeout. A hanging adapter can block forever. |

---

### 17. `approval.py` (608 lines)

| ID | Priority | Category | Finding |
|----|----------|----------|---------|
| APPR-01 | **P0** | Data Loss — In-memory-only approval records | Lines 163, 528: `self._records: Dict[str, ApprovalRecord] = {}` — all approval records live only in memory. Process restart loses ALL pending approvals. This is acknowledged in `architecture_review.py` but not fixed. |
| APPR-02 | **P2** | Bug — `ApprovalSystem.request` creates orphaned approval instances | Line 568–570: `approval = create_approval(level, ...); record_id = approval.request(...)` — creates a new `BaseApproval` instance with its own `_records` dict, then extracts one record to the system. The `BaseApproval` object is discarded, making `check_and_timeout` on it impossible. |
| APPR-03 | **P2** | Bug — `ApprovalSystem` doesn't delegate `is_expired` | The `ApprovalSystem` class at line 509 has no `is_expired` or `check_and_timeout` methods. It only has `approve`/`reject`/`get_status`. Timeout logic from the three subclasses is not accessible. |

---

### 18. `adapters.py` (1566 lines)

| ID | Priority | Category | Finding |
|----|----------|----------|---------|
| ADPT-01 | **P1** | Bug — `OutputParser.parse_json_block` infinite brace count | Lines 156–167: when `start != -1`, counts braces. If the text has unbalanced braces (e.g., `{"a": "}"}` — a string containing `}`), the brace counter incorrectly matches the string-internal `}` as closing, truncating the JSON. This produces malformed JSON that fails to parse. |
| ADPT-02 | **P1** | Bug — `COST_RE` matches commit hashes | Line 114: `COST_RE = re.compile(r"\$?([\d]+(?:\.[\d]+)?)\s*(USD|usd)?")` — matches any number followed by optional whitespace. In output like "commit abc123 42 files changed", this captures "42". The comment says "avoid matching commit hash" but the regex doesn't actually avoid it. |
| ADPT-03 | **P2** | Bug — `ToleranceLayer.execute` returns wrong type | Lines 636–642: returns `result` from `callable_obj()`, which may not be an `AgentResult`. The calling code in `run_with_tolerance` expects `AgentResult` but the `execute` method's return type hints say `AgentResult` while actually returning whatever the callable returns. |
| ADPT-04 | **P2** | Bug — `_retry_with_backoff` loops infinitely | Lines 492–500: calls `adapter.execute()` which calls `adapter.run_with_tolerance` which calls back to tolerance layer. If `execute()` raises `TimeoutError` again, there's no guard against infinite recursion between `run_with_tolerance` and `_retry_with_backoff`. |
| ADPT-05 | **P2** | Design — `run_with_tolerance` ignores `task` and `context` | Line 780: `def run_with_tolerance(self, task, context=None)` — the `task` and `context` parameters are completely ignored. The method calls `self.execute()` with no arguments. |

---

### 19. `architecture_review.py` (237 lines)

| ID | Priority | Category | Finding |
|----|----------|----------|---------|
| ARCH-01 | **P2** | Missing — Review is a static report | The entire file is a pre-written review, not generated code. It cannot adapt to new code changes. Should be a tool that analyzes actual code, not a dataclass with hardcoded findings. |
| ARCH-02 | **P2** | Bug — `generate_review_report` writes to CWD | Line 224: `with open(output_path, "w")` — no path validation. If called from wrong directory, writes to unexpected location. |

---

### 20. `worktree.py` (494 lines)

| ID | Priority | Category | Finding |
|----|----------|----------|---------|
| WKTR-01 | **P0** | Security — Command injection via project/feature names | Lines 79–86: `_run_git` passes `project` and `feature` to git commands. `_make_branch_name(feature, agent)` at line 126 produces `agent/{feature.lower()}-{agent}`. If `feature` contains `\n` or `--`, it could inject git options or create multi-line branch names. |
| WKTR-02 | **P1** | Bug — `_project_path` fallback broken | Lines 179–189: if project is not abs and doesn't exist in CWD, returns `p` (original string) which git can't resolve. Then `remove` at line 360 calls `_run_git` with `project_path` that doesn't exist → subprocess error. |
| WKTR-03 | **P2** | Bug — Race condition in `create` | Line 243–246: checks branch existence, then at line 246 creates branch. Between check and create, another process could create it. Should use `-B` (force create/reset) which is already used at line 253. |
| WKTR-04 | **P2** | Missing — Hardcoded `C:/agent-worktrees` | Line 32: `DEFAULT_WORKTREE_ROOT = Path("C:/agent-worktrees")` — Windows-only hardcoded path. No env var override. |

---

### 21. `prompt_cache.py` (534 lines)

| ID | Priority | Category | Finding |
|----|----------|----------|---------|
| PRMC-01 | **P1** | Bug — `_load_from_sqlite` skips valid entries if TTL=0 | Lines 231–232: `if data["ttl"] > 0 and (now - data["created_at"]) > data["ttl"]` correctly skips expired. But what about entries with `ttl = 0` (never expire)? The condition `data["ttl"] > 0` means TTL=0 entries always pass the expiration gate, which is correct. But the `CacheEntry.is_expired` also returns False for ttl<=0. Consistent. However, the SQL store `save_entry` at store line 133 inserts `created_at` as the `accessed_at` value (parameter position 5), which is wrong — `accessed_at` should be NULL for new entries. |
| PRMC-02 | **P2** | Bug — `_load_config` called before `max_entries` assigned | Lines 122–123: `if config_path is not None: self._load_config(config_path)` runs BEFORE `self.max_entries = max_entries` at line 125. The config could set a max_entries value, but it's overwritten by the constructor parameter. Intentional or bug? |
| PRMC-03 | **P2** | Bug — `set` method hashes prompt but stores raw prompt | Line 367: stores the full raw `prompt` in `CacheEntry`. For large prompts, the in-memory footprint is doubled (hash + full text). |

---

### 22. `prompt_cache_store.py` (319 lines)

| ID | Priority | Category | Finding |
|----|----------|----------|---------|
| PCST-01 | **P0** | Data Loss — `save_entry` overwrites access_count | Lines 120–127: `ON CONFLICT(hash) DO UPDATE SET ... access_count=0` — when updating an existing entry, the access count is reset to 0. All previous access history for that hash is wiped. |
| PCST-02 | **P1** | Bug — `_incr_stat` creates new connection without lock | Lines 211–222: `_incr_stat` opens a new SQLite connection without acquiring the `_lock`. The method is called from `record_hit`/`record_miss` which DO hold the lock, but from `load_entry` at line 155 which does NOT hold the lock. Thread safety violation. |
| PCST-03 | **P2** | Bug — `_serialize_response` doesn't handle non-serializable types | Lines 93–95: `json.dumps(response, ensure_ascii=False)` — if `response` contains datetime, bytes, or custom objects, this throws `TypeError` silently caught in `prompt_cache.py`'s try/except. |

---

### 23. `observability.py` (732 lines)

| ID | Priority | Category | Finding |
|----|----------|----------|---------|
| OBSV-01 | **P1** | Bug — `ObservabilityStore` doesn't extend `StateStore` | Lines 66–257: completely reimplements SQL queries that `StateStore` already has. `get_traces`, `get_audit_logs`, `get_checkpoints` are duplicates. Any schema change must be updated in both places. |
| OBSV-02 | **P2** | Bug — `Dashboard.render_text` column widths hardcoded | Lines 501–526: uses fixed-width ASCII box drawing with specific column widths (29 chars for project, 8 for phase). Long project names will break formatting. |
| OBSV-03 | **P2** | Bug — `AlertManager.check_traces` never clears window | Lines 318–321: sliding window grows to `max_window * 2`, then truncated. But if `check_traces` is called infrequently with large batches, the window contains very old data that skews statistics. |

---

### 24. `performance_optimizer.py` (669 lines)

| ID | Priority | Category | Finding |
|----|----------|----------|---------|
| PERF-01 | **P2** | Bug — `benchmark_cache` uses fresh cache | Line 526: `cache = self.cache or PromptCache(...)` — if `self.cache` is None, creates a brand new, empty cache for benchmarking. Benchmark results are meaningless because they test empty cache performance. |
| PERF-02 | **P2** | Bug — `analyze_context_loss` division by zero | Lines 366–367: `loss_ratio = (total_tokens_before - total_tokens_after) / total_tokens_before` — but `total_tokens_before` is initialized to 0, and the method returns early only if `context_manager is None`. If `context_manager` exists but has zero compression logs (and current tokens <= max), the `elif` at line 369 is reached with `total_tokens_before=0` causing division by zero. |
| PERF-03 | **P2** | Bug — `tune_cache_params` potentially reduces config | Lines 307–318: recommends `max_entries * 1.5` regardless of current capacity. If max_entries is already 1,000,000, it recommends 1,500,000. No upper bound. |

---

## Test Coverage Gaps

Based on 1,065 test cases across 25 test files:

| Module | Test File | Tests | Gaps |
|--------|-----------|-------|------|
| adapters.py | test_adapters.py (61), test_adapter_tolerance.py (70), test_adapters_fallback.py (5), test_qwen_adapter.py (61) | 197 | No tests for `OutputParser.extract_diff_stats` with edge cases; no tests for truncation recovery with real truncated data |
| pipeline.py | test_pipeline_state_machine.py (22) | 22 | No integration test for init→develop→review→test full flow; no test for `cmd_mark_tests` interaction |
| phase_checks.py | test_phase_checks.py (26) | 26 | Only 26 tests for 8 check functions + audit. No test for `check_accept` E2E execution; no test for `audit_e2e` |
| approval.py | test_approval_system.py (64) | 64 | No persistence test (approvals survive restart); no concurrent approval test |
| worktree.py | test_worktree.py (88) | 88 | No test for race conditions; no test for path traversal attacks |
| e2e_framework.py | **MISSING** | 0 | No test file exists for `e2e_framework.py` |
| fallback_manager.py | **MISSING** | 0 | No test file exists for `fallback_manager.py` |
| config.py | **MISSING** | 0 | No test file exists for `config.py` |
| config_loader.py | test_prompt_cache_config.py (36) | 36 | Only tests prompt_cache config paths, not full config |
| architecture_review.py | **MISSING** | 0 | No test file |

---

## Cross-Cutting Issues

1. **Circular Import Risk** (P1): `adapters.py` imports `circuit_breaker.py` which imports from `adapters.py` (guarded by try/except). `observability.py` imports `state_store.py` but also duplicates its logic.

2. **No Input Sanitization** (P0): Project names are used directly in shell commands (`os.system`, `subprocess.run` with git) without validation. A project named `test; rm -rf /` would execute arbitrary commands.

3. **Inconsistent State Persistence** (P1): Some modules use `StateStore`, some use raw SQLite, some keep state only in memory. No unified persistence layer.

4. **No Integration/E2E Tests** (P2): The test directory has only unit tests. No test exercises `init → design → decompose → develop → test → accept → deploy` end-to-end.

5. **Mixed Chinese/English UI** (P2): Some modules output in Chinese, some in English. Error messages are not consistent.

6. **No Rate Limiting/Throttling** (P2): `AlertManager`, `Sandbox`, `SuggestionEngine` have no rate limiting — could be spammed by malicious input.

7. **Concurrent Access Vulnerable** (P1): `StateStore` opens fresh connections per operation with no connection pooling or WAL mode. Multi-agent concurrent access will cause `database is locked`.

---

## Recommendations by Priority

### P0 — MUST FIX before production
1. Replace all `os.system()` calls with `subprocess.run()` with argument lists (pipeline.py:228, phase_checks.py:364,508,537,733)
2. Validate project names against a whitelist (alphanumeric + hyphens only) at system boundary
3. Persist approval records to SQLite (approval.py — currently memory-only)
4. Fix `prompt_cache_store.save_entry` resetting `access_count` to 0 on update
5. Replace SHA-256 password hashing with bcrypt/pbkdf2_hmac in system_constraint.py
6. Add path traversal protection for project directories

### P1 — SHOULD FIX before release
1. Eliminate circular imports: extract shared types to `src/types.py`
2. Unify `StateStore` usage — remove raw SQLite reads from `phase_checks.py._load_state`
3. Add WAL mode and connection pooling to SQLite
4. Fix `sandbox.py` ALLOW/ASK/DENY rule ordering (DENY must be checked last against exact patterns)
5. Fix `suggestion_engine.py` duplicate blocker checker
6. Fix `circuit_breaker.py` half-open race condition
7. Fix `ToleranceLayer._retry_with_backoff` infinite recursion risk
8. Add `Phase.REVIEW` removal plan
9. Fix `pipeline.py` `--mark-tests` default behavior

### P2 — SHOULD FIX when possible
1. Remove duplicate check functions between pipeline.py and phase_checks.py
2. Add e2e_framework.py test file
3. Fix Chinese char token estimation (3-4x undercount)
4. Delete `PlaywrightDriver` or integrate real Playwright
5. Add upper bound to performance optimizer tuning recommendations
6. Standardize error message language (Chinese or English, not both)
7. Add rate limiting to alert/sandbox modules
8. Add dry-run flag to init command
