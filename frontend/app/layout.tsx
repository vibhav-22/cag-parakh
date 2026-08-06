import type { Metadata } from "next";
import { headers } from "next/headers";
import { SessionProvider } from "./lib/session";
import "./globals.css";
// Faces first, so every stylesheet below can name them. This file also defines
// --font-geist-sans and --font-geist-mono, which next/font used to set on the
// body. next/font cannot be used here at all: under vinext it writes an
// absolute C:/ path into the generated @font-face, the browser refuses it as a
// file:// resource, and every face fails silently. See tools/fonts/sync_fonts.py.
import "./styles/fonts.css";
// Shell first (it defines the per-route surface tokens), then one stylesheet
// per route. Each route file owns its own selectors so they never collide.
import "./styles/shell.css";
import "./styles/home.css";
import "./styles/new.css";
import "./styles/history.css";
import "./styles/projects.css";
import "./styles/settings.css";
import "./styles/case.css";
import "./styles/ask.css";
import "./styles/pen-design.css";
import "./styles/reports.css";
import "./styles/welcome.css";

export async function generateMetadata(): Promise<Metadata> {
  const requestHeaders = await headers();
  const host = requestHeaders.get("x-forwarded-host") || requestHeaders.get("host") || "localhost:3000";
  const isLoopback = host.startsWith("localhost") || host.startsWith("127.0.0.1") || host.startsWith("[::1]");
  const protocol = requestHeaders.get("x-forwarded-proto") || (isLoopback ? "http" : "https");
  const metadataBase = new URL(`${protocol}://${host}`);
  const title = "Parakh | Document Integrity Workspace";
  const description = "Run local PDF integrity checks, inspect evidence, and record review decisions.";
  return {
    metadataBase,
    title,
    description,
    icons: { icon: "/favicon.svg", shortcut: "/favicon.svg" },
    openGraph: { title, description, type: "website", images: [{ url: "/og.png", width: 1536, height: 1024 }] },
    twitter: { card: "summary_large_image", title, description, images: ["/og.png"] },
  };
}

export default function RootLayout({
  children,
}: Readonly<{
  children: React.ReactNode;
}>) {
  return (
    <html lang="en">
      <body className="antialiased">
        <SessionProvider>{children}</SessionProvider>
      </body>
    </html>
  );
}
