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
import os
import re
import signal
import subprocess
import time
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable, Optional

from .prompt_builder import build_prompt
from .task_contract import TaskContract

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

    def _create_worktree(self, repo_path: Path, task_id: str, branch_name: str | None) -> tuple[Path, str]:
        # task_id can originate from a Task row created via the API - it must
        # be treated as untrusted input here. A task_id containing path
        # separators (e.g. "../../../../tmp/pwned") would otherwise let the
        # f-string below escape .worktrees entirely once resolved by pathlib,
        # since `/` joins are split into real path components regardless of
        # how many separators arrived in one string. Reject anything that
        # isn't a safe single path-component slug.
        safe_task_id = _safe_path_component(task_id)
        branch = branch_name or f"agent/task-{safe_task_id}"
        worktree_dir = repo_path.parent / ".worktrees" / f"{repo_path.name}-{safe_task_id}"
        worktree_dir.parent.mkdir(parents=True, exist_ok=True)

        resolved = worktree_dir.resolve()
        expected_parent = (repo_path.parent / ".worktrees").resolve()
        if expected_parent not in resolved.parents:
            raise WorkspaceEscapeError(f"Computed worktree path '{resolved}' escapes '.worktrees'")

        subprocess.run(
            ["git", "-C", str(repo_path), "worktree", "add", "-b", branch, str(worktree_dir), "HEAD"],
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

    # -- run ------------------------------------------------------------

    def start_run(
        self,
        task: TaskContract,
        run_id: str | None = None,
        on_event: Optional[Callable[[dict], None]] = None,
    ) -> tuple[RunHandle, Callable[[], RunResult]]:
        """Starts the CLI subprocess and returns (handle, wait_fn). Calling
        wait_fn() blocks (with the task's timeout) until the run finishes and
        returns the RunResult - split this way so a caller can hold the
        handle for cancellation while awaiting completion elsewhere."""
        run_id = run_id or str(uuid.uuid4())
        auth_status = self.check_auth()
        capabilities = self.detect_capabilities()

        repo_path = self._resolve_workspace_repo(task.workspace_repo) if task.workspace_repo else None
        if repo_path is not None:
            baseline_commit = subprocess.run(
                ["git", "-C", str(repo_path), "rev-parse", "HEAD"],
                capture_output=True, text=True, check=True,
            ).stdout.strip()
            worktree_dir, branch_name = self._create_worktree(repo_path, task.task_id, task.branch_name)
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
        if capabilities.supports_allowed_tools and task.allowed_tools:
            args += ["--allowedTools", ",".join(task.allowed_tools)]
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
        )
        handle = RunHandle(process)
        started_at = time.time()

        def _wait() -> RunResult:
            stdout_lines: list[str] = []
            tool_calls: list[dict] = []
            result_summary = ""
            timed_out = False

            deadline = started_at + task.timeout_seconds
            try:
                while True:
                    remaining = deadline - time.time()
                    if remaining <= 0:
                        timed_out = True
                        handle.cancel()
                        break
                    line = process.stdout.readline()
                    if line == "" and process.poll() is not None:
                        break
                    if not line:
                        continue
                    stdout_lines.append(line)
                    event = _parse_stream_line(line)
                    if event is None:
                        continue
                    if event.get("kind") == "tool_call":
                        tool_calls.append(event)
                    if event.get("kind") == "final_text":
                        result_summary = event.get("text", result_summary)
                    if on_event:
                        on_event(event)
            finally:
                stderr_output = process.stderr.read() if process.stderr else ""
                process.wait(timeout=10)

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
            )

        return handle, _wait


def _parse_stream_line(line: str) -> Optional[dict]:
    """Parses one line of --output-format stream-json and returns a SAFE
    event dict (never includes thinking/redacted_thinking content blocks -
    constitution: "must not expose or persist hidden chain-of-thought")."""
    line = line.strip()
    if not line:
        return None
    try:
        obj = json.loads(line)
    except json.JSONDecodeError:
        # Text-fallback mode: treat the raw line as plain progress text.
        return {"kind": "text_fallback", "text": line}

    msg_type = obj.get("type")
    if msg_type == "assistant":
        content = obj.get("message", {}).get("content", [])
        for block in content:
            if block.get("type") in THINKING_BLOCK_TYPES:
                continue
            if block.get("type") == "tool_use":
                return {"kind": "tool_call", "tool_name": block.get("name"), "input": _redact(block.get("input", {}))}
            if block.get("type") == "text":
                return {"kind": "final_text", "text": block.get("text", "")}
    if msg_type == "user":
        # Tool RESULTS arrive as a "user"-role message in the stream-json
        # protocol (the CLI feeding the tool's output back to the model) -
        # this is the completion signal paired with the "tool_call" kind
        # above, needed to emit a run.tool.completed event alongside
        # run.tool.started.
        content = obj.get("message", {}).get("content", [])
        for block in content:
            if isinstance(block, dict) and block.get("type") == "tool_result":
                raw_content = block.get("content", "")
                preview = raw_content if isinstance(raw_content, str) else json.dumps(raw_content)
                return {
                    "kind": "tool_result",
                    "tool_use_id": block.get("tool_use_id"),
                    "is_error": bool(block.get("is_error", False)),
                    "content_preview": preview[:500],
                }
    if msg_type == "result":
        return {"kind": "final_text", "text": obj.get("result", "")}
    return {"kind": "other", "raw_type": msg_type}


_SECRET_KEY_PATTERN = re.compile(r"(password|secret|api[_-]?key|token|credential)", re.IGNORECASE)


def _redact(payload: dict) -> dict:
    return {
        k: ("***REDACTED***" if _SECRET_KEY_PATTERN.search(k) else v)
        for k, v in payload.items()
    }
