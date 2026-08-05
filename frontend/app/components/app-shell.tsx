"use client";

import { ReactNode } from "react";
import GraceBanner from "./grace-banner";
import NavLink from "./nav-link";
import NavRail from "./nav-rail";
import { useSession } from "../lib/session";

export type RouteName = "home" | "new" | "ask" | "batches" | "document" | "history" | "projects" | "reports" | "settings";
export type Chrome = "bare" | "rail" | "icons";

/**
 * The one constant across the app is the topbar and the brand. Everything else
 * (rail or no rail, surface depth, content width, density) is driven by the
 * `route` and `chrome` props so each screen feels like a different room.
 *
 * `route` sets [data-route], which drives the surface ladder in styles/shell.css.
 * Route stylesheets scope everything under `.app[data-route="x"]`, so two route
 * files can never collide.
 */
export default function AppShell({
  route,
  chrome = "rail",
  header,
  children,
}: {
  route: RouteName;
  chrome?: Chrome;
  header?: ReactNode;
  children: ReactNode;
}) {
  const { session, signOut } = useSession();

  return (
    <main className="app" data-route={route}>
      {chrome === "bare" && (
        <header className="topbar">
          <NavLink className="brand-link" href="/">
            <div className="brand-mark" aria-hidden="true">P</div>
            <div className="brand-copy">
              <span className="brand-name">CAG Parakh</span>
              <p>Document forensics</p>
            </div>
          </NavLink>
          {session.required && (
            <button type="button" className="text-button signout-button" onClick={signOut}>Sign out</button>
          )}
        </header>
      )}

      <div className="shell" data-chrome={chrome}>
        {chrome !== "bare" && <NavRail collapsed={chrome === "icons"} />}
        <div className="route-body">
          <GraceBanner />
          {header}
          <div className="route-content">{children}</div>
        </div>
      </div>
    </main>
  );
}
