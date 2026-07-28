"use client";

import { useQuery } from "@tanstack/react-query";
import { useI18n } from "@/i18n/I18nProvider";
import { agentsApi } from "@/lib/api";
import { ErrorState } from "@/components/EmptyState";

const RISK_COLORS: Record<string, string> = {
  R0: "bg-slate-500/15 text-slate-300",
  R1: "bg-emerald-500/15 text-emerald-300",
  R2: "bg-sky-500/15 text-sky-300",
  R3: "bg-amber-500/15 text-amber-300",
  R4: "bg-red-500/15 text-red-300",
  R5: "bg-red-600/25 text-red-200",
};

export default function AgentsPage() {
  const { t } = useI18n();
  const agentsQuery = useQuery({ queryKey: ["agents"], queryFn: agentsApi.list });

  return (
    <div className="mx-auto flex max-w-6xl flex-col gap-4">
      <h1 className="text-lg font-semibold">{t("agents.title")}</h1>

      {agentsQuery.isLoading ? (
        <p className="text-sm text-slate-500">{t("common.loading")}</p>
      ) : agentsQuery.isError ? (
        <ErrorState message={t("common.error_generic")} onRetry={() => agentsQuery.refetch()} />
      ) : (
        <div className="grid grid-cols-1 gap-3 sm:grid-cols-2 lg:grid-cols-3">
          {agentsQuery.data?.map((agent) => (
            <div
              key={agent.agent_key}
              className={`flex flex-col gap-2 rounded-2xl border p-4 ${
                agent.enabled
                  ? "border-white/10 bg-white/[0.03]"
                  : "border-white/5 bg-white/[0.015] opacity-70"
              }`}
            >
              <div className="flex items-start justify-between gap-2">
                <div>
                  <p className="font-medium text-slate-100">{agent.display_name_en}</p>
                  <p className="text-sm text-slate-400" dir="rtl">
                    {agent.display_name_ar}
                  </p>
                </div>
                <span
                  className={`shrink-0 rounded-full px-2 py-0.5 text-xs font-semibold ${RISK_COLORS[agent.risk_ceiling] || "bg-slate-500/15 text-slate-300"}`}
                >
                  {agent.risk_ceiling}
                </span>
              </div>

              <p className="text-xs text-slate-500">{agent.mission}</p>

              <div className="mt-auto flex items-center justify-between gap-2 pt-2">
                <span
                  className={`inline-flex items-center gap-1.5 rounded-full px-2 py-0.5 text-xs font-medium ${
                    agent.enabled
                      ? "bg-emerald-500/15 text-emerald-300"
                      : "bg-slate-600/20 text-slate-400"
                  }`}
                >
                  <span
                    className={`h-1.5 w-1.5 rounded-full ${agent.enabled ? "bg-emerald-400" : "bg-slate-500"}`}
                  />
                  {agent.enabled ? t("common.enabled") : t("common.disabled")}
                </span>
                <span className="text-xs text-slate-500">{agent.current_state}</span>
              </div>

              {!agent.enabled && agent.disabled_reason && (
                <p className="rounded-lg border border-amber-400/20 bg-amber-500/[0.06] px-2 py-1 text-xs text-amber-200">
                  {agent.disabled_reason}
                </p>
              )}
            </div>
          ))}
        </div>
      )}
    </div>
  );
}
