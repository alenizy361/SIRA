// Small typed fetch wrapper around the real Rabit AI Company OS FastAPI backend.
// Always sends credentials so the httpOnly `rabit_session` cookie flows.

export const API_URL =
  process.env.NEXT_PUBLIC_API_URL?.replace(/\/$/, "") || "http://localhost:8000";

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
  actor_id: string;
  action: string;
  entity_type: string;
  entity_id: string;
  result: string;
  explanation: string;
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

export const healthApi = {
  dependencies: () => api.get<HealthDependencies>("/health/dependencies"),
  live: () => api.get<{ status: string }>("/health/live"),
};

export const systemApi = {
  emergencyStop: () => api.post<{ status: string }>("/system/emergency-stop"),
};
