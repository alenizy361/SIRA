"use client";

import { useEffect } from "react";
import { usePathname, useRouter } from "next/navigation";
import { useQuery } from "@tanstack/react-query";
import { authApi, ApiError } from "@/lib/api";
import { NavShell } from "@/components/NavShell";
import { RealtimeBridge } from "@/components/RealtimeBridge";
import { ChatWidget } from "@/components/ChatWidget";

const PUBLIC_PATHS = ["/login", "/onboarding"];

export function AppShell({ children }: { children: React.ReactNode }) {
  const pathname = usePathname();
  const router = useRouter();
  const isPublic = PUBLIC_PATHS.some((p) => pathname === p || pathname.startsWith(`${p}/`));

  const { data: me, isLoading, isError, error, refetch } = useQuery({
    queryKey: ["me"],
    queryFn: authApi.me,
    // Retry transient/network/5xx errors a couple of times; a real 401/403
    // is authoritative and should not be retried.
    retry: (count, err) =>
      !(err instanceof ApiError && (err.status === 401 || err.status === 403)) && count < 2,
  });

  // Only a genuine auth failure (401/403) means "not logged in". A 502/503
  // while the API restarts, or a network blip on laptop-wake, must NOT bounce
  // a validly-authenticated operator to /login.
  const isAuthError = error instanceof ApiError && (error.status === 401 || error.status === 403);

  useEffect(() => {
    if (isLoading) return;
    if (isAuthError && !isPublic) {
      router.replace("/login");
      return;
    }
    if (me && isPublic) {
      router.replace("/command-center");
    }
  }, [isLoading, isAuthError, isPublic, me, router]);

  if (isPublic) {
    return <>{children}</>;
  }

  if (isLoading) {
    return (
      <div className="flex min-h-dvh items-center justify-center text-sm text-slate-400">
        …
      </div>
    );
  }

  if (isError && !isAuthError) {
    // API unreachable (not an auth problem) - keep the operator here and offer
    // a retry rather than kicking them to a login they don't need.
    return (
      <div className="flex min-h-dvh flex-col items-center justify-center gap-3 text-center text-sm text-slate-400">
        <span>تعذّر الوصول إلى الخادم — سيُعاد المحاولة تلقائيًا.</span>
        <button
          type="button"
          onClick={() => refetch()}
          className="rounded-lg border border-white/15 px-4 py-2 text-slate-200 hover:bg-white/5"
        >
          إعادة المحاولة
        </button>
      </div>
    );
  }

  if (!me) {
    // Auth redirect effect above will kick in; render nothing to avoid a flash
    // of protected content.
    return <div className="min-h-dvh" />;
  }

  return (
    <NavShell me={me}>
      <RealtimeBridge />
      {children}
      <ChatWidget />
    </NavShell>
  );
}
