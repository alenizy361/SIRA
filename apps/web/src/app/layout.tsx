import type { Metadata, Viewport } from "next";
import "./globals.css";
import { QueryProvider } from "@/components/QueryProvider";
import { I18nProvider } from "@/i18n/I18nProvider";
import { ToastProvider } from "@/components/ToastProvider";
import { AppShell } from "@/components/AppShell";

export const metadata: Metadata = {
  title: "Rabit AI Company OS",
  description: "Command center for your autonomous company",
};

export const viewport: Viewport = {
  width: "device-width",
  initialScale: 1,
  viewportFit: "cover",
  themeColor: "#020617",
};

export default function RootLayout({
  children,
}: Readonly<{
  children: React.ReactNode;
}>) {
  return (
    <html lang="ar" dir="rtl" className="h-full" suppressHydrationWarning>
      <body className="min-h-full text-slate-100 antialiased" suppressHydrationWarning>
        {/* fixed cinematic deep-space backdrop behind everything */}
        <div className="space-backdrop" aria-hidden />
        <QueryProvider>
          <I18nProvider>
            <ToastProvider>
              <AppShell>{children}</AppShell>
            </ToastProvider>
          </I18nProvider>
        </QueryProvider>
      </body>
    </html>
  );
}
