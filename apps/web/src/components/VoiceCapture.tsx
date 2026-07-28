"use client";

import { useEffect, useRef, useState } from "react";
import { useI18n } from "@/i18n/I18nProvider";

// Minimal shape of the non-standard Web Speech API we rely on.
interface SpeechRecognitionResultLike {
  transcript: string;
}
interface SpeechRecognitionEventLike extends Event {
  results: ArrayLike<ArrayLike<SpeechRecognitionResultLike>>;
}
interface SpeechRecognitionLike extends EventTarget {
  lang: string;
  continuous: boolean;
  interimResults: boolean;
  start: () => void;
  stop: () => void;
  abort?: () => void;
  onresult: ((event: SpeechRecognitionEventLike) => void) | null;
  onerror: ((event: Event) => void) | null;
  onend: (() => void) | null;
}

type SpeechRecognitionCtor = new () => SpeechRecognitionLike;

function getSpeechRecognitionCtor(): SpeechRecognitionCtor | null {
  if (typeof window === "undefined") return null;
  const w = window as unknown as {
    SpeechRecognition?: SpeechRecognitionCtor;
    webkitSpeechRecognition?: SpeechRecognitionCtor;
  };
  return w.SpeechRecognition || w.webkitSpeechRecognition || null;
}

export function VoiceCapture({
  onConfirmed,
}: {
  onConfirmed: (text: string) => void;
}) {
  const { locale, t } = useI18n();
  const [supported, setSupported] = useState(true);
  const [listening, setListening] = useState(false);
  const [transcript, setTranscript] = useState<string | null>(null);
  const recognitionRef = useRef<SpeechRecognitionLike | null>(null);

  useEffect(() => {
    setSupported(!!getSpeechRecognitionCtor());
    // Tear down any active recognition on unmount so the microphone can't keep
    // capturing after the voice UI is gone (e.g. navigating away mid-capture).
    return () => {
      try {
        recognitionRef.current?.abort?.();
        recognitionRef.current?.stop?.();
      } catch {
        // best-effort teardown
      }
      recognitionRef.current = null;
    };
  }, []);

  function startListening() {
    const Ctor = getSpeechRecognitionCtor();
    if (!Ctor) {
      setSupported(false);
      return;
    }
    const recognition = new Ctor();
    recognition.lang = locale === "ar" ? "ar-SA" : "en-US";
    recognition.continuous = false;
    recognition.interimResults = false;

    recognition.onresult = (event) => {
      const first = event.results[0]?.[0]?.transcript;
      if (first) setTranscript(first);
      setListening(false);
    };
    recognition.onerror = () => setListening(false);
    recognition.onend = () => setListening(false);

    recognitionRef.current = recognition;
    setListening(true);
    recognition.start();
  }

  function stopListening() {
    recognitionRef.current?.stop();
    setListening(false);
  }

  if (!supported) {
    return (
      <p className="rounded-lg border border-white/10 bg-white/5 px-3 py-2 text-xs text-slate-400">
        {t("command_center.voice_unsupported")}
      </p>
    );
  }

  return (
    <div className="flex flex-col gap-2">
      <button
        type="button"
        onClick={listening ? stopListening : startListening}
        aria-pressed={listening}
        className={`flex min-h-[44px] items-center justify-center gap-2 rounded-full border px-4 text-sm font-medium transition-colors ${
          listening
            ? "border-red-400/50 bg-red-500/20 text-red-200"
            : "border-emerald-400/40 bg-emerald-500/10 text-emerald-200 hover:bg-emerald-500/20"
        }`}
      >
        <span
          className={`h-2.5 w-2.5 rounded-full ${listening ? "bg-red-400 animate-pulse" : "bg-emerald-400"}`}
          aria-hidden
        />
        {listening ? t("command_center.voice_listening") : t("command_center.voice_ptt")}
      </button>

      {transcript !== null && (
        <div className="rounded-xl border border-white/10 bg-white/5 p-3">
          <p className="mb-1 text-xs font-medium text-slate-400">
            {t("command_center.voice_confirm_title")}
          </p>
          <p className="mb-2 text-xs text-slate-500">{t("command_center.voice_confirm_body")}</p>
          <textarea
            value={transcript}
            onChange={(e) => setTranscript(e.target.value)}
            rows={2}
            className="mb-2 w-full rounded-lg border border-white/10 bg-slate-900 p-2 text-sm text-slate-100"
          />
          <div className="flex gap-2">
            <button
              type="button"
              onClick={() => {
                onConfirmed(transcript);
                setTranscript(null);
              }}
              className="min-h-[44px] flex-1 rounded-lg bg-emerald-500 px-3 text-sm font-medium text-slate-950"
            >
              {t("common.confirm")}
            </button>
            <button
              type="button"
              onClick={() => setTranscript(null)}
              className="min-h-[44px] flex-1 rounded-lg border border-white/10 px-3 text-sm text-slate-300"
            >
              {t("common.cancel")}
            </button>
          </div>
        </div>
      )}
    </div>
  );
}
