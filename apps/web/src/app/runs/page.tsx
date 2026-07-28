"use client";

import { useI18n } from "@/i18n/I18nProvider";
import { GracefulList } from "@/components/GracefulList";
import { runsApi } from "@/lib/api";

export default function RunsPage() {
  const { t } = useI18n();
  return (
    <GracefulList
      title={t("placeholders.runs_title")}
      queryKey={["runs"]}
      queryFn={runsApi.list}
      renderItem={(run, i) => (
        <pre
          key={i}
          className="overflow-auto rounded-2xl border border-white/10 bg-white/[0.03] p-4 text-xs text-slate-300"
        >
          {JSON.stringify(run, null, 2)}
        </pre>
      )}
    />
  );
}
