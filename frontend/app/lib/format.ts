import { AnalysisSettings, DEFAULT_ANALYSIS_SETTINGS } from "../advanced-settings";
import type { Job, NormalizedResult } from "./types";

export const API_URL = process.env.NEXT_PUBLIC_API_URL || "";
export const MAX_BATCH_FILES = 50;

/**
 * Fetches a protected /api/ report and hands it to the browser as a real file
 * download, instead of navigating to it.
 *
 * A plain `<a href="/api/...">` is a browser navigation, not a fetch — it
 * cannot carry the x-parakh-launch header that installLaunchToken() attaches
 * by patching window.fetch (see launch-token.ts). In an installed build that
 * header is required, so the navigation 403s and replaces the whole app
 * window with the raw error JSON, with no way back (same problem
 * useAuthorizedImage works around for <img src>). Routing through fetch
 * carries the header; the resulting blob is what actually gets downloaded.
 */
export async function downloadReport(url: string, filename: string): Promise<void> {
  const response = await fetch(url);
  if (!response.ok) throw new Error(`Report download failed (${response.status})`);
  const blob = await response.blob();
  const objectUrl = URL.createObjectURL(blob);
  const link = document.createElement("a");
  link.href = objectUrl;
  link.download = filename;
  document.body.appendChild(link);
  link.click();
  link.remove();
  URL.revokeObjectURL(objectUrl);
}

/**
 * Fetches a protected /api/ report and opens it in a new tab for viewing.
 *
 * Same launch-token problem as downloadReport above, but for `target="_blank"`
 * links: those go through Electron's window-open handler, which only allows
 * navigation to the app's own origin (or now, blob: URLs — see main.cjs). A
 * direct link to the backend still 403s there for the same reason.
 */
export async function viewReport(url: string): Promise<void> {
  const response = await fetch(url);
  if (!response.ok) throw new Error(`Report load failed (${response.status})`);
  const blob = await response.blob();
  const objectUrl = URL.createObjectURL(blob);
  window.open(objectUrl, "_blank", "noopener,noreferrer");
}

export function titleCase(value: string) {
  return value.replaceAll("_", " ").replace(/\b\w/g, (letter) => letter.toUpperCase());
}

const ANALYZER_LABELS: Record<string, string> = {
  metadata: "Metadata Ledger",
  font_analysis: "Typeface Fingerprint",
  signature: "Signature Trace",
  photo_detection: "Photo Extraction",
  qr_presence: "QR Seal",
  tamper_scan: "Whitener Detection",
  moire: "Moiré Trace",
  same_phone: "Capture Match",
  readability: "Clarity Scan",
};

export function analyzerLabel(value: string) {
  return ANALYZER_LABELS[value] || titleCase(value);
}

const SHORT_LABELS: Record<string, string> = {
  metadata: "Ledger",
  qr_presence: "QR",
  font_analysis: "Fonts",
  moire: "Moiré",
  same_phone: "Capture",
  tamper_scan: "Whitener",
  readability: "Clarity",
  photo_detection: "Photo",
};

export function shortLabel(id: string) {
  return SHORT_LABELS[id] || analyzerLabel(id);
}

export function artifactLabel(path: string) {
  const filename = path.split("/").at(-1) || path;
  const detectedPhoto = filename.match(/^detected_photo(?:_(\d+))?\.jpe?g$/i);
  if (detectedPhoto) {
    const number = detectedPhoto[1] ? Number(detectedPhoto[1]) : 1;
    return `Open extracted photo ${number} ↗`;
  }
  if (filename.endsWith("annotated.pdf")) return "Open annotated PDF ↗";
  const page = filename.match(/^page_(\d+)_annotated\.png$/)?.[1];
  return page ? `Open annotated page ${Number(page)} ↗` : "Open detector artifact ↗";
}

export function resultTone(result: NormalizedResult) {
  if (result.outcome === "error") return "danger";
  if (result.outcome === "review") return "warning";
  if (result.outcome === "inconclusive" || result.outcome === "info") return "neutral";
  return "good";
}

/**
 * How each check result reads on screen.
 *
 * Five states, not two. A detector that could not run is not a failure, and a
 * check that reports provenance is not a pass — collapsing either into a cross
 * is exactly what makes a tick/cross grid impossible to read.
 */
export const CHECK_STATES = {
  pass: { glyph: "✓", label: "Pass", tone: "good" },
  fail: { glyph: "✕", label: "Fail", tone: "warning" },
  inconclusive: { glyph: "?", label: "Could not determine", tone: "neutral" },
  info: { glyph: "i", label: "Reported", tone: "info" },
  error: { glyph: "E", label: "Check error", tone: "danger" },
} as const;

export type CheckStatusKey = keyof typeof CHECK_STATES;

export function checkStatusOf(result: NormalizedResult | undefined): CheckStatusKey {
  const status = result?.check?.status;
  if (status && status in CHECK_STATES) return status as CheckStatusKey;
  // A result stored before criteria existed still has to render, so fall back
  // to the outcome it was saved with.
  if (!result) return "inconclusive";
  if (result.outcome === "error") return "error";
  if (result.outcome === "review") return "fail";
  if (result.outcome === "clear") return "pass";
  if (result.outcome === "info") return "info";
  return "inconclusive";
}

export function checkState(result: NormalizedResult | undefined) {
  return CHECK_STATES[checkStatusOf(result)];
}

// Checks that grade the document. Metadata reports how the file was made and
// takes no position, so it is excluded from every pass/fail total.
export function gradedResults(job: Job): NormalizedResult[] {
  return Object.values(job.results || {}).filter((result) => result.outcome !== "info");
}

export function riskTone(risk: NormalizedResult["risk"]) {
  if (risk === "high") return "danger";
  if (risk === "medium") return "warning";
  if (risk === "low") return "good";
  return "neutral";
}

export function evidenceLabel(key: string) {
  return titleCase(key).replace(/\bQr\b/g, "QR").replace(/\bDpi\b/g, "DPI").replace(/\bPdf\b/g, "PDF");
}

/** Human day label above the exact timestamp, so a scan down a list reads as
 *  time passing rather than as identical dates. Shared by /history and
 *  /projects — both list stored records newest-first. */
export function relativeDay(iso: string, now: number): string {
  const then = new Date(iso);
  if (Number.isNaN(then.getTime())) return "";
  const startOf = (date: Date) => new Date(date.getFullYear(), date.getMonth(), date.getDate()).getTime();
  const days = Math.round((startOf(new Date(now)) - startOf(then)) / 86_400_000);
  if (days <= 0) return "Today";
  if (days === 1) return "Yesterday";
  if (days < 7) return `${days} days ago`;
  if (days < 14) return "Last week";
  if (days < 60) return `${Math.round(days / 7)} weeks ago`;
  return `${Math.round(days / 30)} months ago`;
}

export function formatWhen(iso: string) {
  const date = new Date(iso);
  return Number.isNaN(date.getTime())
    ? ""
    : date.toLocaleString([], { month: "short", day: "numeric", hour: "numeric", minute: "2-digit" });
}

export const SETTINGS_STORAGE_KEY = "parakh-analysis-settings";

/**
 * The saved default check selection, written by /settings alongside the
 * thresholds. Returns null when the user has never saved defaults, which means
 * "every check on".
 *
 * Note for anyone writing this key: it holds MORE than `AnalysisSettings`.
 * `initialAnalysisSettings()` deliberately allow-lists detector keys, so a
 * naive read-modify-write round trip silently drops this field.
 */
export function storedDefaultAnalyzers(): string[] | null {
  if (typeof window === "undefined") return null;
  try {
    const raw = window.localStorage.getItem(SETTINGS_STORAGE_KEY);
    if (!raw) return null;
    const parsed = JSON.parse(raw) as { default_analyzers?: unknown };
    if (!Array.isArray(parsed.default_analyzers)) return null;
    return parsed.default_analyzers.filter((item): item is string => typeof item === "string");
  } catch {
    return null;
  }
}

export function initialAnalysisSettings(): AnalysisSettings {
  if (typeof window === "undefined") return DEFAULT_ANALYSIS_SETTINGS;
  try {
    const stored = window.localStorage.getItem("parakh-analysis-settings");
    if (!stored) return DEFAULT_ANALYSIS_SETTINGS;
    const parsed = JSON.parse(stored) as Partial<AnalysisSettings>;
    return {
      metadata: { ...DEFAULT_ANALYSIS_SETTINGS.metadata, ...parsed.metadata },
      qr_presence: { ...DEFAULT_ANALYSIS_SETTINGS.qr_presence, ...parsed.qr_presence },
      same_phone: { ...DEFAULT_ANALYSIS_SETTINGS.same_phone, ...parsed.same_phone },
      tamper_scan: { ...DEFAULT_ANALYSIS_SETTINGS.tamper_scan, ...parsed.tamper_scan },
      readability: { ...DEFAULT_ANALYSIS_SETTINGS.readability, ...parsed.readability },
      photo_detection: { ...DEFAULT_ANALYSIS_SETTINGS.photo_detection, ...parsed.photo_detection },
    };
  } catch {
    return DEFAULT_ANALYSIS_SETTINGS;
  }
}

// Status-cell states for the batch outcome matrix. Each pairs a glyph with a
// label so meaning never relies on color alone. The five result states come
// from the check itself; these three are lifecycle, not verdict.
export const CELL_STATES = {
  good: { glyph: "✓", label: "Pass" },
  warning: { glyph: "✕", label: "Fail" },
  neutral: { glyph: "?", label: "Could not determine" },
  info: { glyph: "i", label: "Reported, not graded" },
  danger: { glyph: "E", label: "Check error" },
  pending: { glyph: "·", label: "Pending" },
  running: { glyph: "…", label: "Running" },
  empty: { glyph: "–", label: "Not requested" },
} as const;
export type CellTone = keyof typeof CELL_STATES;

export type MatrixCell = {
  tone: CellTone;
  summary?: string;
  glyph?: string;
  label?: string;
  actual?: string;
};

export function cellFor(job: Job, analyzer: string): MatrixCell {
  if (!job.analyzers.includes(analyzer)) return { tone: "empty" };
  const result = job.results[analyzer];
  if (!result) {
    return { tone: job.analyzer_runs[analyzer]?.status === "running" ? "running" : "pending" };
  }
  const state = checkState(result);
  return {
    tone: state.tone as CellTone,
    glyph: state.glyph,
    label: state.label,
    summary: result.check?.reason || result.summary,
    actual: result.check?.criterion,
  };
}

export function docVerdict(job: Job): { label: string; tone: string } {
  // A document a reviewer has declared unscreenable is not a pending result and
  // not a finding. It is closed, and it must leave the flagged queue.
  if (job.unanalyzable) return { label: "Unanalyzable", tone: "neutral" };
  if (job.status !== "completed") {
    return { label: job.status === "running" ? "Analyzing" : "Queued", tone: "pending" };
  }
  const results = gradedResults(job);
  if (results.some((item) => item.outcome === "review")) return { label: "Needs review", tone: "warning" };
  if (results.some((item) => item.outcome === "error")) return { label: "Check errors", tone: "danger" };
  if (results.length > 0 && results.every((item) => item.outcome === "clear")) return { label: "Clear", tone: "good" };
  return { label: "Inconclusive", tone: "neutral" };
}

// True when the document carries at least one signal a reviewer must look at.
// Drives the "next flagged" walk through a batch.
export function isFlagged(job: Job): boolean {
  const tone = docVerdict(job).tone;
  return tone === "warning" || tone === "danger";
}

// One-line reading of the verdict for the reveal anchor, phrased for the
// reviewer rather than the machine.
export function verdictSubline(
  counts: { completed: number; clear: number; review: number; errors: number },
  tone: string,
): string {
  if (tone === "good") return `All ${counts.completed} checks clear. No tampering signals found.`;
  if (tone === "warning") return `${counts.review} ${counts.review === 1 ? "check" : "checks"} flagged for review${counts.errors ? `, ${counts.errors} could not complete` : ""}. Examine the marked evidence.`;
  if (tone === "danger") return `${counts.errors} ${counts.errors === 1 ? "check" : "checks"} could not complete. Re-run to confirm the result.`;
  return "Screening was inconclusive. Review the marked evidence and record a decision.";
}

// The four calls a reviewer can record. `escalated` exists because a reviewer
// who is not the right person to decide needs somewhere to put the case that is
// not "needs investigation" — that is a finding about the document, not a
// handoff.
export const DECISION_OPTIONS = [
  { value: "verified", label: "Verified", hint: "Accepted. No further action on this document." },
  { value: "needs_investigation", label: "Needs investigation", hint: "Flagged for follow-up with the evidence on record." },
  { value: "inconclusive", label: "Inconclusive", hint: "The evidence does not settle it either way." },
  { value: "escalated", label: "Escalate", hint: "Hand to a second reviewer and name them." },
] as const;

export function decisionLabel(value: string | undefined) {
  return DECISION_OPTIONS.find((option) => option.value === value)?.label
    || (value ? titleCase(value) : "Not recorded");
}

// Checks that ran and could not finish. These are the retry candidates: a
// detector crash is not a finding about the document.
export function failedAnalyzers(job: Job): string[] {
  return Object.entries(job.analyzer_runs || {})
    .filter(([, run]) => run?.status === "failed")
    .map(([name]) => name);
}

// The file extension as a short, uppercase type tag for the batch matrix.
export function documentTypeLabel(filename: string) {
  const ext = filename.split(".").pop();
  return ext && ext !== filename ? ext.toUpperCase() : "FILE";
}

// A composite authenticity read: the share of completed checks that came back
// clear. Not a probability of forgery — a plain count, presented as a bar so a
// reviewer scanning fifty rows can spot the low ones without reading numbers.
export function authenticityPercent(job: Job): number | null {
  const results = gradedResults(job);
  if (results.length === 0) return null;
  const clear = results.filter((item) => item.outcome === "clear").length;
  return Math.round((clear / results.length) * 100);
}

// Short labels for the checks that actually flagged something on this
// document, for the "Forensic Signals" chips. Real detector output only —
// nothing here is inferred.
export function flaggedSignalLabels(job: Job): string[] {
  return Object.entries(job.results || {})
    .filter(([, result]) => result.outcome === "review" || result.outcome === "error")
    .map(([analyzer]) => shortLabel(analyzer));
}

// Looks for a detector-reported confidence-like number (0–1) in a result's raw
// output. Several detectors surface one under different keys; none is
// fabricated here — the bar is only shown when a real value is found.
export function resultConfidencePercent(result: NormalizedResult): number | null {
  const raw = result.raw || {};
  for (const key of ["confidence", "score", "probability", "similarity"]) {
    const value = raw[key];
    if (typeof value === "number" && Number.isFinite(value)) {
      return Math.round((value <= 1 ? value * 100 : value));
    }
  }
  return null;
}

export function formatBytes(bytes: number) {
  if (bytes < 1024) return `${bytes} B`;
  if (bytes < 1024 * 1024) return `${(bytes / 1024).toFixed(0)} KB`;
  return `${(bytes / 1024 / 1024).toFixed(2)} MB`;
}

/**
 * Promote the checks and thresholds an actual run used into the saved defaults.
 *
 * This is the inversion of profile-first setup: nobody can write a useful
 * screening rule before they have seen what the output looks like, so the
 * profile is captured from a run that already happened. /settings still owns the
 * key; this writes through the same shape so a round trip cannot drop
 * `default_analyzers`.
 */
export function saveRunAsDefaults(analyzers: string[], settings: AnalysisSettings): boolean {
  if (typeof window === "undefined") return false;
  try {
    window.localStorage.setItem(
      SETTINGS_STORAGE_KEY,
      JSON.stringify({ ...settings, default_analyzers: analyzers }),
    );
    return true;
  } catch {
    return false;
  }
}

// Keys hidden from the generic evidence table: internals, blobs, and values
// already surfaced by the verdict line or the outcome/risk chips.
const HIDDEN_EVIDENCE_KEYS = new Set([
  "artifacts", "regions", "report", "detail", "hits", "value", "input", "timing",
  "status", "passed", "risk", "verdict", "file_verdict", "exit_code", "feature",
  "pdf_path", "file", "outputs", "pages", "tests", "score", "tests_passed", "tests_total",
]);

export type RawTest = { name?: string; passed?: boolean; value?: string; threshold?: string };

export function evidenceEntries(raw: Record<string, unknown>): Array<[string, string]> {
  const rows: Array<[string, string]> = [];
  const push = (key: string, value: unknown) => {
    if (rows.length >= 10) return;
    if (typeof value === "string" && value.trim() && value.length <= 120) rows.push([key, value]);
    else if (typeof value === "number") rows.push([key, key.includes("probability") ? `${Math.round(value * 100)}%` : String(value)]);
    else if (typeof value === "boolean") rows.push([key, String(value)]);
  };
  for (const [key, value] of Object.entries(raw)) {
    if (HIDDEN_EVIDENCE_KEYS.has(key)) continue;
    if (key === "summary" && value && typeof value === "object" && !Array.isArray(value)) {
      for (const [inner, innerValue] of Object.entries(value as Record<string, unknown>)) push(inner, innerValue);
      continue;
    }
    push(key, value);
  }
  return rows;
}
