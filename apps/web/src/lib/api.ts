// Small typed fetch wrapper around the real Rabit AI Company OS FastAPI backend.
// Always sends credentials so the httpOnly `rabit_session` cookie flows.

// Relative by default: nginx (infra/nginx/rabit-os.conf.template) proxies
// same-origin "/api/*" to the FastAPI container and "/ws" to its WebSocket
// route, which is what every real deployment (a VPS behind Nginx, reached
// from an external browser) actually needs - "http://localhost:8000" only
// ever works when the browser and the server are the same machine, which
// is true in local sandbox testing but never true for a real user. Local
// dev without Nginx in front (`npm run dev` hitting the API directly) can
// still override this with NEXT_PUBLIC_API_URL=http://localhost:8000.
export const API_URL = process.env.NEXT_PUBLIC_API_URL?.replace(/\/$/, "") || "/api";

export class ApiError extends Error {
  status: number;
  detail: unknown;
  /** True when the endpoint simply doesn't exist in this build yet (404/405). */
  notImplemented: boolean;

  constructor(status: number, detail: unknown, message?: string) {
    super(message || `API error ${status}`);
    this.status = status;
    this.detail = detail;
    this.notImplemented = status === 404 || status === 405;
  }
}

async function request<T>(
  path: string,
  init?: RequestInit
): Promise<T> {
  const res = await fetch(`${API_URL}${path}`, {
    ...init,
    credentials: "include",
    headers: {
      "Content-Type": "application/json",
      ...(init?.headers || {}),
    },
  });

  if (!res.ok) {
    let detail: unknown = undefined;
    try {
      detail = await res.json();
    } catch {
      // no JSON body
    }
    const message =
      (detail && typeof detail === "object" && "detail" in detail
        ? String((detail as { detail: unknown }).detail)
        : undefined) || `${res.status} ${res.statusText}`;
    throw new ApiError(res.status, detail, message);
  }

  if (res.status === 204) return undefined as T;
  const text = await res.text();
  if (!text) return undefined as T;
  return JSON.parse(text) as T;
}

export const api = {
  get: <T>(path: string) => request<T>(path, { method: "GET" }),
  post: <T>(path: string, body?: unknown) =>
    request<T>(path, {
      method: "POST",
      body: body !== undefined ? JSON.stringify(body) : undefined,
    }),
};

// ---- Domain types (mirrors apps/api/app/routers/*.py response shapes) ----

export interface Me {
  id: string;
  organization_id: string;
  email: string;
  display_name: string;
  totp_enabled: boolean;
}

export interface Agent {
  agent_key: string;
  display_name_en: string;
  display_name_ar: string;
  mission: string;
  risk_ceiling: string;
  enabled: boolean;
  disabled_reason: string | null;
  current_state: string;
}

export interface Goal {
  id: string;
  title: string;
  description: string;
  state: string;
  source: string;
  priority: number;
  created_at: string;
}

export interface Approval {
  id: string;
  requested_by_agent_key: string;
  action: string;
  risk_level: string;
  reason: string;
  expected_benefit: string;
  cost_sar: number | null;
  reversible: boolean;
  evidence: unknown;
  state: string;
  deadline_at: string | null;
  created_at: string;
}

export interface AuditLogEntry {
  id: string;
  actor_type: string;
  // Nullable in the DB (e.g. the emergency-stop audit row omits actor/entity),
  // so the type must admit null - the UI guards against it.
  actor_id: string | null;
  action: string;
  entity_type: string | null;
  entity_id: string | null;
  result: string;
  explanation: string | null;
  created_at: string;
}

export interface DependencyCheck {
  status: "ok" | "fail";
  detail?: string;
}

export interface HealthDependencies {
  postgres: DependencyCheck;
  redis: DependencyCheck;
  [key: string]: DependencyCheck;
}

// ---- API functions ----

export const authApi = {
  me: () => api.get<Me>("/auth/me"),
  login: (email: string, password: string) =>
    api.post<{ user_id: string }>("/auth/login", { email, password }),
  onboard: (payload: {
    organization_name: string;
    admin_email: string;
    admin_password: string;
    admin_display_name: string;
  }) =>
    api.post<{ organization_id: string; user_id: string }>(
      "/auth/onboard",
      payload
    ),
  logout: () => api.post<{ status: string }>("/auth/logout"),
};

export const agentsApi = {
  list: () => api.get<Agent[]>("/agents"),
};

export const goalsApi = {
  list: () => api.get<Goal[]>("/goals"),
  get: (id: string) => api.get<Goal>(`/goals/${id}`),
  create: (payload: { title: string; description: string; source?: string }) =>
    api.post<Goal>("/goals", payload),
  transition: (id: string, target_state: string) =>
    api.post<Goal>(`/goals/${id}/transition`, { target_state }),
  // Enqueues CEO-agent planning on the host worker (returns 202). The plan +
  // tasks appear asynchronously via the goal state and the event stream.
  requestPlan: (id: string) =>
    api.post<{ plan_status: string }>(`/goals/${id}/plan`, {}),
};

export const approvalsApi = {
  list: () => api.get<Approval[]>("/approvals"),
  resolve: (
    id: string,
    payload: {
      decision: "approve" | "approve_with_conditions" | "reject" | "request_revision";
      conditions?: Record<string, unknown>;
      comment?: string;
    }
  ) => api.post<Approval>(`/approvals/${id}/resolve`, payload),
};

export const auditApi = {
  list: (limit = 100) => api.get<AuditLogEntry[]>(`/audit-logs?limit=${limit}`),
};

export interface WorkerStatus {
  alive: boolean;
  authed: boolean;
  seconds_since_heartbeat?: number | null;
  reason?: string;
  hint?: string;
}

export const healthApi = {
  dependencies: () => api.get<HealthDependencies>("/health/dependencies"),
  live: () => api.get<{ status: string }>("/health/live"),
  worker: () => api.get<WorkerStatus>("/health/worker"),
};

export const systemApi = {
  emergencyStop: () => api.post<{ status: string }>("/system/emergency-stop"),
};

// ---- Endpoints not yet implemented on the backend (see docs/BUILD_STATUS.md).
// These call sensibly-named REST paths; callers treat 404/405 as "not
// available in this build yet" via ApiError.notImplemented, never as empty
// real data. ----

export const plansApi = {
  list: () => api.get<unknown[]>("/plans"),
};

export const tasksApi = {
  list: () => api.get<unknown[]>("/tasks"),
};

export const runsApi = {
  list: () => api.get<unknown[]>("/runs"),
};

export const memoryApi = {
  list: () => api.get<unknown[]>("/memory"),
};

export const budgetsApi = {
  list: () => api.get<unknown[]>("/budgets"),
};

export const analyticsApi = {
  summary: () => api.get<unknown>("/analytics"),
};

export const incidentsApi = {
  list: () => api.get<unknown[]>("/incidents"),
};
