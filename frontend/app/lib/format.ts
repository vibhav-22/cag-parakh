import { AnalysisSettings, DEFAULT_ANALYSIS_SETTINGS } from "../advanced-settings";
import type { Job, NormalizedResult } from "./types";

export const API_URL = process.env.NEXT_PUBLIC_API_URL || "";
export const MAX_BATCH_FILES = 50;

export function titleCase(value: string) {
  return value.replaceAll("_", " ").replace(/\b\w/g, (letter) => letter.toUpperCase());
}

export function analyzerLabel(value: string) {
  if (value === "tamper_scan") return "Whitener Detection";
  if (value === "photo_detection") return "Document Photo";
  return titleCase(value);
}

const SHORT_LABELS: Record<string, string> = {
  metadata: "Metadata",
  qr_presence: "QR",
  font_analysis: "Fonts",
  moire: "Moire",
  scanner_noise: "Scanner",
  same_phone: "Same Phone",
  tamper_scan: "Whitener",
  readability: "Readability",
  photo_detection: "Photo",
};

export function shortLabel(id: string) {
  return SHORT_LABELS[id] || analyzerLabel(id);
}

export function artifactLabel(path: string) {
  const filename = path.split("/").at(-1) || path;
  if (filename === "detected_photo.jpg") return "Open extracted photo ↗";
  if (filename.endsWith("_annotated.pdf")) return "Open annotated PDF ↗";
  const page = filename.match(/^page_(\d+)_annotated\.png$/)?.[1];
  return page ? `Open annotated page ${Number(page)} ↗` : "Open detector artifact ↗";
}

export function resultTone(result: NormalizedResult) {
  if (result.outcome === "error") return "danger";
  if (result.outcome === "review") return "warning";
  if (result.outcome === "inconclusive") return "neutral";
  return "good";
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
      scanner_noise: { ...DEFAULT_ANALYSIS_SETTINGS.scanner_noise, ...parsed.scanner_noise },
      same_phone: { ...DEFAULT_ANALYSIS_SETTINGS.same_phone, ...parsed.same_phone },
      tamper_scan: { ...DEFAULT_ANALYSIS_SETTINGS.tamper_scan, ...parsed.tamper_scan },
      readability: { ...DEFAULT_ANALYSIS_SETTINGS.readability, ...parsed.readability },
    };
  } catch {
    return DEFAULT_ANALYSIS_SETTINGS;
  }
}

// Status-cell states for the batch outcome matrix. Each pairs a glyph with a
// label so meaning never relies on color alone.
export const CELL_STATES = {
  good: { glyph: "✓", label: "Clear" },
  warning: { glyph: "!", label: "Needs review" },
  neutral: { glyph: "?", label: "Inconclusive" },
  danger: { glyph: "×", label: "Check error" },
  pending: { glyph: "·", label: "Pending" },
  running: { glyph: "…", label: "Running" },
  empty: { glyph: "–", label: "Not requested" },
} as const;
export type CellTone = keyof typeof CELL_STATES;

export function cellFor(job: Job, analyzer: string): { tone: CellTone; summary?: string } {
  if (!job.analyzers.includes(analyzer)) return { tone: "empty" };
  const result = job.results[analyzer];
  if (!result) {
    return { tone: job.analyzer_runs[analyzer]?.status === "running" ? "running" : "pending" };
  }
  const tone = resultTone(result) as CellTone;
  return { tone, summary: result.summary };
}

export function docVerdict(job: Job): { label: string; tone: string } {
  if (job.status !== "completed") {
    return { label: job.status === "running" ? "Analyzing" : "Queued", tone: "pending" };
  }
  const results = Object.values(job.results);
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
