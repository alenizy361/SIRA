"use client";

import { useI18n } from "@/i18n/I18nProvider";
import { GracefulList } from "@/components/GracefulList";
import { plansApi } from "@/lib/api";

export default function PlansPage() {
  const { t } = useI18n();
  return (
    <GracefulList
      title={t("placeholders.plans_title")}
      queryKey={["plans"]}
      queryFn={plansApi.list}
      renderItem={(plan, i) => (
        <pre
          key={i}
          className="overflow-auto rounded-2xl border border-white/10 bg-white/[0.03] p-4 text-xs text-slate-300"
        >
          {JSON.stringify(plan, null, 2)}
        </pre>
      )}
    />
  );
}
