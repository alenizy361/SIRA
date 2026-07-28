"""Constitution section 7.11 / 17: no raw shell built from user/task-provided
strings, no filesystem access outside approved workspace roots."""
import ast
import inspect

import pytest

from claude_worker import cli_adapter
from claude_worker.cli_adapter import ClaudeCodeAdapter, WorkspaceEscapeError


def test_adapter_never_uses_shell_true():
    """Static guarantee: shell=True would let a malicious task title or
    external content achieve shell injection via string interpolation. Uses
    an AST check (not a substring search) so a docstring/comment merely
    *mentioning* "shell=True" can't produce a false positive or, worse, a
    false negative if someone writes shell = True across two lines."""
    tree = ast.parse(inspect.getsource(cli_adapter))
    for node in ast.walk(tree):
        if isinstance(node, ast.Call):
            for kw in node.keywords:
                if kw.arg == "shell" and isinstance(kw.value, ast.Constant) and kw.value.value is True:
                    pytest.fail(f"Found shell=True in a Call at line {node.lineno}")


def test_adapter_builds_subprocess_args_as_a_list_not_a_joined_string():
    source = inspect.getsource(cli_adapter.ClaudeCodeAdapter.start_run)
    # args is built with `+=` on a list literal ([self.cli_path, "-p", prompt]);
    # a regression to f-string/join-based command construction would show up
    # as "shlex.join" or "' '.join(args)" feeding Popen - neither should exist.
    assert "shlex" not in source
    assert '" ".join' not in source


@pytest.mark.parametrize(
    "malicious_repo_path",
    [
        "../../etc",
        "../../../root",
        "sample-repo/../../../etc/passwd",
        "/etc/passwd",
        "sample-repo/../../..",
    ],
)
def test_workspace_traversal_rejected(tmp_path, malicious_repo_path):
    root = tmp_path / "workspace"
    (root / "sample-repo").mkdir(parents=True)
    adapter = ClaudeCodeAdapter(cli_path="claude", workspace_root=root)
    with pytest.raises((WorkspaceEscapeError, FileNotFoundError)):
        adapter._resolve_workspace_repo(malicious_repo_path)


@pytest.mark.parametrize(
    "malicious_task_id",
    [
        "../../../../tmp/pwned",
        "../../etc/passwd",
        "..",
        "foo/../../bar",
        "foo/bar",
        "",
    ],
)
def test_malicious_task_id_rejected_before_touching_filesystem(tmp_path, malicious_task_id):
    """This was a REAL path-traversal bug found while writing this test:
    `worktree_dir = repo.parent / ".worktrees" / f"{repo.name}-{task_id}"`
    looks contained (task_id is embedded inside one f-string), but pathlib's
    `/` operator splits on path separators regardless of how many arrived
    in a single string - `repo.parent / ".worktrees" / "sample-repo-../../../../tmp/pwned"`
    resolves to `/tmp/tmp/pwned`, completely outside the workspace root.
    Fixed in cli_adapter.py by validating task_id as a single safe path
    component (and double-checking containment after resolve()) before it
    is ever used to build a filesystem path."""
    root = tmp_path / "workspace"
    repo = root / "sample-repo"
    repo.mkdir(parents=True)
    adapter = ClaudeCodeAdapter(cli_path="claude", workspace_root=root)

    with pytest.raises(WorkspaceEscapeError):
        adapter._create_worktree(repo, malicious_task_id, branch_name=None)

    # Confirm nothing was created outside the workspace as a side effect.
    assert not (tmp_path / "tmp").exists()
    assert not (root.parent / "etc").exists()


def test_safe_task_id_still_works(tmp_path):
    from claude_worker.cli_adapter import _safe_path_component

    assert _safe_path_component("task-abc123") == "task-abc123"
    assert _safe_path_component("e2e-50bfca30") == "e2e-50bfca30"
