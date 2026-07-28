"use client";

import { useMemo, useState } from "react";
import Link from "next/link";
import { useMutation, useQuery, useQueryClient, type UseQueryResult } from "@tanstack/react-query";
import { useI18n } from "@/i18n/I18nProvider";
import { useToast } from "@/components/ToastProvider";
import { NeuralCommandBoard } from "@/components/NeuralCommandBoard";
import { VoiceCapture } from "@/components/VoiceCapture";
import { SpeakButton } from "@/components/SpeakButton";
import { useEventStore, type WireEvent } from "@/store/eventStore";
import { agentMeta } from "@/lib/agentMeta";
import { toTransmission, TONE_COLOR } from "@/lib/transmissions";
import {
  agentsApi,
  approvalsApi,
  goalsApi,
  healthApi,
  systemApi,
  ApiError,
  type Goal,
  type WorkerStatus,
} from "@/lib/api";

/** Honest, always-visible answer to "will anything happen when I send a
 *  command?". Three states from the host worker's Redis heartbeat:
 *  running (green) / asleep-not-authed (amber) / down (red). */
function WorkerBanner({
  query,
}: {
  query: UseQueryResult<WorkerStatus, unknown>;
}) {
  const { t } = useI18n();

  // Still fetching the very first time, or the endpoint isn't in this build
  // yet (older API) - stay quiet rather than crying wolf.
  if (query.isLoading) {
    return (
      <div className="glass flex items-center gap-3 rounded-2xl px-4 py-2.5 text-sm text-slate-400">
        <span className="h-2.5 w-2.5 animate-pulse rounded-full bg-slate-500" aria-hidden />
        {t("command_center.worker_checking")}
      </div>
    );
  }
  const data = query.data;
  if (!data) {
    // /health/worker not available (404) - don't render a scary banner.
    return null;
  }

  let tone: "ok" | "warn" | "bad";
  let title: string;
  let hint: string | null = null;

  if (!data.alive) {
    tone = "bad";
    title = t("command_center.worker_down");
    hint = t("command_center.worker_down_hint");
  } else if (!data.authed) {
    tone = "warn";
    title = t("command_center.worker_asleep");
    hint = t("command_center.worker_asleep_hint");
  } else {
    tone = "ok";
    const lag = data.seconds_since_heartbeat ?? 0;
    title =
      lag > 45
        ? t("command_center.worker_running_lag").replace("{seconds}", String(lag))
        : t("command_center.worker_running");
  }

  const styles: Record<typeof tone, { border: string; bg: string; dot: string; text: string }> = {
    ok: {
      border: "border-emerald-400/30",
      bg: "bg-emerald-500/[0.07]",
      dot: "bg-emerald-400 shadow-[0_0_10px_#34d399]",
      text: "text-emerald-200",
    },
    warn: {
      border: "border-amber-400/40",
      bg: "bg-amber-500/[0.08]",
      dot: "bg-amber-400 shadow-[0_0_10px_#fbbf24] animate-pulse",
      text: "text-amber-200",
    },
    bad: {
      border: "border-red-400/40",
      bg: "bg-red-500/[0.08]",
      dot: "bg-red-400 shadow-[0_0_10px_#f87171] animate-pulse",
      text: "text-red-200",
    },
  };
  const s = styles[tone];

  return (
    <div className={`flex flex-wrap items-center gap-x-3 gap-y-1 rounded-2xl border ${s.border} ${s.bg} px-4 py-2.5`}>
      <span className={`h-2.5 w-2.5 shrink-0 rounded-full ${s.dot}`} aria-hidden />
      <span className={`text-sm font-semibold ${s.text}`}>{title}</span>
      {hint ? (
        <code className="rounded bg-black/30 px-2 py-0.5 font-mono text-[11px] text-slate-300">
          {hint}
        </code>
      ) : null}
    </div>
  );
}

/** Compact live feed - every REAL event as "<agent> <did> <what>". */
function LiveFeed({ agents }: { agents: { agent_key: string; display_name_en: string; display_name_ar: string }[] }) {
  const { t, locale } = useI18n();
  const events = useEventStore((s) => s.events);

  const nameFor = useMemo(() => {
    const map = new Map<string, string>();
    for (const a of agents) map.set(a.agent_key, locale === "ar" ? a.display_name_ar : a.display_name_en);
    return (actor: string) => map.get(actor) || (actor === "system" ? (locale === "ar" ? "النظام" : "System") : actor);
  }, [agents, locale]);

  const visible = useMemo(
    () => events.filter((e) => e.type !== "core.state.changed").slice(0, 14),
    [events]
  );

  if (visible.length === 0) {
    return <p className="py-6 text-center text-xs text-slate-500">{t("command_center.no_events")}</p>;
  }
  return (
    <div className="flex flex-col">
      {visible.map((e: WireEvent) => {
        const tx = toTransmission(e);
        const meta = agentMeta(e.actor);
        const time = new Date(e.timestamp).toLocaleTimeString(locale === "ar" ? "ar-SA" : undefined, {
          hour: "2-digit",
          minute: "2-digit",
          second: "2-digit",
        });
        return (
          <div
            key={e.event_id}
            className="grid grid-cols-[auto_1fr_auto] items-center gap-2 border-t border-white/5 py-2 first:border-t-0"
          >
            <span
              className="h-2 w-2 rounded-full"
              style={{ background: meta.color, boxShadow: `0 0 8px ${meta.color}` }}
              aria-hidden
            />
            <span className="min-w-0">
              <span className="block truncate text-[11.5px] font-bold" style={{ color: meta.color }}>
                {nameFor(e.actor)}
              </span>
              <span className="block truncate text-[10.5px] text-slate-400">
                <span style={{ color: TONE_COLOR[tx.tone] }}>{locale === "ar" ? tx.verbAr : tx.verbEn}</span>
                {tx.detail ? ` · ${tx.detail}` : ""}
              </span>
            </span>
            <time className="text-[9.5px] tabular-nums text-slate-600">{time}</time>
          </div>
        );
      })}
    </div>
  );
}

export default function CommandCenterPage() {
  const { t } = useI18n();
  const toast = useToast();
  const queryClient = useQueryClient();
  const [goalTitle, setGoalTitle] = useState("");
  const [confirmingStop, setConfirmingStop] = useState(false);

  const coreState = useEventStore((s) => s.coreState);
  const wsStatus = useEventStore((s) => s.wsStatus);
  const events = useEventStore((s) => s.events);

  const agentsQuery = useQuery({ queryKey: ["agents"], queryFn: agentsApi.list });
  const approvalsQuery = useQuery({ queryKey: ["approvals"], queryFn: approvalsApi.list });
  const goalsQuery = useQuery({ queryKey: ["goals"], queryFn: goalsApi.list });
  const healthQuery = useQuery({
    queryKey: ["health-dependencies"],
    queryFn: healthApi.dependencies,
    refetchInterval: 15_000,
  });

  const workerQuery = useQuery({
    queryKey: ["worker-status"],
    queryFn: healthApi.worker,
    refetchInterval: 8_000,
  });

  const requestPlan = useMutation({
    mutationFn: (goalId: string) => goalsApi.requestPlan(goalId),
    onSuccess: () => {
      toast.show(t("command_center.plan_requested"), "success");
      queryClient.invalidateQueries({ queryKey: ["goals"] });
    },
    onError: (err) => {
      if (err instanceof ApiError && err.status === 409) {
        toast.show(t("command_center.plan_already_requested"), "info");
      } else {
        toast.show(t("common.error_generic"), "error");
      }
    },
  });

  // Sending a goal must actually START the company: create it AND immediately
  // ask the CEO to plan (which fires the PLANNING core-state + the downstream
  // plan/task cascade). One action, immediate reaction - no separate step.
  const createGoal = useMutation({
    mutationFn: (title: string) => goalsApi.create({ title, description: title, source: "user" }),
    onSuccess: (goal) => {
      toast.show(t("command_center.goal_created"), "success");
      setGoalTitle("");
      queryClient.invalidateQueries({ queryKey: ["goals"] });
      if (goal?.id) requestPlan.mutate(goal.id);
    },
    onError: () => toast.show(t("common.error_generic"), "error"),
  });

  const emergencyStop = useMutation({
    mutationFn: systemApi.emergencyStop,
    onSuccess: () => toast.show(t("command_center.emergency_stop_sent"), "success"),
    onError: (err) => {
      if (err instanceof ApiError && err.notImplemented) {
        toast.show(t("command_center.emergency_stop_not_wired"), "info");
      } else {
        toast.show(t("common.error_generic"), "error");
      }
    },
  });

  function handleCreateGoal(e: React.FormEvent) {
    e.preventDefault();
    const trimmed = goalTitle.trim();
    if (!trimmed) return;
    createGoal.mutate(trimmed);
  }

  const agents = agentsQuery.data ?? [];
  const enabledCount = agents.filter((a) => a.enabled).length;
  const stateLabel = t(`core_states.${coreState}`);
  const capturedGoal: Goal | undefined = (goalsQuery.data ?? []).find(
    (g) => g.state === "goal_captured"
  );
  const pendingApprovals = approvalsQuery.data ?? [];

  // Real activity gauge: events that landed in the last 60s.
  const eventsPerMin = useMemo(() => {
    const cutoff = Date.now() - 60_000;
    return events.filter((e) => new Date(e.timestamp).getTime() > cutoff).length;
  }, [events]);

  return (
    <div className="mx-auto flex max-w-[1680px] flex-col gap-4">
      {/* HUD header */}
      <div className="flex flex-wrap items-center justify-between gap-3">
        <div className="flex items-center gap-3">
          <h1 className="text-lg font-semibold tracking-tight sm:text-xl">
            {t("command_center.title")}
          </h1>
          <span className="rounded-full border border-white/10 bg-white/[0.04] px-3 py-1 text-xs font-medium text-slate-200">
            {stateLabel}
          </span>
          <SpeakButton text={stateLabel} />
        </div>
        <div className="flex items-center gap-2 text-xs">
          <span
            className={`inline-block h-2 w-2 rounded-full ${
              wsStatus === "open" ? "bg-emerald-400 shadow-[0_0_8px_#34d399]" : "bg-amber-400 animate-pulse"
            }`}
            aria-hidden
          />
          <span className="text-slate-400">
            {wsStatus === "open" ? t("common.connected") : t("common.reconnecting")}
          </span>
          <span className="text-slate-600">·</span>
          <span className="text-slate-400">
            {enabledCount}/{agents.length} {t("command_center.agents_active")}
          </span>
        </div>
      </div>

      {/* Company status banner - the honest answer to "is anything actually
          going to happen when I send a command?". Reads the host worker's
          heartbeat: running / asleep (not logged in) / down. */}
      <WorkerBanner query={workerQuery} />

      {/* three-column neural layout */}
      <div className="grid grid-cols-1 gap-4 xl:grid-cols-[250px_minmax(0,1fr)_300px]">
        {/* side A: status + voice + approvals */}
        <div className="order-2 flex min-w-0 flex-col gap-4 xl:order-1">
          <section className="glass rounded-2xl p-4">
            <h3 className="mb-3 text-[11px] font-bold uppercase tracking-[0.14em] text-slate-400">
              {t("command_center.core_status")}
            </h3>
            <div className="flex flex-col gap-3 text-sm">
              <div className="flex items-baseline justify-between">
                <span className="text-xs text-slate-400">{t("command_center.core_stage")}</span>
                <b className="text-violet-300">{stateLabel}</b>
              </div>
              <div className="flex items-baseline justify-between">
                <span className="text-xs text-slate-400">{t("command_center.connections")}</span>
                <b className="tabular-nums">{enabledCount} / {agents.length || 23}</b>
              </div>
              <div className="flex items-baseline justify-between">
                <span className="text-xs text-slate-400">{t("command_center.events_per_min")}</span>
                <b className="tabular-nums text-cyan-300">{eventsPerMin}</b>
              </div>
              <div className="h-1 overflow-hidden rounded-full bg-white/10">
                <i
                  className="block h-full rounded-full bg-gradient-to-r from-violet-500 to-cyan-400 transition-all duration-700"
                  style={{ width: `${Math.min(100, eventsPerMin * 8)}%` }}
                />
              </div>
            </div>
          </section>

          <section className="glass rounded-2xl p-4">
            <h3 className="mb-3 text-[11px] font-bold uppercase tracking-[0.14em] text-slate-400">
              {t("command_center.voice_ptt")}
            </h3>
            <VoiceCapture onConfirmed={(text) => setGoalTitle(text)} />
            {capturedGoal ? (
              <button
                type="button"
                onClick={() => requestPlan.mutate(capturedGoal.id)}
                disabled={requestPlan.isPending}
                className="mt-3 w-full rounded-lg border border-violet-400/40 bg-violet-500/10 px-3 py-2 text-sm font-semibold text-violet-200 transition-colors hover:bg-violet-500/20 disabled:opacity-40"
              >
                {t("command_center.request_plan")}
                <span className="block max-w-full truncate text-[11px] font-normal text-violet-300/70">
                  {capturedGoal.title}
                </span>
              </button>
            ) : null}
          </section>

          <Link
            href="/approvals"
            className="glass flex flex-col gap-1 rounded-2xl p-4 transition-colors hover:bg-white/[0.05]"
          >
            <span className="text-[11px] font-bold uppercase tracking-[0.14em] text-slate-400">
              {t("command_center.pending_approvals")}
            </span>
            <span className="text-3xl font-semibold tabular-nums">
              {approvalsQuery.isLoading ? "…" : pendingApprovals.length}
            </span>
            {pendingApprovals.slice(0, 2).map((a) => (
              <span key={a.id} className="truncate text-[11px] text-amber-200/80">
                • {a.action}
              </span>
            ))}
            <span className="text-xs text-emerald-300">{t("command_center.view_all")} →</span>
          </Link>
        </div>

        {/* center: the neural board */}
        <div className="order-1 min-w-0 xl:order-2">
          <NeuralCommandBoard agents={agents} />
        </div>

        {/* side B: live feed + system metrics */}
        <div className="order-3 flex min-w-0 flex-col gap-4">
          <section className="glass rounded-2xl p-4">
            <div className="mb-2 flex items-center justify-between">
              <h3 className="text-[11px] font-bold uppercase tracking-[0.14em] text-slate-400">
                {t("command_center.live_feed")}
              </h3>
              <span className="h-1.5 w-1.5 animate-pulse rounded-full bg-cyan-400 shadow-[0_0_8px_#22d3ee]" aria-hidden />
            </div>
            <LiveFeed agents={agents} />
          </section>

          <section className="glass rounded-2xl p-4">
            <h3 className="mb-3 text-[11px] font-bold uppercase tracking-[0.14em] text-slate-400">
              {t("command_center.system_health")}
            </h3>
            {healthQuery.isLoading ? (
              <span className="text-sm text-slate-500">{t("common.loading")}</span>
            ) : healthQuery.isError ? (
              <span className="text-sm text-red-300">{t("common.error_generic")}</span>
            ) : (
              <div className="flex flex-col gap-2">
                {Object.entries(healthQuery.data || {}).map(([name, check]) => (
                  <div key={name} className="flex items-center justify-between text-sm">
                    <span className="capitalize text-slate-300">{name}</span>
                    <span
                      className={
                        check.status === "ok"
                          ? "rounded-full bg-emerald-500/15 px-2 py-0.5 text-xs text-emerald-300"
                          : "rounded-full bg-red-500/15 px-2 py-0.5 text-xs text-red-300"
                      }
                    >
                      {check.status}
                    </span>
                  </div>
                ))}
              </div>
            )}
          </section>
        </div>
      </div>

      {/* bottom command bar */}
      <div className="flex flex-col gap-3 sm:flex-row sm:items-stretch">
        <form onSubmit={handleCreateGoal} className="glass flex flex-1 items-center gap-2 rounded-2xl p-2 ps-4">
          <input
            value={goalTitle}
            onChange={(e) => setGoalTitle(e.target.value)}
            placeholder={t("command_center.ask_placeholder")}
            className="min-h-[44px] flex-1 bg-transparent text-sm outline-none placeholder:text-slate-500"
          />
          <button
            type="submit"
            disabled={createGoal.isPending || !goalTitle.trim()}
            className="min-h-[44px] rounded-xl bg-gradient-to-l from-violet-500 to-blue-500 px-5 text-sm font-semibold text-white shadow-[0_0_14px_rgba(139,92,246,0.4)] transition-opacity disabled:opacity-40"
          >
            {t("command_center.create_goal")}
          </button>
        </form>

        <div className="glass flex items-center gap-2 rounded-2xl p-2 px-3">
          {!confirmingStop ? (
            <button
              type="button"
              onClick={() => setConfirmingStop(true)}
              className="min-h-[44px] rounded-xl border border-red-400/40 bg-red-500/10 px-4 text-sm font-semibold text-red-200"
            >
              {t("command_center.emergency_stop")}
            </button>
          ) : (
            <>
              <span className="text-xs text-red-200">{t("command_center.emergency_stop_confirm")}</span>
              <button
                type="button"
                onClick={() => {
                  setConfirmingStop(false);
                  emergencyStop.mutate();
                }}
                className="min-h-[44px] rounded-xl bg-red-500 px-4 text-sm font-semibold text-slate-950"
              >
                {t("common.yes")}
              </button>
              <button
                type="button"
                onClick={() => setConfirmingStop(false)}
                className="min-h-[44px] rounded-xl border border-white/10 px-4 text-sm text-slate-300"
              >
                {t("common.no")}
              </button>
            </>
          )}
        </div>
      </div>
    </div>
  );
}
