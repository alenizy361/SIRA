"use client";

import { useEffect } from "react";
import { usePathname, useRouter } from "next/navigation";
import { useQuery } from "@tanstack/react-query";
import { authApi } from "@/lib/api";
import { NavShell } from "@/components/NavShell";
import { RealtimeBridge } from "@/components/RealtimeBridge";

const PUBLIC_PATHS = ["/login", "/onboarding"];

export function AppShell({ children }: { children: React.ReactNode }) {
  const pathname = usePathname();
  const router = useRouter();
  const isPublic = PUBLIC_PATHS.some((p) => pathname === p || pathname.startsWith(`${p}/`));

  const { data: me, isLoading, isError } = useQuery({
    queryKey: ["me"],
    queryFn: authApi.me,
    retry: false,
  });

  useEffect(() => {
    if (isLoading) return;
    if (isError && !isPublic) {
      router.replace("/login");
      return;
    }
    if (me && isPublic) {
      router.replace("/command-center");
    }
  }, [isLoading, isError, isPublic, me, router]);

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

  if (isError || !me) {
    // Redirect effect above will kick in; render nothing to avoid a flash
    // of protected content.
    return <div className="min-h-dvh" />;
  }

  return (
    <NavShell me={me}>
      <RealtimeBridge />
      {children}
    </NavShell>
  );
}
