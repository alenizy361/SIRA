"use client";

import { useEffect, useState } from "react";
import { useI18n } from "@/i18n/I18nProvider";

export function SpeakButton({ text }: { text: string }) {
  const { locale, t } = useI18n();
  const [supported, setSupported] = useState(true);

  useEffect(() => {
    setSupported(typeof window !== "undefined" && "speechSynthesis" in window);
  }, []);

  if (!supported) return null;

  function speak() {
    window.speechSynthesis.cancel();
    const utterance = new SpeechSynthesisUtterance(text);
    utterance.lang = locale === "ar" ? "ar-SA" : "en-US";
    window.speechSynthesis.speak(utterance);
  }

  return (
    <button
      type="button"
      onClick={speak}
      className="min-h-[44px] rounded-lg border border-white/10 px-3 text-xs font-medium text-slate-300 hover:bg-white/5"
    >
      {t("command_center.voice_speak_back")}
    </button>
  );
}
