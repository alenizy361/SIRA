"use client";

import { useQuery } from "@tanstack/react-query";
import { useI18n } from "@/i18n/I18nProvider";
import { auditApi } from "@/lib/api";
import { EmptyState, ErrorState } from "@/components/EmptyState";

export default function AuditPage() {
  const { t } = useI18n();
  const auditQuery = useQuery({ queryKey: ["audit-logs"], queryFn: () => auditApi.list(100) });

  return (
    <div className="mx-auto flex max-w-5xl flex-col gap-4">
      <h1 className="text-lg font-semibold">{t("audit.title")}</h1>

      {auditQuery.isLoading ? (
        <p className="text-sm text-slate-500">{t("common.loading")}</p>
      ) : auditQuery.isError ? (
        <ErrorState message={t("common.error_generic")} onRetry={() => auditQuery.refetch()} />
      ) : auditQuery.data && auditQuery.data.length > 0 ? (
        <div className="overflow-x-auto rounded-2xl border border-white/10">
          <table className="w-full min-w-[640px] text-start text-sm">
            <thead className="bg-white/[0.04] text-xs text-slate-400">
              <tr>
                <th className="px-3 py-2 text-start">{t("audit.time")}</th>
                <th className="px-3 py-2 text-start">{t("audit.actor")}</th>
                <th className="px-3 py-2 text-start">{t("audit.action")}</th>
                <th className="px-3 py-2 text-start">{t("audit.entity")}</th>
                <th className="px-3 py-2 text-start">{t("audit.result")}</th>
                <th className="px-3 py-2 text-start">{t("audit.explanation")}</th>
              </tr>
            </thead>
            <tbody>
              {auditQuery.data.map((row) => (
                <tr key={row.id} className="border-t border-white/5">
                  <td className="whitespace-nowrap px-3 py-2 text-xs text-slate-500">
                    {new Date(row.created_at).toLocaleString()}
                  </td>
                  <td className="px-3 py-2 text-xs text-slate-400">
                    {row.actor_type}:{row.actor_id.slice(0, 8)}
                  </td>
                  <td className="px-3 py-2 font-medium text-slate-200">{row.action}</td>
                  <td className="px-3 py-2 text-xs text-slate-400">
                    {row.entity_type}:{row.entity_id.slice(0, 8)}
                  </td>
                  <td className="px-3 py-2">
                    <span
                      className={`rounded-full px-2 py-0.5 text-xs ${
                        row.result === "executed"
                          ? "bg-emerald-500/15 text-emerald-300"
                          : "bg-slate-500/15 text-slate-300"
                      }`}
                    >
                      {row.result}
                    </span>
                  </td>
                  <td className="px-3 py-2 text-xs text-slate-400">{row.explanation}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      ) : (
        <EmptyState message={t("common.empty")} />
      )}
    </div>
  );
}
