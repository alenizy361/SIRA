"use client";

import {
  Component,
  ReactNode,
  useEffect,
  useMemo,
  useRef,
  useState,
} from "react";
import { Canvas, useFrame } from "@react-three/fiber";
import {
  AdditiveBlending,
  BackSide,
  Color,
  type Group,
  type Mesh,
  type Points,
} from "three";
import type { CoreState } from "@/store/eventStore";

interface StateConfig {
  color: string;
  secondary: string;
  speed: number;
  pulse: number;
}

const STATE_CONFIG: Record<CoreState, StateConfig> = {
  offline: { color: "#3f4657", secondary: "#20242e", speed: 0.02, pulse: 0.1 },
  connecting: { color: "#5b7bff", secondary: "#233156", speed: 0.4, pulse: 0.8 },
  idle: { color: "#4fd6c9", secondary: "#123b38", speed: 0.16, pulse: 0.32 },
  listening: { color: "#4ade80", secondary: "#0f3d24", speed: 0.6, pulse: 1.1 },
  understanding: { color: "#38bdf8", secondary: "#0b3049", speed: 0.5, pulse: 0.6 },
  planning: { color: "#a78bfa", secondary: "#2c2154", speed: 0.42, pulse: 0.5 },
  delegating: { color: "#818cf8", secondary: "#232152", speed: 0.55, pulse: 0.55 },
  coding: { color: "#22d3ee", secondary: "#0a3a44", speed: 1.15, pulse: 0.75 },
  testing: { color: "#facc15", secondary: "#4a3c07", speed: 0.7, pulse: 0.6 },
  reviewing: { color: "#fb923c", secondary: "#4a2607", speed: 0.45, pulse: 0.55 },
  waiting_for_approval: { color: "#fbbf24", secondary: "#4a3907", speed: 0.15, pulse: 1.4 },
  deploying: { color: "#f472b6", secondary: "#4a1236", speed: 1.4, pulse: 0.9 },
  monitoring: { color: "#38bdf8", secondary: "#0b2c49", speed: 0.28, pulse: 0.34 },
  learning: { color: "#c084fc", secondary: "#341b4a", speed: 0.32, pulse: 0.4 },
  speaking: { color: "#34d399", secondary: "#0d3d2a", speed: 0.85, pulse: 1.2 },
  warning: { color: "#f97316", secondary: "#4a1f07", speed: 0.95, pulse: 1.7 },
  incident: { color: "#ef4444", secondary: "#4a0d0d", speed: 1.7, pulse: 2.1 },
  paused: { color: "#94a3b8", secondary: "#1e293b", speed: 0.03, pulse: 0.15 },
};

export function coreStateColor(state: CoreState): string {
  return (STATE_CONFIG[state] ?? STATE_CONFIG.idle).color;
}

function detectWebGL(): boolean {
  if (typeof window === "undefined") return false;
  try {
    const canvas = document.createElement("canvas");
    const gl =
      canvas.getContext("webgl2") ||
      canvas.getContext("webgl") ||
      canvas.getContext("experimental-webgl");
    return !!gl;
  } catch {
    return false;
  }
}

function usePrefersReducedMotion(): boolean {
  const [reduced, setReduced] = useState(false);
  useEffect(() => {
    const mql = window.matchMedia("(prefers-reduced-motion: reduce)");
    setReduced(mql.matches);
    const handler = (e: MediaQueryListEvent) => setReduced(e.matches);
    mql.addEventListener("change", handler);
    return () => mql.removeEventListener("change", handler);
  }, []);
  return reduced;
}

function usePageVisible(): boolean {
  const [visible, setVisible] = useState(true);
  useEffect(() => {
    setVisible(!document.hidden);
    const handler = () => setVisible(!document.hidden);
    document.addEventListener("visibilitychange", handler);
    return () => document.removeEventListener("visibilitychange", handler);
  }, []);
  return visible;
}

// Fibonacci-sphere shell so particles are evenly spread, not clumped.
function sphereShell(count: number, rMin: number, rMax: number): Float32Array {
  const arr = new Float32Array(count * 3);
  const golden = Math.PI * (3 - Math.sqrt(5));
  for (let i = 0; i < count; i++) {
    const y = 1 - (i / (count - 1)) * 2;
    const radius = Math.sqrt(1 - y * y);
    const theta = golden * i;
    const r = rMin + Math.random() * (rMax - rMin);
    arr[i * 3] = Math.cos(theta) * radius * r;
    arr[i * 3 + 1] = y * r;
    arr[i * 3 + 2] = Math.sin(theta) * radius * r;
  }
  return arr;
}

function CoreScene({
  config,
  frozen,
  energy,
  lowPower,
}: {
  config: StateConfig;
  frozen: boolean;
  energy: number;
  lowPower: boolean;
}) {
  const groupRef = useRef<Group>(null);
  const coreRef = useRef<Mesh>(null);
  const wireRef = useRef<Mesh>(null);
  const haloRef = useRef<Mesh>(null);
  const ringRef = useRef<Mesh>(null);
  const nearRef = useRef<Points>(null);
  const farRef = useRef<Points>(null);
  const clockRef = useRef(0);
  const energyRef = useRef(0);

  const nearCount = lowPower ? 320 : 900;
  const farCount = lowPower ? 260 : 700;
  const near = useMemo(() => sphereShell(nearCount, 1.5, 1.95), [nearCount]);
  const far = useMemo(() => sphereShell(farCount, 2.2, 3.1), [farCount]);
  const color = useMemo(() => new Color(config.color), [config.color]);
  const secondary = useMemo(() => new Color(config.secondary), [config.secondary]);

  useFrame((_state, delta) => {
    const dt = frozen ? 0 : Math.min(delta, 0.05);
    clockRef.current += dt;
    const t = clockRef.current;
    // Smoothly chase the target energy so bursts of activity ramp in/out.
    energyRef.current += (energy - energyRef.current) * Math.min(1, dt * 3);
    const e = energyRef.current;
    const spin = config.speed * (1 + e * 0.8);

    if (groupRef.current) groupRef.current.rotation.y += dt * spin * 0.25;
    if (coreRef.current) {
      coreRef.current.rotation.y += dt * spin;
      coreRef.current.rotation.x += dt * spin * 0.35;
      const pulse = frozen ? 1 : 1 + Math.sin(t * config.pulse * 2) * (0.06 + e * 0.05);
      coreRef.current.scale.setScalar(pulse);
    }
    if (wireRef.current) {
      wireRef.current.rotation.y -= dt * spin * 0.7;
      wireRef.current.rotation.z += dt * spin * 0.2;
    }
    if (haloRef.current) {
      const mat = haloRef.current.material as { opacity: number };
      mat.opacity = 0.09 + Math.sin(t * config.pulse * 2) * 0.03 + e * 0.1;
      const s = 1 + e * 0.08;
      haloRef.current.scale.setScalar(s);
    }
    if (ringRef.current) {
      ringRef.current.rotation.z += dt * spin * 0.9;
      ringRef.current.rotation.x = 1.15 + Math.sin(t * 0.3) * 0.08;
    }
    if (nearRef.current) {
      nearRef.current.rotation.y -= dt * spin * 0.3;
      nearRef.current.rotation.x += dt * spin * 0.12;
    }
    if (farRef.current) {
      farRef.current.rotation.y += dt * spin * 0.12;
    }
  });

  return (
    <group ref={groupRef}>
      <ambientLight intensity={0.35} />
      <pointLight color={config.color} position={[0, 0, 0]} intensity={1.6} distance={7} />
      <pointLight color="#ffffff" position={[2.5, 2, 2.5]} intensity={0.7} distance={12} />

      {/* volumetric glow halo (back-side additive sphere) */}
      <mesh ref={haloRef}>
        <sphereGeometry args={[1.9, 32, 32]} />
        <meshBasicMaterial
          color={color}
          transparent
          opacity={0.1}
          blending={AdditiveBlending}
          side={BackSide}
          depthWrite={false}
        />
      </mesh>

      {/* luminous inner core - kept below white-out so its facets, the
          wireframe shell and the ring all stay legible as real 3D depth */}
      <mesh ref={coreRef}>
        <icosahedronGeometry args={[1, 4]} />
        <meshStandardMaterial
          color={color}
          emissive={color}
          emissiveIntensity={0.6}
          roughness={0.35}
          metalness={0.45}
        />
      </mesh>

      {/* rotating wireframe shell */}
      <mesh ref={wireRef}>
        <icosahedronGeometry args={[1.28, 1]} />
        <meshBasicMaterial color={color} wireframe transparent opacity={0.28} blending={AdditiveBlending} depthWrite={false} />
      </mesh>

      {/* energy ring */}
      <mesh ref={ringRef} rotation={[1.15, 0, 0]}>
        <torusGeometry args={[1.75, 0.02, 12, 120]} />
        <meshBasicMaterial color={color} transparent opacity={0.55} blending={AdditiveBlending} depthWrite={false} />
      </mesh>

      {/* near particle shell */}
      <points ref={nearRef}>
        <bufferGeometry>
          <bufferAttribute attach="attributes-position" args={[near, 3]} />
        </bufferGeometry>
        <pointsMaterial
          color={color}
          size={0.03}
          sizeAttenuation
          transparent
          opacity={0.85}
          blending={AdditiveBlending}
          depthWrite={false}
        />
      </points>

      {/* far dust */}
      <points ref={farRef}>
        <bufferGeometry>
          <bufferAttribute attach="attributes-position" args={[far, 3]} />
        </bufferGeometry>
        <pointsMaterial
          color={secondary}
          size={0.022}
          sizeAttenuation
          transparent
          opacity={0.55}
          blending={AdditiveBlending}
          depthWrite={false}
        />
      </points>
    </group>
  );
}

class WebGLBoundary extends Component<
  { children: ReactNode; fallback: ReactNode },
  { hasError: boolean }
> {
  constructor(props: { children: ReactNode; fallback: ReactNode }) {
    super(props);
    this.state = { hasError: false };
  }
  static getDerivedStateFromError() {
    return { hasError: true };
  }
  componentDidCatch() {
    // Swallow: falling back to the CSS orb is the intended behavior.
  }
  render() {
    if (this.state.hasError) return this.props.fallback;
    return this.props.children;
  }
}

function CssOrbFallback({ config }: { config: StateConfig }) {
  return (
    <div
      className="ai-core-fallback flex h-full w-full items-center justify-center"
      role="img"
      aria-label="AI core visual (WebGL unavailable, showing simplified indicator)"
    >
      <div
        className="ai-core-fallback-orb"
        style={
          {
            "--core-color": config.color,
            "--core-secondary": config.secondary,
          } as React.CSSProperties
        }
      />
      <style jsx>{`
        .ai-core-fallback-orb {
          width: min(55%, 240px);
          aspect-ratio: 1 / 1;
          border-radius: 9999px;
          background: radial-gradient(
            circle at 35% 30%,
            color-mix(in srgb, var(--core-color) 92%, white),
            var(--core-color) 45%,
            var(--core-secondary) 100%
          );
          box-shadow: 0 0 80px 14px color-mix(in srgb, var(--core-color) 55%, transparent);
          animation: core-pulse 2.6s ease-in-out infinite;
        }
        @keyframes core-pulse {
          0%,
          100% { transform: scale(1); filter: brightness(1); }
          50% { transform: scale(1.06); filter: brightness(1.18); }
        }
        @media (prefers-reduced-motion: reduce) {
          .ai-core-fallback-orb { animation: none; }
        }
      `}</style>
    </div>
  );
}

/**
 * The central AI core. A layered, bloom-lit WebGL scene (glow halo, luminous
 * core, wireframe shell, energy ring, two counter-rotating particle shells)
 * that morphs color/speed by CoreState and intensifies with `energy` (0..1),
 * which the command center feeds from live event activity - so the core
 * visibly surges while agents are talking. Respects reduced-motion, pauses
 * when the tab is hidden, drops particle counts on low-power devices, and
 * falls back to a CSS orb when WebGL is unavailable.
 */
export function AICore({ state, energy = 0 }: { state: CoreState; energy?: number }) {
  const [webglSupported, setWebglSupported] = useState<boolean | null>(null);
  const [lowPower, setLowPower] = useState(false);
  const reducedMotion = usePrefersReducedMotion();
  const visible = usePageVisible();
  const config = STATE_CONFIG[state] ?? STATE_CONFIG.idle;

  useEffect(() => {
    setWebglSupported(detectWebGL());
    const cores = typeof navigator !== "undefined" ? navigator.hardwareConcurrency || 8 : 8;
    const small = typeof window !== "undefined" && window.innerWidth < 640;
    setLowPower(cores <= 4 || small);
  }, []);

  if (webglSupported === null) return <div className="h-full w-full" />;
  if (!webglSupported) return <CssOrbFallback config={config} />;

  return (
    <WebGLBoundary fallback={<CssOrbFallback config={config} />}>
      <Canvas
        camera={{ position: [0, 0, 4.4], fov: 45 }}
        dpr={[1, lowPower ? 1.5 : 2]}
        frameloop={visible ? "always" : "never"}
        gl={{ antialias: true, alpha: true, powerPreference: "high-performance" }}
      >
        <CoreScene config={config} frozen={reducedMotion} energy={Math.max(0, Math.min(1, energy))} lowPower={lowPower} />
      </Canvas>
    </WebGLBoundary>
  );
}
