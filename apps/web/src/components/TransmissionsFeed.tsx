"use client";

import { useMemo } from "react";
import { useEventStore } from "@/store/eventStore";
import { agentMeta, AGENT_META } from "@/lib/agentMeta";
import { toTransmission, TONE_COLOR } from "@/lib/transmissions";
import { useI18n } from "@/i18n/I18nProvider";

interface AgentLite {
  agent_key: string;
  display_name_en: string;
  display_name_ar: string;
}

/**
 * The live agent-comms feed. Each realtime event is rendered as a readable
 * transmission - "CEO drafted a plan · Fix funnel drop-off" - colored by tone,
 * with the acting agent's identity hue. This is the operator's running view of
 * what every agent is doing and saying, straight off the WebSocket stream.
 */
export function TransmissionsFeed({ agents }: { agents: AgentLite[] }) {
  const { t, locale } = useI18n();
  const events = useEventStore((s) => s.events);

  const nameFor = useMemo(() => {
    const map = new Map<string, string>();
    for (const a of agents) {
      map.set(a.agent_key, locale === "ar" ? a.display_name_ar : a.display_name_en);
    }
    return (actor: string) => {
      if (map.has(actor)) return map.get(actor)!;
      if (actor in AGENT_META) return actor;
      // Non-agent actors (a user id, "system") - keep it short and human.
      if (actor === "system") return locale === "ar" ? "النظام" : "System";
      if (actor && actor.length > 12) return locale === "ar" ? "المشغّل" : "Operator";
      return actor || (locale === "ar" ? "النظام" : "System");
    };
  }, [agents, locale]);

  if (events.length === 0) {
    return (
      <p className="py-8 text-center text-sm text-slate-500">
        {t("command_center.no_events")}
      </p>
    );
  }

  return (
    <ul className="flex max-h-[28rem] flex-col gap-1.5 overflow-y-auto pr-1">
      {events.map((e) => {
        const tx = toTransmission(e);
        const isAgent = e.actor in AGENT_META;
        const hue = isAgent ? agentMeta(e.actor).color : TONE_COLOR[tx.tone];
        const verb = locale === "ar" ? tx.verbAr : tx.verbEn;
        return (
          <li
            key={e.event_id}
            className="flex items-start gap-2 rounded-lg border border-white/5 bg-white/[0.02] px-2.5 py-1.5"
            style={{ borderInlineStartColor: hue, borderInlineStartWidth: 2 }}
          >
            <span
              className="mt-1.5 inline-block h-1.5 w-1.5 shrink-0 rounded-full"
              style={{ background: TONE_COLOR[tx.tone], boxShadow: `0 0 6px ${TONE_COLOR[tx.tone]}` }}
              aria-hidden
            />
            <div className="min-w-0 flex-1">
              <div className="flex items-baseline justify-between gap-2">
                <span className="truncate text-xs">
                  <span className="font-semibold" style={{ color: hue }}>
                    {nameFor(e.actor)}
                  </span>{" "}
                  <span className="text-slate-300">{verb}</span>
                </span>
                <time className="shrink-0 text-[10px] text-slate-500">
                  {new Date(e.timestamp).toLocaleTimeString(locale === "ar" ? "ar-SA" : undefined, {
                    hour: "2-digit",
                    minute: "2-digit",
                    second: "2-digit",
                  })}
                </time>
              </div>
              {tx.detail ? (
                <p className="truncate text-[11px] text-slate-500">{tx.detail}</p>
              ) : null}
            </div>
          </li>
        );
      })}
    </ul>
  );
}
