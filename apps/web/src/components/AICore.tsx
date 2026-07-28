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
import type { Mesh, Points } from "three";
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
  idle: { color: "#4fd6c9", secondary: "#123b38", speed: 0.15, pulse: 0.3 },
  listening: { color: "#4ade80", secondary: "#0f3d24", speed: 0.6, pulse: 1.1 },
  understanding: { color: "#38bdf8", secondary: "#0b3049", speed: 0.5, pulse: 0.6 },
  planning: { color: "#a78bfa", secondary: "#2c2154", speed: 0.35, pulse: 0.4 },
  delegating: { color: "#818cf8", secondary: "#232152", speed: 0.5, pulse: 0.5 },
  coding: { color: "#22d3ee", secondary: "#0a3a44", speed: 1.1, pulse: 0.7 },
  testing: { color: "#facc15", secondary: "#4a3c07", speed: 0.7, pulse: 0.6 },
  reviewing: { color: "#fb923c", secondary: "#4a2607", speed: 0.4, pulse: 0.5 },
  waiting_for_approval: { color: "#fbbf24", secondary: "#4a3907", speed: 0.15, pulse: 1.4 },
  deploying: { color: "#f472b6", secondary: "#4a1236", speed: 1.4, pulse: 0.9 },
  monitoring: { color: "#38bdf8", secondary: "#0b2c49", speed: 0.25, pulse: 0.3 },
  learning: { color: "#c084fc", secondary: "#341b4a", speed: 0.3, pulse: 0.35 },
  speaking: { color: "#34d399", secondary: "#0d3d2a", speed: 0.8, pulse: 1.2 },
  warning: { color: "#f97316", secondary: "#4a1f07", speed: 0.9, pulse: 1.6 },
  incident: { color: "#ef4444", secondary: "#4a0d0d", speed: 1.6, pulse: 2.0 },
  paused: { color: "#94a3b8", secondary: "#1e293b", speed: 0.03, pulse: 0.15 },
};

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

function CoreScene({
  config,
  frozen,
}: {
  config: StateConfig;
  frozen: boolean;
}) {
  const coreRef = useRef<Mesh>(null);
  const particlesRef = useRef<Points>(null);
  const clockRef = useRef(0);

  const positions = useMemo(() => {
    const count = 700;
    const arr = new Float32Array(count * 3);
    for (let i = 0; i < count; i++) {
      const r = 1.55 + Math.random() * 0.55;
      const theta = Math.random() * Math.PI * 2;
      const phi = Math.acos(2 * Math.random() - 1);
      arr[i * 3] = r * Math.sin(phi) * Math.cos(theta);
      arr[i * 3 + 1] = r * Math.sin(phi) * Math.sin(theta);
      arr[i * 3 + 2] = r * Math.cos(phi);
    }
    return arr;
  }, []);

  useFrame((_state, delta) => {
    const dt = frozen ? 0 : Math.min(delta, 0.05);
    clockRef.current += dt;
    const t = clockRef.current;
    if (coreRef.current) {
      coreRef.current.rotation.y += dt * config.speed;
      coreRef.current.rotation.x += dt * config.speed * 0.35;
      const pulse = frozen ? 1 : 1 + Math.sin(t * config.pulse * 2) * 0.07;
      coreRef.current.scale.setScalar(pulse);
    }
    if (particlesRef.current) {
      particlesRef.current.rotation.y -= dt * config.speed * 0.25;
      particlesRef.current.rotation.x += dt * config.speed * 0.1;
    }
  });

  return (
    <>
      <ambientLight intensity={0.4} />
      <pointLight color={config.color} position={[2, 2, 2]} intensity={1.4} distance={10} />
      <pointLight color={config.color} position={[0, 0, 0]} intensity={2.4} distance={6} />
      <mesh ref={coreRef}>
        <icosahedronGeometry args={[1, 3]} />
        <meshStandardMaterial
          color={config.color}
          emissive={config.color}
          emissiveIntensity={0.85}
          roughness={0.3}
          metalness={0.35}
        />
      </mesh>
      <points ref={particlesRef}>
        <bufferGeometry>
          <bufferAttribute attach="attributes-position" args={[positions, 3]} />
        </bufferGeometry>
        <pointsMaterial
          color={config.secondary}
          size={0.032}
          sizeAttenuation
          transparent
          opacity={0.9}
        />
      </points>
    </>
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
          width: min(55%, 220px);
          aspect-ratio: 1 / 1;
          border-radius: 9999px;
          background: radial-gradient(
            circle at 35% 30%,
            color-mix(in srgb, var(--core-color) 90%, white),
            var(--core-color) 45%,
            var(--core-secondary) 100%
          );
          box-shadow: 0 0 60px 10px color-mix(in srgb, var(--core-color) 55%, transparent);
          animation: core-pulse 2.6s ease-in-out infinite;
        }
        @keyframes core-pulse {
          0%,
          100% {
            transform: scale(1);
            filter: brightness(1);
          }
          50% {
            transform: scale(1.06);
            filter: brightness(1.15);
          }
        }
        @media (prefers-reduced-motion: reduce) {
          .ai-core-fallback-orb {
            animation: none;
          }
        }
      `}</style>
    </div>
  );
}

export function AICore({ state }: { state: CoreState }) {
  const [webglSupported, setWebglSupported] = useState<boolean | null>(null);
  const reducedMotion = usePrefersReducedMotion();
  const visible = usePageVisible();
  const config = STATE_CONFIG[state] ?? STATE_CONFIG.idle;

  useEffect(() => {
    setWebglSupported(detectWebGL());
  }, []);

  if (webglSupported === null) {
    return <div className="h-full w-full" />;
  }

  if (!webglSupported) {
    return <CssOrbFallback config={config} />;
  }

  return (
    <WebGLBoundary fallback={<CssOrbFallback config={config} />}>
      <Canvas
        camera={{ position: [0, 0, 4.2], fov: 45 }}
        dpr={[1, 1.75]}
        frameloop={visible ? "always" : "never"}
        gl={{ antialias: true, alpha: true }}
      >
        <CoreScene config={config} frozen={reducedMotion} />
      </Canvas>
    </WebGLBoundary>
  );
}
