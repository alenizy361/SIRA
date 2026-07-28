# Onboarding

## First run

1. Open the dashboard URL. With no organization yet created, you're
   redirected to `/onboarding`.
2. Fill in: organization name, your admin email, a password (12+
   characters), and your display name. This is a one-time bootstrap -
   `POST /auth/onboard` returns 403 on any subsequent attempt once an
   organization exists.
3. You're logged in immediately (a session cookie is set) and land on
   `/command-center`.

## Your first goal

1. On the command center, type a goal in plain language (Arabic or
   English - the UI defaults to Arabic with instant switching), e.g.
   "Review the CV builder funnel and fix the largest conversion
   drop-off." Or use the push-to-talk voice button (browser
   SpeechRecognition; falls back to text entry with a clear message where
   unsupported) - it shows a transcript confirmation step before creating
   anything.
2. This calls `POST /goals` for real.
3. From the goal's detail page, trigger planning
   (`POST /goals/{id}/plan`) - this makes a genuine Claude Code CLI call
   as the CEO agent and can take 20 seconds to a few minutes. It returns a
   real plan with 2-5 concrete tasks assigned to specific agents
   (frontend_engineer, qa, analytics, etc.) at bounded R0-R2 risk levels.
4. Check `/agents` to see which of the 23 agents are active vs. disabled
   (agents needing an unconfigured integration - marketing, analytics,
   support, etc. - show `enabled: false` with a clear reason; this is
   accurate, not a bug).
5. Check `/approvals` for anything requiring your sign-off, and `/audit`
   for the full attributable history of every action taken so far.

## What "autonomy mode" controls

Set at the organization level (`organizations.autonomy_mode`, default
`execute_low_risk`): `observe_only` and `suggest` prevent the
claude-worker poll loop from picking up any new task automatically;
`execute_low_risk` (default) and `controlled_autonomous` allow it. `POST
/system/emergency-stop` sets this to `observe_only` immediately.

## Configuring integrations

None are configured out of the box (`/integrations` correctly shows every
provider as "Not configured" - GitHub, Vercel, Sentry, PostHog, Google
Analytics/Search Console/Ads, email, support platform, payment
processor). Wiring a real one requires supplying real credentials via
environment variables (see `.env.example`) - there is no in-UI credential
entry yet (a deliberate scope cut: constitution section 16 requires
secrets never enter the database or logs, so credential entry needs a
dedicated secure-storage flow that is a follow-up, not this build).
