import { redirect } from "next/navigation";

// Force a real HTTP redirect on every request instead of letting this route
// be statically prerendered (which would embed the redirect only in the
// client-side RSC flight payload).
export const dynamic = "force-dynamic";

export default function RootPage() {
  redirect("/command-center");
}
