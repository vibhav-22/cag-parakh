"use client";

import { ReactNode } from "react";

/**
 * The band under the topbar. Every route fills it differently, and that
 * difference is the fastest answer to "where am I": /new shows the three
 * steps, /batches shows live progress, the workspace shows the verdict,
 * /history shows search, /settings shows section links. Home skips it.
 */
export default function RouteHeader({
  eyebrow,
  title,
  sub,
  aside,
  below,
  rule = true,
}: {
  eyebrow?: ReactNode;
  title: ReactNode;
  sub?: ReactNode;
  aside?: ReactNode;
  below?: ReactNode;
  rule?: boolean;
}) {
  return (
    <header className="route-header">
      {/* A div, not a p: routes pass rich eyebrows (the workspace passes a
          <nav> breadcrumb) and nav-inside-p is invalid HTML that trips
          hydration. */}
      {eyebrow && <div className="route-header-eyebrow">{eyebrow}</div>}
      <div className="route-header-main">
        <div>
          <h1>{title}</h1>
          {sub && <p className="route-header-sub">{sub}</p>}
        </div>
        {aside && <div className="route-header-aside">{aside}</div>}
      </div>
      {below && <div className="route-header-below">{below}</div>}
      {rule && <div className="route-header-rule" />}
    </header>
  );
}
