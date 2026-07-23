"use client";

import { useEffect, useMemo, useState } from "react";
import NavLink from "../../../../components/nav-link";
import { useParams } from "next/navigation";
import AppShell from "../../../../components/app-shell";
import RouteHeader from "../../../../components/route-header";
import {
  API_URL,
  RawTest,
  analyzerLabel,
  artifactLabel,
  docVerdict,
  evidenceEntries,
  evidenceLabel,
  formatWhen,
  isFlagged,
  resultTone,
  riskTone,
  titleCase,
  verdictSubline,
} from "../../../../lib/format";
import { useBatch } from "../../../../lib/use-batch";
import type { DisplayRegion, DocumentManifest } from "../../../../lib/types";

export default function DocumentPage() {
  const params = useParams<{ batchId: string; jobId: string }>();
  const batchId = params?.batchId;
  const jobId = params?.jobId;
  const { batch, loadState, pollStalled, retryPoll, patchJob } = useBatch(batchId);

  const job = useMemo(() => batch?.jobs.find((item) => item.id === jobId) || null, [batch, jobId]);

  const [documentManifest, setDocumentManifest] = useState<DocumentManifest | null>(null);
  const [documentError, setDocumentError] = useState("");
  const [currentPage, setCurrentPage] = useState(1);
  const [zoom, setZoom] = useState(100);
  const [activeMarker, setActiveMarker] = useState<number | null>(null);
  const [decision, setDecision] = useState("verified");
  const [notes, setNotes] = useState("");
  const [reviewSaved, setReviewSaved] = useState(false);
  const [reviewError, setReviewError] = useState("");
  const [savingReview, setSavingReview] = useState(false);

  // Switching documents resets the viewer during render rather than in an
  // effect, so page 3 of the previous PDF never flashes over the new one.
  const [viewedJobId, setViewedJobId] = useState(jobId);
  if (viewedJobId !== jobId) {
    setViewedJobId(jobId);
    setDocumentManifest(null);
    setDocumentError("");
    setCurrentPage(1);
    setZoom(100);
    setActiveMarker(null);
    setReviewSaved(false);
    setReviewError("");
  }

  useEffect(() => {
    if (!jobId) return;
    let cancelled = false;
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

  // Seed the decision controls from any review already on record so a second
  // reviewer edits the existing call rather than silently starting from
  // "verified".
  const savedReview = job?.review;
  const reviewKey = `${jobId}:${savedReview?.reviewed_at || ""}`;
  const [seededReview, setSeededReview] = useState(reviewKey);
  if (seededReview !== reviewKey) {
    setSeededReview(reviewKey);
    setDecision(savedReview?.decision || "verified");
    setNotes(savedReview?.notes || "");
  }

  const counts = useMemo(() => {
    const values = Object.values(job?.results || {});
    return {
      completed: values.length,
      clear: values.filter((item) => resultTone(item) === "good").length,
      review: values.filter((item) => resultTone(item) === "warning").length,
      errors: values.filter((item) => resultTone(item) === "danger").length,
    };
  }, [job]);

  const regions = useMemo<DisplayRegion[]>(() => {
    let marker = 0;
    return Object.entries(job?.results || {}).flatMap(([analyzerId, result]) =>
      (result.regions || []).map((region) => ({ ...region, analyzer_id: analyzerId, marker: ++marker })),
    );
  }, [job]);

  // Walking flagged documents is the batch reviewer's entire job, so give it a
  // first-class control instead of a round trip through the matrix.
  const flagged = useMemo(() => (batch?.jobs || []).filter(isFlagged), [batch]);
  const flaggedIndex = flagged.findIndex((item) => item.id === jobId);
  const prevFlagged = flaggedIndex > 0 ? flagged[flaggedIndex - 1] : null;
  const nextFlagged = flaggedIndex >= 0 && flaggedIndex < flagged.length - 1
    ? flagged[flaggedIndex + 1]
    : flaggedIndex === -1 ? flagged[0] || null : null;

  const progressPercent = job ? Math.round((counts.completed / Math.max(1, job.analyzers.length)) * 100) : 0;
  const activeAnalyzer = job && job.status !== "completed"
    ? job.analyzers[Math.min(counts.completed, job.analyzers.length - 1)]
    : null;
  const pageRegions = regions.filter((region) => region.page === currentPage);
  const currentPageInfo = documentManifest?.pages.find((page) => page.page === currentPage);

  async function saveReview() {
    if (!job || savingReview) return;
    setSavingReview(true);
    setReviewError("");
    try {
      const response = await fetch(`${API_URL}/api/v1/jobs/${job.id}/review`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ decision, notes }),
      });
      if (!response.ok) throw new Error("Save failed");
      const review = await response.json();
      patchJob(job.id, { review });
      setReviewSaved(true);
    } catch {
      setReviewError("Your decision could not be saved. Your notes are kept — try again.");
    } finally {
      setSavingReview(false);
    }
  }

  const missing = loadState === "missing" || (loadState === "ready" && !job);

  // This route's identity band: the verdict. The reviewer's first question is
  // "what did it find", so the answer sits above everything, and the walk
  // between flagged documents sits with it.
  const verdict = job ? docVerdict(job) : null;
  const header = job && batch && verdict ? (
    <RouteHeader
      rule={false}
      eyebrow={batch.document_count > 1
        ? (
          <nav className="case-breadcrumb" aria-label="Breadcrumb">
            <NavLink href={`/batches/${batch.id}`}>Batch {batch.id.slice(0, 8)}</NavLink>
            <span aria-hidden="true">›</span>
            <span aria-current="page">Document {flaggedIndex >= 0 ? flaggedIndex + 1 : ""}</span>
          </nav>
        )
        : <>Case <span className="mono-ref">{job.id.slice(0, 8)}</span></>}
      title={job.filename}
      sub={job.status === "completed" ? undefined : "Screening in progress. This page updates automatically."}
      aside={flagged.length > 1 ? (
        <div className="flag-walk" aria-label="Move between flagged documents">
          <NavLink
            className={prevFlagged ? "" : "disabled"}
            aria-disabled={prevFlagged ? undefined : true}
            href={prevFlagged ? `/batches/${batch.id}/documents/${prevFlagged.id}` : `/batches/${batch.id}`}
          >‹</NavLink>
          <span>{flaggedIndex >= 0 ? `${flaggedIndex + 1} of ${flagged.length} flagged` : `${flagged.length} flagged`}</span>
          <NavLink
            className={nextFlagged ? "" : "disabled"}
            aria-disabled={nextFlagged ? undefined : true}
            href={nextFlagged ? `/batches/${batch.id}/documents/${nextFlagged.id}` : `/batches/${batch.id}`}
          >›</NavLink>
        </div>
      ) : undefined}
      below={job.status === "completed" ? (
        <div className={`verdict-anchor ${verdict.tone}`} role="status">
          <span className="va-dot" aria-hidden="true" />
          <div className="va-copy">
            <strong className="va-label">{verdict.label}</strong>
            <span className="va-sub">{verdictSubline(counts, verdict.tone)}</span>
          </div>
          <span className="va-count">{counts.completed}<small>/{job.analyzers.length} checks</small></span>
        </div>
      ) : (
        <div className="doc-progress" aria-label={`${progressPercent}% complete`}>
          <span style={{ width: `${Math.max(4, progressPercent)}%` }} />
        </div>
      )}
    />
  ) : undefined;

  return (
    <AppShell route="document" chrome="icons" header={header}>
      {loadState === "loading" && (
        <div className="route-message" role="status">
          <strong>Opening document…</strong>
          <span>Loading the screening evidence for this case.</span>
        </div>
      )}

      {missing && (
        <div className="route-message error" role="alert">
          <strong>This document is not on this device</strong>
          <span>Screening results are stored locally by the machine that ran them, so a link opened elsewhere will not resolve. Ask for the document to be re-run, or start a new review.</span>
        </div>
      )}

      {loadState === "error" && (
        <div className="route-message error" role="alert">
          <strong>The document could not be loaded</strong>
          <span>The screening service did not answer. Confirm the backend is running, then retry.</span>
          <button type="button" onClick={retryPoll}>Retry now</button>
        </div>
      )}

      {job && batch && (
        <div className="job-view">
          <div className="case-grid">
            <div className="results-column">
              <div className="panel-title"><h2>Screening results</h2><span>{counts.clear} clear · {counts.review} to review · {counts.errors} errors</span></div>
              <div className="result-list">
                {job.analyzers.map((name) => {
                  const result = job.results[name];
                  const run = job.analyzer_runs[name];
                  if (!result) return <div className="result-card pending" key={name}><span className="result-dot" /><div><strong>{analyzerLabel(name)}</strong><small>{run?.status === "running" ? "Running now" : "Waiting to run"}</small></div></div>;
                  const tone = resultTone(result);
                  const artifacts = Array.isArray(result.artifacts) ? result.artifacts as string[] : [];
                  const detectedPhoto = name === "photo_detection"
                    ? artifacts.find((artifact) => /\.(?:jpe?g|png|webp)$/i.test(artifact))
                    : undefined;
                  const evidenceRows = evidenceEntries(result.raw);
                  const rawTests = Array.isArray(result.raw.tests) ? (result.raw.tests as RawTest[]) : [];
                  const resultRegions = regions.filter((region) => region.analyzer_id === name);
                  return (
                    <details className={`result-card ${tone}`} key={name}>
                      <summary><span className="result-dot" /><div><strong>{analyzerLabel(name)}</strong><small>{result.summary}</small></div><span className="expand">+</span></summary>
                      <div className="result-detail"><div className="result-detail-inner">
                        <p className="finding-verdict">{result.summary}</p>
                        <div className="finding-chips">
                          <span className={`finding-chip ${tone}`}>{titleCase(result.outcome)}</span>
                          {result.risk !== "unknown" && <span className={`finding-chip ${riskTone(result.risk)}`}>{titleCase(result.risk)} risk</span>}
                          <span className="finding-chip neutral">{result.findings_count} {result.findings_count === 1 ? "finding" : "findings"}</span>
                        </div>
                        {detectedPhoto && (
                          <figure className="detected-photo">
                            <img
                              src={`${API_URL}/api/v1/jobs/${job.id}/artifacts/${name}/${detectedPhoto}`}
                              alt={`Photo detected in ${job.filename}`}
                              loading="lazy"
                            />
                            <figcaption>Extracted from page {String(result.raw.page || 1)} for visual review</figcaption>
                          </figure>
                        )}
                        {evidenceRows.length > 0 && (
                          <table className="finding-table"><tbody>
                            {evidenceRows.map(([key, value]) => <tr key={key}><th scope="row">{evidenceLabel(key)}</th><td>{value}</td></tr>)}
                          </tbody></table>
                        )}
                        {rawTests.length > 0 && (
                          <table className="finding-table finding-tests" aria-label={`Individual checks from ${analyzerLabel(name)}`}><tbody>
                            {rawTests.map((test, index) => (
                              <tr key={test.name || index}>
                                <th scope="row">{test.name}</th>
                                <td className="test-status"><span className={`finding-chip ${test.passed ? "good" : "danger"}`}>{test.passed ? "Pass" : "Fail"}</span></td>
                                <td>{test.value}</td>
                              </tr>
                            ))}
                          </tbody></table>
                        )}
                        {resultRegions.length > 0 && (
                          <div className="finding-regions" aria-label={`Jump to evidence from ${analyzerLabel(name)}`}>
                            {resultRegions.map((region) => (
                              <button type="button" key={`jump-${region.marker}`} onClick={() => { setCurrentPage(region.page); setActiveMarker(region.marker); }}>
                                <span className={`evidence-number severity-${region.severity}`}>{region.marker}</span>
                                Page {region.page} · {region.label}
                              </button>
                            ))}
                          </div>
                        )}
                        {artifacts.map((artifact) => <a className="artifact-link" key={artifact} href={`${API_URL}/api/v1/jobs/${job.id}/artifacts/${name}/${artifact}`} target="_blank" rel="noreferrer">{artifactLabel(artifact)}</a>)}
                        {typeof result.raw.report === "string" && <pre className="finding-report">{result.raw.report}</pre>}
                        <details className="raw-output">
                          <summary>Raw output</summary>
                          <pre>{JSON.stringify(result.raw, null, 2)}</pre>
                        </details>
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
                          <span><strong>{region.label}</strong><small>{analyzerLabel(region.analyzer_id)} · {region.message}</small></span>
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
                  {savedReview && (
                    <div className="prior-review" role="note">
                      <strong>Decision on record: {titleCase(savedReview.decision)}</strong>
                      <small>Saved {formatWhen(savedReview.reviewed_at)}</small>
                      {savedReview.notes && <p>{savedReview.notes}</p>}
                    </div>
                  )}
                  <div className="decision-options">
                    {["verified", "needs_investigation", "inconclusive"].map((value) => <button type="button" className={decision === value ? "active" : ""} onClick={() => { setDecision(value); setReviewSaved(false); }} key={value}>{titleCase(value)}</button>)}
                  </div>
                  <label className="notes-label" htmlFor="review-notes">Review notes <span>Optional</span></label>
                  <textarea id="review-notes" value={notes} onChange={(event) => { setNotes(event.target.value); setReviewSaved(false); }} placeholder="Add portal-match details, evidence considered, or reasons for escalation." maxLength={4000} />
                  <button className="save-button" type="button" onClick={saveReview} disabled={savingReview}><span className="button-state" key={savingReview ? "saving" : reviewSaved ? "saved" : "idle"}>{savingReview ? "Saving…" : reviewSaved ? "Decision saved ✓" : savedReview ? "Update review decision" : "Save review decision"}</span></button>
                  {reviewSaved && <div className="feedback-toast success" role="status"><span aria-hidden="true">✓</span><p><strong>Review saved</strong>Your decision is attached to this case.</p></div>}
                  {reviewError && <div className="feedback-toast error" role="alert"><span aria-hidden="true">!</span><p><strong>Save failed</strong>{reviewError}</p></div>}
                </div>
              )}
            </div>
          </div>
        </div>
      )}

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
              <div className="active-check"><span className="pulse-dot" /><div><small>Currently checking</small><strong>{analyzerLabel(activeAnalyzer || "Preparing analysis")}</strong></div></div>
              <div className="loading-progress"><span style={{ width: `${Math.max(5, progressPercent)}%` }} /></div>
              <div className="progress-meta"><span>{counts.completed} of {job.analyzers.length} checks complete</span><strong>{progressPercent}%</strong></div>
              <div className="check-stream">
                {job.analyzers.map((name, index) => (
                  <span className={index < counts.completed ? "done" : index === counts.completed ? "active" : ""} key={name}>
                    <i>{index < counts.completed ? "✓" : index === counts.completed ? "•" : ""}</i>{analyzerLabel(name)}
                  </span>
                ))}
              </div>
              {pollStalled ? (
                <div className="stall-notice" role="alert">
                  <strong>Screening is taking longer than expected.</strong>
                  <span>The analysis service may be unreachable. Your job continues if the service is still running.</span>
                  <button type="button" onClick={retryPoll}>Retry now</button>
                </div>
              ) : (
                <p className="loading-note">Keep this page open. Results appear automatically when screening is complete.</p>
              )}
            </div>
          </div>
        </section>
      )}
    </AppShell>
  );
}
