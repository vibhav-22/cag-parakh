"use client";

import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import {
  ArrowLeft,
  ArrowSquareOut,
  CaretLeft,
  CaretRight,
  CheckCircle,
  Keyboard,
  Minus,
  Plus,
} from "@phosphor-icons/react";
import NavLink from "../../../../components/nav-link";
import { useParams, useRouter } from "next/navigation";
import AppShell from "../../../../components/app-shell";
import AskDocumentPanel from "../../../../components/ask-document-panel";
import { CALIBRATION_NOTICE } from "../../../../lib/calibration";
import {
  API_URL,
  DECISION_OPTIONS,
  RawTest,
  analyzerLabel,
  artifactLabel,
  checkState,
  checkStatusOf,
  decisionLabel,
  evidenceEntries,
  evidenceLabel,
  failedAnalyzers,
  formatWhen,
  resultConfidencePercent,
  resultTone,
  riskTone,
  titleCase,
} from "../../../../lib/format";
import { useBatch } from "../../../../lib/use-batch";
import type {
  DisplayRegion,
  DocumentManifest,
  Job,
  PriorScreening,
  ReviewDecision,
} from "../../../../lib/types";

export default function DocumentPage() {
  const params = useParams<{ batchId: string; jobId: string }>();
  const batchId = params?.batchId;
  const jobId = params?.jobId;
  const router = useRouter();
  const { batch, loadState, pollStalled, retryPoll, patchJob } = useBatch(batchId);

  const job = useMemo(() => batch?.jobs.find((item) => item.id === jobId) || null, [batch, jobId]);

  const [documentManifest, setDocumentManifest] = useState<DocumentManifest | null>(null);
  const [documentError, setDocumentError] = useState("");
  const [currentPage, setCurrentPage] = useState(1);
  const [zoom, setZoom] = useState(100);
  const [activeMarker, setActiveMarker] = useState<number | null>(null);
  const [decision, setDecision] = useState<ReviewDecision>("verified");
  const [notes, setNotes] = useState("");
  const [reviewer, setReviewer] = useState("");
  const [assignedTo, setAssignedTo] = useState("");
  const [reviewSaved, setReviewSaved] = useState(false);
  const [reviewError, setReviewError] = useState("");
  const [savingReview, setSavingReview] = useState(false);
  const [priors, setPriors] = useState<PriorScreening[]>([]);
  const [retrying, setRetrying] = useState(false);
  const [actionError, setActionError] = useState("");
  const [keyHintOpen, setKeyHintOpen] = useState(false);
  const decisionCardRef = useRef<HTMLDivElement | null>(null);
  const scrollToDecision = useCallback((preset?: ReviewDecision) => {
    if (preset) { setDecision(preset); setReviewSaved(false); setReviewError(""); }
    decisionCardRef.current?.scrollIntoView({ behavior: "smooth", block: "start" });
  }, []);

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
    setActionError("");
    setPriors([]);
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

  // "This exact file was screened before." A byte-identical resubmission is
  // often a stronger signal than any single detector, and it costs one request.
  useEffect(() => {
    if (!jobId) return;
    let cancelled = false;
    fetch(`${API_URL}/api/v1/jobs/${jobId}/duplicates`)
      .then((response) => (response.ok ? response.json() : []))
      .then((items: PriorScreening[]) => { if (!cancelled) setPriors(items); })
      .catch(() => { /* a missing cross-reference must never block the evidence */ });
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
    setReviewer(savedReview?.reviewer || "");
    setAssignedTo(savedReview?.assigned_to || "");
  }

  const counts = useMemo(() => {
    const statuses = Object.values(job?.results || {}).map(checkStatusOf);
    return {
      completed: statuses.length,
      clear: statuses.filter((status) => status === "pass").length,
      review: statuses.filter((status) => status === "fail").length,
      errors: statuses.filter((status) => status === "error").length,
      undetermined: statuses.filter((status) => status === "inconclusive").length,
      // Checks that grade the document. Metadata reports how the file was made
      // and takes no position, so "3 of 4 passed" must not count it.
      graded: statuses.filter((status) => status !== "info").length,
    };
  }, [job]);

  const primaryReviewAnalyzer = useMemo(
    () => job?.analyzers.find((name) => {
      const status = job.results[name] ? checkStatusOf(job.results[name]) : null;
      return status === "fail" || status === "error";
    }) || null,
    [job],
  );

  // A pass is still a claim that needs a reason and detector evidence. Keeping
  // every check here makes false positives auditable instead of hiding them.
  const evidenceAnalyzers = useMemo(() => {
    if (!job) return [];
    return job.analyzers;
  }, [job]);

  const checkCriteria = useMemo(() => (job?.analyzers || []).map((name) => {
    const result = job?.results[name];
    const detectorSettings = (job?.settings?.[name] || {}) as Record<string, unknown>;
    const dpi = typeof detectorSettings.dpi === "number" ? detectorSettings.dpi : null;
    return {
      name,
      dpi,
      result,
      status: result ? checkStatusOf(result) : null,
      state: result ? checkState(result) : null,
      criterion: result?.check?.criterion || "",
      reason: result?.check?.reason || "",
      facts: result?.check?.facts || [],
    };
  }), [job]);

  // The most recent completion timestamp across this document's checks — the
  // closest real signal to "when this document was last analysed."
  const lastAnalysedAt = useMemo(() => {
    const timestamps = Object.values(job?.analyzer_runs || {})
      .map((run) => run.completed_at)
      .filter((value): value is string => Boolean(value));
    if (timestamps.length === 0) return null;
    return timestamps.reduce((latest, value) => (value > latest ? value : latest));
  }, [job]);

  const regions = useMemo<DisplayRegion[]>(() => {
    let marker = 0;
    return Object.entries(job?.results || {}).flatMap(([analyzerId, result]) =>
      (result.regions || []).map((region) => ({ ...region, analyzer_id: analyzerId, marker: ++marker })),
    );
  }, [job]);

  // Keep the complete batch walkable from the viewer. This deliberately follows
  // the batch order, including clean PDFs, rather than skipping to flagged jobs.
  const documents = batch?.jobs || [];
  const documentIndex = documents.findIndex((item) => item.id === jobId);
  const previousDocument = documentIndex > 0 ? documents[documentIndex - 1] : null;
  const nextDocument = documentIndex >= 0 && documentIndex < documents.length - 1
    ? documents[documentIndex + 1]
    : null;

  const failed = useMemo(() => (job ? failedAnalyzers(job) : []), [job]);

  // Keyboard triage. A reviewer working forty documents should never have to
  // reach for the mouse to walk the markers or move to the next PDF.
  const stepMarker = useCallback((direction: 1 | -1) => {
    if (regions.length === 0) return;
    const index = regions.findIndex((region) => region.marker === activeMarker);
    const next = regions[(index + direction + regions.length * 2) % regions.length]
      || regions[direction === 1 ? 0 : regions.length - 1];
    setActiveMarker(next.marker);
    setCurrentPage(next.page);
  }, [regions, activeMarker]);

  const goToDocument = useCallback((target: Job | null) => {
    if (target && batchId) router.push(`/batches/${batchId}/documents/${target.id}`);
  }, [batchId, router]);

  useEffect(() => {
    function onKeyDown(event: KeyboardEvent) {
      if (event.metaKey || event.ctrlKey || event.altKey) return;
      const target = event.target as HTMLElement | null;
      // Never steal a keystroke from a field the reviewer is typing in.
      if (target && (/^(INPUT|TEXTAREA|SELECT)$/.test(target.tagName) || target.isContentEditable)) return;
      switch (event.key) {
        case "j": stepMarker(1); break;
        case "k": stepMarker(-1); break;
        case "n": goToDocument(nextDocument); break;
        case "p": goToDocument(previousDocument); break;
        case "?": setKeyHintOpen((open) => !open); break;
        default: return;
      }
      event.preventDefault();
    }
    window.addEventListener("keydown", onKeyDown);
    return () => window.removeEventListener("keydown", onKeyDown);
  }, [stepMarker, goToDocument, nextDocument, previousDocument]);

  const progressPercent = job ? Math.round((counts.completed / Math.max(1, job.analyzers.length)) * 100) : 0;
  const activeAnalyzer = job && job.status !== "completed"
    ? job.analyzers[Math.min(counts.completed, job.analyzers.length - 1)]
    : null;
  const pageRegions = regions.filter((region) => region.page === currentPage);
  const currentPageInfo = documentManifest?.pages.find((page) => page.page === currentPage);

  async function saveReview() {
    if (!job || savingReview) return;
    if (decision === "escalated" && !assignedTo.trim()) {
      setReviewError("Name the reviewer you are handing this case to before escalating.");
      return;
    }
    setSavingReview(true);
    setReviewError("");
    try {
      const response = await fetch(`${API_URL}/api/v1/jobs/${job.id}/review`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          decision,
          notes,
          reviewer: reviewer.trim(),
          assigned_to: decision === "escalated" ? assignedTo.trim() : "",
        }),
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

  // Retrying a crashed detector is the difference between a partial result and a
  // document nobody can rule on. Only the failed checks re-run; the ones that
  // completed keep their results.
  async function retryFailedChecks() {
    if (!job || retrying) return;
    setRetrying(true);
    setActionError("");
    try {
      const response = await fetch(`${API_URL}/api/v1/jobs/${job.id}/rerun`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ analyzers: failed }),
      });
      const payload = await response.json();
      if (!response.ok) throw new Error(payload?.detail || "Retry failed");
      patchJob(job.id, payload as Job);
    } catch (cause) {
      setActionError(cause instanceof Error ? cause.message : "These checks could not be re-run.");
    } finally {
      setRetrying(false);
    }
  }

  async function setUnanalyzable(unanalyzable: boolean) {
    if (!job) return;
    setActionError("");
    try {
      const response = await fetch(`${API_URL}/api/v1/jobs/${job.id}/unanalyzable`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          unanalyzable,
          reason: unanalyzable ? (notes.trim() || "Marked unanalyzable from the review workspace.") : "",
        }),
      });
      const payload = await response.json();
      if (!response.ok) throw new Error(payload?.detail || "Update failed");
      patchJob(job.id, payload as Job);
    } catch (cause) {
      setActionError(cause instanceof Error ? cause.message : "This document could not be updated.");
    }
  }

  const missing = loadState === "missing" || (loadState === "ready" && !job);

  const fileKind = job?.filename.split(".").pop()?.toUpperCase() || "DOCUMENT";
  const header = job && batch ? (
    <header className="document-compact-header">
      <div className="document-header-identity">
        <a className="document-back" href={`/batches/${batch.id}`} aria-label="Back to batch">
          <ArrowLeft aria-hidden="true" weight="regular" />
          <span>Batch</span>
        </a>
        <div>
          <h1>{job.filename}</h1>
          <p>
            <span>{job.id.slice(0, 12)}</span>
            <i aria-hidden="true" />
            <span>{documentManifest?.page_count || "—"} {documentManifest?.page_count === 1 ? "page" : "pages"}</span>
            <i aria-hidden="true" />
            <span>{fileKind}</span>
          </p>
        </div>
      </div>
      <div className="document-header-actions">
        {documents.length > 1 && (
          <nav className="document-sequence" aria-label="Move between PDFs">
            <button
              type="button"
              aria-label="Previous PDF"
              disabled={!previousDocument}
              onClick={() => goToDocument(previousDocument)}
            >
              <CaretLeft aria-hidden="true" weight="bold" />
              <span>Previous</span>
            </button>
            <span className="document-position" aria-live="polite">
              {documentIndex + 1} / {documents.length}
            </span>
            <button
              type="button"
              aria-label="Next PDF"
              disabled={!nextDocument}
              onClick={() => goToDocument(nextDocument)}
            >
              <span>Next</span>
              <CaretRight aria-hidden="true" weight="bold" />
            </button>
          </nav>
        )}
        {(counts.review + counts.errors) > 0 && (
          <span className="unresolved-pill">{counts.review + counts.errors} unresolved</span>
        )}
        {job.status === "completed" ? (
          <button type="button" className="complete-review-button" onClick={() => scrollToDecision()}>
            <CheckCircle aria-hidden="true" weight="bold" />
            {savedReview ? "Update review" : "Complete review"}
          </button>
        ) : (
          <div className="document-header-progress" aria-label={`${progressPercent}% complete`}>
            <span style={{ width: `${Math.max(4, progressPercent)}%` }} />
          </div>
        )}
      </div>
    </header>
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
          {priors.length > 0 && (
            <details className="prior-screening">
              <summary>
                <span className="ps-mark" aria-hidden="true">↺</span>
                <span className="prior-screening-summary">
                  <strong>This exact file was screened before</strong>
                  <small>{priors.length} {priors.length === 1 ? "earlier screening" : "earlier screenings"} · Open history</small>
                </span>
                <CaretRight className="prior-screening-caret" aria-hidden="true" />
              </summary>
              <div className="prior-screening-detail">
                <p>
                  Byte-identical content has passed through {priors.length}{" "}
                  {priors.length === 1 ? "earlier screening" : "earlier screenings"}. This says the same
                  file was submitted again — it does not mean anyone else screened this document, since a
                  second photo of the same certificate is a different file.
                </p>
                <ul>
                  {priors.slice(0, 4).map((prior) => (
                    <li key={prior.job_id}>
                      {prior.batch_id ? (
                        <NavLink href={`/batches/${prior.batch_id}/documents/${prior.job_id}`}>{prior.filename}</NavLink>
                      ) : <span>{prior.filename}</span>}
                      <small>
                        {formatWhen(prior.created_at)} · {titleCase(prior.machine_verdict)}
                        {prior.review_decision ? ` · reviewer: ${decisionLabel(prior.review_decision)}` : " · no decision recorded"}
                      </small>
                    </li>
                  ))}
                </ul>
              </div>
            </details>
          )}

          {job.unanalyzable && (
            <div className="unanalyzable-notice" role="note">
              <div>
                <strong>Marked unanalyzable</strong>
                <p>{job.unanalyzable_reason || "No reason was recorded."} This document has left the flagged queue.</p>
              </div>
              <button type="button" onClick={() => void setUnanalyzable(false)}>Return to the queue</button>
            </div>
          )}

          <div className="document-workspace-toolbar" aria-label="Document viewer controls">
            <div className="workspace-page-controls">
              <button
                type="button"
                aria-label="Previous page"
                disabled={currentPage <= 1}
                onClick={() => { setCurrentPage((page) => Math.max(1, page - 1)); setActiveMarker(null); }}
              >
                <CaretLeft aria-hidden="true" weight="bold" />
              </button>
              <button
                type="button"
                aria-label="Next page"
                disabled={!documentManifest || currentPage >= documentManifest.page_count}
                onClick={() => { setCurrentPage((page) => Math.min(documentManifest?.page_count || page, page + 1)); setActiveMarker(null); }}
              >
                <CaretRight aria-hidden="true" weight="bold" />
              </button>
              <label>
                <span className="sr-only">Page</span>
                <select value={currentPage} onChange={(event) => { setCurrentPage(Number(event.target.value)); setActiveMarker(null); }}>
                  {documentManifest?.pages.map((page) => <option value={page.page} key={page.page}>{page.page}</option>)}
                </select>
              </label>
              <span className="workspace-page-count">/ {documentManifest?.page_count || "—"}</span>
              <span className="workspace-page-kind">{fileKind} page</span>
            </div>
            <div className="workspace-view-controls">
              <a
                className="workspace-icon-button"
                href={`${API_URL}/api/v1/jobs/${job.id}/document`}
                target="_blank"
                rel="noreferrer"
                aria-label="Open full document"
              >
                <ArrowSquareOut aria-hidden="true" weight="regular" />
              </a>
              <button
                type="button"
                className="workspace-icon-button"
                aria-label="Show keyboard shortcuts"
                aria-expanded={keyHintOpen}
                onClick={() => setKeyHintOpen((open) => !open)}
              >
                <Keyboard aria-hidden="true" weight="regular" />
              </button>
              <button type="button" aria-label="Zoom out" disabled={zoom <= 75} onClick={() => setZoom((value) => Math.max(75, value - 25))}>
                <Minus aria-hidden="true" weight="bold" />
              </button>
              <button type="button" className="workspace-zoom-value" aria-label="Reset document zoom" onClick={() => setZoom(100)}>{zoom}%</button>
              <button type="button" aria-label="Zoom in" disabled={zoom >= 200} onClick={() => setZoom((value) => Math.min(200, value + 25))}>
                <Plus aria-hidden="true" weight="bold" />
              </button>
            </div>
          </div>
          {keyHintOpen && (
            <dl className="key-hints workspace-key-hints" aria-label="Keyboard shortcuts">
              <div><dt>j / k</dt><dd>Next / previous marked area</dd></div>
              <div><dt>n / p</dt><dd>Next / previous PDF in this batch</dd></div>
              <div><dt>?</dt><dd>Show or hide this list</dd></div>
            </dl>
          )}

          <div className="case-grid">
            <div className="results-column">
              <div className="panel-title">
                <h2>Evidence review</h2>
                <span>{job.analyzers.length} checks · {counts.review + counts.errors} unresolved · Page {currentPage}</span>
              </div>

              {job.status === "completed" && failed.length > 0 && !job.unanalyzable && (
                <div className="partial-failure" role="alert">
                  <div>
                    <strong>{failed.length} {failed.length === 1 ? "check" : "checks"} could not complete</strong>
                    <p>
                      {failed.map(analyzerLabel).join(", ")} stopped with an error, so this document has
                      not been fully screened. A crashed detector is not a finding — re-run it, or record
                      that the file cannot be screened at all.
                    </p>
                  </div>
                  <div className="pf-actions">
                    <button type="button" onClick={() => void retryFailedChecks()} disabled={retrying}>
                      {retrying ? "Re-running…" : `Re-run ${failed.length === 1 ? "the check" : "these checks"}`}
                    </button>
                    <button type="button" className="text-button" onClick={() => void setUnanalyzable(true)}>
                      Mark unanalyzable
                    </button>
                  </div>
                  {actionError && <p className="pf-error">{actionError}</p>}
                </div>
              )}

              <div className="result-list">
                {evidenceAnalyzers.map((name) => {
                  const result = job.results[name];
                  const run = job.analyzer_runs[name];
                  if (!result) return <div className="result-card pending" key={name}><span className="result-dot" /><div><strong>{analyzerLabel(name)}</strong><small>{run?.status === "running" ? "Running now" : "Waiting to run"}</small></div></div>;
                  const tone = resultTone(result);
                  const artifacts = Array.isArray(result.artifacts) ? result.artifacts as string[] : [];
                  const rawPhotos = Array.isArray(result.raw.photos)
                    ? result.raw.photos.filter(
                      (photo): photo is Record<string, unknown> => Boolean(photo) && typeof photo === "object",
                    )
                    : [];
                  const detectedPhotos = name === "photo_detection"
                    ? artifacts
                      .filter((artifact) => /\.(?:jpe?g|png|webp)$/i.test(artifact))
                      .map((artifact, index) => {
                        const photo = rawPhotos.find((item) => item.artifact === artifact);
                        return {
                          artifact,
                          page: typeof photo?.page === "number"
                            ? photo.page
                            : Number(result.raw.page || 1),
                          needsReview: photo?.passed === false,
                          index: index + 1,
                        };
                      })
                    : [];
                  const rawHits = Array.isArray(result.raw.hits)
                    ? result.raw.hits.filter(
                      (hit): hit is Record<string, unknown> => Boolean(hit) && typeof hit === "object",
                    )
                    : [];
                  const rawRegions = Array.isArray(result.raw.regions)
                    ? result.raw.regions.filter(
                      (region): region is Record<string, unknown> => Boolean(region) && typeof region === "object",
                    )
                    : [];
                  const locatedEvidenceImages = (name === "qr_presence" || name === "signature")
                    ? artifacts
                      .filter((artifact) => /\.(?:jpe?g|png|webp)$/i.test(artifact))
                      .map((artifact, index) => {
                        const hit = rawHits.find((item) => item.crop_path === artifact);
                        const region = rawRegions.find((item) => item.artifact === artifact);
                        const page = typeof hit?.page === "number"
                          ? hit.page
                          : typeof region?.page === "number" ? region.page : Number(result.raw.page || 1);
                        return { artifact, page, index: index + 1 };
                      })
                    : [];
                  const evidenceRows = evidenceEntries(result.raw);
                  const rawTests = Array.isArray(result.raw.tests) ? (result.raw.tests as RawTest[]) : [];
                  const resultRegions = regions.filter((region) => region.analyzer_id === name);
                  const confidence = resultConfidencePercent(result);
                  const status = checkStatusOf(result);
                  const state = checkState(result);
                  const check = result.check;
                  const needsCall = status === "fail" || status === "error";
                  return (
                    <details
                      className={`result-card ${tone}`}
                      key={name}
                      open={name === primaryReviewAnalyzer || detectedPhotos.length > 0 || locatedEvidenceImages.length > 0 || undefined}
                    >
                      <summary>
                        <span className={`check-mark ${status}`} aria-hidden="true">{state.glyph}</span>
                        <div><strong>{analyzerLabel(name)}</strong><small>{check?.reason || result.summary}</small></div>
                        <span className="expand">+</span>
                      </summary>
                      <div className="result-detail"><div className="result-detail-inner">
                        {check?.criterion && (
                          <p className="check-criterion"><span>Criterion</span>{check.criterion}</p>
                        )}
                        <p className="finding-verdict">{check?.reason || result.summary}</p>
                        <div className="finding-chips">
                          <span className={`finding-chip ${state.tone}`}>{state.label}</span>
                          {result.risk !== "unknown" && <span className={`finding-chip ${riskTone(result.risk)}`}>{titleCase(result.risk)} risk</span>}
                          <span className="finding-chip neutral">{result.findings_count} {result.findings_count === 1 ? "finding" : "findings"}</span>
                        </div>
                        {check && check.facts.length > 0 && (
                          <dl className="check-facts" aria-label={`What ${analyzerLabel(name)} measured`}>
                            {check.facts.map((fact) => (
                              <div key={fact.label}><dt>{fact.label}</dt><dd>{fact.value}</dd></div>
                            ))}
                          </dl>
                        )}
                        {confidence !== null && (
                          <div className="confidence-meter">
                            <span>Model confidence</span>
                            <div className={`confidence-bar tone-${tone}`}><i style={{ width: `${Math.min(100, Math.max(0, confidence))}%` }} /></div>
                            <b>{confidence}%</b>
                          </div>
                        )}
                        {needsCall && (
                          <div className="finding-actions">
                            <button type="button" className="confirm-flag" onClick={() => scrollToDecision("needs_investigation")}>Confirm flag</button>
                            <button type="button" className="mark-clean" onClick={() => scrollToDecision("verified")}>Mark clean</button>
                          </div>
                        )}
                        {detectedPhotos.length > 0 && (
                          <div
                            className="detected-photos"
                            aria-label={`${detectedPhotos.length} detected ${detectedPhotos.length === 1 ? "photo" : "photos"}`}
                          >
                            {detectedPhotos.map((photo) => (
                              <figure className="detected-photo" key={photo.artifact}>
                                <img
                                  src={`${API_URL}/api/v1/jobs/${job.id}/artifacts/${name}/${photo.artifact}`}
                                  alt={`Photo ${photo.index} detected in ${job.filename}`}
                                  loading="lazy"
                                />
                                <figcaption>
                                  Photo {photo.index} · Page {photo.page}
                                  {photo.needsReview ? " · Quality review" : ""}
                                </figcaption>
                              </figure>
                            ))}
                          </div>
                        )}
                        {locatedEvidenceImages.length > 0 && (
                          <div className="detected-photos" aria-label={`${analyzerLabel(name)} image evidence`}>
                            {locatedEvidenceImages.map((evidence) => (
                              <figure className="detected-photo" key={evidence.artifact}>
                                <img
                                  src={`${API_URL}/api/v1/jobs/${job.id}/artifacts/${name}/${evidence.artifact}`}
                                  alt={`${analyzerLabel(name)} evidence ${evidence.index} detected in ${job.filename}`}
                                  loading="lazy"
                                />
                                <figcaption>
                                  {analyzerLabel(name)} evidence {evidence.index} · Page {evidence.page}
                                </figcaption>
                              </figure>
                            ))}
                          </div>
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
                        {(evidenceRows.length > 0 || rawTests.length > 0 || artifacts.length > 0 || typeof result.raw.report === "string") && (
                          <details className="technical-evidence">
                            <summary>Technical details</summary>
                            <div>
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
                              {artifacts.map((artifact) => <a className="artifact-link" key={artifact} href={`${API_URL}/api/v1/jobs/${job.id}/artifacts/${name}/${artifact}`} target="_blank" rel="noreferrer">{artifactLabel(artifact)}</a>)}
                              {typeof result.raw.report === "string" && <pre className="finding-report">{result.raw.report}</pre>}
                              <details className="raw-output">
                                <summary>Raw output</summary>
                                <pre>{JSON.stringify(result.raw, null, 2)}</pre>
                              </details>
                            </div>
                          </details>
                        )}
                      </div></div>
                    </details>
                  );
                })}
                {job.status === "completed" && evidenceAnalyzers.length === 0 && (
                  <div className="evidence-clear-state">
                    <CheckCircle aria-hidden="true" weight="fill" />
                    <div><strong>No unresolved evidence</strong><small>Every completed detector returned a clear outcome.</small></div>
                  </div>
                )}
              </div>

              <section className="document-checks" aria-label="Document checks">
                <div className="document-checks-heading">
                  <strong>Document checks</strong>
                  <span>{counts.clear}<small>/{counts.graded || job.analyzers.length} passed</small></span>
                </div>
                <ul>
                  {job.analyzers.map((name) => {
                    const result = job.results[name];
                    if (!result) {
                      return (
                        <li key={name}>
                          <span>{analyzerLabel(name)}</span>
                          <span className="dc-status pending">Pending</span>
                        </li>
                      );
                    }
                    const state = checkState(result);
                    return (
                      <li key={name}>
                        <span>{analyzerLabel(name)}</span>
                        <span className={`dc-status ${state.tone}`}>{state.label}</span>
                      </li>
                    );
                  })}
                </ul>
                <p className="document-checks-meta">
                  Last analysed {lastAnalysedAt ? formatWhen(lastAnalysedAt) : "—"}
                  {savedReview?.detectors_version ? ` · Detectors ${savedReview.detectors_version}` : ""}
                </p>
              </section>

              {/* Every check states its own rule and then how this document met
                  it. The rule is a property of the check, identical on every
                  document, so there is nothing here to configure — and a check
                  that could not run says why instead of taking a cross. */}
              <details className="check-criteria">
                <summary>
                  <div>
                    <strong>Check criteria &amp; results</strong>
                    <small>What each check tests for, and what it found here</small>
                  </div>
                  <span>{counts.clear}/{counts.graded} passed</span>
                </summary>
                <div className="check-criteria-body">
                  <div className="check-criteria-legend" aria-label="Result symbols">
                    <span><b className="pass">✓</b> criterion met</span>
                    <span><b className="fail">✕</b> criterion not met</span>
                    <span><b className="inconclusive">?</b> could not be determined</span>
                    <span><b className="info">i</b> reported, not graded</span>
                    <span><b className="error">E</b> check error</span>
                  </div>
                  <ul>
                    {checkCriteria.map((item) => (
                      <li className={`criteria-row ${item.status || "pending"}`} key={item.name}>
                        <span className={`check-mark ${item.status || "pending"}`} aria-hidden="true">
                          {item.state?.glyph || "·"}
                        </span>
                        <div className="criteria-copy">
                          <strong>
                            {analyzerLabel(item.name)}
                            <em className={`criteria-state ${item.status || "pending"}`}>
                              {item.state?.label || "Pending"}
                            </em>
                          </strong>
                          <small className="criteria-rule">
                            {item.criterion || "This check has not reported a criterion yet."}
                            {item.dpi ? ` · ${item.dpi} DPI` : ""}
                          </small>
                          {item.reason && <p className="criteria-reason">{item.reason}</p>}
                          {item.facts.length > 0 && (
                            <ul className="criteria-facts">
                              {item.facts.map((fact) => (
                                <li key={fact.label}><b>{fact.label}</b>{fact.value}</li>
                              ))}
                            </ul>
                          )}
                        </div>
                      </li>
                    ))}
                  </ul>
                </div>
              </details>
            </div>

            <div className="evidence-column">
              <div className="document-viewer">
                <div className="document-stage">
                  {documentError ? (
                    <div className="document-message" role="alert"><strong>Preview unavailable</strong><span>{documentError}</span></div>
                  ) : currentPageInfo ? (
                    <div className="document-page" style={{ width: `${Math.round(zoom * 0.72)}%`, aspectRatio: `${currentPageInfo.width} / ${currentPageInfo.height}` }}>
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
                {pageRegions.length > 0 && (
                  <div className="evidence-register" aria-live="polite">
                    <div className="evidence-register-heading"><strong>Evidence on page {currentPage}</strong><span>{pageRegions.length} {pageRegions.length === 1 ? "area" : "areas"}</span></div>
                    <div className="evidence-items">
                      {pageRegions.map((region) => (
                        <button type="button" className={activeMarker === region.marker ? "active" : ""} onClick={() => setActiveMarker(region.marker)} key={`evidence-${region.marker}`}>
                          <span className={`evidence-number severity-${region.severity}`}>{region.marker}</span>
                          <span><strong>{region.label}</strong><small>{analyzerLabel(region.analyzer_id)} · {region.message}</small></span>
                        </button>
                      ))}
                    </div>
                  </div>
                )}
              </div>
              <AskDocumentPanel
                jobId={job.id}
                currentPage={currentPage}
                onCitePage={(page) => { setCurrentPage(page); setActiveMarker(null); }}
              />

              {job.status === "completed" && (
                <div className="decision-card" ref={decisionCardRef}>
                  <div className="decision-heading"><h3>Final review decision</h3><p>Required after examining all screening evidence.</p></div>

                  {/* The screening test's own decision rule, carried from the
                      profile the run was started under, so the same rule is
                      applied to every document in the test. */}
                  {job.profile && (
                    <div className="decision-rule" role="note">
                      <div className="dr-head"><span className="dr-eyebrow">Preset</span><strong>{job.profile.name}</strong></div>
                      {job.profile.goal && <p className="dr-goal">{job.profile.goal}</p>}
                      {job.profile.guidance && <p className="dr-guidance"><span>Decision rule</span>{job.profile.guidance}</p>}
                    </div>
                  )}

                  {/* The calibration moment. It sits between the evidence and the
                      control on purpose: a reviewer about to record a call is the
                      only person for whom this matters. */}
                  <details className="calibration-note">
                    <summary>
                      <strong>What this screening does not claim</strong>
                      <small>Read before recording a decision</small>
                    </summary>
                    <ul>
                      {CALIBRATION_NOTICE.map((line) => <li key={line}>{line}</li>)}
                    </ul>
                  </details>

                  {savedReview && (
                    <div className="prior-review" role="note">
                      <strong>Decision on record: {decisionLabel(savedReview.decision)}</strong>
                      <small>
                        Saved {formatWhen(savedReview.reviewed_at)}
                        {savedReview.reviewer ? ` by ${savedReview.reviewer}` : ""}
                        {savedReview.assigned_to ? ` · assigned to ${savedReview.assigned_to}` : ""}
                      </small>
                      {savedReview.notes && <p>{savedReview.notes}</p>}
                    </div>
                  )}

                  <div className="decision-options">
                    {DECISION_OPTIONS.map((option) => (
                      <button
                        type="button"
                        className={decision === option.value ? "active" : ""}
                        onClick={() => { setDecision(option.value); setReviewSaved(false); setReviewError(""); }}
                        key={option.value}
                      >
                        <strong>{option.label}</strong>
                        <small>{option.hint}</small>
                      </button>
                    ))}
                  </div>

                  <div className="decision-fields">
                    <label htmlFor="reviewer-name">Your name <span>Optional</span>
                      <input
                        id="reviewer-name"
                        value={reviewer}
                        maxLength={120}
                        placeholder="Recorded on the exported report"
                        onChange={(event) => { setReviewer(event.target.value); setReviewSaved(false); }}
                      />
                    </label>
                    {decision === "escalated" && (
                      <label htmlFor="assigned-to">Hand to <span>Required</span>
                        <input
                          id="assigned-to"
                          value={assignedTo}
                          maxLength={120}
                          placeholder="Name of the second reviewer"
                          onChange={(event) => { setAssignedTo(event.target.value); setReviewSaved(false); setReviewError(""); }}
                        />
                      </label>
                    )}
                  </div>
                  {decision === "escalated" && (
                    <p className="escalation-note">
                      This records the handoff on the case and the exported report. It does not notify
                      anyone — this build has no user accounts, so tell them yourself.
                    </p>
                  )}

                  <label className="notes-label" htmlFor="review-notes">Review notes <span>Optional</span></label>
                  <textarea id="review-notes" value={notes} onChange={(event) => { setNotes(event.target.value); setReviewSaved(false); }} placeholder="Add portal-match details, evidence considered, or reasons for escalation." maxLength={4000} />
                  <button className="save-button" type="button" onClick={saveReview} disabled={savingReview}><span className="button-state" key={savingReview ? "saving" : reviewSaved ? "saved" : "idle"}>{savingReview ? "Saving…" : reviewSaved ? "Decision saved ✓" : savedReview ? "Update review decision" : "Save review decision"}</span></button>
                  {reviewSaved && <div className="feedback-toast success" role="status"><span aria-hidden="true">✓</span><p><strong>Review saved</strong>Your decision is attached to this case.</p></div>}
                  {reviewError && <div className="feedback-toast error" role="alert"><span aria-hidden="true">!</span><p><strong>Save failed</strong>{reviewError}</p></div>}

                  {/* The case has to be able to leave the app. Without this the
                      whole journey ends in contemplation. */}
                  <div className="export-block">
                    <div className="export-copy">
                      <strong>Export the case report</strong>
                      <small>
                        Verdict, every check, located evidence, the decision on record, and the settings
                        it was made against. Print the report to PDF to attach it to a file.
                      </small>
                    </div>
                    <div className="export-actions">
                      <a className="export-primary" href={`${API_URL}/api/v1/jobs/${job.id}/report.html`} target="_blank" rel="noreferrer">
                        Case report ↗
                      </a>
                      <a href={`${API_URL}/api/v1/jobs/${job.id}/report.json`} target="_blank" rel="noreferrer">JSON</a>
                    </div>
                  </div>
                  {!savedReview && (
                    <p className="export-warning" role="note">
                      No decision is recorded yet. An exported report will say so.
                    </p>
                  )}
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
