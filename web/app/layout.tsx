import type { Metadata, Viewport } from "next";
import "./globals.css";

export const metadata: Metadata = { title: "GuardLine", description: "Credit union phone line (sandbox demo)" };
export const viewport: Viewport = { width: "device-width", initialScale: 1, maximumScale: 1 };

export default function RootLayout({ children }: { children: React.ReactNode }) {
  return (
    <html lang="en">
      <body>{children}</body>
    </html>
  );
}
