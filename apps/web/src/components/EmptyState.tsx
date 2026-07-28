"use client";

import { useI18n } from "@/i18n/I18nProvider";

export function NotAvailableYet({ detail }: { detail?: string }) {
  const { t } = useI18n();
  return (
    <div className="rounded-2xl border border-dashed border-white/15 bg-white/[0.03] p-6 text-center">
      <p className="text-sm font-medium text-slate-300">{t("common.not_available_yet")}</p>
      <p className="mt-1 text-xs text-slate-500">{detail || t("common.not_available_detail")}</p>
    </div>
  );
}

export function EmptyState({ message }: { message: string }) {
  return (
    <div className="rounded-2xl border border-white/10 bg-white/[0.02] p-6 text-center text-sm text-slate-400">
      {message}
    </div>
  );
}

export function ErrorState({ message, onRetry }: { message: string; onRetry?: () => void }) {
  const { t } = useI18n();
  return (
    <div className="rounded-2xl border border-red-400/20 bg-red-500/[0.06] p-6 text-center">
      <p className="text-sm text-red-200">{message}</p>
      {onRetry && (
        <button
          type="button"
          onClick={onRetry}
          className="mt-3 min-h-[44px] rounded-lg border border-red-400/30 px-4 text-xs font-medium text-red-200"
        >
          {t("common.retry")}
        </button>
      )}
    </div>
  );
}
