"use client";

import { useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { chatApi } from "@/lib/api";
import { useI18n } from "@/i18n/I18nProvider";

/**
 * The separate, always-available casual-chat surface (constitution: a plain
 * "hi" must not spin up a full CEO planning run and a multi-agent task
 * tree). Genuinely independent of the goal input on the command center page
 * and of TaskConversation (per-task replies) - no goal/plan/task is ever
 * created here. Mounted once in AppShell so it floats over every
 * authenticated page, not just the command center - the whole point is that
 * it's always reachable, not one more thing to discover on one screen.
 */
export function ChatWidget() {
  const { t } = useI18n();
  const queryClient = useQueryClient();
  const [open, setOpen] = useState(false);
  const [draft, setDraft] = useState("");

  const messagesQuery = useQuery({
    queryKey: ["chat-messages"],
    queryFn: chatApi.listMessages,
    enabled: open,
    refetchInterval: (query) => {
      if (!open) return false;
      const msgs = query.state.data;
      if (!msgs || msgs.length === 0) return false;
      return msgs[msgs.length - 1].role === "human" ? 3_000 : false;
    },
  });

  const sendMutation = useMutation({
    mutationFn: (content: string) => chatApi.sendMessage(content),
    onSuccess: () => {
      setDraft("");
      queryClient.invalidateQueries({ queryKey: ["chat-messages"] });
    },
  });

  const messages = messagesQuery.data || [];

  const handleSend = () => {
    const content = draft.trim();
    if (!content || sendMutation.isPending) return;
    sendMutation.mutate(content);
  };

  return (
    <div className="fixed bottom-4 end-4 z-[150] flex flex-col items-end gap-3">
      {open ? (
        <div
          className="flex h-[440px] w-[340px] max-w-[calc(100vw-2rem)] flex-col rounded-2xl border border-white/10 p-3 shadow-2xl backdrop-blur-xl"
          style={{ background: "rgba(8, 10, 20, 0.96)" }}
        >
          <div className="mb-2 flex items-start justify-between gap-2">
            <div className="min-w-0">
              <h3 className="text-sm font-bold text-slate-100">💬 {t("chat.title")}</h3>
              <p className="mt-0.5 text-[10.5px] leading-snug text-slate-500">{t("chat.subtitle")}</p>
            </div>
            <button
              type="button"
              onClick={() => setOpen(false)}
              aria-label={t("chat.close")}
              className="shrink-0 rounded-full border border-white/10 px-2 py-1 text-[11px] text-slate-400 hover:bg-white/5"
            >
              ✕
            </button>
          </div>

          <div className="flex-1 overflow-y-auto rounded-lg border border-white/5 bg-black/10 p-2">
            {messages.length === 0 ? (
              <p className="py-6 text-center text-[11px] text-slate-500">{t("chat.empty")}</p>
            ) : (
              <div className="flex flex-col gap-1.5">
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
                    <span className="mb-0.5 block text-[9px] font-bold uppercase tracking-[0.1em] text-slate-500">
                      {m.role === "human" ? t("chat.you") : "Agent"}
                    </span>
                    <p className="whitespace-pre-wrap break-words">{m.body}</p>
                  </div>
                ))}
              </div>
            )}
          </div>

          <div className="mt-2 flex items-center gap-1.5">
            <input
              type="text"
              value={draft}
              onChange={(e) => setDraft(e.target.value)}
              onKeyDown={(e) => {
                if (e.key === "Enter") handleSend();
              }}
              placeholder={t("chat.placeholder")}
              className="min-w-0 flex-1 rounded-full border border-white/10 bg-white/[0.03] px-3 py-1.5 text-[12px] text-slate-100 placeholder:text-slate-600 focus:border-cyan-400/50 focus:outline-none"
            />
            <button
              type="button"
              onClick={handleSend}
              disabled={!draft.trim() || sendMutation.isPending}
              className="shrink-0 rounded-full border border-cyan-400/30 bg-cyan-500/[0.1] px-3 py-1.5 text-[11px] font-semibold text-cyan-200 hover:bg-cyan-500/20 disabled:opacity-50"
            >
              {sendMutation.isPending ? t("chat.sending") : t("chat.send")}
            </button>
          </div>
          {sendMutation.isError ? (
            <p className="mt-1 px-1 text-[10px] text-rose-400">{t("chat.failed")}</p>
          ) : null}
        </div>
      ) : null}

      <button
        type="button"
        onClick={() => setOpen((v) => !v)}
        className="flex items-center gap-2 rounded-full border border-cyan-400/30 bg-cyan-500/[0.12] px-4 py-3 text-sm font-semibold text-cyan-100 shadow-[0_0_20px_rgba(34,211,238,0.25)] backdrop-blur hover:bg-cyan-500/20"
      >
        💬 {t("chat.launcher")}
      </button>
    </div>
  );
}
