"use client";

import { useState } from "react";
import Link from "next/link";
import { useRouter } from "next/navigation";
import { useQueryClient } from "@tanstack/react-query";
import { useI18n } from "@/i18n/I18nProvider";
import { authApi, ApiError } from "@/lib/api";
import { LocaleSwitcher } from "@/components/LocaleSwitcher";

export default function LoginPage() {
  const { t } = useI18n();
  const router = useRouter();
  const queryClient = useQueryClient();
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [submitting, setSubmitting] = useState(false);

  async function handleSubmit(e: React.FormEvent) {
    e.preventDefault();
    setError(null);
    setSubmitting(true);
    try {
      await authApi.login(email, password);
      await queryClient.invalidateQueries({ queryKey: ["me"] });
      router.replace("/command-center");
    } catch (err) {
      if (err instanceof ApiError && err.status === 423) {
        setError(t("auth.locked_error"));
      } else {
        setError(t("auth.login_error"));
      }
    } finally {
      setSubmitting(false);
    }
  }

  return (
    <div
      className="flex min-h-dvh flex-col items-center justify-center bg-slate-950 px-4"
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
        <p className="mb-6 text-center text-sm text-slate-400">{t("auth.login_title")}</p>

        <form onSubmit={handleSubmit} className="flex flex-col gap-3">
          <label className="flex flex-col gap-1 text-sm">
            {t("auth.email")}
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
            {t("auth.password")}
            <input
              type="password"
              required
              value={password}
              onChange={(e) => setPassword(e.target.value)}
              className="min-h-[44px] rounded-lg border border-white/10 bg-slate-900 px-3 text-sm"
              autoComplete="current-password"
            />
          </label>

          {error && <p className="text-sm text-red-300">{error}</p>}

          <button
            type="submit"
            disabled={submitting}
            className="mt-2 min-h-[44px] rounded-lg bg-emerald-500 text-sm font-semibold text-slate-950 disabled:opacity-50"
          >
            {t("auth.login_button")}
          </button>
        </form>

        <div className="mt-6 border-t border-white/10 pt-4 text-center text-xs text-slate-400">
          <p>{t("auth.first_time")}</p>
          <Link href="/onboarding" className="mt-1 inline-block min-h-[44px] py-2 text-emerald-300">
            {t("auth.go_onboard")}
          </Link>
        </div>
      </div>
    </div>
  );
}
