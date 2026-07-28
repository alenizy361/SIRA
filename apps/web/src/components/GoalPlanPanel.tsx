"use client";

import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import Link from "next/link";
import { useI18n } from "@/i18n/I18nProvider";
import { goalsApi, tasksApi } from "@/lib/api";
import { agentMeta } from "@/lib/agentMeta";
import { TaskConversation } from "./TaskConversation";

const RISK_COLOR: Record<string, string> = {
  R0: "#34d399",
  R1: "#fbbf24",
  R2: "#fb7185",
};

// States a task can still be interrupted out of - mirrors TaskState's
// non-terminal members (services/orchestrator/state_machine.py), so the
// button only ever appears on a task that /tasks/{id}/cancel will accept.
const CANCELLABLE_STATES = new Set([
  "ready",
  "assigned",
  "running",
  "retry_wait",
  "blocked",
  "human_input_required",
]);

/**
 * The readable "response" to a goal: the CEO's plan summary and the tasks it
 * created, each tagged with its agent and risk. This is the durable answer -
 * it reads the /goals/{id}/plan record, so it survives reloads and doesn't
 * depend on catching the live WS stream at the right moment.
 *
 * While the CEO is still planning (no plan row yet, plan_status
 * requested/running) it polls every few seconds and shows a "planning now"
 * state, so the operator always knows something IS happening.
 */
export function GoalPlanPanel({
  goalId,
  showOpenLink = true,
}: {
  goalId: string;
  showOpenLink?: boolean;
}) {
  const { t, locale } = useI18n();
  const queryClient = useQueryClient();

  const cancelMutation = useMutation({
    mutationFn: (taskId: string) => tasksApi.cancel(taskId),
    onSuccess: () => queryClient.invalidateQueries({ queryKey: ["goal-plan", goalId] }),
  });

  const TERMINAL = ["completed", "cancelled", "failed", "incident_opened"];
  const planQuery = useQuery({
    queryKey: ["goal-plan", goalId],
    queryFn: () => goalsApi.getPlan(goalId),
    // Poll fast while the CEO is planning; keep polling (slower) while any task
    // is still running so their states update; stop once everything is done.
    refetchInterval: (query) => {
      const d = query.state.data;
      if (!d?.plan) return 4_000;
      const allDone = d.tasks.length > 0 && d.tasks.every((t) => TERMINAL.includes(t.state));
      return allDone ? false : 8_000;
    },
  });

  const data = planQuery.data;
  const planning =
    !data?.plan && (data?.plan_status === "requested" || data?.plan_status === "running");

  if (planQuery.isLoading) {
    return <p className="py-4 text-center text-sm text-slate-500">{t("common.loading")}</p>;
  }

  // CEO still working - honest "planning now" state with a live pulse.
  if (planning) {
    return (
      <div className="flex flex-col items-center gap-2 py-6 text-center">
        <div className="flex items-center gap-1.5">
          <span className="h-2 w-2 animate-pulse rounded-full bg-violet-400 shadow-[0_0_10px_#a78bfa]" />
          <span className="text-sm font-semibold text-violet-200">{t("command_center.ceo_planning_now")}</span>
        </div>
        <p className="max-w-[36ch] text-xs text-slate-500">{t("command_center.ceo_planning_wait")}</p>
      </div>
    );
  }

  if (!data?.plan) {
    return <p className="py-4 text-center text-sm text-slate-500">{t("command_center.no_plan_yet")}</p>;
  }

  const { plan, tasks, all_done } = data;

  return (
    <div className="flex flex-col gap-3">
      {/* The CEO's worded answer */}
      <div className="rounded-2xl border border-violet-400/25 bg-violet-500/[0.06] p-3">
        <div className="mb-1 flex items-center gap-2">
          <span className="flex h-6 w-6 items-center justify-center rounded-full border border-violet-400/50 bg-violet-500/20 text-[9px] font-bold text-violet-100">
            CEO
          </span>
          <span className="text-[11px] font-bold uppercase tracking-[0.14em] text-violet-300">
            {t("command_center.ceo_response")}
          </span>
        </div>
        <p className="text-sm font-semibold text-slate-100">{plan.title}</p>
        {plan.summary ? (
          <p className="mt-1 whitespace-pre-wrap break-words text-[13px] leading-relaxed text-slate-300">
            {plan.summary}
          </p>
        ) : null}
      </div>

      {/* Once every task has actually finished, say so plainly - this is the
          "did it actually do anything" answer, not just a plan. */}
      {all_done ? (
        <div className="flex items-center gap-2 rounded-xl border border-emerald-400/30 bg-emerald-500/[0.08] px-3 py-2">
          <span className="h-2 w-2 rounded-full bg-emerald-400 shadow-[0_0_8px_#34d399]" aria-hidden />
          <span className="text-[13px] font-semibold text-emerald-200">{t("command_center.goal_all_done")}</span>
        </div>
      ) : null}

      {/* The tasks it handed out */}
      {tasks.length > 0 ? (
        <div className="flex flex-col gap-2">
          <span className="text-[11px] font-bold uppercase tracking-[0.14em] text-slate-400">
            {t("command_center.plan_tasks")} · {tasks.length}
          </span>
          {tasks.map((task, i) => {
            const meta = agentMeta(task.agent_key || "");
            const risk = RISK_COLOR[task.risk_level] || "#94a3b8";
            return (
              <div
                key={task.id}
                className="rounded-xl border border-white/8 bg-white/[0.02] p-3"
              >
                <div className="flex items-start justify-between gap-2">
                  <div className="flex items-center gap-2">
                    <span className="text-xs tabular-nums text-slate-600">{i + 1}.</span>
                    <span className="text-sm font-medium text-slate-100">{task.title}</span>
                  </div>
                  <span
                    className="shrink-0 rounded-full px-2 py-0.5 text-[10px] font-bold"
                    style={{ color: risk, background: `${risk}1a`, border: `1px solid ${risk}44` }}
                  >
                    {task.risk_level}
                  </span>
                </div>
                {task.description ? (
                  <p className="mt-1 whitespace-pre-wrap break-words text-[12px] leading-relaxed text-slate-400">
                    {task.description}
                  </p>
                ) : null}
                {task.result ? (
                  <div className="mt-2 rounded-lg border border-white/8 bg-black/20 p-2">
                    <span className="mb-1 block text-[9.5px] font-bold uppercase tracking-[0.12em] text-slate-500">
                      {t("command_center.agent_result")}
                    </span>
                    <p className="whitespace-pre-wrap break-words text-[12px] leading-relaxed text-slate-200">
                      {task.result}
                    </p>
                  </div>
                ) : null}
                {task.work_log.length > 0 ? (
                  <details className="mt-2 rounded-lg border border-white/8 bg-black/10 open:bg-black/20">
                    <summary className="cursor-pointer select-none px-2 py-1.5 text-[10px] font-bold uppercase tracking-[0.1em] text-slate-500 hover:text-slate-300">
                      {t("command_center.work_log")} · {task.work_log.length}
                    </summary>
                    <div className="flex flex-col gap-1 px-2 pb-2">
                      {task.work_log.map((step, si) => {
                        const inputPreview = Object.entries(step.input || {})
                          .map(([k, v]) => `${k}=${String(v).slice(0, 60)}`)
                          .join(" · ");
                        return (
                          <div key={si} className="rounded border border-white/5 bg-white/[0.02] px-2 py-1.5">
                            <div className="flex items-center gap-1.5">
                              <span
                                className="h-1.5 w-1.5 rounded-full"
                                style={{
                                  background:
                                    step.succeeded === false ? "#fb7185" : step.succeeded === true ? "#34d399" : "#94a3b8",
                                }}
                                aria-hidden
                              />
                              <span className="font-mono text-[11px] font-semibold text-slate-300">{step.tool_name}</span>
                            </div>
                            {inputPreview ? (
                              <p className="mt-0.5 truncate font-mono text-[10.5px] text-slate-500">{inputPreview}</p>
                            ) : null}
                            {step.output_preview ? (
                              <p className="mt-0.5 whitespace-pre-wrap break-words text-[10.5px] text-slate-500">
                                {step.output_preview.slice(0, 240)}
                              </p>
                            ) : null}
                          </div>
                        );
                      })}
                    </div>
                  </details>
                ) : null}
                {task.result ? <TaskConversation taskId={task.id} /> : null}
                <div className="mt-2 flex items-center gap-2">
                  <span
                    className="inline-flex items-center gap-1 rounded-full px-2 py-0.5 text-[10px] font-semibold"
                    style={{ color: meta.color, background: `${meta.color}14`, border: `1px solid ${meta.color}33` }}
                  >
                    <span className="h-1.5 w-1.5 rounded-full" style={{ background: meta.color }} aria-hidden />
                    {t("command_center.assigned_to")}: {task.agent_key || "—"}
                  </span>
                  <span className="rounded-full bg-white/5 px-2 py-0.5 text-[10px] text-slate-400">
                    {task.state}
                  </span>
                  {CANCELLABLE_STATES.has(task.state) ? (
                    <button
                      type="button"
                      onClick={() => cancelMutation.mutate(task.id)}
                      disabled={cancelMutation.isPending && cancelMutation.variables === task.id}
                      className="ml-auto rounded-full border border-rose-400/30 bg-rose-500/[0.08] px-2 py-0.5 text-[10px] font-semibold text-rose-300 hover:bg-rose-500/20 disabled:opacity-50"
                    >
                      {cancelMutation.isPending && cancelMutation.variables === task.id
                        ? t("command_center.cancelling_task")
                        : t("command_center.cancel_task")}
                    </button>
                  ) : null}
                  {cancelMutation.isError && cancelMutation.variables === task.id ? (
                    <span className="text-[10px] text-rose-400">{t("command_center.task_cancel_failed")}</span>
                  ) : null}
                </div>
              </div>
            );
          })}
        </div>
      ) : null}

      {showOpenLink ? (
        <Link
          href={`/goals/${goalId}`}
          className="text-xs text-emerald-300 hover:text-emerald-200"
          lang={locale}
        >
          {t("command_center.open_goal")} →
        </Link>
      ) : null}
    </div>
  );
}
