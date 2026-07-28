"use client";

import { useState } from "react";
import Link from "next/link";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useI18n } from "@/i18n/I18nProvider";
import { useToast } from "@/components/ToastProvider";
import { goalsApi } from "@/lib/api";
import { EmptyState, ErrorState } from "@/components/EmptyState";

export default function GoalsPage() {
  const { t } = useI18n();
  const toast = useToast();
  const queryClient = useQueryClient();
  const [title, setTitle] = useState("");
  const [description, setDescription] = useState("");

  const goalsQuery = useQuery({ queryKey: ["goals"], queryFn: goalsApi.list });

  const createGoal = useMutation({
    mutationFn: () => goalsApi.create({ title, description: description || title, source: "user" }),
    onSuccess: () => {
      setTitle("");
      setDescription("");
      queryClient.invalidateQueries({ queryKey: ["goals"] });
    },
    onError: () => toast.show(t("common.error_generic"), "error"),
  });

  return (
    <div className="mx-auto flex max-w-4xl flex-col gap-4">
      <h1 className="text-lg font-semibold">{t("goals.title")}</h1>

      <form
        onSubmit={(e) => {
          e.preventDefault();
          if (title.trim()) createGoal.mutate();
        }}
        className="flex flex-col gap-2 rounded-2xl border border-white/10 bg-white/[0.03] p-4"
      >
        <h2 className="text-sm font-semibold text-slate-300">{t("goals.new_goal")}</h2>
        <input
          value={title}
          onChange={(e) => setTitle(e.target.value)}
          placeholder={t("goals.goal_title")}
          required
          className="min-h-[44px] rounded-lg border border-white/10 bg-slate-900 px-3 text-sm"
        />
        <textarea
          value={description}
          onChange={(e) => setDescription(e.target.value)}
          placeholder={t("goals.goal_description")}
          rows={2}
          className="rounded-lg border border-white/10 bg-slate-900 p-3 text-sm"
        />
        <button
          type="submit"
          disabled={createGoal.isPending || !title.trim()}
          className="min-h-[44px] self-start rounded-lg bg-emerald-500 px-4 text-sm font-semibold text-slate-950 disabled:opacity-40"
        >
          {t("goals.create")}
        </button>
      </form>

      {goalsQuery.isLoading ? (
        <p className="text-sm text-slate-500">{t("common.loading")}</p>
      ) : goalsQuery.isError ? (
        <ErrorState
          message={t("common.error_generic")}
          onRetry={() => goalsQuery.refetch()}
        />
      ) : goalsQuery.data && goalsQuery.data.length > 0 ? (
        <ul className="flex flex-col gap-2">
          {goalsQuery.data.map((goal) => (
            <li key={goal.id}>
              <Link
                href={`/goals/${goal.id}`}
                className="flex flex-col gap-1 rounded-2xl border border-white/10 bg-white/[0.03] p-4 hover:bg-white/[0.05]"
              >
                <div className="flex items-center justify-between gap-2">
                  <span className="font-medium text-slate-100">{goal.title}</span>
                  <span className="rounded-full bg-emerald-500/15 px-2 py-0.5 text-xs text-emerald-300">
                    {goal.state}
                  </span>
                </div>
                <p className="line-clamp-2 text-xs text-slate-500">{goal.description}</p>
              </Link>
            </li>
          ))}
        </ul>
      ) : (
        <EmptyState message={t("goals.no_goals")} />
      )}
    </div>
  );
}
