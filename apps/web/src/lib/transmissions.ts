import type { WireEvent } from "@/store/eventStore";

// Turns a raw realtime event into a human-readable "transmission" - the line
// the command center shows in its live agent-comms feed. Bilingual, with a
// tone that drives its color. This is the layer that makes the event stream
// read like agents talking to each other rather than a log of enum names.

export type Tone = "info" | "active" | "success" | "warning" | "critical";

export interface Transmission {
  tone: Tone;
  verbEn: string;
  verbAr: string;
  detail: string; // safe, already-sanitized payload snippet (may be empty)
}

function str(v: unknown): string {
  return typeof v === "string" ? v : v == null ? "" : String(v);
}

export function toTransmission(e: WireEvent): Transmission {
  const p = e.payload || {};
  switch (e.type) {
    case "goal.created":
      return {
        tone: p.plan_status === "requested" ? "active" : "info",
        verbEn: p.plan_status === "requested" ? "was asked to plan" : "captured a goal",
        verbAr: p.plan_status === "requested" ? "طُلب منه التخطيط" : "التقط هدفاً",
        detail: str(p.title),
      };
    case "plan.created":
      return { tone: "success", verbEn: "drafted a plan", verbAr: "صاغ خطة", detail: str(p.title) };
    case "task.created":
      return {
        tone: "info",
        verbEn: "created a task",
        verbAr: "أنشأ مهمة",
        detail: `${str(p.title)}${p.risk_level ? ` · ${str(p.risk_level)}` : ""}`,
      };
    case "task.assigned":
      return { tone: "info", verbEn: "was assigned", verbAr: "استُلمت مهمته", detail: str(p.title) };
    case "task.started":
      return { tone: "active", verbEn: "started working", verbAr: "بدأ التنفيذ", detail: str(p.title) };
    case "task.progress":
      return { tone: "active", verbEn: "made progress", verbAr: "أحرز تقدماً", detail: str(p.stage) };
    case "task.blocked":
      return { tone: "warning", verbEn: "is blocked", verbAr: "توقف", detail: str(p.reason) };
    case "task.completed":
      return { tone: "success", verbEn: "completed a task", verbAr: "أكمل مهمة", detail: str(p.title) };
    case "run.tool.started":
      return {
        tone: "active",
        verbEn: `is using ${str(p.tool_name) || "a tool"}`,
        verbAr: `يستخدم ${str(p.tool_name) || "أداة"}`,
        detail: "",
      };
    case "run.tool.completed":
      return {
        tone: p.is_error ? "warning" : "info",
        verbEn: p.is_error ? "hit a tool error" : "finished a tool call",
        verbAr: p.is_error ? "واجه خطأ أداة" : "أنهى استدعاء أداة",
        detail: str(p.content_preview),
      };
    case "run.output.delta":
      return { tone: "active", verbEn: "reported", verbAr: "أفاد", detail: str(p.text) };
    case "review.requested":
      return { tone: "info", verbEn: "requested a review", verbAr: "طلب مراجعة", detail: "" };
    case "review.completed":
      return { tone: "success", verbEn: "completed a review", verbAr: "أنجز مراجعة", detail: str(p.decision) };
    case "approval.requested":
      return { tone: "warning", verbEn: "requested approval", verbAr: "طلب موافقة", detail: str(p.action) };
    case "approval.resolved":
      return { tone: "success", verbEn: "resolved an approval", verbAr: "بتّ موافقة", detail: str(p.decision) };
    case "incident.opened":
      return { tone: "critical", verbEn: "opened an incident", verbAr: "فتح حادثة", detail: str(p.title) };
    case "incident.resolved":
      return { tone: "success", verbEn: "resolved an incident", verbAr: "أغلق حادثة", detail: str(p.title) };
    case "budget.threshold.reached":
      return { tone: "warning", verbEn: "hit a budget threshold", verbAr: "بلغ حد الميزانية", detail: "" };
    case "core.state.changed":
      return { tone: "info", verbEn: `shifted to ${str(p.state)}`, verbAr: `تحوّل إلى ${str(p.state)}`, detail: "" };
    case "agent.status.changed":
      return { tone: "info", verbEn: "changed status", verbAr: "غيّر حالته", detail: str(p.state) };
    case "integration.status.changed":
      return { tone: "info", verbEn: "integration changed", verbAr: "تغيّر تكامل", detail: str(p.provider) };
    default:
      return { tone: "info", verbEn: e.type, verbAr: e.type, detail: "" };
  }
}

export const TONE_COLOR: Record<Tone, string> = {
  info: "#38bdf8",
  active: "#22d3ee",
  success: "#34d399",
  warning: "#fbbf24",
  critical: "#fb7185",
};
