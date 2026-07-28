"use client";

import { useI18n } from "@/i18n/I18nProvider";
import { GracefulList } from "@/components/GracefulList";
import { budgetsApi } from "@/lib/api";

export default function BudgetsPage() {
  const { t } = useI18n();
  return (
    <GracefulList
      title={t("placeholders.budgets_title")}
      queryKey={["budgets"]}
      queryFn={budgetsApi.list}
      renderItem={(item, i) => (
        <pre
          key={i}
          className="overflow-auto rounded-2xl border border-white/10 bg-white/[0.03] p-4 text-xs text-slate-300"
        >
          {JSON.stringify(item, null, 2)}
        </pre>
      )}
    />
  );
}
