export type Analyzer = {
  id: string;
  description: string;
  available?: boolean;
  availability_message?: string | null;
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
  outcome: "clear" | "review" | "inconclusive" | "error";
  risk: "low" | "medium" | "high" | "unknown";
  summary: string;
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

export type Job = {
  id: string;
  filename: string;
  status: "queued" | "running" | "completed";
  analyzers: string[];
  analyzer_runs: Record<string, AnalyzerRun>;
  results: Record<string, NormalizedResult>;
  review?: { decision: string; notes: string; reviewed_at: string };
};

export type Batch = {
  id: string;
  created_at: string;
  status: "queued" | "running" | "completed";
  document_count: number;
  jobs: Job[];
};

export type BatchSummary = {
  id: string;
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
