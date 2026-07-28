# Security

## Authentication

- Passwords hashed with Argon2id (`passlib`, `apps/api/app/auth/security.py`).
- Sessions are server-side, revocable rows (`sessions` table) referenced by
  an opaque `{session_id}.{random_token}` cookie value - the raw token is
  never stored, only its Argon2 hash. This is deliberately not a JWT: a
  compromised or logged-out session can be immediately invalidated by
  deleting/marking the DB row, which a stateless JWT cannot do.
- Account lockout: 5 failed attempts locks the account for 15 minutes
  (configurable via `ACCESS_LOCKOUT_MAX_ATTEMPTS` /
  `ACCESS_LOCKOUT_WINDOW_SECONDS`). Verified: a 6th attempt (even with the
  correct password) returns 423 during the lockout window.
- Onboarding (`POST /auth/onboard`) is a one-time bootstrap - it 403s if
  any organization already exists. There is currently no self-service
  multi-tenant signup; additional users are provisioned by an
  administrator (not yet a UI flow - direct DB/API access today).

## Authorization

- Every mutating query filters by `organization_id` derived from the
  authenticated session, never from a client-supplied value. Verified with
  a dedicated cross-organization isolation test suite
  (`tests/security/test_org_isolation.py`) that inserts a second
  organization directly via SQL and confirms org A's session cannot list,
  fetch, or transition org B's goals.
- `packages/permission-engine` enforces R0-R5 risk levels: R5 and a
  hard-coded prohibited-action list are always rejected regardless of any
  policy configuration (`contracts.risk_levels.HARD_PROHIBITED_ACTIONS`);
  an agent can never execute above its own configured risk ceiling, even
  if its tool/action allowlists would otherwise permit it.

## Prompt-injection defense

`services/claude-worker/claude_worker/prompt_builder.py` treats task
context (which may include repository contents or other external data) as
labeled, untrusted reference data, explicitly instructed to never be
treated as an instruction. This is defense-in-depth only - the real
enforcement is code-level: tool access is restricted via the `claude` CLI's
`--allowedTools` flag (an allowlist derived from the agent's spec, not
from anything in the prompt), so even a successful injection cannot
grant a tool the agent wasn't already permitted to use.
`--dangerously-skip-permissions` is never used anywhere in this codebase.
See `tests/security/test_prompt_injection.py` for the structural
regression tests (constraints/prohibited-actions always precede untrusted
context in the rendered prompt; task-level fields are never derived from
context text).

## Path traversal / command injection

- `ClaudeCodeAdapter` never uses `shell=True` (enforced by an AST-based
  test, not a substring search, in `tests/security/test_command_injection.py`).
- A real path-traversal vulnerability was found and fixed during
  development: a task's `task_id` embedded in a worktree directory name
  could contain `../` sequences that escaped the workspace root entirely
  once resolved by `pathlib`. Fixed via `_safe_path_component()` (rejects
  anything that isn't a plain `[A-Za-z0-9._-]+` slug) plus a post-resolve
  containment check. See `docs/BUILD_STATUS.md` for the full writeup.
- Workspace repo paths are resolved and checked against the configured
  workspace root before any filesystem or git operation
  (`_resolve_workspace_repo`).

## Secrets

- `.env` is gitignored and never committed; `.env.example` uses
  `CHANGE_ME` placeholders that `scripts/install.sh` replaces with
  `openssl rand`-generated values.
- `CredentialMetadata` rows store only metadata (storage location,
  rotation/expiry dates) - never a raw secret value.
- Structured JSON logs redact any key matching
  `password|secret|api[_-]?key|token|credential` (`apps/api/app/logging_config.py`).
- The Claude Code worker redacts the same key patterns from tool-call
  input before it is ever stored or forwarded as an event
  (`claude_worker.cli_adapter._redact`).

## Dependency scanning

See `docs/BUILD_STATUS.md` "Dependency security scan" section - `pip-audit`
found and this build fixed 21 CVEs (unused `python-jose` removed entirely,
`python-multipart` and `fastapi`/`starlette` upgraded); `npm audit`'s 3
findings are unfixable without a 6-major-version Next.js downgrade and are
assessed as not exploitable in this app's usage pattern.

## What is NOT yet done

- No dedicated load/DoS testing (`tests/load/` is empty).
- No TOTP/2FA implementation yet (the `users.totp_secret`/`totp_enabled`
  columns exist but nothing sets or verifies them).
- CSRF: session cookies use `SameSite=Lax`, which mitigates most
  cross-site request forgery for a same-origin SPA, but there is no
  additional CSRF token yet for state-changing requests.
