"use client";

import { usePathname } from "next/navigation";
import {
  ChartLineUp,
  ChatCenteredText,
  GearSix,
  Scan,
  SquaresFour,
  Stack,
} from "@phosphor-icons/react";
import NavLink from "./nav-link";
import { useSession } from "../lib/session";

const ITEMS = [
  {
    href: "/",
    label: "Dashboard",
    match: (path: string) => path === "/",
    icon: <SquaresFour aria-hidden="true" weight="regular" />,
  },
  {
    href: "/new",
    label: "New Screening",
    match: (path: string) => path === "/new",
    icon: <Scan aria-hidden="true" weight="regular" />,
  },
  // The case library and the batch matrix are one concept, not two. /history
  // is the list; /batches/[id] is a case opened from it. A second rail row
  // pointing at the same route only made the rail look like it had somewhere
  // else to go.
  {
    href: "/history",
    label: "Batches",
    match: (path: string) => path === "/history" || path.startsWith("/batches"),
    icon: <Stack aria-hidden="true" weight="regular" />,
  },
  {
    href: "/reports",
    label: "Reports",
    match: (path: string) => path === "/reports",
    icon: <ChartLineUp aria-hidden="true" weight="regular" />,
  },
  {
    href: "/ask",
    label: "Ask Documents",
    match: (path: string) => path === "/ask",
    icon: <ChatCenteredText aria-hidden="true" weight="regular" />,
  },
  {
    href: "/settings",
    label: "Settings",
    match: (path: string) => path === "/settings",
    icon: <GearSix aria-hidden="true" weight="regular" />,
  },
];

/**
 * Persistent navigation for every signed-in route. Replaces the old
 * always-visible upload sidebar, which stayed on screen while a reviewer was
 * examining evidence and made every route look identical.
 */
export default function NavRail({ collapsed }: { collapsed: boolean }) {
  const pathname = usePathname() || "";
  const { session, serviceStatus, signOut } = useSession();

  const statusLabel = serviceStatus === "online"
    ? "Service online"
    : serviceStatus === "checking" ? "Checking service" : "Service offline";

  return (
    <aside className="nav-rail">
      <NavLink className="rail-brand" href="/">
        <span className="rail-brand-mark" aria-hidden="true"><Scan weight="bold" /></span>
        <span className="rail-brand-copy">
          <strong>CAG Parakh</strong>
          <small>DOC FORENSICS</small>
        </span>
      </NavLink>

      <nav aria-label="Sections">
        {ITEMS.map((item) => (
          <NavLink
            key={item.label}
            href={item.href}
            title={collapsed ? item.label : undefined}
            aria-current={item.match(pathname) ? "page" : undefined}
          >
            {item.icon}
            <span className="rail-label">{item.label}</span>
          </NavLink>
        ))}
      </nav>

      <div className="rail-foot">
        <div className="rail-account">
          <span className="rail-avatar" aria-hidden="true">FI</span>
          <span>
            <strong>Field Investigator</strong>
            <small>{statusLabel}</small>
          </span>
        </div>
        {session.required && (
          <button type="button" className="rail-signout" onClick={signOut}>Sign out</button>
        )}
      </div>
    </aside>
  );
}
