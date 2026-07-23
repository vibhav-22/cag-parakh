"use client";

import { usePathname } from "next/navigation";
import NavLink from "./nav-link";
import { useSession } from "../lib/session";

const ITEMS = [
  {
    href: "/new",
    label: "New review",
    match: (path: string) => path === "/new",
    icon: (
      <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.7" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true">
        <path d="M12 5v14M5 12h14" />
      </svg>
    ),
  },
  {
    href: "/history",
    label: "History",
    match: (path: string) => path === "/history" || path.startsWith("/batches"),
    icon: (
      <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.7" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true">
        <circle cx="12" cy="12" r="8.5" />
        <path d="M12 7.5V12l3 1.8" />
      </svg>
    ),
  },
  {
    href: "/ask",
    label: "Ask documents",
    match: (path: string) => path === "/ask",
    icon: (
      <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.7" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true">
        <path d="M6 3.5h8l4 4V20.5H6z" />
        <path d="M14 3.5v4h4M9 12h6M9 15.5h4" />
      </svg>
    ),
  },
  {
    href: "/settings",
    label: "Settings",
    match: (path: string) => path === "/settings",
    icon: (
      <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.7" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true">
        <path d="M4 7h10M18 7h2M4 17h4M12 17h8" />
        <circle cx="16" cy="7" r="2.1" />
        <circle cx="10" cy="17" r="2.1" />
      </svg>
    ),
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
      <nav aria-label="Sections">
        {ITEMS.map((item) => (
          <NavLink
            key={item.href}
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
        <div className={`rail-status ${serviceStatus}`} role="status" title={statusLabel}>
          <i aria-hidden="true" />
          <span>{statusLabel}</span>
        </div>
        {session.required && (
          <button type="button" className="rail-signout" onClick={signOut}>Sign out</button>
        )}
      </div>
    </aside>
  );
}
