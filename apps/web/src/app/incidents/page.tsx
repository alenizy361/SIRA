"use client";

import { useI18n } from "@/i18n/I18nProvider";
import { GracefulList } from "@/components/GracefulList";
import { incidentsApi } from "@/lib/api";

export default function IncidentsPage() {
  const { t } = useI18n();
  return (
    <GracefulList
      title={t("placeholders.incidents_title")}
      queryKey={["incidents"]}
      queryFn={incidentsApi.list}
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
