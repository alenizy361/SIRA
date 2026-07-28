"use client";

import { useState } from "react";
import Link from "next/link";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useI18n } from "@/i18n/I18nProvider";
import { useToast } from "@/components/ToastProvider";
import { AgentOrbit } from "@/components/AgentOrbit";
import { VoiceCapture } from "@/components/VoiceCapture";
import { SpeakButton } from "@/components/SpeakButton";
import { TransmissionsFeed } from "@/components/TransmissionsFeed";
import { useEventStore } from "@/store/eventStore";
import {
  agentsApi,
  approvalsApi,
  goalsApi,
  healthApi,
  systemApi,
  ApiError,
  type Goal,
} from "@/lib/api";

export default function CommandCenterPage() {
  const { t, locale } = useI18n();
  const toast = useToast();
  const queryClient = useQueryClient();
  const [goalTitle, setGoalTitle] = useState("");
  const [confirmingStop, setConfirmingStop] = useState(false);

  const coreState = useEventStore((s) => s.coreState);
  const wsStatus = useEventStore((s) => s.wsStatus);

  const agentsQuery = useQuery({ queryKey: ["agents"], queryFn: agentsApi.list });
  const approvalsQuery = useQuery({ queryKey: ["approvals"], queryFn: approvalsApi.list });
  const goalsQuery = useQuery({ queryKey: ["goals"], queryFn: goalsApi.list });
  const healthQuery = useQuery({
    queryKey: ["health-dependencies"],
    queryFn: healthApi.dependencies,
    refetchInterval: 15_000,
  });

  const createGoal = useMutation({
    mutationFn: (title: string) => goalsApi.create({ title, description: title, source: "user" }),
    onSuccess: () => {
      toast.show(t("command_center.goal_created"), "success");
      setGoalTitle("");
      queryClient.invalidateQueries({ queryKey: ["goals"] });
    },
    onError: () => toast.show(t("common.error_generic"), "error"),
  });

  // Kick off CEO-agent planning for the most recent goal that hasn't been
  // planned yet - this is what makes the orbit come alive with real activity.
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

  return (
    <div className="mx-auto flex max-w-7xl flex-col gap-4">
      {/* header strip */}
      <div className="flex flex-wrap items-center justify-between gap-3">
        <div className="flex items-center gap-3">
          <h1 className="text-lg font-semibold tracking-tight">{t("command_center.title")}</h1>
          <span className="rounded-full border border-white/10 bg-white/[0.03] px-3 py-1 text-xs font-medium text-slate-300">
            {stateLabel}
          </span>
          <SpeakButton text={stateLabel} />
        </div>
        <div className="flex items-center gap-2 text-xs">
          <span
            className={`inline-block h-2 w-2 rounded-full ${
              wsStatus === "open" ? "bg-emerald-400" : "bg-amber-400 animate-pulse"
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

      <div className="grid grid-cols-1 gap-4 lg:grid-cols-5">
        {/* the living orbit - the hero */}
        <div className="lg:col-span-3">
          <AgentOrbit agents={agents} coreState={coreState} />
        </div>

        {/* command console + live transmissions */}
        <div className="flex flex-col gap-4 lg:col-span-2">
          {/* command console */}
          <div className="flex flex-col gap-3 rounded-2xl border border-white/10 bg-white/[0.03] p-4">
            <form onSubmit={handleCreateGoal} className="flex flex-col gap-2">
              <label className="text-sm font-medium text-slate-300">
                {t("command_center.goal_input_label")}
              </label>
              <textarea
                value={goalTitle}
                onChange={(e) => setGoalTitle(e.target.value)}
                placeholder={t("command_center.goal_input_placeholder")}
                rows={2}
                className="rounded-lg border border-white/10 bg-slate-900 p-3 text-sm outline-none focus:border-emerald-400/40"
              />
              <button
                type="submit"
                disabled={createGoal.isPending || !goalTitle.trim()}
                className="min-h-[44px] rounded-lg bg-emerald-500 text-sm font-semibold text-slate-950 transition-opacity disabled:opacity-40"
              >
                {t("command_center.create_goal")}
              </button>
            </form>

            <VoiceCapture onConfirmed={(text) => setGoalTitle(text)} />

            {/* plan trigger - only when there is a captured goal awaiting a plan */}
            {capturedGoal ? (
              <button
                type="button"
                onClick={() => requestPlan.mutate(capturedGoal.id)}
                disabled={requestPlan.isPending}
                className="min-h-[44px] rounded-lg border border-violet-400/40 bg-violet-500/10 text-sm font-semibold text-violet-200 transition-colors hover:bg-violet-500/20 disabled:opacity-40"
              >
                {t("command_center.request_plan")}
                <span className="block truncate text-[11px] font-normal text-violet-300/70">
                  {capturedGoal.title}
                </span>
              </button>
            ) : null}
          </div>

          {/* live transmissions */}
          <div className="flex flex-1 flex-col rounded-2xl border border-white/10 bg-white/[0.03] p-4">
            <div className="mb-2 flex items-center justify-between">
              <h2 className="text-sm font-semibold text-slate-300">
                {t("command_center.transmissions")}
              </h2>
              <span className="h-1.5 w-1.5 animate-pulse rounded-full bg-cyan-400" aria-hidden />
            </div>
            <TransmissionsFeed agents={agents} />
          </div>
        </div>
      </div>

      {/* status strip */}
      <div className="grid grid-cols-1 gap-4 sm:grid-cols-3">
        <Link
          href="/approvals"
          className="flex flex-col gap-1 rounded-2xl border border-white/10 bg-white/[0.03] p-4 hover:bg-white/[0.05]"
        >
          <span className="text-xs font-medium text-slate-400">
            {t("command_center.pending_approvals")}
          </span>
          <span className="text-3xl font-semibold">
            {approvalsQuery.isLoading ? "…" : (approvalsQuery.data?.length ?? 0)}
          </span>
          <span className="text-xs text-emerald-300">{t("command_center.view_all")} →</span>
        </Link>

        <div className="flex flex-col gap-2 rounded-2xl border border-white/10 bg-white/[0.03] p-4">
          <span className="text-xs font-medium text-slate-400">
            {t("command_center.system_health")}
          </span>
          {healthQuery.isLoading ? (
            <span className="text-sm text-slate-500">{t("common.loading")}</span>
          ) : healthQuery.isError ? (
            <span className="text-sm text-red-300">{t("common.error_generic")}</span>
          ) : (
            <div className="flex flex-col gap-1">
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
        </div>

        <div className="flex flex-col justify-between gap-2 rounded-2xl border border-red-400/20 bg-red-500/[0.05] p-4">
          <span className="text-xs font-medium text-red-200">
            {t("command_center.emergency_stop")}
          </span>
          {!confirmingStop ? (
            <button
              type="button"
              onClick={() => setConfirmingStop(true)}
              className="min-h-[44px] rounded-lg border border-red-400/40 bg-red-500/10 text-sm font-semibold text-red-200"
            >
              {t("command_center.emergency_stop")}
            </button>
          ) : (
            <div className="flex flex-col gap-2">
              <p className="text-xs text-red-200">{t("command_center.emergency_stop_confirm")}</p>
              <div className="flex gap-2">
                <button
                  type="button"
                  onClick={() => {
                    setConfirmingStop(false);
                    emergencyStop.mutate();
                  }}
                  className="min-h-[44px] flex-1 rounded-lg bg-red-500 text-sm font-semibold text-slate-950"
                >
                  {t("common.yes")}
                </button>
                <button
                  type="button"
                  onClick={() => setConfirmingStop(false)}
                  className="min-h-[44px] flex-1 rounded-lg border border-white/10 text-sm text-slate-300"
                >
                  {t("common.no")}
                </button>
              </div>
            </div>
          )}
        </div>
      </div>
    </div>
  );
}
