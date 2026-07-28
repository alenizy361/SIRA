"use client";

import { useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useI18n } from "@/i18n/I18nProvider";
import { useToast } from "@/components/ToastProvider";
import { approvalsApi, Approval } from "@/lib/api";
import { EmptyState, ErrorState } from "@/components/EmptyState";

const RISK_COLORS: Record<string, string> = {
  R0: "bg-slate-500/15 text-slate-300",
  R1: "bg-emerald-500/15 text-emerald-300",
  R2: "bg-sky-500/15 text-sky-300",
  R3: "bg-amber-500/15 text-amber-300",
  R4: "bg-red-500/15 text-red-300",
  R5: "bg-red-600/25 text-red-200",
};

function ApprovalCard({ approval }: { approval: Approval }) {
  const { t } = useI18n();
  const toast = useToast();
  const queryClient = useQueryClient();
  const [comment, setComment] = useState("");

  const resolve = useMutation({
    mutationFn: (decision: "approve" | "approve_with_conditions" | "reject" | "request_revision") =>
      approvalsApi.resolve(approval.id, { decision, comment: comment || undefined }),
    onSuccess: () => {
      toast.show(t("approvals.resolved"), "success");
      queryClient.invalidateQueries({ queryKey: ["approvals"] });
    },
    onError: () => toast.show(t("common.error_generic"), "error"),
  });

  return (
    <div className="flex flex-col gap-3 rounded-2xl border border-white/10 bg-white/[0.03] p-4">
      <div className="flex flex-wrap items-center justify-between gap-2">
        <div>
          <p className="text-xs text-slate-500">{t("approvals.requested_by")}</p>
          <p className="font-medium text-slate-100">{approval.requested_by_agent_key}</p>
        </div>
        <span
          className={`rounded-full px-2 py-0.5 text-xs font-semibold ${RISK_COLORS[approval.risk_level] || "bg-slate-500/15 text-slate-300"}`}
        >
          {approval.risk_level}
        </span>
      </div>

      <div>
        <p className="text-xs text-slate-500">{t("approvals.action")}</p>
        <p className="text-sm text-slate-200">{approval.action}</p>
      </div>

      <div className="grid grid-cols-1 gap-3 sm:grid-cols-2">
        <div>
          <p className="text-xs text-slate-500">{t("approvals.reason")}</p>
          <p className="text-sm text-slate-300">{approval.reason}</p>
        </div>
        <div>
          <p className="text-xs text-slate-500">{t("approvals.expected_benefit")}</p>
          <p className="text-sm text-slate-300">{approval.expected_benefit}</p>
        </div>
        <div>
          <p className="text-xs text-slate-500">{t("approvals.cost")}</p>
          <p className="text-sm text-slate-300">
            {approval.cost_sar != null ? `${approval.cost_sar} ${t("common.sar")}` : "—"}
          </p>
        </div>
        <div>
          <p className="text-xs text-slate-500">{t("approvals.reversible")}</p>
          <p className={`text-sm ${approval.reversible ? "text-emerald-300" : "text-red-300"}`}>
            {approval.reversible ? t("common.yes") : t("approvals.not_reversible")}
          </p>
        </div>
        <div>
          <p className="text-xs text-slate-500">{t("approvals.deadline")}</p>
          <p className="text-sm text-slate-300">
            {approval.deadline_at ? new Date(approval.deadline_at).toLocaleString() : "—"}
          </p>
        </div>
      </div>

      {!!approval.evidence && (
        <div>
          <p className="mb-1 text-xs text-slate-500">{t("approvals.evidence")}</p>
          <pre className="max-h-40 overflow-auto rounded-lg border border-white/5 bg-slate-950 p-2 text-xs text-slate-400">
            {JSON.stringify(approval.evidence, null, 2)}
          </pre>
        </div>
      )}

      <textarea
        value={comment}
        onChange={(e) => setComment(e.target.value)}
        placeholder={t("approvals.comment_label")}
        rows={2}
        className="rounded-lg border border-white/10 bg-slate-900 p-2 text-sm"
      />

      <div className="flex flex-wrap gap-2">
        <button
          type="button"
          disabled={resolve.isPending}
          onClick={() => resolve.mutate("approve")}
          className="min-h-[44px] flex-1 rounded-lg bg-emerald-500 px-3 text-sm font-semibold text-slate-950 disabled:opacity-40"
        >
          {t("approvals.approve")}
        </button>
        <button
          type="button"
          disabled={resolve.isPending}
          onClick={() => resolve.mutate("approve_with_conditions")}
          className="min-h-[44px] flex-1 rounded-lg border border-emerald-400/30 px-3 text-sm text-emerald-200 disabled:opacity-40"
        >
          {t("approvals.approve_conditions")}
        </button>
        <button
          type="button"
          disabled={resolve.isPending}
          onClick={() => resolve.mutate("request_revision")}
          className="min-h-[44px] flex-1 rounded-lg border border-amber-400/30 px-3 text-sm text-amber-200 disabled:opacity-40"
        >
          {t("approvals.request_revision")}
        </button>
        <button
          type="button"
          disabled={resolve.isPending}
          onClick={() => resolve.mutate("reject")}
          className="min-h-[44px] flex-1 rounded-lg border border-red-400/30 px-3 text-sm text-red-200 disabled:opacity-40"
        >
          {t("approvals.reject")}
        </button>
      </div>
    </div>
  );
}

export default function ApprovalsPage() {
  const { t } = useI18n();
  const approvalsQuery = useQuery({ queryKey: ["approvals"], queryFn: approvalsApi.list });

  return (
    <div className="mx-auto flex max-w-3xl flex-col gap-4">
      <h1 className="text-lg font-semibold">{t("approvals.title")}</h1>

      {approvalsQuery.isLoading ? (
        <p className="text-sm text-slate-500">{t("common.loading")}</p>
      ) : approvalsQuery.isError ? (
        <ErrorState message={t("common.error_generic")} onRetry={() => approvalsQuery.refetch()} />
      ) : approvalsQuery.data && approvalsQuery.data.length > 0 ? (
        <div className="flex flex-col gap-4">
          {approvalsQuery.data.map((approval) => (
            <ApprovalCard key={approval.id} approval={approval} />
          ))}
        </div>
      ) : (
        <EmptyState message={t("approvals.no_approvals")} />
      )}
    </div>
  );
}
