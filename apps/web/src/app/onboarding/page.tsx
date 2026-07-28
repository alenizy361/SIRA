"use client";

import { useState } from "react";
import Link from "next/link";
import { useRouter } from "next/navigation";
import { useQueryClient } from "@tanstack/react-query";
import { useI18n } from "@/i18n/I18nProvider";
import { authApi, ApiError } from "@/lib/api";
import { LocaleSwitcher } from "@/components/LocaleSwitcher";

export default function OnboardingPage() {
  const { t } = useI18n();
  const router = useRouter();
  const queryClient = useQueryClient();
  const [orgName, setOrgName] = useState("");
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [displayName, setDisplayName] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [alreadyOnboarded, setAlreadyOnboarded] = useState(false);
  const [submitting, setSubmitting] = useState(false);

  async function handleSubmit(e: React.FormEvent) {
    e.preventDefault();
    setError(null);
    setAlreadyOnboarded(false);
    setSubmitting(true);
    try {
      await authApi.onboard({
        organization_name: orgName,
        admin_email: email,
        admin_password: password,
        admin_display_name: displayName,
      });
      await queryClient.invalidateQueries({ queryKey: ["me"] });
      router.replace("/command-center");
    } catch (err) {
      if (err instanceof ApiError && err.status === 403) {
        setAlreadyOnboarded(true);
      } else if (err instanceof ApiError && err.status === 400) {
        setError(String(err.message));
      } else {
        setError(t("common.error_generic"));
      }
    } finally {
      setSubmitting(false);
    }
  }

  return (
    <div
      className="flex min-h-dvh flex-col items-center justify-center bg-slate-950 px-4 py-8"
      style={{
        paddingTop: "env(safe-area-inset-top, 0px)",
        paddingBottom: "env(safe-area-inset-bottom, 0px)",
      }}
    >
      <div className="absolute top-4 end-4">
        <LocaleSwitcher />
      </div>
      <div className="w-full max-w-sm rounded-2xl border border-white/10 bg-white/[0.03] p-6">
        <h1 className="mb-1 text-center text-lg font-semibold">{t("app.name")}</h1>
        <p className="mb-6 text-center text-sm text-slate-400">{t("auth.onboard_title")}</p>

        {alreadyOnboarded ? (
          <div className="flex flex-col items-center gap-3 text-center text-sm">
            <p className="text-amber-300">{t("auth.already_onboarded")}</p>
            <Link
              href="/login"
              className="min-h-[44px] rounded-lg border border-white/10 px-4 py-2 text-emerald-300"
            >
              {t("auth.go_login")}
            </Link>
          </div>
        ) : (
          <form onSubmit={handleSubmit} className="flex flex-col gap-3">
            <label className="flex flex-col gap-1 text-sm">
              {t("auth.org_name")}
              <input
                required
                value={orgName}
                onChange={(e) => setOrgName(e.target.value)}
                className="min-h-[44px] rounded-lg border border-white/10 bg-slate-900 px-3 text-sm"
              />
            </label>
            <label className="flex flex-col gap-1 text-sm">
              {t("auth.admin_display_name")}
              <input
                required
                value={displayName}
                onChange={(e) => setDisplayName(e.target.value)}
                className="min-h-[44px] rounded-lg border border-white/10 bg-slate-900 px-3 text-sm"
              />
            </label>
            <label className="flex flex-col gap-1 text-sm">
              {t("auth.admin_email")}
              <input
                type="email"
                required
                value={email}
                onChange={(e) => setEmail(e.target.value)}
                className="min-h-[44px] rounded-lg border border-white/10 bg-slate-900 px-3 text-sm"
                autoComplete="email"
              />
            </label>
            <label className="flex flex-col gap-1 text-sm">
              {t("auth.admin_password")}
              <input
                type="password"
                required
                minLength={12}
                value={password}
                onChange={(e) => setPassword(e.target.value)}
                className="min-h-[44px] rounded-lg border border-white/10 bg-slate-900 px-3 text-sm"
                autoComplete="new-password"
              />
            </label>

            {error && <p className="text-sm text-red-300">{error}</p>}

            <button
              type="submit"
              disabled={submitting}
              className="mt-2 min-h-[44px] rounded-lg bg-emerald-500 text-sm font-semibold text-slate-950 disabled:opacity-50"
            >
              {t("auth.onboard_button")}
            </button>

            <div className="mt-2 border-t border-white/10 pt-4 text-center text-xs text-slate-400">
              <p>{t("auth.onboard_done_login")}</p>
              <Link href="/login" className="mt-1 inline-block min-h-[44px] py-2 text-emerald-300">
                {t("auth.go_login")}
              </Link>
            </div>
          </form>
        )}
      </div>
    </div>
  );
}
