"use client";

import { FormEvent, useEffect, useMemo, useState } from "react";

type Analyzer = { id: string; description: string };
type EvidenceRegion = {
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
type NormalizedResult = {
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
type AnalyzerRun = {
  analyzer_id: string;
  status: "queued" | "running" | "completed" | "failed";
  queued_at: string;
  started_at: string | null;
  completed_at: string | null;
  result: NormalizedResult | null;
  error: string | null;
};
type Job = {
  id: string;
  filename: string;
  status: "queued" | "running" | "completed";
  analyzers: string[];
  analyzer_runs: Record<string, AnalyzerRun>;
  results: Record<string, NormalizedResult>;
  review?: { decision: string; notes: string; reviewed_at: string };
};
type DocumentManifest = {
  page_count: number;
  pages: Array<{ page: number; width: number; height: number }>;
};
type DisplayRegion = EvidenceRegion & { analyzer_id: string; marker: number };

const API_URL = process.env.NEXT_PUBLIC_API_URL || "http://127.0.0.1:8000";

function titleCase(value: string) {
  return value.replaceAll("_", " ").replace(/\b\w/g, (letter) => letter.toUpperCase());
}

function resultTone(result: NormalizedResult) {
  if (result.outcome === "error") return "danger";
  if (result.outcome === "review") return "warning";
  if (result.outcome === "inconclusive") return "neutral";
  return "good";
}

function resultSummary(result: NormalizedResult) {
  return result.summary;
}

export default function Home() {
  const [analyzers, setAnalyzers] = useState<Analyzer[]>([]);
  const [selected, setSelected] = useState<string[]>([]);
  const [file, setFile] = useState<File | null>(null);
  const [job, setJob] = useState<Job | null>(null);
  const [error, setError] = useState("");
  const [submitting, setSubmitting] = useState(false);
  const [decision, setDecision] = useState("verified");
  const [notes, setNotes] = useState("");
  const [reviewSaved, setReviewSaved] = useState(false);
  const [sidebarOpen, setSidebarOpen] = useState(true);
  const [documentManifest, setDocumentManifest] = useState<DocumentManifest | null>(null);
  const [documentError, setDocumentError] = useState("");
  const [currentPage, setCurrentPage] = useState(1);
  const [zoom, setZoom] = useState(100);
  const [activeMarker, setActiveMarker] = useState<number | null>(null);
  const jobId = job?.id;
  const jobStatus = job?.status;

  useEffect(() => {
    fetch(`${API_URL}/api/v1/analyzers`)
      .then((response) => {
        if (!response.ok) throw new Error("Backend is unavailable");
        return response.json();
      })
      .then((data: { analyzers: Analyzer[] }) => {
        setAnalyzers(data.analyzers);
        setSelected(data.analyzers.map((item) => item.id));
      })
      .catch(() => setError("Start the backend at http://127.0.0.1:8000, then refresh this page."));
  }, []);

  useEffect(() => {
    if (!jobId || jobStatus === "completed") return;
    const timer = window.setInterval(async () => {
      const response = await fetch(`${API_URL}/api/v1/jobs/${jobId}`);
      if (response.ok) setJob(await response.json());
    }, 1800);
    return () => window.clearInterval(timer);
  }, [jobId, jobStatus]);

  useEffect(() => {
    if (!jobId) return;
    let cancelled = false;
    setDocumentManifest(null);
    setDocumentError("");
    setCurrentPage(1);
    setZoom(100);
    setActiveMarker(null);
    fetch(`${API_URL}/api/v1/jobs/${jobId}/document/manifest`)
      .then((response) => {
        if (!response.ok) throw new Error("Document preview is unavailable");
        return response.json();
      })
      .then((manifest: DocumentManifest) => {
        if (!cancelled) setDocumentManifest(manifest);
      })
      .catch(() => {
        if (!cancelled) setDocumentError("The document could not be displayed in the review workspace.");
      });
    return () => { cancelled = true; };
  }, [jobId]);

  const counts = useMemo(() => {
    const values = Object.values(job?.results || {});
    return {
      completed: values.length,
      clear: values.filter((item) => resultTone(item) === "good").length,
      review: values.filter((item) => resultTone(item) === "warning").length,
      errors: values.filter((item) => resultTone(item) === "danger").length,
    };
  }, [job]);

  const progressPercent = job ? Math.round((counts.completed / Math.max(1, job.analyzers.length)) * 100) : 0;
  const activeAnalyzer = job && job.status !== "completed"
    ? job.analyzers[Math.min(counts.completed, job.analyzers.length - 1)]
    : null;
  const regions = useMemo<DisplayRegion[]>(() => {
    let marker = 0;
    return Object.entries(job?.results || {}).flatMap(([analyzerId, result]) =>
      (result.regions || []).map((region) => ({ ...region, analyzer_id: analyzerId, marker: ++marker })),
    );
  }, [job]);
  const pageRegions = regions.filter((region) => region.page === currentPage);
  const currentPageInfo = documentManifest?.pages.find((page) => page.page === currentPage);

  async function submit(event: FormEvent) {
    event.preventDefault();
    if (!file || selected.length === 0) return;
    setError("");
    setSubmitting(true);
    setReviewSaved(false);
    const body = new FormData();
    body.append("file", file);
    try {
      const response = await fetch(`${API_URL}/api/v1/jobs?analyzers=${encodeURIComponent(selected.join(","))}`, { method: "POST", body });
      const payload = await response.json();
      if (!response.ok) throw new Error(payload.detail || "Unable to start analysis");
      setJob(payload);
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : "Unable to start analysis");
    } finally {
      setSubmitting(false);
    }
  }

  async function saveReview() {
    if (!job) return;
    const response = await fetch(`${API_URL}/api/v1/jobs/${job.id}/review`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ decision, notes }),
    });
    if (response.ok) {
      const review = await response.json();
      setJob({ ...job, review });
      setReviewSaved(true);
    }
  }

  function toggleAnalyzer(id: string) {
    setSelected((current) => current.includes(id) ? current.filter((item) => item !== id) : [...current, id]);
  }

  return (
    <main>
      <header className="topbar">
        <button className="sidebar-toggle" type="button" aria-label={sidebarOpen ? "Close analysis sidebar" : "Open analysis sidebar"} aria-expanded={sidebarOpen} onClick={() => setSidebarOpen((open) => !open)}>
          <span /><span /><span />
        </button>
        <div className="brand-mark">DS</div>
        <div className="brand-copy">
          <h1>Document Suspicion System</h1>
          <p>Audit review workspace</p>
        </div>
        <div className="system-state"><span /> Screening service available</div>
      </header>

      <section className={`workspace ${sidebarOpen ? "sidebar-open" : "sidebar-closed"}`}>
        <aside className="control-panel">
          <div className="section-heading">
            <div><h2>New document review</h2><p>Upload one PDF and select the checks required for this case.</p></div>
          </div>

          <form onSubmit={submit}>
            <div className="field-label"><span>Document</span><small>PDF, up to 25 MB</small></div>
            <label className={`dropzone ${file ? "has-file" : ""}`}>
              <input type="file" accept="application/pdf,.pdf" onChange={(event) => setFile(event.target.files?.[0] || null)} />
              <span className="upload-icon" aria-hidden="true">+</span>
              {file ? <><strong>{file.name}</strong><small>{(file.size / 1024 / 1024).toFixed(2)} MB, ready for screening</small></> : <><strong>Choose a PDF document</strong><small>or drag and drop it here</small></>}
            </label>
            {submitting && <div className="upload-feedback" role="status"><div><span>Uploading document</span><small>Preparing secure analysis</small></div><i><b /></i></div>}

            <div className="analyzer-heading">
              <div><h3>Screening checks</h3><span>{selected.length} of {analyzers.length} selected</span></div>
              <button type="button" className="text-button" onClick={() => setSelected(selected.length === analyzers.length ? [] : analyzers.map((item) => item.id))}>{selected.length === analyzers.length ? "Clear all" : "Select all"}</button>
            </div>
            <div className="analyzer-list">
              {analyzers.length === 0 && !error && Array.from({ length: 5 }, (_, index) => <div className="analyzer-skeleton" aria-hidden="true" key={index}><i /><span><b /><small /></span></div>)}
              {analyzers.map((analyzer) => (
                <label className="analyzer-row" key={analyzer.id}>
                  <input type="checkbox" checked={selected.includes(analyzer.id)} onChange={() => toggleAnalyzer(analyzer.id)} />
                  <span className="checkmark">✓</span>
                  <span><strong>{titleCase(analyzer.id)}</strong><small>{analyzer.description}</small></span>
                </label>
              ))}
            </div>
            {error && <div className="feedback-toast error" role="alert"><span aria-hidden="true">!</span><p><strong>Unable to continue</strong>{error}</p></div>}
            <button className="primary-button" disabled={!file || selected.length === 0 || submitting}>{submitting ? "Submitting..." : "Run document analysis"}<span aria-hidden="true">→</span></button>
          </form>
        </aside>

        <section className="review-panel">
          {!job ? (
            <div className="empty-state">
              <div className="document-ghost" aria-hidden="true"><strong>PDF</strong><span /><span /><span /><span /></div>
              <h2>No review is open</h2>
              <p>Choose a PDF and run the required checks. This workspace will keep the source document, screening evidence, and final decision together.</p>
              <div className="empty-guidance" aria-label="Review workflow"><span><b>1</b> Select a document</span><span><b>2</b> Run checks</span><span><b>3</b> Record a decision</span></div>
            </div>
          ) : (
            <div className="job-view">
              <div className="job-header">
                <div><p className="case-reference">Case reference <strong>{job.id.slice(0, 8)}</strong></p><h2>{job.filename}</h2><p>{job.status === "completed" ? "Analysis complete" : "Screening in progress. This page updates automatically."}</p></div>
                <span className={`status-pill ${job.status}`}><span className="status-copy" key={job.status}>{job.status === "running" ? "Analyzing" : titleCase(job.status)}</span></span>
              </div>

              <div className="metrics" aria-label="Analysis summary">
                <div><span>Checks completed</span><strong>{counts.completed}<small>/{job.analyzers.length}</small></strong></div>
                <div><span>Clear</span><strong className="good-text">{counts.clear}</strong></div>
                <div><span>Needs review</span><strong className="warning-text">{counts.review}</strong></div>
                <div><span>Errors</span><strong className="danger-text">{counts.errors}</strong></div>
              </div>

              {job.status !== "completed" && <div className="progress" aria-label={`${progressPercent}% complete`}><span style={{ width: `${Math.max(8, progressPercent)}%` }} /></div>}

              <div className="case-grid">
                <div className="results-column">
                  <div className="panel-title"><h3>Screening results</h3><span>{Object.keys(job.results).length} reports</span></div>
                  <div className="result-list">
                    {job.analyzers.map((name) => {
                      const result = job.results[name];
                      const run = job.analyzer_runs[name];
                      if (!result) return <div className="result-card pending" key={name}><span className="result-dot" /><div><strong>{titleCase(name)}</strong><small>{run?.status === "running" ? "Running now" : "Waiting to run"}</small></div></div>;
                      const tone = resultTone(result);
                      const artifacts = Array.isArray(result.artifacts) ? result.artifacts as string[] : [];
                      return (
                        <details className={`result-card ${tone}`} key={name}>
                          <summary><span className="result-dot" /><div><strong>{titleCase(name)}</strong><small>{resultSummary(result)}</small></div><span className="expand">+</span></summary>
                          <div className="result-detail"><div className="result-detail-inner">
                            {artifacts.map((artifact) => <a className="artifact-link" key={artifact} href={`${API_URL}/api/v1/jobs/${job.id}/artifacts/${name}/${artifact}`} target="_blank" rel="noreferrer">Open visual report ↗</a>)}
                            {typeof result.raw.report === "string" ? <pre>{result.raw.report}</pre> : <pre>{JSON.stringify(result.raw, null, 2)}</pre>}
                          </div></div>
                        </details>
                      );
                    })}
                  </div>
                </div>

                <div className="evidence-column">
                  <div className="panel-title">
                    <h3>Annotated document</h3>
                    <div className="viewer-title-actions">
                      <span>{regions.length} marked {regions.length === 1 ? "area" : "areas"}</span>
                      <a href={`${API_URL}/api/v1/jobs/${job.id}/document`} target="_blank" rel="noreferrer">Open full document ↗</a>
                    </div>
                  </div>
                  <div className="document-viewer">
                    <div className="viewer-toolbar" aria-label="Document viewer controls">
                      <div className="page-controls">
                        <button type="button" aria-label="Previous page" disabled={currentPage <= 1} onClick={() => { setCurrentPage((page) => Math.max(1, page - 1)); setActiveMarker(null); }}>‹</button>
                        <label>Page
                          <select value={currentPage} onChange={(event) => { setCurrentPage(Number(event.target.value)); setActiveMarker(null); }}>
                            {documentManifest?.pages.map((page) => <option value={page.page} key={page.page}>{page.page}</option>)}
                          </select>
                        </label>
                        <span>of {documentManifest?.page_count || "–"}</span>
                        <button type="button" aria-label="Next page" disabled={!documentManifest || currentPage >= documentManifest.page_count} onClick={() => { setCurrentPage((page) => Math.min(documentManifest?.page_count || page, page + 1)); setActiveMarker(null); }}>›</button>
                      </div>
                      <div className="zoom-controls">
                        <button type="button" aria-label="Zoom out" disabled={zoom <= 75} onClick={() => setZoom((value) => Math.max(75, value - 25))}>−</button>
                        <button type="button" className="zoom-value" aria-label="Reset document zoom" onClick={() => setZoom(100)}>{zoom}%</button>
                        <button type="button" aria-label="Zoom in" disabled={zoom >= 200} onClick={() => setZoom((value) => Math.min(200, value + 25))}>+</button>
                      </div>
                    </div>
                    <div className="document-stage">
                      {documentError ? (
                        <div className="document-message" role="alert"><strong>Preview unavailable</strong><span>{documentError}</span></div>
                      ) : currentPageInfo ? (
                        <div className="document-page" style={{ width: `${zoom}%`, aspectRatio: `${currentPageInfo.width} / ${currentPageInfo.height}` }}>
                          <img src={`${API_URL}/api/v1/jobs/${job.id}/document/pages/${currentPage}.png?dpi=144`} alt={`Page ${currentPage} of ${job.filename}`} draggable={false} />
                          <div className="annotation-layer" aria-label={`${pageRegions.length} marked areas on page ${currentPage}`}>
                            {pageRegions.map((region) => (
                              <button
                                type="button"
                                className={`evidence-marker severity-${region.severity} ${activeMarker === region.marker ? "active" : ""}`}
                                style={{ left: `${region.x * 100}%`, top: `${region.y * 100}%`, width: `${region.width * 100}%`, height: `${region.height * 100}%` }}
                                aria-label={`${region.marker}. ${region.label}: ${region.message}`}
                                title={`${region.label}: ${region.message}`}
                                onClick={() => setActiveMarker(region.marker)}
                                key={`${region.analyzer_id}-${region.marker}`}
                              ><span>{region.marker}</span></button>
                            ))}
                          </div>
                        </div>
                      ) : (
                        <div className="document-skeleton" aria-label="Loading document preview"><span /><span /><span /></div>
                      )}
                    </div>
                    <div className="evidence-register" aria-live="polite">
                      <div className="evidence-register-heading"><strong>Evidence on page {currentPage}</strong><span>{pageRegions.length} {pageRegions.length === 1 ? "area" : "areas"}</span></div>
                      {pageRegions.length ? (
                        <div className="evidence-items">
                          {pageRegions.map((region) => (
                            <button type="button" className={activeMarker === region.marker ? "active" : ""} onClick={() => setActiveMarker(region.marker)} key={`evidence-${region.marker}`}>
                              <span className={`evidence-number severity-${region.severity}`}>{region.marker}</span>
                              <span><strong>{region.label}</strong><small>{titleCase(region.analyzer_id)} · {region.message}</small></span>
                            </button>
                          ))}
                        </div>
                      ) : (
                        <p>No location-based findings are available on this page. Some checks, such as metadata and readability, apply to the document as a whole.</p>
                      )}
                    </div>
                  </div>
                  {job.status === "completed" && (
                    <div className="decision-card">
                      <div className="decision-heading"><h3>Final review decision</h3><p>Required after examining all screening evidence.</p></div>
                      <div className="decision-options">
                        {["verified", "needs_investigation", "inconclusive"].map((value) => <button type="button" className={decision === value ? "active" : ""} onClick={() => { setDecision(value); setReviewSaved(false); }} key={value}>{titleCase(value)}</button>)}
                      </div>
                      <label className="notes-label" htmlFor="review-notes">Review notes <span>Optional</span></label>
                      <textarea id="review-notes" value={notes} onChange={(event) => { setNotes(event.target.value); setReviewSaved(false); }} placeholder="Add portal-match details, evidence considered, or reasons for escalation." maxLength={4000} />
                      <button className="save-button" type="button" onClick={saveReview}><span className="button-state" key={reviewSaved ? "saved" : "idle"}>{reviewSaved ? "Decision saved ✓" : "Save review decision"}</span></button>
                      {reviewSaved && <div className="feedback-toast success" role="status"><span aria-hidden="true">✓</span><p><strong>Review saved</strong>Your decision is attached to this case.</p></div>}
                    </div>
                  )}
                </div>
              </div>
            </div>
          )}
        </section>
      </section>

      {job && (
        <section className={`analysis-loading ${job.status !== "completed" ? "is-open" : "is-closed"}`} role="status" aria-live="polite" aria-hidden={job.status === "completed"} aria-label="Document analysis in progress">
          <div className="loading-shell">
            <div className="scan-stage" aria-hidden="true">
              <div className="scanner-bed">
                <div className="scan-track" />
                <div className="moving-document">
                  <span className="doc-seal">DS</span>
                  <span className="doc-line wide" /><span className="doc-line" /><span className="doc-line short" />
                  <span className="doc-field left" /><span className="doc-field right" />
                  <span className="doc-line wide lower" /><span className="doc-line lower-two" />
                  <span className="doc-qr" />
                </div>
                <div className="scanner-beam"><span /></div>
                <div className="scanner-lip"><i /><i /><i /></div>
              </div>
              <div className="scan-shadow" />
            </div>

            <div className="loading-copy">
              <p className="case-reference">Case reference <strong>{job.id.slice(0, 8)}</strong></p>
              <h2>Screening document</h2>
              <p className="loading-filename">{job.filename}</p>
              <div className="active-check"><span className="pulse-dot" /><div><small>Currently checking</small><strong>{titleCase(activeAnalyzer || "Preparing analysis")}</strong></div></div>
              <div className="loading-progress"><span style={{ width: `${Math.max(5, progressPercent)}%` }} /></div>
              <div className="progress-meta"><span>{counts.completed} of {job.analyzers.length} checks complete</span><strong>{progressPercent}%</strong></div>
              <div className="check-stream">
                {job.analyzers.map((name, index) => (
                  <span className={index < counts.completed ? "done" : index === counts.completed ? "active" : ""} key={name}>
                    <i>{index < counts.completed ? "✓" : index === counts.completed ? "•" : ""}</i>{titleCase(name)}
                  </span>
                ))}
              </div>
              <p className="loading-note">Keep this page open. Results appear automatically when screening is complete.</p>
            </div>
          </div>
        </section>
      )}
    </main>
  );
}
