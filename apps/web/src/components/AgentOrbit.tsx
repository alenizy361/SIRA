"use client";

import { useEffect, useMemo, useRef, useState } from "react";
import { AICore } from "@/components/AICore";
import { useEventStore, type CoreState } from "@/store/eventStore";
import { agentMeta } from "@/lib/agentMeta";
import { useI18n } from "@/i18n/I18nProvider";

interface AgentLite {
  agent_key: string;
  display_name_en: string;
  display_name_ar: string;
  enabled: boolean;
}

// How long an agent node stays "lit" after its last event, in ms.
const ACTIVE_WINDOW_MS = 7000;
// How long a comms beam from an agent to the core is drawn, in ms.
const BEAM_MS = 1600;

interface Beam {
  id: string;
  agentKey: string;
  bornAt: number;
}

/**
 * The living command-center centerpiece: the AI core at the center with every
 * agent on an orbital ring around it. Nodes brighten and pulse when that agent
 * emits a real event, and a light beam streaks from the agent to the core on
 * each transmission - so the operator literally watches which agent is acting
 * and when. Everything here is driven by real WebSocket events from the store;
 * nothing is simulated. On an idle system the ring simply rests.
 */
export function AgentOrbit({ agents, coreState }: { agents: AgentLite[]; coreState: CoreState }) {
  const { locale } = useI18n();
  const events = useEventStore((s) => s.events);
  const [now, setNow] = useState(0);
  const beamsRef = useRef<Beam[]>([]);
  const lastSeqRef = useRef<number>(-1);

  // A slow clock so "active within the last N seconds" fades smoothly without
  // depending on new events arriving. Respect reduced-motion by ticking rarely.
  useEffect(() => {
    const reduce =
      typeof window !== "undefined" &&
      window.matchMedia?.("(prefers-reduced-motion: reduce)").matches;
    const interval = reduce ? 2000 : 400;
    const id = setInterval(() => setNow(Date.now()), interval);
    setNow(Date.now());
    return () => clearInterval(id);
  }, []);

  // Spawn a beam whenever a fresh event with a known agent actor arrives.
  useEffect(() => {
    const newest = events[0];
    if (!newest || newest.sequence === lastSeqRef.current) return;
    lastSeqRef.current = newest.sequence;
    if (newest.actor && agentMeta(newest.actor)) {
      beamsRef.current = [
        { id: newest.event_id, agentKey: newest.actor, bornAt: Date.now() },
        ...beamsRef.current,
      ].slice(0, 8);
    }
  }, [events]);

  // Last-activity timestamp per agent_key, from the event stream.
  const lastActivity = useMemo(() => {
    const map = new Map<string, { at: number; type: string }>();
    for (const e of events) {
      if (!e.actor) continue;
      if (!map.has(e.actor)) {
        map.set(e.actor, { at: new Date(e.timestamp).getTime(), type: e.type });
      }
    }
    return map;
  }, [events]);

  // Order agents so the ring layout is stable (enabled first, then by key).
  const ordered = useMemo(
    () =>
      [...agents].sort((a, b) =>
        a.enabled === b.enabled
          ? a.agent_key.localeCompare(b.agent_key)
          : a.enabled
            ? -1
            : 1
      ),
    [agents]
  );

  // Split across two concentric rings for breathing room with 23 nodes.
  const rings = useMemo(() => {
    const inner = ordered.filter((_, i) => i % 2 === 0);
    const outer = ordered.filter((_, i) => i % 2 === 1);
    return [
      { agents: inner, rx: 34, ry: 30 },
      { agents: outer, rx: 47, ry: 42 },
    ];
  }, [ordered]);

  const activeBeams = beamsRef.current.filter((b) => now - b.bornAt < BEAM_MS);

  return (
    <div className="relative aspect-square w-full overflow-hidden rounded-2xl border border-white/10 bg-[radial-gradient(circle_at_50%_45%,#0b1220_0%,#05070d_70%)]">
      {/* faint orbital guide rings */}
      <svg className="pointer-events-none absolute inset-0 h-full w-full" viewBox="0 0 100 100" preserveAspectRatio="none">
        {rings.map((r, i) => (
          <ellipse
            key={i}
            cx="50"
            cy="50"
            rx={r.rx}
            ry={r.ry}
            fill="none"
            stroke="rgba(148,163,184,0.10)"
            strokeWidth="0.15"
          />
        ))}
        {/* comms beams: agent node -> core */}
        {activeBeams.map((b) => {
          const pos = positionFor(b.agentKey, rings);
          if (!pos) return null;
          const t = (now - b.bornAt) / BEAM_MS;
          const color = agentMeta(b.agentKey).color;
          return (
            <line
              key={b.id}
              x1={pos.x}
              y1={pos.y}
              x2="50"
              y2="50"
              stroke={color}
              strokeWidth="0.35"
              strokeLinecap="round"
              opacity={Math.max(0, 0.9 * (1 - t))}
            />
          );
        })}
      </svg>

      {/* the core */}
      <div className="absolute left-1/2 top-1/2 h-[42%] w-[42%] -translate-x-1/2 -translate-y-1/2">
        <AICore state={coreState} />
      </div>

      {/* agent nodes */}
      {rings.map((ring) =>
        ring.agents.map((agent, idx) => {
          const pos = positionOnRing(idx, ring.agents.length, ring.rx, ring.ry);
          const act = lastActivity.get(agent.agent_key);
          const sinceActive = act ? now - act.at : Infinity;
          const isActive = sinceActive < ACTIVE_WINDOW_MS;
          const intensity = isActive ? 1 - sinceActive / ACTIVE_WINDOW_MS : 0;
          const meta = agentMeta(agent.agent_key);
          const name = locale === "ar" ? agent.display_name_ar : agent.display_name_en;
          return (
            <div
              key={agent.agent_key}
              className="group absolute -translate-x-1/2 -translate-y-1/2"
              style={{ left: `${pos.x}%`, top: `${pos.y}%` }}
              title={name}
            >
              <div
                className="flex items-center justify-center rounded-full border text-[8px] font-bold transition-all duration-500 sm:text-[10px]"
                style={{
                  width: isActive ? 34 : 28,
                  height: isActive ? 34 : 28,
                  color: agent.enabled ? "#e2e8f0" : "#64748b",
                  borderColor: meta.color,
                  background: agent.enabled
                    ? `radial-gradient(circle at 50% 40%, ${meta.color}${isActive ? "cc" : "22"}, #0a0f1a 80%)`
                    : "rgba(15,23,42,0.6)",
                  boxShadow: isActive
                    ? `0 0 ${8 + intensity * 22}px ${meta.color}, 0 0 4px ${meta.color}`
                    : agent.enabled
                      ? `0 0 4px ${meta.color}55`
                      : "none",
                  opacity: agent.enabled ? 1 : 0.55,
                }}
              >
                {meta.glyph}
              </div>
              {/* name label, appears on activity or hover */}
              <div
                className="pointer-events-none absolute left-1/2 top-full mt-1 -translate-x-1/2 whitespace-nowrap rounded bg-black/70 px-1.5 py-0.5 text-[9px] text-slate-200 opacity-0 transition-opacity group-hover:opacity-100"
                style={{ opacity: isActive ? 1 : undefined }}
              >
                {name}
              </div>
            </div>
          );
        })
      )}
    </div>
  );
}

// --- geometry helpers ------------------------------------------------------

function positionOnRing(index: number, count: number, rx: number, ry: number) {
  const angle = (index / Math.max(1, count)) * Math.PI * 2 - Math.PI / 2;
  return {
    x: 50 + Math.cos(angle) * rx,
    y: 50 + Math.sin(angle) * ry,
  };
}

function positionFor(
  agentKey: string,
  rings: { agents: AgentLite[]; rx: number; ry: number }[]
): { x: number; y: number } | null {
  for (const ring of rings) {
    const idx = ring.agents.findIndex((a) => a.agent_key === agentKey);
    if (idx >= 0) return positionOnRing(idx, ring.agents.length, ring.rx, ring.ry);
  }
  return null;
}
