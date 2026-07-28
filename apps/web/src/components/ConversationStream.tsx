"use client";

import { useMemo } from "react";
import { useEventStore, type CoreState, type WireEvent } from "@/store/eventStore";
import { agentMeta, AGENT_META } from "@/lib/agentMeta";
import { toTransmission, TONE_COLOR } from "@/lib/transmissions";
import { useI18n } from "@/i18n/I18nProvider";

interface AgentLite {
  agent_key: string;
  display_name_en: string;
  display_name_ar: string;
}

// The agent that "speaks" in the conversation is the event's actor. States
// in which the newest actor should show a live "thinking" indicator.
const THINKING_STATES: CoreState[] = ["planning", "coding", "delegating", "understanding", "reviewing"];

/**
 * The live agent conversation - the heart of "watch them think and talk".
 * Each realtime event becomes a message from the acting agent, rendered as a
 * chat thread: the CEO announces a plan and its rationale, hands work to
 * named agents, each engineer reports the tool it is using and its result,
 * QA reviews, and so on. Consecutive messages from one agent group into a
 * cluster. What's shown are decision summaries, tool activity and outputs -
 * never raw chain-of-thought (the worker strips that before it ever leaves
 * the host), so this honors the constitution while still reading like the
 * agents thinking out loud.
 */
export function ConversationStream({ agents }: { agents: AgentLite[] }) {
  const { t, locale } = useI18n();
  const events = useEventStore((s) => s.events);
  const coreState = useEventStore((s) => s.coreState);

  const nameFor = useMemo(() => {
    const map = new Map<string, string>();
    for (const a of agents) map.set(a.agent_key, locale === "ar" ? a.display_name_ar : a.display_name_en);
    return (actor: string) => {
      if (map.has(actor)) return map.get(actor)!;
      if (actor in AGENT_META) return actor;
      if (actor === "system") return locale === "ar" ? "النظام" : "System";
      if (actor && actor.length > 12) return locale === "ar" ? "المشغّل" : "Operator";
      return actor || (locale === "ar" ? "النظام" : "System");
    };
  }, [agents, locale]);

  // Oldest -> newest for a natural chat reading order, then cluster by actor.
  const clusters = useMemo(() => {
    const chron = [...events].reverse();
    const out: { actor: string; items: WireEvent[] }[] = [];
    for (const e of chron) {
      const last = out[out.length - 1];
      if (last && last.actor === e.actor && e.actor) last.items.push(e);
      else out.push({ actor: e.actor, items: [e] });
    }
    return out.slice(-40); // keep the view bounded
  }, [events]);

  const newest = events[0];
  const showThinking =
    newest && newest.actor in AGENT_META && THINKING_STATES.includes(coreState);

  if (events.length === 0) {
    return (
      <div className="flex flex-1 flex-col items-center justify-center gap-2 py-10 text-center">
        <div className="h-10 w-10 rounded-full border border-white/10 bg-white/[0.02]" />
        <p className="text-sm text-slate-500">{t("command_center.no_events")}</p>
        <p className="max-w-[22ch] text-xs text-slate-600">{t("command_center.conversation_hint")}</p>
      </div>
    );
  }

  return (
    <div className="flex flex-1 flex-col gap-3 overflow-y-auto pe-1">
      {clusters.map((cluster, ci) => {
        const isAgent = cluster.actor in AGENT_META;
        const meta = isAgent ? agentMeta(cluster.actor) : null;
        const hue = meta ? meta.color : "#64748b";
        const glyph = meta ? meta.glyph : (cluster.actor || "•").slice(0, 2).toUpperCase();
        return (
          <div key={ci} className="flex gap-2.5">
            {/* avatar */}
            <div className="shrink-0 pt-0.5">
              <div
                className="flex h-8 w-8 items-center justify-center rounded-full border text-[9px] font-bold"
                style={{
                  color: "#e2e8f0",
                  borderColor: hue,
                  background: `radial-gradient(circle at 50% 35%, ${hue}44, #0a0f1a 80%)`,
                  boxShadow: `0 0 10px ${hue}55`,
                }}
              >
                {glyph}
              </div>
            </div>
            {/* messages */}
            <div className="min-w-0 flex-1">
              <div className="mb-1 flex items-baseline gap-2">
                <span className="text-xs font-semibold" style={{ color: hue }}>
                  {nameFor(cluster.actor)}
                </span>
                <time className="text-[10px] text-slate-600">
                  {new Date(cluster.items[cluster.items.length - 1].timestamp).toLocaleTimeString(
                    locale === "ar" ? "ar-SA" : undefined,
                    { hour: "2-digit", minute: "2-digit" }
                  )}
                </time>
              </div>
              <div className="flex flex-col gap-1.5">
                {cluster.items.map((e) => {
                  const tx = toTransmission(e);
                  const verb = locale === "ar" ? tx.verbAr : tx.verbEn;
                  return (
                    <div
                      key={e.event_id}
                      className="rounded-2xl rounded-ss-sm border bg-white/[0.03] px-3 py-2"
                      style={{ borderColor: `${TONE_COLOR[tx.tone]}33` }}
                    >
                      <div className="flex items-center gap-1.5">
                        <span
                          className="inline-block h-1.5 w-1.5 rounded-full"
                          style={{ background: TONE_COLOR[tx.tone], boxShadow: `0 0 6px ${TONE_COLOR[tx.tone]}` }}
                          aria-hidden
                        />
                        <span className="text-xs text-slate-200">{verb}</span>
                      </div>
                      {tx.detail ? (
                        <p className="mt-1 whitespace-pre-wrap break-words text-[12px] leading-relaxed text-slate-400">
                          {tx.detail}
                        </p>
                      ) : null}
                    </div>
                  );
                })}
              </div>
            </div>
          </div>
        );
      })}

      {showThinking ? (
        <div className="flex items-center gap-2.5 opacity-80">
          <div className="h-8 w-8" />
          <div className="flex items-center gap-1 rounded-2xl border border-white/10 bg-white/[0.03] px-3 py-2">
            <span className="typing-dot" />
            <span className="typing-dot" style={{ animationDelay: "0.15s" }} />
            <span className="typing-dot" style={{ animationDelay: "0.3s" }} />
            <span className="ms-1 text-[11px] text-slate-500">
              {nameFor(newest.actor)} {t("command_center.thinking")}
            </span>
          </div>
          <style jsx>{`
            .typing-dot {
              width: 5px;
              height: 5px;
              border-radius: 9999px;
              background: #64748b;
              animation: blink 1s ease-in-out infinite;
            }
            @keyframes blink {
              0%, 100% { opacity: 0.25; transform: translateY(0); }
              50% { opacity: 1; transform: translateY(-2px); }
            }
            @media (prefers-reduced-motion: reduce) {
              .typing-dot { animation: none; }
            }
          `}</style>
        </div>
      ) : null}
    </div>
  );
}
