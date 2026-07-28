// Stable identity metadata for the 23 agents: department grouping, a fixed
// accent hue (categorical identity encoding - assigned by department, never
// cycled per render), and a short glyph for the orbital node. Colors follow
// the entity, never its position, so the orbit never repaints when the set
// of "active" agents changes.

export type Department =
  | "leadership"
  | "product"
  | "engineering"
  | "growth"
  | "customer"
  | "governance"
  | "operations";

export interface AgentMeta {
  department: Department;
  color: string; // accent hue (identity)
  glyph: string; // short label drawn in the node
}

// One accent per department. Distinct hues, high chroma on the deep-space
// surface; verified to read apart in both light and dark contexts.
export const DEPARTMENT_COLOR: Record<Department, string> = {
  leadership: "#a78bfa", // violet
  product: "#38bdf8", // sky
  engineering: "#22d3ee", // cyan
  growth: "#34d399", // emerald
  customer: "#f472b6", // pink
  governance: "#fb7185", // rose (risk/oversight)
  operations: "#fbbf24", // amber
};

export const DEPARTMENT_LABEL: Record<Department, { en: string; ar: string }> = {
  leadership: { en: "Leadership", ar: "القيادة" },
  product: { en: "Product & Design", ar: "المنتج والتصميم" },
  engineering: { en: "Engineering", ar: "الهندسة" },
  growth: { en: "Growth & Data", ar: "النمو والبيانات" },
  customer: { en: "Customer", ar: "العملاء" },
  governance: { en: "Governance", ar: "الحوكمة" },
  operations: { en: "Operations", ar: "العمليات" },
};

export const AGENT_META: Record<string, AgentMeta> = {
  ceo: { department: "leadership", color: DEPARTMENT_COLOR.leadership, glyph: "CEO" },
  cto: { department: "leadership", color: DEPARTMENT_COLOR.leadership, glyph: "CTO" },
  product_manager: { department: "product", color: DEPARTMENT_COLOR.product, glyph: "PM" },
  product_design: { department: "product", color: DEPARTMENT_COLOR.product, glyph: "DS" },
  ux_research: { department: "product", color: DEPARTMENT_COLOR.product, glyph: "UX" },
  research: { department: "product", color: DEPARTMENT_COLOR.product, glyph: "RS" },
  frontend_engineer: { department: "engineering", color: DEPARTMENT_COLOR.engineering, glyph: "FE" },
  backend_engineer: { department: "engineering", color: DEPARTMENT_COLOR.engineering, glyph: "BE" },
  database_engineer: { department: "engineering", color: DEPARTMENT_COLOR.engineering, glyph: "DB" },
  devops_sre: { department: "engineering", color: DEPARTMENT_COLOR.engineering, glyph: "OPS" },
  qa: { department: "engineering", color: DEPARTMENT_COLOR.engineering, glyph: "QA" },
  independent_reviewer: { department: "engineering", color: DEPARTMENT_COLOR.engineering, glyph: "REV" },
  seo_geo: { department: "growth", color: DEPARTMENT_COLOR.growth, glyph: "SEO" },
  marketing_growth: { department: "growth", color: DEPARTMENT_COLOR.growth, glyph: "MKT" },
  analytics: { department: "growth", color: DEPARTMENT_COLOR.growth, glyph: "AN" },
  customer_success: { department: "customer", color: DEPARTMENT_COLOR.customer, glyph: "CS" },
  customer_support: { department: "customer", color: DEPARTMENT_COLOR.customer, glyph: "SUP" },
  security: { department: "governance", color: DEPARTMENT_COLOR.governance, glyph: "SEC" },
  legal_compliance: { department: "governance", color: DEPARTMENT_COLOR.governance, glyph: "LEG" },
  incident_commander: { department: "governance", color: DEPARTMENT_COLOR.governance, glyph: "IC" },
  finance_procurement: { department: "operations", color: DEPARTMENT_COLOR.operations, glyph: "FIN" },
  operations: { department: "operations", color: DEPARTMENT_COLOR.operations, glyph: "OP" },
  memory_librarian: { department: "operations", color: DEPARTMENT_COLOR.operations, glyph: "MEM" },
};

export function agentMeta(agentKey: string): AgentMeta {
  return (
    AGENT_META[agentKey] || {
      department: "operations",
      color: "#94a3b8",
      glyph: (agentKey || "?").slice(0, 3).toUpperCase(),
    }
  );
}
