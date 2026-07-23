"use client";

import AppShell from "./components/app-shell";
import NavLink from "./components/nav-link";

/**
 * Home is the poster route: no rail, no panels, no cards. It fills the
 * viewport, centres itself, and exists to do exactly one thing — send the
 * reader into /new. Everything else on this page is atmosphere.
 *
 * Styling lives in styles/home.css, scoped under .app[data-route="home"].
 */
export default function Home() {
  return (
    <AppShell route="home" chrome="bare">
      <div className="poster">
        <div className="poster-copy">
          <p className="poster-eyebrow">Forensic document screening</p>
          <h2 className="poster-title">It sees what your <em>eyes can&rsquo;t.</em></h2>
          <p className="poster-lede">Eight independent checks examine every page &mdash; fonts, ink, whitener, moir&eacute;, metadata and more &mdash; and mark exactly where a document was altered.</p>
          <div className="poster-cta">
            <NavLink href="/new" className="poster-cta-primary">
              Start a review<i aria-hidden="true">&rarr;</i>
            </NavLink>
            <NavLink href="/history" className="poster-cta-secondary">View past cases</NavLink>
          </div>
          <div className="poster-checks-block">
            <p className="poster-checks-cap">The eight checks</p>
            <div className="poster-checks">
              <span>Fonts</span><span>Ink</span><span>Whitener</span><span>Moir&eacute;</span>
              <span>Metadata</span><span>QR</span><span>Scanner noise</span><span>Capture</span>
            </div>
          </div>
          <div className="poster-steps" aria-label="Review workflow">
            <span><b>1</b> Select PDFs</span><span><b>2</b> Run checks</span><span><b>3</b> Record decisions</span>
          </div>
        </div>
        <div className="poster-stage" aria-hidden="true">
          <div className="specimen-scan">
            <div className="specimen">
              <span className="sp-seal">GOV</span>
              <span className="sp-title">Certificate of Record</span>
              <span className="sp-line w88" /><span className="sp-line w70" /><span className="sp-line" />
              <span className="sp-grid"><i /><i /></span>
              <span className="sp-line w45" /><span className="sp-qr" />
            </div>
            <div className="scan-beam" />
            <span className="ev-mark sev-high" style={{ left: "52%", top: "34%", width: "34%", height: "8%", animationDelay: "1.4s" }}><b>1</b></span>
            <span className="ev-mark sev-medium" style={{ left: "10%", top: "57%", width: "30%", height: "12%", animationDelay: "1.8s" }}><b>2</b></span>
            <span className="ev-mark sev-low" style={{ left: "12%", top: "82%", width: "18%", height: "11%", animationDelay: "2.2s" }}><b>3</b></span>
          </div>
          <p className="specimen-caption">Specimen &middot; 3 findings marked</p>
        </div>
      </div>
    </AppShell>
  );
}
