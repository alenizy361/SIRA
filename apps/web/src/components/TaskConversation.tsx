"use client";

import { useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { ApiError, tasksApi } from "@/lib/api";
import { useI18n } from "@/i18n/I18nProvider";

/**
 * A real, continued conversation with one task's agent - not a new task,
 * the SAME CLI session resumed (claude_worker.worker._execute_reply /
 * cli_adapter's --resume). Only shown once the task has produced a result,
 * since a session to resume can't exist before that.
 */
export function TaskConversation({ taskId }: { taskId: string }) {
  const { t } = useI18n();
  const queryClient = useQueryClient();
  const [open, setOpen] = useState(false);
  const [draft, setDraft] = useState("");

  const messagesQuery = useQuery({
    queryKey: ["task-messages", taskId],
    queryFn: () => tasksApi.listMessages(taskId),
    enabled: open,
    refetchInterval: (query) => {
      const msgs = query.state.data;
      if (!msgs || msgs.length === 0) return false;
      // Keep polling only while the latest turn is still an unanswered
      // human message - once the agent replies there's nothing to wait for.
      return msgs[msgs.length - 1].role === "human" ? 4_000 : false;
    },
  });

  const sendMutation = useMutation({
    mutationFn: (content: string) => tasksApi.sendMessage(taskId, content),
    onSuccess: () => {
      setDraft("");
      queryClient.invalidateQueries({ queryKey: ["task-messages", taskId] });
    },
  });

  const messages = messagesQuery.data || [];
  const noSessionYet = sendMutation.isError && sendMutation.error instanceof ApiError && sendMutation.error.status === 409;

  const handleSend = () => {
    const content = draft.trim();
    if (!content || sendMutation.isPending) return;
    sendMutation.mutate(content);
  };

  return (
    <details
      className="mt-2 rounded-lg border border-white/8 bg-black/10 open:bg-black/20"
      open={open}
      onToggle={(e) => setOpen((e.target as HTMLDetailsElement).open)}
    >
      <summary className="cursor-pointer select-none px-2 py-1.5 text-[10px] font-bold uppercase tracking-[0.1em] text-slate-500 hover:text-slate-300">
        {t("command_center.reply_toggle")}
        {messages.length > 0 ? ` · ${messages.length}` : ""}
      </summary>
      <div className="flex flex-col gap-1.5 px-2 pb-2">
        {messages.map((m) => (
          <div
            key={m.id}
            className="rounded-lg border px-2 py-1.5 text-[12px] leading-relaxed"
            style={
              m.role === "human"
                ? { borderColor: "#38bdf833", background: "#38bdf80d", color: "#e2e8f0" }
                : { borderColor: "#a78bfa33", background: "#a78bfa0d", color: "#e2e8f0" }
            }
          >
            <span className="mb-0.5 block text-[9.5px] font-bold uppercase tracking-[0.1em] text-slate-500">
              {m.role === "human" ? t("command_center.reply_toggle") : "Agent"}
            </span>
            <p className="whitespace-pre-wrap break-words">{m.body}</p>
          </div>
        ))}
        {messages.length > 0 && messages[messages.length - 1].role === "human" ? (
          <p className="px-1 text-[10px] text-slate-500">{t("command_center.reply_waiting")}</p>
        ) : null}

        <div className="mt-1 flex items-center gap-1.5">
          <input
            type="text"
            value={draft}
            onChange={(e) => setDraft(e.target.value)}
            onKeyDown={(e) => {
              if (e.key === "Enter") handleSend();
            }}
            placeholder={t("command_center.reply_placeholder")}
            className="min-w-0 flex-1 rounded-full border border-white/10 bg-white/[0.03] px-3 py-1.5 text-[12px] text-slate-100 placeholder:text-slate-600 focus:border-violet-400/50 focus:outline-none"
          />
          <button
            type="button"
            onClick={handleSend}
            disabled={!draft.trim() || sendMutation.isPending}
            className="shrink-0 rounded-full border border-violet-400/30 bg-violet-500/[0.1] px-3 py-1.5 text-[11px] font-semibold text-violet-200 hover:bg-violet-500/20 disabled:opacity-50"
          >
            {sendMutation.isPending ? t("command_center.reply_sending") : t("command_center.reply_send")}
          </button>
        </div>
        {noSessionYet ? (
          <p className="px-1 text-[10px] text-rose-400">{t("command_center.reply_no_session_yet")}</p>
        ) : sendMutation.isError ? (
          <p className="px-1 text-[10px] text-rose-400">{t("command_center.reply_failed")}</p>
        ) : null}
      </div>
    </details>
  );
}
