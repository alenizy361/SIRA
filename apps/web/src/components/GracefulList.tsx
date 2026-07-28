"use client";

import { useQuery } from "@tanstack/react-query";
import { useI18n } from "@/i18n/I18nProvider";
import { ApiError } from "@/lib/api";
import { EmptyState, ErrorState, NotAvailableYet } from "@/components/EmptyState";

/**
 * Shared shell for pages whose backend endpoint may not exist yet
 * (plans/tasks/runs/memory/budgets/analytics/incidents). Always performs a
 * real fetch; degrades honestly to "not available yet" on 404/405 instead
 * of ever rendering fabricated rows.
 */
export function GracefulList<T>({
  title,
  subtitle,
  queryKey,
  queryFn,
  renderItem,
}: {
  title: string;
  subtitle?: string;
  queryKey: unknown[];
  queryFn: () => Promise<T[]>;
  renderItem: (item: T, index: number) => React.ReactNode;
}) {
  const { t } = useI18n();
  const query = useQuery({ queryKey, queryFn, retry: false });

  return (
    <div className="mx-auto flex max-w-4xl flex-col gap-4">
      <div>
        <h1 className="text-lg font-semibold">{title}</h1>
        {subtitle && <p className="mt-1 text-sm text-slate-500">{subtitle}</p>}
      </div>

      {query.isLoading ? (
        <p className="text-sm text-slate-500">{t("common.loading")}</p>
      ) : query.isError ? (
        query.error instanceof ApiError && query.error.notImplemented ? (
          <NotAvailableYet />
        ) : (
          <ErrorState message={t("common.error_generic")} onRetry={() => query.refetch()} />
        )
      ) : query.data && query.data.length > 0 ? (
        <div className="flex flex-col gap-2">{query.data.map((item, i) => renderItem(item, i))}</div>
      ) : (
        <EmptyState message={t("common.empty")} />
      )}
    </div>
  );
}
