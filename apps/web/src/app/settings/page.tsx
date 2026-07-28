"use client";

import { useQuery } from "@tanstack/react-query";
import { useI18n } from "@/i18n/I18nProvider";
import { authApi } from "@/lib/api";
import { LocaleSwitcher } from "@/components/LocaleSwitcher";
import { ErrorState } from "@/components/EmptyState";

export default function SettingsPage() {
  const { t } = useI18n();
  const meQuery = useQuery({ queryKey: ["me"], queryFn: authApi.me });

  return (
    <div className="mx-auto flex max-w-2xl flex-col gap-4">
      <h1 className="text-lg font-semibold">{t("placeholders.settings_title")}</h1>

      <div className="rounded-2xl border border-white/10 bg-white/[0.03] p-4">
        <h2 className="mb-3 text-sm font-semibold text-slate-300">{t("auth.login_title")}</h2>
        {meQuery.isLoading ? (
          <p className="text-sm text-slate-500">{t("common.loading")}</p>
        ) : meQuery.isError || !meQuery.data ? (
          <ErrorState message={t("common.error_generic")} onRetry={() => meQuery.refetch()} />
        ) : (
          <dl className="grid grid-cols-2 gap-2 text-sm">
            <dt className="text-xs text-slate-500">{t("auth.admin_display_name")}</dt>
            <dd>{meQuery.data.display_name}</dd>
            <dt className="text-xs text-slate-500">{t("auth.email")}</dt>
            <dd>{meQuery.data.email}</dd>
            <dt className="text-xs text-slate-500">2FA</dt>
            <dd>{meQuery.data.totp_enabled ? t("common.enabled") : t("common.disabled")}</dd>
          </dl>
        )}
      </div>

      <div className="rounded-2xl border border-white/10 bg-white/[0.03] p-4">
        <h2 className="mb-3 text-sm font-semibold text-slate-300">{t("nav.settings")} · AR / EN</h2>
        <LocaleSwitcher />
      </div>

      <div className="rounded-2xl border border-dashed border-white/15 bg-white/[0.02] p-4">
        <p className="text-sm text-slate-400">{t("common.not_available_yet")}</p>
        <p className="mt-1 text-xs text-slate-500">{t("common.not_available_detail")}</p>
      </div>
    </div>
  );
}
