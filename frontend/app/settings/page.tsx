"use client";

import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import AppShell from "../components/app-shell";
import RouteHeader from "../components/route-header";
import NavLink from "../components/nav-link";
import AdvancedSettings, { AnalysisSettings, DEFAULT_ANALYSIS_SETTINGS } from "../advanced-settings";
import { API_URL, analyzerLabel, initialAnalysisSettings } from "../lib/format";
import {
  MAX_PROFILE_GOAL,
  MAX_PROFILE_GUIDANCE,
  MAX_PROFILE_NAME,
  ScreeningProfile,
  deleteProfile,
  listProfiles,
  saveProfile,
} from "../lib/profiles";
import { useSession } from "../lib/session";
import type { Analyzer } from "../lib/types";

/**
 * /settings is the home for SAVED DEFAULTS.
 *
 * Everything here is persisted to localStorage under the single shared key
 * `parakh-analysis-settings` and seeds every future run. Per-run changes live
 * on /new and never write back here. That distinction is stated in the header
 * band, repeated on the save bar, and is the reason this route uses explicit
 * save semantics rather than autosaving on keystroke.
 */

const STORAGE_KEY = "parakh-analysis-settings";

const SECTIONS = [
  { id: "checks", label: "Default checks" },
  { id: "thresholds", label: "Thresholds" },
  { id: "profiles", label: "Screening tests" },
  { id: "session", label: "Session" },
  { id: "about", label: "About" },
] as const;

type SectionId = (typeof SECTIONS)[number]["id"];
type LoadState = "loading" | "ready" | "error";

/**
 * Mirrors the names AdvancedSettings uses for the same detectors, so the two
 * sections on this page never call the same check by two different names.
 * Falls back to the shared title-caser for anything the backend adds later.
 */
const CHECK_LABELS: Record<string, string> = {
  metadata: "Metadata",
  qr_presence: "QR detection",
  font_analysis: "Font analysis",
  moire: "Moire detection",
  scanner_noise: "Scanner noise",
  same_phone: "Same-phone consistency",
  tamper_scan: "Whitener detection",
  readability: "Readability",
  photo_detection: "Document photo",
};

function checkLabel(id: string) {
  return CHECK_LABELS[id] || analyzerLabel(id);
}

/**
 * The stored blob is an AnalysisSettings object; we keep the default check
 * selection alongside it on the same key rather than inventing a second one.
 * `initialAnalysisSettings()` allow-lists the detector keys it knows about, so
 * this extra field is simply ignored by every other reader, and the backend
 * sanitiser does the same on submit.
 */
function storedDefaultChecks(): string[] | null {
  if (typeof window === "undefined") return null;
  try {
    const raw = window.localStorage.getItem(STORAGE_KEY);
    if (!raw) return null;
    const parsed = JSON.parse(raw) as { default_analyzers?: unknown };
    if (!Array.isArray(parsed.default_analyzers)) return null;
    return parsed.default_analyzers.filter((item): item is string => typeof item === "string");
  } catch {
    return null;
  }
}

function sameChecks(a: string[], b: string[]) {
  return a.length === b.length && [...a].sort().join(",") === [...b].sort().join(",");
}

function prefersReducedMotion() {
  return typeof window !== "undefined" && window.matchMedia("(prefers-reduced-motion: reduce)").matches;
}

export default function SettingsPage() {
  const { session, serviceStatus, handleUnauthorized, signOut } = useSession();

  const [analyzers, setAnalyzers] = useState<Analyzer[]>([]);
  const [loadState, setLoadState] = useState<LoadState>("loading");

  // Draft = what is on screen. Saved = what is in localStorage.
  const [settings, setSettings] = useState<AnalysisSettings>(initialAnalysisSettings);
  const [savedSettings, setSavedSettings] = useState<AnalysisSettings>(initialAnalysisSettings);
  const [checks, setChecks] = useState<string[]>([]);
  const [savedChecks, setSavedChecks] = useState<string[]>([]);

  const [saveState, setSaveState] = useState<"idle" | "saved" | "failed">("idle");
  const [confirmReset, setConfirmReset] = useState(false);
  const [active, setActive] = useState<SectionId>("checks");

  // Screening tests (profiles). This route manages the library — the name,
  // goal, and decision rule of each saved test, plus deletion. Creating a test
  // and choosing its checks/thresholds happens on /new, where the check UI
  // lives; a test carries those, and they are shown here read-only.
  const [profiles, setProfiles] = useState<ScreeningProfile[]>([]);
  const [editingProfileId, setEditingProfileId] = useState<string | null>(null);
  const [profileDraft, setProfileDraft] = useState<{ name: string; goal: string; guidance: string }>({ name: "", goal: "", guidance: "" });
  const [profileError, setProfileError] = useState("");
  useEffect(() => { setProfiles(listProfiles()); }, []);

  function beginEditProfile(profile: ScreeningProfile) {
    setEditingProfileId(profile.id);
    setProfileDraft({ name: profile.name, goal: profile.goal, guidance: profile.guidance });
    setProfileError("");
  }

  function saveProfileEdit(profile: ScreeningProfile) {
    if (!profileDraft.name.trim()) { setProfileError("A test needs a name."); return; }
    const saved = saveProfile({
      id: profile.id,
      name: profileDraft.name,
      goal: profileDraft.goal,
      guidance: profileDraft.guidance,
      analyzers: profile.analyzers,
      settings: profile.settings,
    });
    if (!saved) { setProfileError("This browser is blocking local storage."); return; }
    setProfiles(listProfiles());
    setEditingProfileId(null);
  }

  function removeProfile(id: string) {
    deleteProfile(id);
    setProfiles(listProfiles());
    if (editingProfileId === id) setEditingProfileId(null);
  }

  const sectionRefs = useRef<Partial<Record<SectionId, HTMLElement | null>>>({});

  const loadAnalyzers = useCallback(async () => {
    setLoadState("loading");
    try {
      const response = await fetch(`${API_URL}/api/v1/analyzers`);
      if (response.status === 401) {
        handleUnauthorized();
        return;
      }
      if (!response.ok) throw new Error("unavailable");
      const data = (await response.json()) as { analyzers: Analyzer[] };
      const ids = data.analyzers.filter((item) => item.available !== false).map((item) => item.id);
      const stored = storedDefaultChecks();
      const restored = stored ? stored.filter((id) => ids.includes(id)) : ids;
      setAnalyzers(data.analyzers);
      setChecks(restored);
      setSavedChecks(restored);
      setLoadState("ready");
    } catch {
      setLoadState("error");
    }
  }, [handleUnauthorized]);

  // loadAnalyzers only sets state after its fetch settles, never synchronously.
  // eslint-disable-next-line react-hooks/set-state-in-effect
  useEffect(() => { loadAnalyzers(); }, [loadAnalyzers]);

  // Reflect the section the reader is actually looking at in the header band.
  //
  // The panels are wildly uneven — thresholds is taller than the viewport — so
  // the answer is always "the last section whose top has passed the reading
  // line", recomputed from scratch. An IntersectionObserver alone is not enough
  // here: with one panel taller than the screen, a narrow observer band can
  // contain no section at all and the marker sticks. So the observer is used
  // only as a cheap "the view moved" trigger alongside scroll and resize, and
  // every trigger runs the same full recompute.
  useEffect(() => {
    let frame = 0;
    const recompute = () => {
      frame = 0;
      if (window.innerHeight + window.scrollY >= document.documentElement.scrollHeight - 4) {
        setActive(SECTIONS[SECTIONS.length - 1].id);
        return;
      }
      let current: SectionId = SECTIONS[0].id;
      for (const section of SECTIONS) {
        const node = sectionRefs.current[section.id];
        if (node && node.getBoundingClientRect().top <= 140) current = section.id;
      }
      setActive(current);
    };
    const schedule = () => { if (!frame) frame = window.requestAnimationFrame(recompute); };

    const nodes = SECTIONS
      .map((section) => sectionRefs.current[section.id])
      .filter((node): node is HTMLElement => Boolean(node));
    const observer = new IntersectionObserver(schedule, { threshold: [0, 0.02, 0.98, 1] });
    for (const node of nodes) observer.observe(node);
    window.addEventListener("scroll", schedule, { passive: true });
    window.addEventListener("resize", schedule);
    schedule();

    return () => {
      if (frame) window.cancelAnimationFrame(frame);
      observer.disconnect();
      window.removeEventListener("scroll", schedule);
      window.removeEventListener("resize", schedule);
    };
  }, [loadState]);

  const dirty = useMemo(
    () => JSON.stringify(settings) !== JSON.stringify(savedSettings) || !sameChecks(checks, savedChecks),
    [settings, savedSettings, checks, savedChecks],
  );

  const atFactory = useMemo(
    () => JSON.stringify(settings) === JSON.stringify(DEFAULT_ANALYSIS_SETTINGS)
      && sameChecks(checks, analyzers.filter((item) => item.available !== false).map((item) => item.id)),
    [settings, checks, analyzers],
  );

  // The confirmation is a statement about the saved state, so it is derived
  // rather than stored: the moment anything is edited again it stops showing.
  const showSaved = saveState === "saved" && !dirty;

  function jumpTo(id: SectionId) {
    setActive(id);
    sectionRefs.current[id]?.scrollIntoView({
      behavior: prefersReducedMotion() ? "auto" : "smooth",
      block: "start",
    });
  }

  function toggleCheck(id: string) {
    if (analyzers.find((item) => item.id === id)?.available === false) return;
    setChecks((current) => (current.includes(id) ? current.filter((item) => item !== id) : [...current, id]));
  }

  function save() {
    try {
      window.localStorage.setItem(STORAGE_KEY, JSON.stringify({ ...settings, default_analyzers: checks }));
      setSavedSettings(settings);
      setSavedChecks(checks);
      setConfirmReset(false);
      setSaveState("saved");
    } catch {
      // Private-mode browsers refuse localStorage writes. Say so instead of
      // pretending the defaults were kept.
      setSaveState("failed");
    }
  }

  function requestReset() {
    if (dirty) setConfirmReset(true);
    else applyReset();
  }

  function applyReset() {
    setSettings(DEFAULT_ANALYSIS_SETTINGS);
    setChecks(analyzers.filter((item) => item.available !== false).map((item) => item.id));
    setConfirmReset(false);
    setSaveState("idle");
  }

  const sectionNav = (
    <nav className="settings-sectionnav" aria-label="Settings sections">
      {SECTIONS.map((section) => (
        <a
          key={section.id}
          href={`#${section.id}`}
          aria-current={active === section.id ? "true" : undefined}
          onClick={(event) => { event.preventDefault(); jumpTo(section.id); }}
        >
          {section.label}
        </a>
      ))}
    </nav>
  );

  return (
    <AppShell
      route="settings"
      chrome="rail"
      header={
        <RouteHeader
          eyebrow="Saved defaults"
          title="Settings"
          sub={
            <>
              These are the defaults that seed every new review. You can still override any of
              them for a single batch on the <NavLink href="/new">New review</NavLink> screen —
              those per-run changes are never written back here.
            </>
          }
          below={sectionNav}
        />
      }
    >
      <div className="settings-room">
        {/* ---- Default checks ---------------------------------- */}
        <section
          className="settings-panel"
          id="checks"
          aria-labelledby="checks-title"
          ref={(node) => { sectionRefs.current.checks = node; }}
        >
          <header className="settings-panel-head">
            <h2 id="checks-title">Default checks</h2>
            <p>Which detectors are switched on when you start a new review. Turn off anything your workflow never needs — you can still add it back for one batch.</p>
          </header>

          {loadState === "loading" && (
            <div className="settings-skeleton" role="status" aria-label="Loading available checks">
              {Array.from({ length: 5 }, (_, index) => (
                <div key={index} aria-hidden="true"><b /><span /><i /></div>
              ))}
            </div>
          )}

          {loadState === "error" && (
            <div className="feedback-toast error settings-error" role="alert">
              <span aria-hidden="true">!</span>
              <p>
                <strong>Cannot list the available checks</strong>
                The screening service is unreachable, so the check list could not be loaded. Your saved thresholds below are unaffected.
              </p>
              <button type="button" className="text-button" onClick={loadAnalyzers}>Retry</button>
            </div>
          )}

          {loadState === "ready" && (
            <>
              <div className="settings-checklist">
                {analyzers.map((analyzer) => {
                  const on = checks.includes(analyzer.id);
                  return (
                    <label className={`settings-check ${on ? "on" : "off"} ${analyzer.available === false ? "unavailable" : ""}`} key={analyzer.id} title={analyzer.availability_message || undefined}>
                      <span className="settings-check-copy">
                        <strong>{checkLabel(analyzer.id)}</strong>
                        <small>{analyzer.available === false ? analyzer.availability_message : analyzer.description}</small>
                      </span>
                      <span className="analyzer-switch">
                        <input type="checkbox" checked={on} disabled={analyzer.available === false} onChange={() => toggleCheck(analyzer.id)} />
                        <span aria-hidden="true"><i /></span>
                      </span>
                    </label>
                  );
                })}
              </div>
              <p className="settings-count">
                {checks.length} of {analyzers.length} checks on by default
                {checks.length === 0 && " — every new review will start with nothing selected."}
              </p>
            </>
          )}
        </section>

        {/* ---- Detection thresholds ---------------------------- */}
        <section
          className="settings-panel"
          id="thresholds"
          aria-labelledby="thresholds-title"
          ref={(node) => { sectionRefs.current.thresholds = node; }}
        >
          <header className="settings-panel-head">
            <h2 id="thresholds-title">Detection thresholds</h2>
            <p>How hard each detector looks, and the point at which a finding is worth a human&apos;s attention. Higher resolutions and wider searches find more but take longer. The switches here are the same ones as in Default checks above.</p>
          </header>

          {loadState === "ready" ? (
            <AdvancedSettings
              analyzers={analyzers}
              selected={checks}
              settings={settings}
              onToggleAnalyzer={toggleCheck}
              onChange={setSettings}
              onReset={requestReset}
              onBack={() => jumpTo("thresholds")}
            />
          ) : (
            <p className="settings-muted">
              {loadState === "error"
                ? "Threshold controls need the check list from the screening service. Retry above once it is running."
                : "Loading threshold controls…"}
            </p>
          )}
        </section>

        {/* ---- Screening tests (profiles) ---------------------- */}
        <section
          className="settings-panel"
          id="profiles"
          aria-labelledby="profiles-title"
          ref={(node) => { sectionRefs.current.profiles = node; }}
        >
          <header className="settings-panel-head">
            <h2 id="profiles-title">Screening tests</h2>
            <p>
              Named tests bundle a goal, a decision rule, and a set of checks and thresholds, so the
              same screening is applied the same way every time. Create one from{" "}
              <NavLink href="/new">New review</NavLink> once you have a setup you want to reuse; edit
              its wording or remove it here.
            </p>
          </header>

          {profiles.length === 0 ? (
            <p className="settings-muted">
              No screening tests saved yet. On the New review screen, choose your checks and
              thresholds, then “Create a test from this setup”.
            </p>
          ) : (
            <ul className="profile-manage-list">
              {profiles.map((profile) => (
                <li key={profile.id} className="profile-manage-item">
                  {editingProfileId === profile.id ? (
                    <div className="profile-manage-edit">
                      <label>
                        <span>Name</span>
                        <input
                          value={profileDraft.name}
                          maxLength={MAX_PROFILE_NAME}
                          onChange={(event) => setProfileDraft((draft) => ({ ...draft, name: event.target.value }))}
                        />
                      </label>
                      <label>
                        <span>Goal</span>
                        <textarea
                          value={profileDraft.goal}
                          maxLength={MAX_PROFILE_GOAL}
                          rows={2}
                          onChange={(event) => setProfileDraft((draft) => ({ ...draft, goal: event.target.value }))}
                        />
                      </label>
                      <label>
                        <span>Decision rule</span>
                        <textarea
                          value={profileDraft.guidance}
                          maxLength={MAX_PROFILE_GUIDANCE}
                          rows={3}
                          onChange={(event) => setProfileDraft((draft) => ({ ...draft, guidance: event.target.value }))}
                        />
                      </label>
                      {profileError && <p className="profile-manage-error" role="alert">{profileError}</p>}
                      <div className="profile-manage-actions">
                        <button type="button" className="text-button" onClick={() => setEditingProfileId(null)}>Cancel</button>
                        <button type="button" className="primary-button" onClick={() => saveProfileEdit(profile)}>Save changes</button>
                      </div>
                    </div>
                  ) : (
                    <>
                      <div className="profile-manage-copy">
                        <strong>{profile.name}</strong>
                        {profile.goal && <p className="profile-manage-goal">{profile.goal}</p>}
                        {profile.guidance && <p className="profile-manage-rule"><span>Decision rule</span>{profile.guidance}</p>}
                        <small className="profile-manage-checks">
                          {profile.analyzers.length} check{profile.analyzers.length === 1 ? "" : "s"}: {profile.analyzers.map(checkLabel).join(", ") || "none selected"}
                        </small>
                      </div>
                      <div className="profile-manage-buttons">
                        <button type="button" className="text-button" onClick={() => beginEditProfile(profile)}>Edit</button>
                        <button type="button" className="settings-danger-button" onClick={() => removeProfile(profile.id)}>Delete</button>
                      </div>
                    </>
                  )}
                </li>
              ))}
            </ul>
          )}
        </section>

        {/* ---- Session ----------------------------------------- */}
        <section
          className="settings-panel"
          id="session"
          aria-labelledby="session-title"
          ref={(node) => { sectionRefs.current.session = node; }}
        >
          <header className="settings-panel-head">
            <h2 id="session-title">Session</h2>
            <p>Parakh has no user accounts. The whole workspace is opened with one shared access code.</p>
          </header>

          <dl className="settings-facts">
            <div>
              <dt>Access code</dt>
              <dd>{session.required ? "Required to open this workspace." : "Not configured — this workspace is open to anyone who can reach it."}</dd>
            </div>
            <div>
              <dt>Screening service</dt>
              <dd className={serviceStatus === "offline" ? "is-offline" : undefined}>
                {serviceStatus === "online" ? "Reachable." : serviceStatus === "offline" ? "Unreachable — start the backend to run new reviews." : "Checking…"}
              </dd>
            </div>
          </dl>

          {session.required && (
            <div className="settings-signout">
              <div>
                <strong>Sign out of this device</strong>
                <small>Clears the access code from this browser. Saved defaults stay on this device.</small>
              </div>
              <button type="button" className="settings-danger-button" onClick={() => { void signOut(); }}>Sign out</button>
            </div>
          )}
        </section>

        {/* ---- About ------------------------------------------- */}
        <section
          className="settings-panel"
          id="about"
          aria-labelledby="about-title"
          ref={(node) => { sectionRefs.current.about = node; }}
        >
          <header className="settings-panel-head">
            <h2 id="about-title">About Parakh</h2>
            <p>A screening workspace for scanned PDF documents.</p>
          </header>
          <p className="settings-statement">
            These tools produce screening signals only. A flag is a reason to look, not a finding
            of forgery, and a clear result is not proof a document is genuine. Combine every
            result here with visual review and your own document-template checks before drawing a
            conclusion.
          </p>
        </section>

        {/* ---- Save bar ---------------------------------------- */}
        <div className="settings-savebar" role="region" aria-label="Save defaults">
          {confirmReset && (
            <div className="settings-confirm" role="alertdialog" aria-label="Discard unsaved changes">
              <p><strong>Discard your unsaved changes?</strong>Resetting restores the original checks and thresholds. Anything you have edited since your last save will be lost.</p>
              <div className="settings-confirm-actions">
                <button type="button" className="text-button" onClick={() => setConfirmReset(false)}>Keep editing</button>
                <button type="button" className="settings-danger-button" onClick={applyReset}>Reset anyway</button>
              </div>
            </div>
          )}

          {showSaved && (
            <div className="feedback-toast success" role="status">
              <span aria-hidden="true">✓</span>
              <p><strong>Defaults saved</strong>Every new review will start from these settings until you change them again.</p>
            </div>
          )}

          {saveState === "failed" && (
            <div className="feedback-toast error" role="alert">
              <span aria-hidden="true">!</span>
              <p><strong>Could not save</strong>This browser is blocking local storage, so your defaults will only last for this tab.</p>
            </div>
          )}

          <div className="settings-savebar-row">
            <p className="settings-savebar-state">
              {dirty
                ? "Unsaved changes — nothing applies to a new review until you save."
                : "Saved. These defaults seed each new review."}
            </p>
            <div className="settings-savebar-actions">
              <button type="button" className="text-button" onClick={requestReset} disabled={atFactory && !dirty}>
                Reset to defaults
              </button>
              <button type="button" className="primary-button" onClick={save} disabled={!dirty}>
                {dirty ? "Save defaults" : "Defaults saved"}
                {dirty && <span aria-hidden="true">→</span>}
              </button>
            </div>
          </div>
        </div>
      </div>
    </AppShell>
  );
}
