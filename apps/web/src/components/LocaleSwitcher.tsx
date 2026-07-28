"use client";

import { useI18n } from "@/i18n/I18nProvider";

export function LocaleSwitcher() {
  const { locale, setLocale } = useI18n();

  return (
    <div className="inline-flex rounded-full border border-white/10 bg-white/5 p-0.5 text-xs font-medium">
      <button
        type="button"
        onClick={() => setLocale("ar")}
        className={`min-h-[36px] min-w-[44px] rounded-full px-3 transition-colors ${
          locale === "ar" ? "bg-emerald-500 text-slate-950" : "text-slate-300"
        }`}
        aria-pressed={locale === "ar"}
      >
        AR
      </button>
      <button
        type="button"
        onClick={() => setLocale("en")}
        className={`min-h-[36px] min-w-[44px] rounded-full px-3 transition-colors ${
          locale === "en" ? "bg-emerald-500 text-slate-950" : "text-slate-300"
        }`}
        aria-pressed={locale === "en"}
      >
        EN
      </button>
    </div>
  );
}
