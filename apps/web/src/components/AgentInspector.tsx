"use client";

import { useEffect, useMemo } from "react";
import { useEventStore, type WireEvent } from "@/store/eventStore";
import { agentMeta } from "@/lib/agentMeta";
import { toTransmission, TONE_COLOR } from "@/lib/transmissions";
import { useI18n } from "@/i18n/I18nProvider";

interface AgentLite {
  agent_key: string;
  display_name_en: string;
  display_name_ar: string;
}

const PHASE_LABEL: Record<string, string> = {
  received: "phase_received",
  working: "phase_working",
  done: "phase_done",
  blocked: "phase_blocked",
};

/**
 * A focused, per-agent view: exactly what THIS agent is saying and doing,
 * pulled live from the real event stream. Opens when you click an agent on the
 * neural board. Every event this agent emitted becomes a readable line - the
 * verb (what it did) plus its own words/output (what it said) - newest first.
 */
export function AgentInspector({
  agentKey,
  agents,
  onClose,
}: {
  agentKey: string;
  agents: AgentLite[];
  onClose: () => void;
}) {
  const { t, locale } = useI18n();
  const events = useEventStore((s) => s.events);
  const activity = useEventStore((s) => s.agentActivity[agentKey]);

  const meta = agentMeta(agentKey);
  const name = useMemo(() => {
    const a = agents.find((x) => x.agent_key === agentKey);
    const raw = a ? (locale === "ar" ? a.display_name_ar : a.display_name_en) : agentKey;
    return raw.replace(/^وكيل\s+/, "").replace(/\s+Agent$/i, "");
  }, [agents, agentKey, locale]);

  // Only this agent's events, oldest -> newest is more natural for a log; but
  // we keep newest-first so the latest thing it said is right at the top.
  const mine = useMemo(
    () => events.filter((e) => e.actor === agentKey && e.type !== "core.state.changed"),
    [events, agentKey]
  );

  // Close on Escape.
  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      if (e.key === "Escape") onClose();
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [onClose]);

  const phaseKey = activity ? PHASE_LABEL[activity.phase] : null;

  return (
    <div className="fixed inset-0 z-50 flex" role="dialog" aria-modal="true">
      {/* scrim */}
      <button
        type="button"
        aria-label={t("common.close")}
        onClick={onClose}
        className="flex-1 bg-black/60 backdrop-blur-sm"
      />
      {/* panel */}
      <aside
        className="flex h-full w-full max-w-[420px] flex-col border-s bg-[#05070f] shadow-2xl"
        style={{ borderColor: `${meta.color}44` }}
        dir={locale === "ar" ? "rtl" : "ltr"}
      >
        {/* header */}
        <div className="flex items-center gap-3 border-b border-white/10 p-4">
          <span
            className="grid h-11 w-11 flex-none place-items-center rounded-full border text-xs font-bold"
            style={{
              borderColor: meta.color,
              color: meta.color,
              background: `radial-gradient(circle at 50% 35%, ${meta.color}44, #05070f 80%)`,
              boxShadow: `0 0 14px ${meta.color}66`,
            }}
          >
            {meta.glyph}
          </span>
          <div className="min-w-0 flex-1">
            <div className="truncate text-sm font-bold text-slate-100">{name}</div>
            {phaseKey ? (
              <div className="text-[11px] font-semibold" style={{ color: TONE_COLOR.active }}>
                ● {t(`command_center.${phaseKey}`)}
                {activity?.detail ? <span className="text-slate-500"> · {activity.detail}</span> : null}
              </div>
            ) : (
              <div className="text-[11px] text-slate-500">{t("command_center.inspector_idle")}</div>
            )}
          </div>
          <button
            type="button"
            onClick={onClose}
            className="rounded-lg border border-white/10 px-2.5 py-1 text-xs text-slate-300 hover:bg-white/5"
          >
            {t("common.close")}
          </button>
        </div>

        {/* stream */}
        <div className="flex-1 overflow-y-auto p-4">
          {mine.length === 0 ? (
            <div className="flex h-full flex-col items-center justify-center gap-2 text-center">
              <p className="text-sm text-slate-500">{t("command_center.inspector_empty")}</p>
            </div>
          ) : (
            <div className="flex flex-col gap-2">
              {mine.map((e: WireEvent) => {
                const tx = toTransmission(e);
                const verb = locale === "ar" ? tx.verbAr : tx.verbEn;
                const time = new Date(e.timestamp).toLocaleTimeString(locale === "ar" ? "ar-SA" : undefined, {
                  hour: "2-digit",
                  minute: "2-digit",
                  second: "2-digit",
                });
                return (
                  <div
                    key={e.event_id}
                    className="rounded-xl border bg-white/[0.03] p-3"
                    style={{ borderColor: `${TONE_COLOR[tx.tone]}33` }}
                  >
                    <div className="flex items-center justify-between gap-2">
                      <span className="flex items-center gap-1.5 text-xs font-semibold text-slate-200">
                        <span
                          className="inline-block h-1.5 w-1.5 rounded-full"
                          style={{ background: TONE_COLOR[tx.tone], boxShadow: `0 0 6px ${TONE_COLOR[tx.tone]}` }}
                          aria-hidden
                        />
                        {verb}
                      </span>
                      <time className="text-[10px] tabular-nums text-slate-600">{time}</time>
                    </div>
                    {tx.detail ? (
                      <p className="mt-1.5 whitespace-pre-wrap break-words text-[12.5px] leading-relaxed text-slate-300">
                        {tx.detail}
                      </p>
                    ) : null}
                  </div>
                );
              })}
            </div>
          )}
        </div>
      </aside>
    </div>
  );
}
