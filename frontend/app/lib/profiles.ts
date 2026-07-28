import { AnalysisSettings, DEFAULT_ANALYSIS_SETTINGS } from "../advanced-settings";

/**
 * A screening profile — a named, reusable test.
 *
 * The flowchart's "Create screening profile → name test + define goal +
 * decision rules" step. In this product a profile's "decision rules" are three
 * concrete things, never an auto-verdict engine (there is deliberately no
 * aggregate suspicion score in Parakh, so inventing one here would be wrong):
 *
 *   - `analyzers` — which checks run.
 *   - `settings`  — the per-detector thresholds that decide when a check flags.
 *   - `guidance`  — the reviewer's own written rule for reading the result,
 *                   carried to the workspace and the exported report so the same
 *                   rule is applied to every document screened under this test.
 *
 * What earns a check a tick is deliberately NOT here. Whether a missing
 * signature is a pass is a property of the signature detector, not of a batch,
 * so that rule lives once in `backend/checks.py` and is the same everywhere.
 *
 * Profiles live in localStorage under their own key, separate from the single
 * saved-defaults blob at `parakh-analysis-settings`, so the two never collide.
 */
export type ScreeningProfile = {
  id: string;
  name: string;
  goal: string;
  guidance: string;
  analyzers: string[];
  settings: AnalysisSettings;
  created_at: string;
  updated_at: string;
};

/** The subset stamped onto a run so the case remembers the test it was screened under. */
export type ProfileStamp = {
  id: string;
  name: string;
  goal: string;
  guidance: string;
};

export const PROFILES_STORAGE_KEY = "parakh-screening-profiles";

export const MAX_PROFILE_NAME = 80;
export const MAX_PROFILE_GOAL = 400;
export const MAX_PROFILE_GUIDANCE = 2000;

function newId() {
  if (typeof crypto !== "undefined" && "randomUUID" in crypto) return crypto.randomUUID();
  return `p-${Date.now().toString(36)}-${Math.random().toString(36).slice(2, 8)}`;
}

function coerceSettings(value: unknown): AnalysisSettings {
  const partial = (value && typeof value === "object" ? value : {}) as Partial<AnalysisSettings>;
  // Merge over the defaults so a profile saved before a detector gained a knob
  // still opens with every field present.
  return {
    metadata: { ...DEFAULT_ANALYSIS_SETTINGS.metadata, ...partial.metadata },
    qr_presence: { ...DEFAULT_ANALYSIS_SETTINGS.qr_presence, ...partial.qr_presence },
    scanner_noise: { ...DEFAULT_ANALYSIS_SETTINGS.scanner_noise, ...partial.scanner_noise },
    same_phone: { ...DEFAULT_ANALYSIS_SETTINGS.same_phone, ...partial.same_phone },
    tamper_scan: { ...DEFAULT_ANALYSIS_SETTINGS.tamper_scan, ...partial.tamper_scan },
    readability: { ...DEFAULT_ANALYSIS_SETTINGS.readability, ...partial.readability },
  };
}

function coerceProfile(value: unknown): ScreeningProfile | null {
  if (!value || typeof value !== "object") return null;
  const raw = value as Record<string, unknown>;
  if (typeof raw.id !== "string" || typeof raw.name !== "string") return null;
  const analyzers = Array.isArray(raw.analyzers)
    ? raw.analyzers.filter((item): item is string => typeof item === "string")
    : [];
  const now = new Date().toISOString();
  return {
    id: raw.id,
    name: raw.name.slice(0, MAX_PROFILE_NAME),
    goal: typeof raw.goal === "string" ? raw.goal.slice(0, MAX_PROFILE_GOAL) : "",
    guidance: typeof raw.guidance === "string" ? raw.guidance.slice(0, MAX_PROFILE_GUIDANCE) : "",
    analyzers,
    settings: coerceSettings(raw.settings),
    created_at: typeof raw.created_at === "string" ? raw.created_at : now,
    updated_at: typeof raw.updated_at === "string" ? raw.updated_at : now,
  };
}

export function listProfiles(): ScreeningProfile[] {
  if (typeof window === "undefined") return [];
  try {
    const raw = window.localStorage.getItem(PROFILES_STORAGE_KEY);
    if (!raw) return [];
    const parsed = JSON.parse(raw);
    if (!Array.isArray(parsed)) return [];
    return parsed
      .map(coerceProfile)
      .filter((item): item is ScreeningProfile => item !== null)
      .sort((a, b) => a.name.localeCompare(b.name));
  } catch {
    return [];
  }
}

export function getProfile(id: string): ScreeningProfile | null {
  return listProfiles().find((item) => item.id === id) || null;
}

function writeAll(profiles: ScreeningProfile[]): boolean {
  if (typeof window === "undefined") return false;
  try {
    window.localStorage.setItem(PROFILES_STORAGE_KEY, JSON.stringify(profiles));
    return true;
  } catch {
    return false;
  }
}

/**
 * Create a profile, or update the one whose id is supplied. Returns the stored
 * profile, or null when the browser refuses localStorage (private mode).
 */
export function saveProfile(input: {
  id?: string;
  name: string;
  goal: string;
  guidance: string;
  analyzers: string[];
  settings: AnalysisSettings;
}): ScreeningProfile | null {
  const now = new Date().toISOString();
  const profiles = listProfiles();
  const existing = input.id ? profiles.find((item) => item.id === input.id) : undefined;
  const profile: ScreeningProfile = {
    id: existing?.id || input.id || newId(),
    name: input.name.trim().slice(0, MAX_PROFILE_NAME) || "Untitled test",
    goal: input.goal.trim().slice(0, MAX_PROFILE_GOAL),
    guidance: input.guidance.trim().slice(0, MAX_PROFILE_GUIDANCE),
    analyzers: [...input.analyzers],
    settings: coerceSettings(input.settings),
    created_at: existing?.created_at || now,
    updated_at: now,
  };
  const next = existing
    ? profiles.map((item) => (item.id === profile.id ? profile : item))
    : [...profiles, profile];
  return writeAll(next) ? profile : null;
}

export function deleteProfile(id: string): boolean {
  return writeAll(listProfiles().filter((item) => item.id !== id));
}

export function profileStamp(profile: ScreeningProfile): ProfileStamp {
  return {
    id: profile.id,
    name: profile.name,
    goal: profile.goal,
    guidance: profile.guidance,
  };
}
