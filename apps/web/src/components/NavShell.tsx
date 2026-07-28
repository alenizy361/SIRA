"use client";

import { useState } from "react";
import Link from "next/link";
import { usePathname, useRouter } from "next/navigation";
import { useQueryClient } from "@tanstack/react-query";
import { useI18n } from "@/i18n/I18nProvider";
import { LocaleSwitcher } from "@/components/LocaleSwitcher";
import { authApi, Me } from "@/lib/api";
import { useEventStore } from "@/store/eventStore";

const NAV_ITEMS: { href: string; key: string }[] = [
  { href: "/command-center", key: "nav.command_center" },
  { href: "/goals", key: "nav.goals" },
  { href: "/plans", key: "nav.plans" },
  { href: "/tasks", key: "nav.tasks" },
  { href: "/agents", key: "nav.agents" },
  { href: "/runs", key: "nav.runs" },
  { href: "/approvals", key: "nav.approvals" },
  { href: "/integrations", key: "nav.integrations" },
  { href: "/memory", key: "nav.memory" },
  { href: "/budgets", key: "nav.budgets" },
  { href: "/analytics", key: "nav.analytics" },
  { href: "/incidents", key: "nav.incidents" },
  { href: "/audit", key: "nav.audit" },
  { href: "/doctor", key: "nav.doctor" },
  { href: "/settings", key: "nav.settings" },
];

function WsStatusDot() {
  const status = useEventStore((s) => s.wsStatus);
  const color =
    status === "open"
      ? "bg-emerald-400"
      : status === "connecting" || status === "reconnecting"
        ? "bg-amber-400 animate-pulse"
        : "bg-slate-500";
  return <span className={`inline-block h-2 w-2 rounded-full ${color}`} aria-hidden />;
}

export function NavShell({ children, me }: { children: React.ReactNode; me: Me }) {
  const { t } = useI18n();
  const pathname = usePathname();
  const router = useRouter();
  const queryClient = useQueryClient();
  const [drawerOpen, setDrawerOpen] = useState(false);

  async function handleLogout() {
    try {
      await authApi.logout();
    } catch {
      // ignore network errors on logout, still clear client state
    }
    queryClient.clear();
    router.replace("/login");
  }

  return (
    <div className="min-h-dvh bg-slate-950 text-slate-100">
      <header
        className="sticky top-0 z-40 flex items-center justify-between gap-2 border-b border-white/10 bg-slate-950/90 px-3 backdrop-blur-md"
        style={{
          paddingTop: "calc(env(safe-area-inset-top, 0px) + 0.5rem)",
          paddingBottom: "0.5rem",
          paddingInlineStart: "calc(env(safe-area-inset-left, 0px) + 0.75rem)",
          paddingInlineEnd: "calc(env(safe-area-inset-right, 0px) + 0.75rem)",
        }}
      >
        <div className="flex items-center gap-2">
          <button
            type="button"
            onClick={() => setDrawerOpen((v) => !v)}
            className="flex h-11 w-11 items-center justify-center rounded-lg border border-white/10 md:hidden"
            aria-label="menu"
            aria-expanded={drawerOpen}
          >
            <span className="flex flex-col gap-1">
              <span className="block h-0.5 w-5 bg-slate-200" />
              <span className="block h-0.5 w-5 bg-slate-200" />
              <span className="block h-0.5 w-5 bg-slate-200" />
            </span>
          </button>
          <span className="truncate text-sm font-semibold tracking-tight sm:text-base">
            {t("app.name")}
          </span>
        </div>
        <div className="flex items-center gap-2">
          <div className="hidden items-center gap-1 text-xs text-slate-400 sm:flex">
            <WsStatusDot />
            <span>{me.display_name}</span>
          </div>
          <LocaleSwitcher />
          <button
            type="button"
            onClick={handleLogout}
            className="min-h-[44px] rounded-lg border border-white/10 px-3 text-xs font-medium text-slate-300 hover:bg-white/5"
          >
            {t("common.logout")}
          </button>
        </div>
      </header>

      <div className="mx-auto flex max-w-7xl">
        <nav className="sticky top-[57px] hidden h-[calc(100dvh-57px)] w-60 shrink-0 overflow-y-auto border-e border-white/10 p-3 md:block">
          <ul className="flex flex-col gap-1">
            {NAV_ITEMS.map((item) => {
              const active = pathname === item.href || pathname.startsWith(`${item.href}/`);
              return (
                <li key={item.href}>
                  <Link
                    href={item.href}
                    className={`block min-h-[44px] rounded-lg px-3 py-2 text-sm transition-colors ${
                      active
                        ? "bg-emerald-500/15 text-emerald-300"
                        : "text-slate-300 hover:bg-white/5"
                    }`}
                  >
                    {t(item.key)}
                  </Link>
                </li>
              );
            })}
          </ul>
        </nav>

        {drawerOpen && (
          <div className="fixed inset-0 z-50 md:hidden">
            <button
              type="button"
              aria-label="close menu"
              className="absolute inset-0 bg-black/60"
              onClick={() => setDrawerOpen(false)}
            />
            <nav className="absolute inset-y-0 start-0 w-72 max-w-[85vw] overflow-y-auto bg-slate-950 p-3 shadow-2xl">
              <ul className="flex flex-col gap-1">
                {NAV_ITEMS.map((item) => {
                  const active = pathname === item.href || pathname.startsWith(`${item.href}/`);
                  return (
                    <li key={item.href}>
                      <Link
                        href={item.href}
                        onClick={() => setDrawerOpen(false)}
                        className={`block min-h-[44px] rounded-lg px-3 py-3 text-sm transition-colors ${
                          active
                            ? "bg-emerald-500/15 text-emerald-300"
                            : "text-slate-300 hover:bg-white/5"
                        }`}
                      >
                        {t(item.key)}
                      </Link>
                    </li>
                  );
                })}
              </ul>
            </nav>
          </div>
        )}

        <main
          className="min-w-0 flex-1 px-3 py-4 sm:px-6"
          style={{
            paddingInlineStart: "calc(env(safe-area-inset-left, 0px) + 0.75rem)",
            paddingInlineEnd: "calc(env(safe-area-inset-right, 0px) + 0.75rem)",
            paddingBottom: "calc(env(safe-area-inset-bottom, 0px) + 1.5rem)",
          }}
        >
          {children}
        </main>
      </div>
    </div>
  );
}
