// Mirrors services/orchestrator/state_machine.py's GoalState transition
// table so the UI only ever offers transitions the backend will accept.
export const GOAL_TRANSITIONS: Record<string, string[]> = {
  goal_captured: ["goal_clarified", "cancelled"],
  goal_clarified: ["discovery", "cancelled"],
  discovery: ["plan_drafted", "cancelled"],
  plan_drafted: ["plan_reviewed", "cancelled"],
  plan_reviewed: ["risk_classified", "cancelled"],
  risk_classified: ["approval_pending", "ready", "cancelled"],
  approval_pending: ["ready", "cancelled"],
  ready: ["assigned", "cancelled"],
  assigned: ["running", "cancelled"],
  running: ["validating", "cancelled"],
  validating: ["reviewing", "cancelled"],
  reviewing: ["preview_ready", "measuring", "cancelled"],
  preview_ready: ["measuring", "cancelled"],
  measuring: ["completed", "cancelled"],
  completed: [],
  cancelled: [],
};
