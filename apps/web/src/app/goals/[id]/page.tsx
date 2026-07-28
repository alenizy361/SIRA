"use client";

import { use } from "react";
import Link from "next/link";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useI18n } from "@/i18n/I18nProvider";
import { useToast } from "@/components/ToastProvider";
import { goalsApi } from "@/lib/api";
import { GOAL_TRANSITIONS } from "@/lib/goalStates";
import { ErrorState } from "@/components/EmptyState";
import { SpeakButton } from "@/components/SpeakButton";
import { GoalPlanPanel } from "@/components/GoalPlanPanel";

export default function GoalDetailPage({
  params,
}: {
  params: Promise<{ id: string }>;
}) {
  const { id } = use(params);
  const { t } = useI18n();
  const toast = useToast();
  const queryClient = useQueryClient();

  const goalQuery = useQuery({ queryKey: ["goal", id], queryFn: () => goalsApi.get(id) });

  const transition = useMutation({
    mutationFn: (target: string) => goalsApi.transition(id, target),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ["goal", id] });
      queryClient.invalidateQueries({ queryKey: ["goals"] });
    },
    onError: () => toast.show(t("common.error_generic"), "error"),
  });

  if (goalQuery.isLoading) {
    return <p className="text-sm text-slate-500">{t("common.loading")}</p>;
  }

  if (goalQuery.isError || !goalQuery.data) {
    return (
      <ErrorState message={t("common.error_generic")} onRetry={() => goalQuery.refetch()} />
    );
  }

  const goal = goalQuery.data;
  const nextStates = GOAL_TRANSITIONS[goal.state] || [];

  return (
    <div className="mx-auto flex max-w-2xl flex-col gap-4">
      <Link href="/goals" className="text-xs text-slate-400">
        ← {t("common.back")}
      </Link>

      <div className="rounded-2xl border border-white/10 bg-white/[0.03] p-4">
        <div className="mb-2 flex items-center justify-between gap-2">
          <h1 className="text-lg font-semibold">{goal.title}</h1>
          <SpeakButton text={`${goal.title}. ${t("goals.state")}: ${goal.state}`} />
        </div>
        <p className="mb-4 text-sm text-slate-400">{goal.description}</p>

        <dl className="grid grid-cols-2 gap-3 text-sm">
          <div>
            <dt className="text-xs text-slate-500">{t("goals.state")}</dt>
            <dd className="mt-0.5 inline-block rounded-full bg-emerald-500/15 px-2 py-0.5 text-xs text-emerald-300">
              {goal.state}
            </dd>
          </div>
          <div>
            <dt className="text-xs text-slate-500">{t("goals.source")}</dt>
            <dd>{goal.source}</dd>
          </div>
          <div>
            <dt className="text-xs text-slate-500">{t("goals.priority")}</dt>
            <dd>{goal.priority}</dd>
          </div>
          <div>
            <dt className="text-xs text-slate-500">{t("goals.created")}</dt>
            <dd>{new Date(goal.created_at).toLocaleString()}</dd>
          </div>
        </dl>
      </div>

      {/* The CEO's response: plan summary + tasks (the durable answer). */}
      <div className="rounded-2xl border border-white/10 bg-white/[0.03] p-4">
        <GoalPlanPanel goalId={id} showOpenLink={false} />
      </div>

      <div className="rounded-2xl border border-white/10 bg-white/[0.03] p-4">
        <h2 className="mb-2 text-sm font-semibold text-slate-300">{t("goals.transition_to")}</h2>
        {nextStates.length === 0 ? (
          <p className="text-sm text-slate-500">—</p>
        ) : (
          <div className="flex flex-wrap gap-2">
            {nextStates.map((target) => (
              <button
                key={target}
                type="button"
                disabled={transition.isPending}
                onClick={() => transition.mutate(target)}
                className="min-h-[44px] rounded-lg border border-white/10 px-3 text-sm text-slate-200 hover:bg-white/5 disabled:opacity-40"
              >
                {target}
              </button>
            ))}
          </div>
        )}
      </div>
    </div>
  );
}
