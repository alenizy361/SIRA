"""REAL end-to-end proof that services/claude-worker can drive the actual,
locally-authenticated Claude Code CLI (Claude Max subscription, no API key)
against a sample repository in an isolated git worktree.

This is intentionally the only test in the suite that spends real Claude
usage - kept to one minimal, cheap task (fix one failing test in a 20-line
sample file) rather than run repeatedly, per the constitution's evidence
requirement ("prove one real end-to-end agent invocation") without wasting
the user's Claude Max quota.

Skipped automatically if the CLI is not authenticated in this environment,
so the rest of the suite stays runnable anywhere.
"""
import json
import subprocess
import sys
import uuid
from pathlib import Path

import pytest

from claude_worker.cli_adapter import AuthenticationRequiredError, ClaudeCodeAdapter
from claude_worker.task_contract import TaskContract

REPO_ROOT = Path(__file__).resolve().parents[2]
SAMPLE_REPO = REPO_ROOT / "workspace" / "sample-cv-app"


def _cli_authenticated() -> bool:
    try:
        result = subprocess.run(["claude", "auth", "status"], capture_output=True, text=True, timeout=15)
        return json.loads(result.stdout).get("loggedIn", False)
    except Exception:
        return False


pytestmark = pytest.mark.skipif(
    not _cli_authenticated(), reason="claude CLI not authenticated in this environment"
)


def test_detect_capabilities_reports_real_cli_flags():
    adapter = ClaudeCodeAdapter(cli_path="claude", workspace_root=REPO_ROOT / "workspace")
    caps = adapter.detect_capabilities()
    assert caps.version
    assert caps.supports_stream_json
    assert caps.supports_permission_mode
    assert caps.supports_allowed_tools


def test_check_auth_returns_oauth_not_api_key():
    adapter = ClaudeCodeAdapter(cli_path="claude", workspace_root=REPO_ROOT / "workspace")
    status = adapter.check_auth()
    assert status["loggedIn"] is True
    # Constitution requirement: no Anthropic API key, use the local Claude Max login.
    assert status.get("authMethod") != "api_key"


def test_real_task_fixes_failing_test_in_isolated_worktree():
    # Internal timeout is enforced by TaskContract.timeout_seconds inside the
    # adapter's own wait loop (see cli_adapter.py) - no external pytest-timeout
    # dependency needed.
    assert SAMPLE_REPO.exists(), "sample repo must exist - see workspace/sample-cv-app"

    adapter = ClaudeCodeAdapter(cli_path="claude", workspace_root=REPO_ROOT / "workspace")
    task_id = f"e2e-{uuid.uuid4().hex[:8]}"

    task = TaskContract(
        task_id=task_id,
        mission=(
            "In this small sample repository, tests/test_step3.py currently fails. "
            "Find the minimal code change in src/funnel/step3.py that makes it pass "
            "without changing the test file, then run the test to confirm it passes."
        ),
        context=(
            "This is a throwaway demo repo for Rabit AI Company OS's Frontend Engineer "
            "agent. It has one Python module (src/funnel/step3.py) and one pytest file "
            "(tests/test_step3.py)."
        ),
        constraints=[
            "Only modify files under src/funnel/",
            "Do not modify tests/test_step3.py",
            "Do not touch anything outside this repository",
        ],
        allowed_tools=["Read", "Edit", "Bash(python3 -m pytest tests/test_step3.py)"],
        prohibited_actions=["deploy_production", "delete_files_outside_repo"],
        acceptance_criteria=["tests/test_step3.py passes"],
        output_schema=None,
        timeout_seconds=300,
        risk_level="R2",
        workspace_repo="sample-cv-app",
    )

    events = []
    handle, wait = adapter.start_run(task, on_event=events.append)
    result = wait()

    assert result.exit_code == 0, f"CLI exited non-zero. stderr tail: {result.stderr_tail}"
    assert not result.timed_out
    assert any("step3.py" in f for f in result.changed_files), (
        f"expected step3.py to be changed, got: {result.changed_files}"
    )
    assert result.final_commit and result.final_commit != result.baseline_commit, (
        "worker must auto-commit its changes onto the isolated branch, not leave them "
        "as an uncommitted worktree diff that a later cleanup step would discard"
    )

    # Verify with the SAME interpreter running this suite (sys.executable),
    # not a bare "python3" which may resolve to an interpreter without pytest
    # installed - the fix's correctness must not hinge on PATH resolution.
    verify = subprocess.run(
        [sys.executable, "-m", "pytest", "tests/test_step3.py", "-q"],
        cwd=result.workspace_path,
        capture_output=True,
        text=True,
    )
    assert verify.returncode == 0, f"post-fix verification failed:\n{verify.stdout}\n{verify.stderr}"

    assert not any(e.get("kind") == "thinking" for e in events)

    # Safe to remove the worktree checkout now - the fix is committed on
    # result.branch_name, which survives (it's a real git ref, not the
    # ephemeral worktree directory).
    adapter._remove_worktree(SAMPLE_REPO, Path(result.workspace_path))

    branch_check = subprocess.run(
        ["git", "-C", str(SAMPLE_REPO), "log", "-1", "--format=%H", result.branch_name],
        capture_output=True, text=True, check=True,
    ).stdout.strip()
    assert branch_check == result.final_commit, "fix must survive worktree removal on the branch ref"


def test_reply_resumes_the_real_cli_session_with_actual_conversational_memory():
    """The whole point of the chat/reply feature: a second real CLI
    invocation with resume_session_id set must genuinely remember the first
    turn - not just re-send a text digest of it. Kept minimal (no workspace,
    no tools) to spend as little real usage as possible while still proving
    it against the actual CLI, not a mock."""
    adapter = ClaudeCodeAdapter(cli_path="claude", workspace_root=REPO_ROOT / "workspace")

    def _bare_contract(task_id: str, mission: str) -> TaskContract:
        return TaskContract(
            task_id=task_id, mission=mission, context="", constraints=[],
            allowed_tools=[], prohibited_actions=[], acceptance_criteria=[],
            output_schema=None, timeout_seconds=120, risk_level="R0",
        )

    task_id = f"e2e-resume-{uuid.uuid4().hex[:8]}"
    secret_number = "471962"
    first = _bare_contract(
        task_id, f"Remember this number for later: {secret_number}. Reply with only the word 'ok'.",
    )
    handle, wait = adapter.start_run(first)
    first_result = wait()
    assert first_result.exit_code == 0, f"first turn failed: {first_result.stderr_tail}"
    assert first_result.cli_session_id, "the CLI must report a session id on its init message"

    second = _bare_contract(task_id, "What number did I just tell you? Reply with only the digits.")
    handle2, wait2 = adapter.start_run(second, resume_session_id=first_result.cli_session_id)
    second_result = wait2()

    assert second_result.exit_code == 0, f"resumed turn failed: {second_result.stderr_tail}"
    assert secret_number in second_result.result_summary, (
        f"resumed session did not recall the number from the first turn - got: {second_result.result_summary!r}"
    )
