"use client";

import { useQuery } from "@tanstack/react-query";
import { useI18n } from "@/i18n/I18nProvider";
import { analyticsApi, ApiError } from "@/lib/api";
import { ErrorState, NotAvailableYet } from "@/components/EmptyState";

export default function AnalyticsPage() {
  const { t } = useI18n();
  const query = useQuery({ queryKey: ["analytics"], queryFn: analyticsApi.summary, retry: false });

  return (
    <div className="mx-auto flex max-w-4xl flex-col gap-4">
      <h1 className="text-lg font-semibold">{t("placeholders.analytics_title")}</h1>

      {query.isLoading ? (
        <p className="text-sm text-slate-500">{t("common.loading")}</p>
      ) : query.isError ? (
        query.error instanceof ApiError && query.error.notImplemented ? (
          <NotAvailableYet />
        ) : (
          <ErrorState message={t("common.error_generic")} onRetry={() => query.refetch()} />
        )
      ) : (
        <pre className="overflow-auto rounded-2xl border border-white/10 bg-white/[0.03] p-4 text-xs text-slate-300">
          {JSON.stringify(query.data, null, 2)}
        </pre>
      )}
    </div>
  );
}
