import type { Metadata } from "next";

import "./globals.css";

export const metadata: Metadata = {
  title: "TenantPilot",
  description: "Control plane for multi-tenant AI agent deployments",
};

export default function RootLayout({ children }: LayoutProps<"/">) {
  return (
    <html lang="en" className="h-full antialiased">
      <body className="flex min-h-full flex-col font-sans">{children}</body>
    </html>
  );
}
