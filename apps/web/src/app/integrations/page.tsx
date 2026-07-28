"use client";

import { useI18n } from "@/i18n/I18nProvider";

// Known optional integrations from the product spec (constitution). None are
// connected in this build - there is no backend endpoint yet that reports
// integration status, so every row is honestly rendered as "Not configured"
// rather than simulated as connected.
const INTEGRATIONS = [
  { key: "github", name: "GitHub" },
  { key: "vercel", name: "Vercel" },
  { key: "sentry", name: "Sentry" },
  { key: "posthog", name: "PostHog" },
  { key: "google_analytics", name: "Google Analytics" },
  { key: "google_search_console", name: "Google Search Console" },
  { key: "google_ads", name: "Google Ads" },
  { key: "email", name: "Email" },
  { key: "support_platform", name: "Support Platform" },
  { key: "payment_processor", name: "Payment Processor" },
];

export default function IntegrationsPage() {
  const { t } = useI18n();

  return (
    <div className="mx-auto flex max-w-3xl flex-col gap-4">
      <div>
        <h1 className="text-lg font-semibold">{t("integrations.title")}</h1>
        <p className="mt-1 text-sm text-slate-500">{t("integrations.subtitle")}</p>
      </div>

      <div className="flex flex-col gap-2">
        {INTEGRATIONS.map((integration) => (
          <div
            key={integration.key}
            className="flex items-center justify-between rounded-2xl border border-white/10 bg-white/[0.03] p-4"
          >
            <span className="font-medium text-slate-100">{integration.name}</span>
            <span className="inline-flex items-center gap-1.5 rounded-full bg-slate-600/20 px-2.5 py-1 text-xs font-medium text-slate-400">
              <span className="h-1.5 w-1.5 rounded-full bg-slate-500" />
              {t("integrations.status_not_configured")}
            </span>
          </div>
        ))}
      </div>
    </div>
  );
}
