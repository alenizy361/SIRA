"""Real adapter around the locally-authenticated Claude Code CLI.

No Anthropic API key is used or accepted here - authentication is whatever
`claude auth status` reports for the OS user running this process (in a
real deployment: the non-root `aicompany` user, per constitution section 7;
in this sandbox: whatever user this container runs as - see
docs/BUILD_STATUS.md for the honest caveat).

Every subprocess call uses an argument LIST, never shell=True and never
str-interpolation into a shell command, so task titles / external content
cannot achieve shell injection (constitution section 7.11).
"""
from __future__ import annotations

import json
import logging
import os
import queue
import re
import signal
import subprocess
import threading
import time
import uuid
from collections import deque
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable, Optional

from .prompt_builder import build_prompt
from .task_contract import TaskContract

logger = logging.getLogger("claude_worker.cli_adapter")

THINKING_BLOCK_TYPES = {"thinking", "redacted_thinking"}

_SAFE_PATH_COMPONENT_PATTERN = re.compile(r"^[A-Za-z0-9._-]+$")


def _safe_path_component(value: str) -> str:
    """Raises WorkspaceEscapeError if `value` is not safe to embed as a
    single filesystem path component (no separators, no '..', no leading
    dot-dot tricks) - see _create_worktree for why this matters."""
    if not value or value in (".", "..") or not _SAFE_PATH_COMPONENT_PATTERN.match(value):
        raise WorkspaceEscapeError(
            f"'{value}' is not a safe path component (must match {_SAFE_PATH_COMPONENT_PATTERN.pattern})"
        )
    return value


class AuthenticationRequiredError(Exception):
    pass


class WorkspaceEscapeError(Exception):
    """Raised when a task tries to operate outside the approved workspace root."""


@dataclass
class CliCapabilities:
    version: str
    raw_help: str
    supports_stream_json: bool
    supports_permission_mode: bool
    supports_allowed_tools: bool
    supports_worktree_flag: bool
    supports_json_schema: bool
    supports_max_budget_usd: bool
    supports_effort: bool


@dataclass
class RunResult:
    task_id: str
    run_id: str
    exit_code: Optional[int]
    started_at: float
    finished_at: Optional[float]
    timed_out: bool
    cancelled: bool
    workspace_path: str
    branch_name: str
    baseline_commit: Optional[str]
    final_commit: Optional[str] = None
    changed_files: list[str] = field(default_factory=list)
    diff_stat: str = ""
    result_summary: str = ""
    raw_stdout_lines: int = 0
    stderr_tail: str = ""
    tool_calls: list[dict] = field(default_factory=list)
    cost_usd: Optional[float] = None
    error: Optional[str] = None


class RunHandle:
    """Returned immediately so a caller can cancel a run in progress."""

    def __init__(self, process: subprocess.Popen):
        self._process = process
        self.cancelled = False

    def cancel(self) -> None:
        self.cancelled = True
        try:
            pgid = os.getpgid(self._process.pid)
            os.killpg(pgid, signal.SIGTERM)
            for _ in range(20):
                if self._process.poll() is not None:
                    return
                time.sleep(0.25)
            os.killpg(pgid, signal.SIGKILL)
        except ProcessLookupError:
            pass


class ClaudeCodeAdapter:
    def __init__(self, cli_path: str = "claude", workspace_root: str | Path = "workspace"):
        self.cli_path = cli_path
        self.workspace_root = Path(workspace_root).resolve()
        self.workspace_root.mkdir(parents=True, exist_ok=True)

    # -- detection & auth --------------------------------------------------

    def detect_capabilities(self) -> CliCapabilities:
        version_out = subprocess.run(
            [self.cli_path, "--version"], capture_output=True, text=True, timeout=15
        ).stdout.strip()
        help_out = subprocess.run(
            [self.cli_path, "--help"], capture_output=True, text=True, timeout=15
        ).stdout

        def supports(flag: str) -> bool:
            return flag in help_out

        return CliCapabilities(
            version=version_out,
            raw_help=help_out,
            supports_stream_json="stream-json" in help_out,
            supports_permission_mode=supports("--permission-mode"),
            supports_allowed_tools=supports("--allowedTools") or supports("--allowed-tools"),
            supports_worktree_flag=supports("-w, --worktree") or supports("--worktree"),
            supports_json_schema=supports("--json-schema"),
            supports_max_budget_usd=supports("--max-budget-usd"),
            supports_effort=supports("--effort"),
        )

    def check_auth(self) -> dict:
        result = subprocess.run(
            [self.cli_path, "auth", "status"], capture_output=True, text=True, timeout=15
        )
        try:
            status = json.loads(result.stdout)
        except json.JSONDecodeError as exc:
            raise AuthenticationRequiredError(
                f"Could not parse `claude auth status` output: {result.stdout!r} / {result.stderr!r}"
            ) from exc
        if not status.get("loggedIn"):
            raise AuthenticationRequiredError(
                "Claude Code CLI is not authenticated for this OS user. Run `claude auth login` "
                "as the service-worker user (aicompany in production) - no API key will be used."
            )
        return status

    # -- workspace isolation -------------------------------------------------

    def _resolve_workspace_repo(self, workspace_repo: str) -> Path:
        candidate = (self.workspace_root / workspace_repo).resolve()
        if self.workspace_root not in candidate.parents and candidate != self.workspace_root:
            raise WorkspaceEscapeError(
                f"Task requested repo path '{workspace_repo}' which resolves outside the "
                f"approved workspace root {self.workspace_root}"
            )
        if not candidate.exists():
            raise FileNotFoundError(f"Workspace repo does not exist: {candidate}")
        return candidate

    def _create_worktree(
        self, repo_path: Path, task_id: str, branch_name: str | None, run_id: str | None = None
    ) -> tuple[Path, str]:
        # task_id can originate from a Task row created via the API - it must
        # be treated as untrusted input here. A task_id containing path
        # separators (e.g. "../../../../tmp/pwned") would otherwise let the
        # f-string below escape .worktrees entirely once resolved by pathlib,
        # since `/` joins are split into real path components regardless of
        # how many separators arrived in one string. Reject anything that
        # isn't a safe single path-component slug.
        safe_task_id = _safe_path_component(task_id)
        # Make the worktree dir AND branch unique per RUN (attempt). Without
        # this, a retry of the same task_id collides with the first attempt's
        # leftover dir/branch and `git worktree add` fails deterministically -
        # every repo-task retry then fails instantly without invoking the CLI.
        suffix = _safe_path_component(run_id) if run_id else safe_task_id
        branch = branch_name or f"agent/task-{safe_task_id}-{suffix[:12]}"
        worktree_dir = repo_path.parent / ".worktrees" / f"{repo_path.name}-{safe_task_id}-{suffix[:12]}"
        worktree_dir.parent.mkdir(parents=True, exist_ok=True)

        resolved = worktree_dir.resolve()
        expected_parent = (repo_path.parent / ".worktrees").resolve()
        if expected_parent not in resolved.parents:
            raise WorkspaceEscapeError(f"Computed worktree path '{resolved}' escapes '.worktrees'")

        # Belt-and-suspenders: clear any stale worktree/branch at these exact
        # names (e.g. from a crash between add and cleanup) so `add` is always
        # a clean start.
        subprocess.run(
            ["git", "-C", str(repo_path), "worktree", "remove", "--force", str(worktree_dir)],
            capture_output=True, text=True,
        )
        subprocess.run(["git", "-C", str(repo_path), "worktree", "prune"], capture_output=True, text=True)
        subprocess.run(
            ["git", "-C", str(repo_path), "worktree", "add", "-B", branch, str(worktree_dir), "HEAD"],
            check=True,
            capture_output=True,
            text=True,
        )
        return worktree_dir, branch

    def _remove_worktree(self, repo_path: Path, worktree_dir: Path) -> None:
        subprocess.run(
            ["git", "-C", str(repo_path), "worktree", "remove", "--force", str(worktree_dir)],
            capture_output=True,
            text=True,
        )

    # -- permission-mode / risk mapping -------------------------------------

    @staticmethod
    def _permission_mode_for_risk(risk_level: str) -> str:
        # R0/R1/R2 tasks are allowed to actually edit within their
        # allowed-tools list (still enforced by --allowedTools, never
        # --dangerously-skip-permissions). R3+ must never execute here -
        # the orchestrator should not even reach this adapter for R3+
        # without a resolved Approval, but "plan" is a safe fallback that
        # produces a plan without touching files.
        return "acceptEdits" if risk_level in ("R0", "R1", "R2") else "plan"

    # Read-only tools: safe for any tier, and enough for a reasoning-only run
    # (the CEO's planning call) to inspect context without ever needing a
    # human decision.
    READ_ONLY_TOOLS = ("Read", "Grep", "Glob")
    # Adds file authoring for tiers permitted to change code. Bash is
    # deliberately EXCLUDED: arbitrary shell is the escalation that genuinely
    # warrants a human approval, so a task needing it must come through the
    # approvals flow rather than being silently pre-authorized here.
    EDIT_TOOLS = ("Read", "Grep", "Glob", "Write", "Edit")

    @classmethod
    def _effective_allowed_tools(cls, task: TaskContract) -> list[str]:
        """The explicit tool allow-list handed to the CLI.

        Falls back to a safe default when the contract names none, so the
        headless run is never left on the CLI's interactive default policy.
        """
        if task.allowed_tools:
            return list(task.allowed_tools)
        if task.risk_level in ("R0", "R1", "R2"):
            return list(cls.EDIT_TOOLS)
        return list(cls.READ_ONLY_TOOLS)

    # -- run ------------------------------------------------------------

    def start_run(
        self,
        task: TaskContract,
        run_id: str | None = None,
        on_event: Optional[Callable[[dict], None]] = None,
        is_cancelled: Optional[Callable[[], bool]] = None,
    ) -> tuple[RunHandle, Callable[[], RunResult]]:
        """Starts the CLI subprocess and returns (handle, wait_fn). Calling
        wait_fn() blocks (with the task's timeout) until the run finishes and
        returns the RunResult - split this way so a caller can hold the
        handle for cancellation while awaiting completion elsewhere.

        `is_cancelled`, if given, is polled (throttled to once/second, piggy-
        backing on the loop's existing per-second timeout check - never on
        every stdout line) and stops the subprocess mid-run the moment it
        returns True - the same "interrupt a specific in-flight agent"
        capability Anthropic's own Managed Agents API treats as a first-class
        primitive (`user.interrupt` on a session thread), which this worker
        had no equivalent of: previously, once a task's subprocess started,
        nothing could stop it before its own timeout.
        """
        run_id = run_id or str(uuid.uuid4())
        auth_status = self.check_auth()
        capabilities = self.detect_capabilities()

        repo_path = self._resolve_workspace_repo(task.workspace_repo) if task.workspace_repo else None
        if repo_path is not None:
            baseline_commit = subprocess.run(
                ["git", "-C", str(repo_path), "rev-parse", "HEAD"],
                capture_output=True, text=True, check=True,
            ).stdout.strip()
            worktree_dir, branch_name = self._create_worktree(repo_path, task.task_id, task.branch_name, run_id)
        else:
            baseline_commit = None
            worktree_dir = self.workspace_root
            branch_name = task.branch_name or f"agent/task-{task.task_id}"

        prompt = build_prompt(task)

        args = [self.cli_path, "-p", prompt]
        output_format = "stream-json" if capabilities.supports_stream_json else "text"
        args += ["--output-format", output_format]
        if output_format == "stream-json":
            args += ["--include-partial-messages", "--verbose"]

        if capabilities.supports_permission_mode:
            args += ["--permission-mode", self._permission_mode_for_risk(task.risk_level)]
        # ALWAYS pass an explicit allow-list. Omitting it (which used to happen
        # whenever task.allowed_tools was empty - e.g. the CEO planning
        # contract) leaves the CLI on its default interactive policy, so the
        # first tool it wants surfaces as "this command needs approval" and the
        # run stalls until it times out. Nothing can answer that prompt: the
        # worker is headless. An explicit list keeps every decision
        # pre-authorized, and anything outside it is refused rather than
        # blocking on a human who will never see the question.
        if capabilities.supports_allowed_tools:
            args += ["--allowedTools", ",".join(self._effective_allowed_tools(task))]
        if task.output_schema and capabilities.supports_json_schema:
            args += ["--json-schema", json.dumps(task.output_schema)]
        if task.model:
            args += ["--model", task.model]
        if task.effort and capabilities.supports_effort:
            args += ["--effort", task.effort]
        if task.max_budget_usd and capabilities.supports_max_budget_usd:
            args += ["--max-budget-usd", str(task.max_budget_usd)]

        def _apply_resource_limits():
            os.setsid()
            try:
                import resource

                resource.setrlimit(resource.RLIMIT_CPU, (task.timeout_seconds + 30, task.timeout_seconds + 60))
            except (ImportError, ValueError, OSError):
                pass

        process = subprocess.Popen(
            args,
            cwd=str(worktree_dir),
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            bufsize=1,
            preexec_fn=_apply_resource_limits,
            env=_sanitized_env(),
        )
        handle = RunHandle(process)
        started_at = time.time()

        def _wait() -> RunResult:
            stdout_lines: list[str] = []
            tool_calls: list[dict] = []
            result_summary = ""
            timed_out = False
            cost_usd: Optional[float] = None

            # stdout is pumped by a background thread into a queue so the main
            # loop can honor the wall-clock deadline even when the CLI stalls
            # WITHOUT emitting output (a blocking readline() here would ignore
            # the timeout entirely and wedge the whole single-threaded worker).
            # stderr is drained concurrently into a bounded buffer so a chatty
            # run can never fill the OS pipe and deadlock the subprocess.
            line_queue: "queue.Queue[Optional[str]]" = queue.Queue()
            stderr_chunks: deque[str] = deque(maxlen=400)

            def _pump_stdout() -> None:
                try:
                    if process.stdout is not None:
                        for line in iter(process.stdout.readline, ""):
                            line_queue.put(line)
                finally:
                    line_queue.put(None)  # sentinel: stdout reached EOF

            def _pump_stderr() -> None:
                try:
                    if process.stderr is not None:
                        for line in iter(process.stderr.readline, ""):
                            stderr_chunks.append(line)
                except Exception:  # noqa: BLE001 - draining is best-effort
                    pass

            stdout_thread = threading.Thread(target=_pump_stdout, daemon=True)
            stderr_thread = threading.Thread(target=_pump_stderr, daemon=True)
            stdout_thread.start()
            stderr_thread.start()

            deadline = started_at + task.timeout_seconds
            last_cancel_check = 0.0
            try:
                while True:
                    remaining = deadline - time.time()
                    if remaining <= 0:
                        timed_out = True
                        handle.cancel()
                        break
                    now = time.time()
                    if is_cancelled is not None and now - last_cancel_check >= 1.0:
                        last_cancel_check = now
                        if is_cancelled():
                            handle.cancel()
                            break
                    try:
                        line = line_queue.get(timeout=min(remaining, 1.0))
                    except queue.Empty:
                        continue
                    if line is None:
                        break  # stdout closed -> the process is done emitting
                    stdout_lines.append(line)
                    for event in _parse_stream_line(line):
                        if event.get("kind") == "tool_call":
                            tool_calls.append(event)
                        if event.get("kind") == "final_text":
                            result_summary = event.get("text", result_summary)
                        if event.get("kind") == "cost":
                            cost_usd = event.get("cost_usd")
                        if on_event:
                            try:
                                on_event(event)
                            except Exception:  # noqa: BLE001
                                logger.exception("on_event callback failed for run %s", run_id)
            finally:
                # Reap the process; if it lingers (e.g. a killed group slow to
                # exit) force-kill and move on rather than raising out of the
                # finally and leaving the caller's task wedged in RUNNING.
                try:
                    process.wait(timeout=10)
                except subprocess.TimeoutExpired:
                    handle.cancel()
                    try:
                        process.wait(timeout=5)
                    except subprocess.TimeoutExpired:
                        logger.error("Run %s did not exit after cancel()", run_id)
                stdout_thread.join(timeout=2)
                stderr_thread.join(timeout=2)
                stderr_output = "".join(stderr_chunks)

            changed_files: list[str] = []
            diff_stat = ""
            final_commit = baseline_commit
            if repo_path is not None:
                status_out = subprocess.run(
                    ["git", "-C", str(worktree_dir), "status", "--porcelain"],
                    capture_output=True, text=True,
                ).stdout
                changed_files = [line[3:] for line in status_out.splitlines() if line.strip()]

                # The worktree is disposable once the branch is pushed/reviewed,
                # so any uncommitted diff must be captured as a real commit here
                # rather than relying on the model to remember to `git commit` -
                # otherwise removing the worktree silently discards the work.
                if changed_files:
                    subprocess.run(["git", "-C", str(worktree_dir), "add", "-A"], check=True, capture_output=True)
                    commit_msg = (
                        f"Rabit agent task {task.task_id}: automated commit of worker changes\n\n"
                        f"Run {run_id} on branch {branch_name}."
                    )
                    subprocess.run(
                        [
                            "git", "-C", str(worktree_dir),
                            "-c", "user.name=Rabit Claude Worker",
                            "-c", "user.email=claude-worker@rabit.local",
                            "commit", "-m", commit_msg,
                        ],
                        check=True, capture_output=True, text=True,
                    )
                    final_commit = subprocess.run(
                        ["git", "-C", str(worktree_dir), "rev-parse", "HEAD"],
                        capture_output=True, text=True, check=True,
                    ).stdout.strip()

                diff_stat = subprocess.run(
                    ["git", "-C", str(worktree_dir), "diff", "--stat", f"{baseline_commit}..{final_commit}"],
                    capture_output=True, text=True,
                ).stdout

            return RunResult(
                task_id=task.task_id,
                run_id=run_id,
                exit_code=process.returncode,
                started_at=started_at,
                finished_at=time.time(),
                timed_out=timed_out,
                cancelled=handle.cancelled,
                workspace_path=str(worktree_dir),
                branch_name=branch_name,
                baseline_commit=baseline_commit,
                final_commit=final_commit,
                changed_files=changed_files,
                diff_stat=diff_stat,
                result_summary=result_summary,
                raw_stdout_lines=len(stdout_lines),
                stderr_tail=stderr_output[-4000:],
                tool_calls=tool_calls,
                cost_usd=cost_usd,
            )

        return handle, _wait


def _parse_stream_line(line: str) -> list[dict]:
    """Parses one line of --output-format stream-json and returns the list of
    SAFE events it carries (never includes thinking/redacted_thinking content
    blocks - constitution: "must not expose or persist hidden chain-of-thought").

    Returns a LIST because a single assistant message routinely holds multiple
    content blocks (e.g. a text block followed by one or more tool_use blocks,
    or several parallel tool_use blocks); emitting only the first would drop
    every sibling tool call and desync run.tool.started/completed on the
    dashboard. An empty list means the line carried nothing forwardable."""
    line = line.strip()
    if not line:
        return []
    try:
        obj = json.loads(line)
    except json.JSONDecodeError:
        # Text-fallback mode: treat the raw line as plain progress text (scrubbed).
        return [{"kind": "text_fallback", "text": _scrub_secrets(line)}]

    msg_type = obj.get("type")
    events: list[dict] = []
    if msg_type == "assistant":
        content = obj.get("message", {}).get("content", [])
        for block in content:
            if not isinstance(block, dict):
                continue
            btype = block.get("type")
            if btype in THINKING_BLOCK_TYPES:
                continue
            if btype == "tool_use":
                # id correlates this call with its later tool_result (which
                # carries the same value as tool_use_id) - needed to persist a
                # durable, matched work-log entry (constitution transparency:
                # what tool was called, with what input, what it returned).
                events.append({
                    "kind": "tool_call",
                    "id": block.get("id"),
                    "tool_name": block.get("name"),
                    "input": _redact(block.get("input", {})),
                })
            elif btype == "text":
                # The model's own prose is also untrusted - it can echo a
                # secret it just read (e.g. summarizing a .env). Scrub it too.
                events.append({"kind": "final_text", "text": _scrub_secrets(block.get("text", ""))})
        return events
    if msg_type == "user":
        # Tool RESULTS arrive as a "user"-role message in the stream-json
        # protocol (the CLI feeding the tool's output back to the model) -
        # this is the completion signal paired with the "tool_call" kind
        # above, needed to emit a run.tool.completed event alongside
        # run.tool.started. The tool's OUTPUT is untrusted and routinely
        # contains secrets (e.g. `cat .env`), so it is scrubbed before it can
        # reach the event bus / replay log.
        content = obj.get("message", {}).get("content", [])
        for block in content:
            if isinstance(block, dict) and block.get("type") == "tool_result":
                raw_content = block.get("content", "")
                preview = raw_content if isinstance(raw_content, str) else json.dumps(raw_content)
                events.append({
                    "kind": "tool_result",
                    "tool_use_id": block.get("tool_use_id"),
                    "is_error": bool(block.get("is_error", False)),
                    "content_preview": _scrub_secrets(preview[:2000])[:500],
                })
        return events
    if msg_type == "result":
        events = [{"kind": "final_text", "text": _scrub_secrets(obj.get("result", ""))}]
        cost = obj.get("total_cost_usd")
        if isinstance(cost, (int, float)):
            events.append({"kind": "cost", "cost_usd": float(cost)})
        return events
    if msg_type is None:
        return [{"kind": "other"}]
    return [{"kind": "other", "raw_type": msg_type}]


_SECRET_KEY_PATTERN = re.compile(r"(password|secret|api[_-]?key|token|credential)", re.IGNORECASE)

# Value-level secret shapes. Tool inputs and outputs carry secrets under
# innocuous keys ({"command": "curl -H 'Authorization: Bearer ...'"}), so key
# names alone are not enough - the values themselves must be scrubbed.
_SECRET_VALUE_PATTERNS = [
    re.compile(r"AKIA[0-9A-Z]{16}"),                                   # AWS access key id
    re.compile(r"\bsk-[A-Za-z0-9_\-]{16,}"),                           # sk- style API keys
    re.compile(r"(?i)bearer\s+[A-Za-z0-9._\-]{12,}"),                  # bearer tokens
    re.compile(r"gh[pousr]_[A-Za-z0-9]{20,}"),                         # GitHub tokens
    re.compile(r"xox[baprs]-[A-Za-z0-9-]{10,}"),                       # Slack tokens
    re.compile(r"(?i)\b(?:postgres(?:ql)?|mysql|mongodb(?:\+srv)?|redis|amqp)://[^\s\"']*:[^\s\"'@/]+@"),  # creds in a URL
    re.compile(r"(?i)(password|secret|api[_-]?key|token|credential|dsn)\s*[=:]\s*\S+"),  # KEY=VALUE / KEY: VALUE
    # PEM private-key blocks (SSH/TLS deploy keys read during debugging).
    re.compile(r"-----BEGIN[A-Z ]*PRIVATE KEY-----[\s\S]+?-----END[A-Z ]*PRIVATE KEY-----"),
    re.compile(r"-----BEGIN[A-Z ]*PRIVATE KEY-----"),  # header alone (truncated body)
]

# Platform secrets the worker holds in its own environment (systemd
# EnvironmentFile=.env). The child `claude` process has no legitimate need
# for them, and inheriting them means an agent that runs `env`/`printenv`
# could echo them into its output. Strip them from the child's environment.
_SENSITIVE_ENV_KEYS = {
    "DATABASE_URL", "POSTGRES_PASSWORD", "POSTGRES_USER", "SESSION_SECRET_KEY",
    "REDIS_URL", "REDIS_PASSWORD", "RABIT_API_URL",
}
_SENSITIVE_ENV_HINT = re.compile(r"(password|secret|token|api[_-]?key|credential|dsn)", re.IGNORECASE)


def _sanitized_env() -> dict:
    """A copy of the current environment with platform secrets removed, for
    the child `claude` process (which never needs them). CLAUDE_* is preserved
    - it can carry the CLI's own auth/session, which we must not strip."""
    def keep(k: str) -> bool:
        if k.startswith("CLAUDE"):
            return True
        if k in _SENSITIVE_ENV_KEYS:
            return False
        return not _SENSITIVE_ENV_HINT.search(k)

    return {k: v for k, v in os.environ.items() if keep(k)}


def _scrub_secrets(text):
    """Redacts common secret shapes from a free-text value. Best-effort and
    intentionally err-on-the-safe-side, since this feeds a broadcast/replayed
    preview that must never carry credentials."""
    if not isinstance(text, str):
        return text
    scrubbed = text
    for pat in _SECRET_VALUE_PATTERNS:
        scrubbed = pat.sub("***REDACTED***", scrubbed)
    return scrubbed


def _redact(payload):
    """Redacts a tool-input structure recursively: a value is dropped when its
    KEY name looks secret, and every remaining string value is run through the
    value-level scrubber (so secrets under innocuous keys, or nested inside
    lists/dicts, are caught too)."""
    def _walk(value):
        if isinstance(value, dict):
            return {
                k: ("***REDACTED***" if _SECRET_KEY_PATTERN.search(str(k)) else _walk(v))
                for k, v in value.items()
            }
        if isinstance(value, list):
            return [_walk(v) for v in value]
        if isinstance(value, str):
            return _scrub_secrets(value)
        return value

    if not isinstance(payload, dict):
        return payload
    return _walk(payload)
