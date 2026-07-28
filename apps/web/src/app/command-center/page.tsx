"use client";

import { useState } from "react";
import Link from "next/link";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useI18n } from "@/i18n/I18nProvider";
import { useToast } from "@/components/ToastProvider";
import { AICore } from "@/components/AICore";
import { VoiceCapture } from "@/components/VoiceCapture";
import { SpeakButton } from "@/components/SpeakButton";
import { useEventStore } from "@/store/eventStore";
import { approvalsApi, goalsApi, healthApi, systemApi, ApiError } from "@/lib/api";

export default function CommandCenterPage() {
  const { t } = useI18n();
  const toast = useToast();
  const queryClient = useQueryClient();
  const [goalTitle, setGoalTitle] = useState("");
  const [confirmingStop, setConfirmingStop] = useState(false);

  const coreState = useEventStore((s) => s.coreState);
  const wsStatus = useEventStore((s) => s.wsStatus);
  const events = useEventStore((s) => s.events);

  const approvalsQuery = useQuery({ queryKey: ["approvals"], queryFn: approvalsApi.list });
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

  const stateLabel = t(`core_states.${coreState}`);

  return (
    <div className="mx-auto flex max-w-6xl flex-col gap-4">
      <h1 className="text-lg font-semibold">{t("command_center.title")}</h1>

      <div className="grid grid-cols-1 gap-4 lg:grid-cols-3">
        {/* AI Core */}
        <div className="flex flex-col items-center justify-center rounded-2xl border border-white/10 bg-gradient-to-b from-slate-900 to-slate-950 p-2 lg:col-span-2">
          <div className="h-64 w-full sm:h-80">
            <AICore state={coreState} />
          </div>
          <div className="mb-2 flex items-center gap-2 text-sm">
            <span className="rounded-full border border-white/10 px-3 py-1 text-xs font-medium text-slate-300">
              {stateLabel}
            </span>
            <span
              className={`inline-block h-2 w-2 rounded-full ${
                wsStatus === "open" ? "bg-emerald-400" : "bg-amber-400 animate-pulse"
              }`}
              aria-hidden
            />
            <span className="text-xs text-slate-500">
              {wsStatus === "open" ? t("common.connected") : t("common.reconnecting")}
            </span>
            <SpeakButton text={stateLabel} />
          </div>
        </div>

        {/* Goal input + voice */}
        <div className="flex flex-col gap-3 rounded-2xl border border-white/10 bg-white/[0.03] p-4">
          <form onSubmit={handleCreateGoal} className="flex flex-col gap-2">
            <label className="text-sm font-medium text-slate-300">
              {t("command_center.goal_input_label")}
            </label>
            <textarea
              value={goalTitle}
              onChange={(e) => setGoalTitle(e.target.value)}
              placeholder={t("command_center.goal_input_placeholder")}
              rows={3}
              className="rounded-lg border border-white/10 bg-slate-900 p-3 text-sm"
            />
            <button
              type="submit"
              disabled={createGoal.isPending || !goalTitle.trim()}
              className="min-h-[44px] rounded-lg bg-emerald-500 text-sm font-semibold text-slate-950 disabled:opacity-40"
            >
              {t("command_center.create_goal")}
            </button>
          </form>

          <VoiceCapture onConfirmed={(text) => setGoalTitle(text)} />
        </div>
      </div>

      <div className="grid grid-cols-1 gap-4 sm:grid-cols-2 lg:grid-cols-3">
        {/* Pending approvals */}
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

        {/* System health */}
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

        {/* Emergency stop */}
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

      {/* Live event stream */}
      <div className="rounded-2xl border border-white/10 bg-white/[0.03] p-4">
        <h2 className="mb-3 text-sm font-semibold text-slate-300">
          {t("command_center.live_events")}
        </h2>
        {events.length === 0 ? (
          <p className="text-sm text-slate-500">{t("command_center.no_events")}</p>
        ) : (
          <ul className="flex max-h-80 flex-col gap-2 overflow-y-auto">
            {events.map((event) => (
              <li
                key={event.event_id}
                className="rounded-lg border border-white/5 bg-white/[0.02] px-3 py-2 text-xs"
              >
                <div className="flex items-center justify-between gap-2">
                  <span className="font-medium text-slate-200">{event.type}</span>
                  <span className="text-slate-500">
                    {new Date(event.timestamp).toLocaleTimeString()}
                  </span>
                </div>
                <div className="text-slate-500">{event.actor}</div>
              </li>
            ))}
          </ul>
        )}
      </div>
    </div>
  );
}
