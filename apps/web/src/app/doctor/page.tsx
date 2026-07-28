"use client";

import { useQuery } from "@tanstack/react-query";
import { useI18n } from "@/i18n/I18nProvider";
import { healthApi } from "@/lib/api";
import { ErrorState } from "@/components/EmptyState";

export default function DoctorPage() {
  const { t } = useI18n();
  const healthQuery = useQuery({
    queryKey: ["health-dependencies-doctor"],
    queryFn: healthApi.dependencies,
    refetchInterval: 10_000,
  });

  return (
    <div className="mx-auto flex max-w-2xl flex-col gap-4">
      <div>
        <h1 className="text-lg font-semibold">{t("doctor.title")}</h1>
        <p className="text-sm text-slate-500">{t("doctor.subtitle")}</p>
      </div>

      {healthQuery.isLoading ? (
        <p className="text-sm text-slate-500">{t("common.loading")}</p>
      ) : healthQuery.isError ? (
        <ErrorState message={t("common.error_generic")} onRetry={() => healthQuery.refetch()} />
      ) : (
        <div className="flex flex-col gap-3">
          {Object.entries(healthQuery.data || {}).map(([name, check]) => (
            <div
              key={name}
              className={`flex items-center justify-between rounded-2xl border p-4 ${
                check.status === "ok"
                  ? "border-emerald-400/20 bg-emerald-500/[0.05]"
                  : "border-red-400/20 bg-red-500/[0.05]"
              }`}
            >
              <div>
                <p className="font-medium capitalize text-slate-100">{name}</p>
                {check.detail && <p className="text-xs text-slate-500">{check.detail}</p>}
              </div>
              <span
                className={`rounded-full px-3 py-1 text-xs font-semibold ${
                  check.status === "ok"
                    ? "bg-emerald-500/20 text-emerald-300"
                    : "bg-red-500/20 text-red-300"
                }`}
              >
                {check.status === "ok" ? t("doctor.status_ok") : t("doctor.status_fail")}
              </span>
            </div>
          ))}
        </div>
      )}
    </div>
  );
}
