import type { Metadata, Viewport } from "next";

export const metadata: Metadata = {
  title: "CU Secure (demo)",
  description: "Demo member app (simulated)",
  manifest: "/manifest.webmanifest",
  appleWebApp: { capable: true, title: "CU Secure", statusBarStyle: "black-translucent" },
  icons: { apple: "/apple-touch-icon.png", icon: "/icon-192.png" },
};

export const viewport: Viewport = {
  width: "device-width",
  initialScale: 1,
  maximumScale: 1,
  themeColor: "#0f204a",
};

export default function ApproveLayout({ children }: { children: React.ReactNode }) {
  return children;
}
