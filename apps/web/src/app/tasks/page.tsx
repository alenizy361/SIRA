"use client";

import { useI18n } from "@/i18n/I18nProvider";
import { GracefulList } from "@/components/GracefulList";
import { tasksApi } from "@/lib/api";

interface TaskLike {
  id?: string;
  title?: string;
  name?: string;
  state?: string;
  status?: string;
  depends_on?: string[];
  dependencies?: string[];
}

export default function TasksPage() {
  const { t } = useI18n();
  return (
    <GracefulList<TaskLike>
      title={t("placeholders.tasks_title")}
      queryKey={["tasks"]}
      queryFn={() => tasksApi.list() as Promise<TaskLike[]>}
      renderItem={(task, i) => {
        const deps = task.depends_on || task.dependencies || [];
        return (
          <div
            key={task.id ?? i}
            className="rounded-2xl border border-white/10 bg-white/[0.03] p-4"
          >
            <div className="flex items-center justify-between gap-2">
              <span className="font-medium text-slate-100">
                {task.title || task.name || task.id || `#${i}`}
              </span>
              {(task.state || task.status) && (
                <span className="rounded-full bg-emerald-500/15 px-2 py-0.5 text-xs text-emerald-300">
                  {task.state || task.status}
                </span>
              )}
            </div>
            {deps.length > 0 && (
              <p className="mt-1 text-xs text-slate-500">
                {t("tasks.depends_on")} {deps.join(", ")}
              </p>
            )}
          </div>
        );
      }}
    />
  );
}
