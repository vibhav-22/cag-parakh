export type Analyzer = {
  id: string;
  description: string;
  // What earns this check a tick. Fixed per detector, so it can be stated on the
  // setup screen before any document has been chosen.
  criterion?: string;
  available?: boolean;
  availability_message?: string | null;
};

export type CheckStatus = "pass" | "fail" | "inconclusive" | "info" | "error";

/**
 * A detector's own pass/fail rule applied to one document.
 *
 * `criterion` is the same sentence for every document; `reason` is why this one
 * landed where it did. For an inconclusive result `reason` says why the check
 * could not reach a conclusion — "only one page was analysed", "the image was
 * too small" — which is the whole point of keeping inconclusive separate from a
 * cross.
 */
export type CheckVerdict = {
  status: CheckStatus;
  criterion: string;
  reason: string;
  facts: Array<{ label: string; value: string }>;
};

export type EvidenceRegion = {
  page: number;
  kind: string;
  label: string;
  message: string;
  severity: "low" | "medium" | "high" | "unknown";
  x: number;
  y: number;
  width: number;
  height: number;
};

export type NormalizedResult = {
  analyzer_id: string;
  // `info` is a check that reports facts and grades nothing — metadata
  // provenance today. It never counts as clear or as flagged.
  outcome: "clear" | "review" | "inconclusive" | "error" | "info";
  risk: "low" | "medium" | "high" | "unknown";
  summary: string;
  // Absent only on results stored before per-detector criteria existed.
  check?: CheckVerdict | null;
  findings_count: number;
  artifacts: string[];
  regions: EvidenceRegion[];
  exit_code: number | null;
  raw: Record<string, unknown>;
};

export type AnalyzerRun = {
  analyzer_id: string;
  status: "queued" | "running" | "completed" | "failed";
  queued_at: string;
  started_at: string | null;
  completed_at: string | null;
  result: NormalizedResult | null;
  error: string | null;
};

export type ReviewDecision = "verified" | "needs_investigation" | "inconclusive" | "escalated";

export type Review = {
  decision: ReviewDecision;
  notes: string;
  reviewed_at: string;
  reviewer?: string;
  assigned_to?: string;
  app_version?: string;
  detectors_version?: string;
};

export type Job = {
  id: string;
  filename: string;
  status: "queued" | "running" | "completed";
  analyzers: string[];
  // The thresholds this run actually used. Sent back so a completed batch can be
  // promoted into the saved defaults, and so a verdict stays reproducible.
  settings?: Record<string, Record<string, unknown>>;
  analyzer_runs: Record<string, AnalyzerRun>;
  results: Record<string, NormalizedResult>;
  review?: Review;
  // SHA-256 of the stored bytes. Identifies a re-submitted file, never the
  // underlying document — two photos of one certificate hash differently.
  sha256?: string | null;
  unanalyzable?: boolean;
  unanalyzable_reason?: string | null;
  // The named screening test this run was started under. Null for an ad-hoc run.
  // Intent only — what earns a tick belongs to the detector, not to the test.
  profile?: {
    id?: string | null;
    name: string;
    goal?: string;
    guidance?: string;
  } | null;
};

export type PriorScreening = {
  job_id: string;
  batch_id: string | null;
  filename: string;
  created_at: string;
  machine_verdict: string;
  review_decision: string | null;
};

export type PreflightFile = {
  filename: string;
  size_bytes: number;
  sha256: string | null;
  readable: boolean;
  page_count: number | null;
  blocker: string | null;
  warning: string | null;
  prior_screenings: PriorScreening[];
};

export type PreflightReport = {
  files: PreflightFile[];
  duplicate_count: number;
  blocked_count: number;
};

export type Batch = {
  id: string;
  // User-entered at intake, or generated from the batch's documents when left
  // blank. Always non-empty.
  name: string;
  created_at: string;
  status: "queued" | "running" | "completed";
  document_count: number;
  jobs: Job[];
};

export type BatchSummary = {
  id: string;
  name: string;
  created_at: string;
  status: "queued" | "running" | "completed";
  document_count: number;
  completed_documents: number;
  flagged_documents: number;
};

export type Session = { required: boolean; authenticated: boolean };

export type DocumentManifest = {
  page_count: number;
  pages: Array<{ page: number; width: number; height: number }>;
};

export type DisplayRegion = EvidenceRegion & { analyzer_id: string; marker: number };

export type ServiceStatus = "checking" | "online" | "offline";

export type VLMStatus = {
  enabled: boolean;
  configured: boolean;
  ready: boolean;
  model: string | null;
  message: string;
};

export type DocumentAnswer = {
  answer: string;
  confidence: "low" | "medium" | "high";
  citations: Array<{ page: number; evidence: string }>;
  limitations: string[];
  retrieved_pages: number[];
  model: string;
};

export type VLMDocument = {
  id: string;
  filename: string;
  created_at: string;
  page_count: number;
};
