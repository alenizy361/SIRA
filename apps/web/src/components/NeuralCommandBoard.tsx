"use client";

import { useEffect, useMemo, useRef, useState } from "react";
import { useEventStore, type AgentActivity, type AgentPhase } from "@/store/eventStore";
import { agentMeta } from "@/lib/agentMeta";
import { coreStateColor } from "@/components/AICore";
import { useI18n } from "@/i18n/I18nProvider";

// How long an agent stays visibly "active" after its last working event, and
// how long a terminal (done/blocked) badge lingers before the capsule rests.
const ACTIVE_TTL = 120_000;
const TERMINAL_TTL = 12_000;

/** The activity still worth showing, given how long ago it happened. */
function livePhase(a: AgentActivity | undefined, now: number): AgentPhase | null {
  if (!a) return null;
  const age = now - a.since;
  if (a.phase === "working" || a.phase === "received") return age < ACTIVE_TTL ? a.phase : null;
  return age < TERMINAL_TTL ? a.phase : null;
}

const PHASE_COLOR: Record<AgentPhase, string> = {
  received: "#fbbf24",
  working: "#22d3ee",
  done: "#34d399",
  blocked: "#fb7185",
};

interface AgentLite {
  agent_key: string;
  display_name_en: string;
  display_name_ar: string;
  enabled: boolean;
}

/**
 * The neural command board: a plasma core with a rotating 3D web inside,
 * electric tendrils reaching out to agent capsules around it. Driven
 * entirely by REAL websocket events - when an agent emits an event its
 * capsule ignites, a spark travels down its tendril into the core, and the
 * core surges in that agent's color. The core's resting tint follows the
 * org-wide CoreState. Nothing here is simulated.
 */

// The 11 agents pinned on the board (the rest still light the core when
// they act - their events surge the orb in their color, and the live feed
// names them). Positions are % of the stage, tuned to the approved design.
const BOARD: { key: string; x: number; y: number }[] = [
  { key: "ceo", x: 50, y: 6 },
  { key: "frontend_engineer", x: 24, y: 12 },
  { key: "qa", x: 75, y: 13 },
  { key: "backend_engineer", x: 15, y: 29 },
  { key: "security", x: 85, y: 31 },
  { key: "database_engineer", x: 14, y: 50 },
  { key: "analytics", x: 85, y: 52 },
  { key: "ux_research", x: 16, y: 71 },
  { key: "marketing_growth", x: 84, y: 73 },
  { key: "product_manager", x: 31, y: 90 },
  { key: "finance_procurement", x: 69, y: 90 },
];

interface Rgb { r: number; g: number; b: number }
function hexRgb(h: string): Rgb {
  const s = h.replace("#", "");
  return { r: parseInt(s.slice(0, 2), 16), g: parseInt(s.slice(2, 4), 16), b: parseInt(s.slice(4, 6), 16) };
}
function rgba(c: Rgb, a: number): string {
  return `rgba(${c.r},${c.g},${c.b},${a})`;
}
function lerpC(a: Rgb, b: Rgb, t: number): Rgb {
  return { r: a.r + (b.r - a.r) * t, g: a.g + (b.g - a.g) * t, b: a.b + (b.b - a.b) * t };
}

const VIOLET = hexRgb("#8b5cf6");
const CYAN = hexRgb("#22d3ee");

interface BoardNode {
  key: string;
  x: number; // %
  y: number; // %
  rgb: Rgb;
  glow: number;
  seed: number;
  active: boolean; // held true while its agent is received/working
}

interface Pulse { node: BoardNode; t: number; sp: number }

export function NeuralCommandBoard({ agents }: { agents: AgentLite[] }) {
  const { locale, t } = useI18n();
  const stageRef = useRef<HTMLDivElement>(null);
  const canvasRef = useRef<HTMLCanvasElement>(null);
  const events = useEventStore((s) => s.events);
  const coreState = useEventStore((s) => s.coreState);
  const agentActivity = useEventStore((s) => s.agentActivity);
  const lastSeqRef = useRef(-1);

  // A 1s tick so the DOM capsules re-evaluate their live phase (fade a "done"
  // badge, drop a stale "working" ring) even when no new event arrives.
  const [, setNow] = useState(0);
  useEffect(() => {
    const id = setInterval(() => setNow((n) => n + 1), 1000);
    return () => clearInterval(id);
  }, []);

  // Mutable scene state lives in refs so the RAF loop never re-renders React.
  const sceneRef = useRef<{
    nodes: BoardNode[];
    pulses: Pulse[];
    burst: number;
    burstColor: Rgb | null;
    stateTint: Rgb;
    frame: number;
  }>({ nodes: [], pulses: [], burst: 0, burstColor: null, stateTint: CYAN, frame: 0 });

  // Live activity for the RAF loop (kept in a ref so the canvas effect, which
  // mounts once, always sees the freshest map without re-subscribing).
  const activityRef = useRef<Record<string, AgentActivity>>({});
  activityRef.current = agentActivity;

  const nodes = useMemo<BoardNode[]>(
    () =>
      BOARD.map((b, i) => ({
        key: b.key,
        x: b.x,
        y: b.y,
        rgb: hexRgb(agentMeta(b.key).color),
        glow: 0,
        seed: i * 17.3,
        active: false,
      })),
    []
  );
  sceneRef.current.nodes = nodes;
  sceneRef.current.stateTint = hexRgb(coreStateColor(coreState));

  const nameFor = useMemo(() => {
    const map = new Map<string, string>();
    for (const a of agents) map.set(a.agent_key, locale === "ar" ? a.display_name_ar : a.display_name_en);
    return (key: string) => {
      const n = map.get(key) || key;
      // Capsule labels drop the uniform "وكيل" prefix so the distinctive
      // part survives truncation ("هندسة الواجهة" beats "وكيل هندسة ال…").
      return n.replace(/^وكيل\s+/, "").replace(/\s+Agent$/i, "");
    };
  }, [agents, locale]);

  // React to REAL events: ignite the actor's capsule + launch a spark.
  useEffect(() => {
    const newest = events[0];
    if (!newest || newest.sequence === lastSeqRef.current) return;
    lastSeqRef.current = newest.sequence;
    if (!newest.actor) return;
    const sc = sceneRef.current;
    const node = sc.nodes.find((n) => n.key === newest.actor);
    if (node) {
      node.glow = 1;
      sc.pulses.push({ node, t: 0, sp: 0.024 });
    } else {
      // Off-board agent (or system): surge the core directly in its color.
      sc.burst = 1;
      sc.burstColor = hexRgb(agentMeta(newest.actor).color);
    }
  }, [events]);

  // The canvas scene.
  useEffect(() => {
    const stage = stageRef.current;
    const canvas = canvasRef.current;
    if (!stage || !canvas) return;
    const ctx = canvas.getContext("2d");
    if (!ctx) return;

    const reduce = window.matchMedia("(prefers-reduced-motion: reduce)").matches;
    let W = 0, H = 0, CX = 0, CY = 0, R = 0, small = false;
    let stars: { x: number; y: number; a: number; tw: number }[] = [];
    let webPts: { x: number; y: number; z: number }[] = [];
    let webEdges: [number, number][] = [];
    let raf = 0;
    let running = true;

    function size() {
      const r = stage!.getBoundingClientRect();
      const dpr = Math.min(window.devicePixelRatio || 1, 2);
      canvas!.width = Math.round(r.width * dpr);
      canvas!.height = Math.round(r.height * dpr);
      ctx!.setTransform(dpr, 0, 0, dpr, 0, 0);
      W = r.width; H = r.height;
      small = W < 720;
      CX = W / 2; CY = H * 0.44;
      R = Math.min(W, H) * (small ? 0.24 : 0.215);
      build();
    }

    function build() {
      stars = [];
      for (let i = 0; i < 150; i++) {
        stars.push({ x: Math.random() * W, y: Math.random() * H, a: 0.08 + Math.random() * 0.35, tw: Math.random() * 6.28 });
      }
      const N = small ? 56 : 88;
      webPts = [];
      for (let p = 0; p < N; p++) {
        const u = Math.random() * 2 - 1;
        const th = Math.random() * 6.283;
        const rr = Math.sqrt(1 - u * u);
        webPts.push({ x: Math.cos(th) * rr, y: u, z: Math.sin(th) * rr });
      }
      webEdges = [];
      for (let a = 0; a < N; a++) {
        for (let b = a + 1; b < N; b++) {
          const dx = webPts[a].x - webPts[b].x, dy = webPts[a].y - webPts[b].y, dz = webPts[a].z - webPts[b].z;
          if (dx * dx + dy * dy + dz * dz < 0.34) webEdges.push([a, b]);
        }
      }
    }

    function anchor(n: BoardNode) {
      const ax = (n.x / 100) * W;
      const ay = (n.y / 100) * H;
      const ang = Math.atan2(ay - CY, ax - CX);
      return { ax, ay, ox: CX + Math.cos(ang) * R * 0.92, oy: CY + Math.sin(ang) * R * 0.92 };
    }

    function tendril(n: BoardNode, t: number) {
      const { ax, ay, ox, oy } = anchor(n);
      const segs = small ? 10 : 16;
      const dx = ax - ox, dy = ay - oy;
      const len = Math.hypot(dx, dy) || 1;
      const nx = -dy / len, ny = dx / len;
      const hot = n.glow > 0.04;
      for (let p = 0; p < 2; p++) {
        ctx!.beginPath();
        for (let s = 0; s <= segs; s++) {
          const k = s / segs;
          const amp = Math.sin(k * Math.PI) * (hot ? 13 : 7);
          const noise =
            Math.sin(n.seed + t * (hot ? 9 : 2.4) + k * 11) * amp +
            Math.sin(n.seed * 2 + t * (hot ? 6 : 1.6) + k * 23) * amp * 0.4;
          const x = ox + dx * k + nx * noise;
          const y = oy + dy * k + ny * noise;
          if (s === 0) ctx!.moveTo(x, y);
          else ctx!.lineTo(x, y);
        }
        const col = hot ? n.rgb : VIOLET;
        ctx!.strokeStyle = rgba(col, p === 0 ? (hot ? 0.28 + 0.3 * n.glow : 0.10) : hot ? 0.85 : 0.30);
        ctx!.lineWidth = p === 0 ? (hot ? 4.5 : 3) : hot ? 1.6 : 0.9;
        if (p === 1 && hot) { ctx!.shadowColor = rgba(n.rgb, 0.9); ctx!.shadowBlur = 10; }
        ctx!.stroke();
        ctx!.shadowBlur = 0;
      }
      ctx!.beginPath();
      ctx!.arc(ax, ay, hot ? 4 : 2.6, 0, 6.283);
      ctx!.fillStyle = hot ? rgba(n.rgb, 0.95) : rgba(CYAN, 0.5);
      ctx!.fill();
      n.glow *= 0.975;
    }

    const t0 = performance.now();
    function draw(now: number) {
      if (!running) return;
      const t = (now - t0) / 1000;
      const sc = sceneRef.current;
      ctx!.clearRect(0, 0, W, H);

      // background glow + stars
      const bg = ctx!.createRadialGradient(CX, CY, 10, CX, CY, Math.max(W, H) * 0.75);
      bg.addColorStop(0, "rgba(124,92,255,0.10)");
      bg.addColorStop(1, "rgba(2,2,10,0)");
      ctx!.fillStyle = bg;
      ctx!.fillRect(0, 0, W, H);
      for (const st of stars) {
        const tw = reduce ? 1 : 0.6 + 0.4 * Math.sin(t * 1.3 + st.tw);
        ctx!.fillStyle = `rgba(190,200,255,${st.a * tw})`;
        ctx!.fillRect(st.x, st.y, 1.4, 1.4);
      }

      sc.burst *= 0.965;
      const E = sc.burst;
      // resting tint follows the org CoreState; bursts follow the actor.
      const base = lerpC(VIOLET, sc.stateTint, 0.45);
      const CA = sc.burstColor || base;

      // pedestal
      const py = CY + R * 1.55;
      const beam = ctx!.createLinearGradient(0, CY + R * 0.6, 0, py);
      beam.addColorStop(0, "rgba(124,92,255,0.18)");
      beam.addColorStop(1, "rgba(34,211,238,0.02)");
      ctx!.fillStyle = beam;
      ctx!.beginPath();
      ctx!.moveTo(CX - R * 0.28, CY + R * 0.55);
      ctx!.lineTo(CX + R * 0.28, CY + R * 0.55);
      ctx!.lineTo(CX + R * 0.85, py);
      ctx!.lineTo(CX - R * 0.85, py);
      ctx!.closePath();
      ctx!.fill();
      for (let e = 0; e < 3; e++) {
        ctx!.beginPath();
        ctx!.ellipse(CX, py + e * 7, R * (0.7 - e * 0.14), R * (0.16 - e * 0.03), 0, 0, 6.283);
        ctx!.strokeStyle = rgba(CYAN, 0.4 - e * 0.11);
        ctx!.lineWidth = 1.4;
        ctx!.stroke();
      }

      // Hold WORKING/RECEIVED agents visibly alive: keep the tendril hot and
      // stream a steady trickle of sparks into the core for as long as the
      // agent works, so an active link reads as a living connection - not a
      // one-frame flash. Driven entirely by the real event-derived activity.
      sc.frame++;
      const nowMs = Date.now();
      const act = activityRef.current;
      for (const n of sc.nodes) {
        const lp = livePhase(act[n.key], nowMs);
        n.active = lp === "working" || lp === "received";
        if (n.active) {
          const pulse = 0.55 + 0.35 * Math.sin(nowMs / 320 + n.seed);
          if (n.glow < pulse) n.glow = pulse; // floor: never fully fade while active
          // A spark roughly every ~0.6s per working agent (36 frames @ 60fps),
          // phase-staggered by seed so multiple agents don't pulse in lockstep.
          if (lp === "working" && (sc.frame + Math.floor(n.seed)) % 36 === 0) {
            sc.pulses.push({ node: n, t: 0, sp: 0.02 });
          }
        }
      }

      // tendrils + sparks
      for (const n of sc.nodes) tendril(n, t);
      for (let i = sc.pulses.length - 1; i >= 0; i--) {
        const pu = sc.pulses[i];
        pu.t += pu.sp;
        if (pu.t >= 1) {
          sc.burst = 1;
          sc.burstColor = pu.node.rgb;
          sc.pulses.splice(i, 1);
          continue;
        }
        const { ax, ay, ox, oy } = anchor(pu.node);
        const x = ax + (ox - ax) * pu.t;
        const y = ay + (oy - ay) * pu.t;
        ctx!.beginPath();
        ctx!.arc(x, y, 3.4, 0, 6.283);
        ctx!.fillStyle = rgba(pu.node.rgb, 0.95);
        ctx!.shadowColor = rgba(pu.node.rgb, 0.9);
        ctx!.shadowBlur = 12;
        ctx!.fill();
        ctx!.shadowBlur = 0;
      }

      // orb halo + body
      const glowR = R * 1.55 * (1 + E * 0.12);
      const halo = ctx!.createRadialGradient(CX, CY, R * 0.2, CX, CY, glowR);
      halo.addColorStop(0, rgba(CA, 0.5 + E * 0.3));
      halo.addColorStop(0.55, rgba(base, 0.22));
      halo.addColorStop(1, "rgba(0,0,0,0)");
      ctx!.fillStyle = halo;
      ctx!.beginPath();
      ctx!.arc(CX, CY, glowR, 0, 6.283);
      ctx!.fill();

      const body = ctx!.createRadialGradient(CX - R * 0.25, CY - R * 0.3, R * 0.1, CX, CY, R);
      body.addColorStop(0, rgba(CYAN, 0.55));
      body.addColorStop(0.5, rgba(base, 0.42));
      body.addColorStop(1, "rgba(5,4,18,0.9)");
      ctx!.fillStyle = body;
      ctx!.beginPath();
      ctx!.arc(CX, CY, R, 0, 6.283);
      ctx!.fill();

      // neural web
      const rot = reduce ? 0 : t * 0.22;
      const cs = Math.cos(rot), sn = Math.sin(rot);
      const proj: { x: number; y: number; z: number }[] = [];
      for (const P of webPts) {
        const x3 = P.x * cs - P.z * sn, z3 = P.x * sn + P.z * cs;
        proj.push({ x: CX + x3 * R * 0.92, y: CY + P.y * R * 0.92, z: z3 });
      }
      for (const [a, b] of webEdges) {
        const A = proj[a], B = proj[b];
        const depth = (A.z + B.z) / 2;
        ctx!.strokeStyle = rgba(depth > 0.1 ? CYAN : base, 0.05 + (depth + 1) * 0.16 + E * 0.12);
        ctx!.lineWidth = 0.7;
        ctx!.beginPath();
        ctx!.moveTo(A.x, A.y);
        ctx!.lineTo(B.x, B.y);
        ctx!.stroke();
      }
      for (const Q of proj) {
        ctx!.beginPath();
        ctx!.arc(Q.x, Q.y, Q.z > 0 ? 1.8 : 1.1, 0, 6.283);
        ctx!.fillStyle = rgba(Q.z > 0 ? CYAN : base, 0.35 + (Q.z + 1) * 0.25);
        ctx!.fill();
      }

      // rim
      ctx!.strokeStyle = rgba(CYAN, 0.5 + E * 0.4);
      ctx!.lineWidth = 1.6;
      ctx!.shadowColor = rgba(CA, 0.8);
      ctx!.shadowBlur = 18 + E * 22;
      ctx!.beginPath();
      ctx!.arc(CX, CY, R, 0, 6.283);
      ctx!.stroke();
      ctx!.shadowBlur = 0;

      raf = requestAnimationFrame(draw);
    }

    size();
    const ro = new ResizeObserver(size);
    ro.observe(stage);
    const onVis = () => {
      if (document.hidden) {
        running = false;
        cancelAnimationFrame(raf);
      } else if (!running) {
        running = true;
        raf = requestAnimationFrame(draw);
      }
    };
    document.addEventListener("visibilitychange", onVis);
    raf = requestAnimationFrame(draw);

    return () => {
      running = false;
      cancelAnimationFrame(raf);
      ro.disconnect();
      document.removeEventListener("visibilitychange", onVis);
    };
  }, []);

  // Render-scope clock (re-read every 1s via the ticker) so capsules fade
  // their live phase in step with the canvas.
  const nowMs = Date.now();

  return (
    <div
      ref={stageRef}
      className="relative h-[clamp(420px,58vh,660px)] overflow-hidden rounded-2xl border border-white/10"
      style={{ background: "linear-gradient(180deg,#050716,#03040c)" }}
    >
      <canvas ref={canvasRef} className="absolute inset-0 h-full w-full" aria-hidden />

      {/* core title */}
      <div className="pointer-events-none absolute left-1/2 top-[44%] z-[2] -translate-x-1/2 -translate-y-1/2 text-center" dir="ltr">
        <div
          className="text-[clamp(26px,3.6vw,44px)] font-extrabold tracking-wide text-white"
          style={{ textShadow: "0 0 24px rgba(139,92,246,.9), 0 0 70px rgba(79,125,255,.55)" }}
        >
          RABIT
        </div>
        <div
          className="mt-0.5 text-[clamp(13px,1.6vw,20px)] font-bold tracking-[0.3em] text-indigo-100"
          style={{ textShadow: "0 0 18px rgba(79,125,255,.8)" }}
        >
          AI CORE
        </div>
        <div className="mt-2 text-[clamp(7px,0.8vw,10px)] font-semibold tracking-[0.34em] text-slate-400">
          {locale === "ar" ? "مركز القيادة العصبي" : "NEURAL COMMAND CENTER"}
        </div>
      </div>

      {/* agent capsules - each reflects its LIVE phase (received/working/
          done/blocked) so you can see a command land on an agent and the
          agent stay lit while it works. */}
      {nodes.map((n) => {
        const meta = agentMeta(n.key);
        const phase = livePhase(agentActivity[n.key], nowMs);
        const active = phase === "working" || phase === "received";
        const ring = phase ? PHASE_COLOR[phase] : `${meta.color}55`;
        const phaseLabel = phase ? t(`command_center.phase_${phase}`) : null;
        return (
          <div
            key={n.key}
            className="neural-capsule absolute z-[3] flex -translate-x-1/2 -translate-y-1/2 flex-col items-center gap-1"
            style={{ left: `${n.x}%`, top: `${n.y}%` }}
            title={nameFor(n.key)}
            dir={locale === "ar" ? "rtl" : "ltr"}
          >
            <div
              className="flex items-center gap-2 whitespace-nowrap rounded-full border px-2.5 py-1.5"
              style={{
                borderColor: ring,
                background: "rgba(6,8,20,0.82)",
                boxShadow: phase ? `0 0 14px ${ring}${active ? "aa" : "66"}` : undefined,
              }}
            >
              <span
                className={`relative grid h-8 w-8 flex-none place-items-center rounded-full border text-[10px] font-bold max-[860px]:h-7 max-[860px]:w-7 ${
                  active ? "capsule-pulse" : ""
                }`}
                style={{
                  borderColor: meta.color,
                  color: meta.color,
                  background: `${meta.color}22`,
                  // CSS var drives the pulse ring color for active agents.
                  ["--ring" as string]: ring,
                }}
              >
                {meta.glyph}
              </span>
              <span className="max-[860px]:hidden">
                <span className="block max-w-[16ch] truncate text-[11px] font-bold uppercase tracking-wide text-slate-100">
                  {nameFor(n.key)}
                </span>
                {phaseLabel ? (
                  <span
                    className="block max-w-[16ch] truncate text-[9.5px] font-semibold"
                    style={{ color: ring }}
                  >
                    {active ? "● " : ""}
                    {phaseLabel}
                  </span>
                ) : null}
              </span>
            </div>
            {/* phone view: no name, so show a tiny status dot under the glyph */}
            {phaseLabel ? (
              <span
                className="hidden max-w-[12ch] truncate rounded-full px-1.5 text-[8px] font-bold max-[860px]:inline-block"
                style={{ color: ring, background: `${ring}22` }}
              >
                {phaseLabel}
              </span>
            ) : null}
          </div>
        );
      })}

      <style jsx>{`
        .capsule-pulse::after {
          content: "";
          position: absolute;
          inset: -3px;
          border-radius: 9999px;
          border: 2px solid var(--ring);
          animation: capsuleRing 1.4s ease-out infinite;
        }
        @keyframes capsuleRing {
          0% { transform: scale(1); opacity: 0.9; }
          100% { transform: scale(1.6); opacity: 0; }
        }
        @media (prefers-reduced-motion: reduce) {
          .capsule-pulse::after { animation: none; opacity: 0.5; }
        }
      `}</style>
    </div>
  );
}
