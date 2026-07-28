import type { Metadata } from "next";
import { Geist, Geist_Mono } from "next/font/google";
import { headers } from "next/headers";
import { SessionProvider } from "./lib/session";
import "./globals.css";
// Shell first (it defines the per-route surface tokens), then one stylesheet
// per route. Each route file owns its own selectors so they never collide.
import "./styles/shell.css";
import "./styles/home.css";
import "./styles/new.css";
import "./styles/history.css";
import "./styles/settings.css";
import "./styles/case.css";
import "./styles/ask.css";
import "./styles/pen-design.css";
import "./styles/reports.css";

const geistSans = Geist({
  variable: "--font-geist-sans",
  subsets: ["latin"],
});

const geistMono = Geist_Mono({
  variable: "--font-geist-mono",
  subsets: ["latin"],
});

export async function generateMetadata(): Promise<Metadata> {
  const requestHeaders = await headers();
  const host = requestHeaders.get("x-forwarded-host") || requestHeaders.get("host") || "localhost:3000";
  const protocol = requestHeaders.get("x-forwarded-proto") || (host.startsWith("localhost") ? "http" : "https");
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
      <body
        className={`${geistSans.variable} ${geistMono.variable} antialiased`}
      >
        <SessionProvider>{children}</SessionProvider>
      </body>
    </html>
  );
}
