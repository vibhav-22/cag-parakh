"use client";

import FlightShowcase from "./components/flight-showcase";
import { Faqs, Features, Hero, SiteFooter, Workflow } from "./components/sections";
import NavLink from "../components/nav-link";

/**
 * The public landing page.
 *
 * Deliberately outside AppShell: the shell's rail and topbar are workspace
 * furniture for someone already signed in, and this page is the thing a
 * visitor sees first. It is also the one route the session gate lets through
 * unauthenticated — see PUBLIC_ROUTES in app/lib/session.tsx.
 */
export default function WelcomePage() {
  return (
    <main className="welcome" data-route="welcome">
      <div className="welcome-topbar">
        <NavLink className="brand-link" href="/welcome">
          <div className="brand-mark" aria-hidden="true">P</div>
          <div className="brand-copy">
            <span className="brand-name">CAG Parakh</span>
            <p>Document forensics</p>
          </div>
        </NavLink>
        <NavLink className="welcome-signin" href="/">
          Sign in
        </NavLink>
      </div>

      <Hero />
      <FlightShowcase />
      <Features />
      <Workflow />
      <Faqs />
      <SiteFooter />
    </main>
  );
}
