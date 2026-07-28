"use client";

import { useEffect, useMemo, useRef, useState } from "react";
import NavLink from "../../components/nav-link";
import { useParams, useRouter } from "next/navigation";
import AppShell from "../../components/app-shell";
import RouteHeader from "../../components/route-header";
import {
  API_URL,
  CELL_STATES,
  analyzerLabel,
  authenticityPercent,
  cellFor,
  decisionLabel,
  docVerdict,
  documentTypeLabel,
  flaggedSignalLabels,
  formatWhen,
  isFlagged,
  saveRunAsDefaults,
  shortLabel,
  titleCase,
} from "../../lib/format";
import { useBatch } from "../../lib/use-batch";
import type { AnalysisSettings } from "../../advanced-settings";
import type { Job } from "../../lib/types";

function settingLabel(key: string) {
  const labels: Record<string, string> = {
    dpi: "DPI",
    min_codes: "Minimum QR codes",
    max_pages: "Page limit",
    rotations: "Rotations",
    stop_after_first: "Stop after first",
    grid: "Region grid",
    max_analysis_side: "Maximum analysis side",
    local_regions: "Local region checks",
    review_threshold: "Review threshold",
    annotate_all: "Annotate every page",
    noise_threshold: "Maximum image noise",
    sharpness_threshold: "Minimum sharpness",
    confidence: "Confidence threshold",
    min_signatures: "Minimum signatures",
  };
  return labels[key] || titleCase(key);
}

function settingValue(key: string, value: unknown) {
  if (typeof value === "boolean") return value ? "On" : "Off";
  if (key === "review_threshold" || key === "confidence") {
    return typeof value === "number" ? `${Math.round(value * 100)}%` : String(value);
  }
  if (key === "max_pages" && value === 0) return "All pages";
  if (Array.isArray(value)) return value.join(", ");
  if (key === "dpi") return `${value} DPI`;
  if (key === "max_analysis_side") return `${value} px`;
  return String(value);
}

function ruleSettings(job: Job, analyzer: string) {
  const settings = job.settings?.[analyzer];
  if (!settings || Object.keys(settings).length === 0) return "Calibrated detector defaults; no per-run controls";
  return Object.entries(settings)
    .map(([key, value]) => `${settingLabel(key)}: ${settingValue(key, value)}`)
    .join(" · ");
}

/**
 * Screening a large batch takes longer than a reviewer will sit and watch, so
 * offer to tell them when it lands. Permission is requested on the click, never
 * on page load — an unprompted permission prompt is the thing everyone denies.
 */
function NotifyOnFinish() {
  const [state, setState] = useState<"idle" | "on" | "blocked" | "unsupported">(() => {
    if (typeof window === "undefined" || typeof Notification === "undefined") return "unsupported";
    if (Notification.permission === "granted") return "on";
    if (Notification.permission === "denied") return "blocked";
    return "idle";
  });

  if (state === "unsupported") return null;
  if (state === "on") return <span className="notify-state">Will notify when done</span>;
  if (state === "blocked") return <span className="notify-state">Notifications blocked</span>;

  return (
    <button
      type="button"
      className="notify-button"
      onClick={() => {
        void Notification.requestPermission().then((permission) =>
          setState(permission === "granted" ? "on" : permission === "denied" ? "blocked" : "idle"));
      }}
    >Notify me when this finishes</button>
  );
}

export default function BatchPage() {
  const params = useParams<{ batchId: string }>();
  const batchId = params?.batchId;
  const router = useRouter();
  const { batch, loadState, pollStalled, retryPoll } = useBatch(batchId);

  // A one-document batch has nothing to compare, so send the reviewer straight
  // to the evidence instead of making them click through a single-row table.
  const soloJobId = batch && batch.jobs.length === 1 ? batch.jobs[0].id : null;
  useEffect(() => {
    if (soloJobId && batchId) router.replace(`/batches/${batchId}/documents/${soloJobId}`);
  }, [soloJobId, batchId, router]);

  const stats = useMemo(() => {
    const jobs = batch?.jobs || [];
    const totalRuns = jobs.reduce((sum, item) => sum + item.analyzers.length, 0);
    const doneRuns = jobs.reduce((sum, item) => sum + Object.keys(item.results).length, 0);
    const tones = jobs.map((item) => docVerdict(item).tone);
    return {
      totalRuns,
      doneRuns,
      completedDocs: jobs.filter((item) => item.status === "completed").length,
      cleanDocs: tones.filter((tone) => tone === "good").length,
      reviewDocs: tones.filter((tone) => tone === "warning").length,
      flaggedDocs: tones.filter((tone) => tone === "danger").length,
      errorDocs: jobs.filter((item) => Object.values(item.results).some((result) => result.outcome === "error")).length,
      percent: totalRuns ? Math.round((doneRuns / totalRuns) * 100) : 0,
    };
  }, [batch]);

  // A 40-document batch is triaged flagged-first, so the table has to be able
  // to float the rows that need a human to the top.
  const [activeTab, setActiveTab] = useState<"all" | "flagged" | "review" | "clean">("all");
  const [sortByVerdict, setSortByVerdict] = useState(false);
  const [defaultsSaved, setDefaultsSaved] = useState(false);
  const [search, setSearch] = useState("");
  const [page, setPage] = useState(0);
  const pageSize = 20;

  // The queue, not the grid. A reviewer working a batch wants to be handed the
  // next document that needs them, not to pick rows out of a matrix.
  const flagged = useMemo(() => (batch?.jobs || []).filter(isFlagged), [batch]);
  const undecided = useMemo(
    () => flagged.filter((item) => !item.review).length,
    [flagged],
  );

  // A fifty-document batch outlives the reviewer's attention span, so tell the
  // browser when it finishes. Permission is only ever requested on a click.
  const notified = useRef(false);
  useEffect(() => {
    if (!batch || batch.status !== "completed" || notified.current) return;
    notified.current = true;
    if (typeof Notification === "undefined" || Notification.permission !== "granted") return;
    if (document.visibilityState === "visible") return;
    new Notification("Screening complete", {
      body: `${batch.document_count} documents screened · ${flagged.length} flagged for review`,
      tag: `parakh-batch-${batch.id}`,
    });
  }, [batch, flagged.length]);

  // Promote the run that just happened into the saved defaults. This is the
  // inversion of profile-first setup: the reviewer has now seen what these
  // checks produce, which is the only moment the choice is informed.
  function saveAsDefaults() {
    const first = batch?.jobs[0];
    if (!first) return;
    const settings = (first.settings || {}) as unknown as AnalysisSettings;
    setDefaultsSaved(saveRunAsDefaults(first.analyzers, settings));
  }

  const rows = useMemo(() => {
    const all = batch?.jobs || [];
    const query = search.trim().toLowerCase();
    const filtered = all.filter((item) => {
      if (query && !item.filename.toLowerCase().includes(query) && !item.id.toLowerCase().includes(query)) return false;
      if (activeTab === "all") return true;
      const tone = docVerdict(item).tone;
      if (activeTab === "flagged") return tone === "danger";
      if (activeTab === "review") return tone === "warning";
      return tone === "good";
    });
    if (!sortByVerdict) return filtered;
    const rank = (tone: string) => (tone === "danger" ? 0 : tone === "warning" ? 1 : tone === "neutral" ? 2 : tone === "pending" ? 3 : 4);
    return [...filtered].sort((a, b) => rank(docVerdict(a).tone) - rank(docVerdict(b).tone));
  }, [batch, activeTab, sortByVerdict, search]);

  // Reset to page one whenever the visible set changes shape, so a filter or
  // search never strands the reviewer on a now-empty page.
  useEffect(() => { setPage(0); }, [activeTab, sortByVerdict, search]);

  const pageCount = Math.max(1, Math.ceil(rows.length / pageSize));
  const pagedRows = useMemo(
    () => rows.slice(page * pageSize, page * pageSize + pageSize),
    [rows, page],
  );

  const matrixColumns = useMemo(() => {
    const columns: string[] = [];
    for (const item of batch?.jobs || []) {
      for (const analyzer of item.analyzers) if (!columns.includes(analyzer)) columns.push(analyzer);
    }
    return columns;
  }, [batch]);
  const testJob = batch?.jobs[0];
  // Every check carries its own criterion, so the key below is a property of the
  // run rather than of a configured profile — it is shown whenever the batch has
  // reported one.
  const checkCriteria = useMemo(() => {
    const found: Record<string, string> = {};
    for (const job of batch?.jobs || []) {
      for (const analyzer of matrixColumns) {
        const criterion = job.results[analyzer]?.check?.criterion;
        if (criterion && !found[analyzer]) found[analyzer] = criterion;
      }
    }
    return found;
  }, [batch, matrixColumns]);
  const hasCriteria = Object.keys(checkCriteria).length > 0;

  const ready = loadState === "ready" && batch && !soloJobId;

  // This route's identity band: a live telemetry strip. Not the generic
  // four-tile metrics row that used to appear on every screen — the counts are
  // folded into the progress readout so the table can start higher.
  const header = ready ? (
    <RouteHeader
      eyebrow={<>Batch <span className="mono-ref">{batch.id.slice(0, 8)}</span></>}
      title={batch.name}
      sub={`${batch.document_count} ${batch.document_count === 1 ? "document" : "documents"} · ${batch.status === "completed"
        ? "Analysis complete. Open any document for its full evidence."
        : "Screening in progress. Results fill in as each document completes."}`}
      aside={
        <div className="batch-aside">
          <span className={`status-pill ${batch.status}`}><span className="status-copy" key={batch.status}>{batch.status === "running" ? "Analyzing" : titleCase(batch.status)}</span></span>
          {batch.status !== "completed" && <NotifyOnFinish />}
        </div>
      }
      below={
        <div className="batch-status-banner">
          <div className="bsb-head">
            <span className={`bsb-pill ${batch.status}`}>
              <i aria-hidden="true" /> {batch.status === "completed" ? "Analysis complete" : batch.status === "running" ? "Analyzing" : "Queued"}
            </span>
            <div className="bsb-title">
              <strong>{batch.name}</strong>
              <small>Batch {batch.id.slice(0, 8)} · Uploaded {formatWhen(batch.created_at)}</small>
            </div>
          </div>
          <dl className="bsb-stats">
            <div><dt>Documents</dt><dd>{batch.document_count}</dd></div>
            <div className="bsb-clean"><dt>Clean</dt><dd>{stats.cleanDocs}</dd></div>
            <div className="bsb-flagged"><dt>Flagged</dt><dd>{stats.flaggedDocs}</dd></div>
            <div className="bsb-review"><dt>Review</dt><dd>{stats.reviewDocs}</dd></div>
          </dl>
          <div className="bt-track" aria-label={`${stats.percent}% complete`}>
            <span style={{ width: `${Math.max(2, stats.percent)}%` }} />
          </div>
        </div>
      }
    />
  ) : undefined;

  return (
    <AppShell route="batches" header={header}>
      {loadState === "loading" && (
        <div className="route-message" role="status">
          <strong>Opening case…</strong>
          <span>Loading the screening results for this batch.</span>
        </div>
      )}

      {loadState === "missing" && (
        <div className="route-message error" role="alert">
          <strong>This case is not on this device</strong>
          <span>Screening results are stored locally by the machine that ran them, so a link opened elsewhere will not resolve. Ask for the batch to be re-run, or start a new review.</span>
        </div>
      )}

      {loadState === "error" && (
        <div className="route-message error" role="alert">
          <strong>The case could not be loaded</strong>
          <span>The screening service did not answer. Confirm the backend is running, then retry.</span>
          <button type="button" onClick={retryPoll}>Retry now</button>
        </div>
      )}

      {ready && (
        <div className="batch-view">
          {pollStalled && (
            <div className="stall-notice" role="alert">
              <strong>Screening is taking longer than expected.</strong>
              <span>The analysis service may be unreachable. Your batch continues if the service is still running.</span>
              <button type="button" onClick={retryPoll}>Retry now</button>
            </div>
          )}

          <div className="batch-actions">
            <div className="ba-queue">
              {flagged.length > 0 ? (
                <>
                  <NavLink className="ba-primary" href={`/batches/${batch.id}/documents/${(flagged.find((item) => !item.review) || flagged[0]).id}`}>
                    Review flagged queue
                    <span aria-hidden="true">→</span>
                  </NavLink>
                  <small>
                    {flagged.length} flagged · {undecided} without a recorded decision. Walk them with
                    n and p; no need to come back to this table.
                  </small>
                </>
              ) : (
                <small>
                  Nothing is flagged in this batch. Export the report to close the case, or open any
                  document to record a decision anyway.
                </small>
              )}
            </div>
            <div className="ba-exports">
              <a className="ba-export" href={`${API_URL}/api/v1/batches/${batch.id}/report.html`} target="_blank" rel="noreferrer">Batch report ↗</a>
              <a className="ba-export" href={`${API_URL}/api/v1/batches/${batch.id}/report.csv`}>CSV</a>
              <button type="button" className="text-button" onClick={saveAsDefaults}>
                {defaultsSaved ? "Saved as defaults ✓" : "Save these checks as my defaults"}
              </button>
            </div>
          </div>

          <div className="matrix-card">
            <div className="panel-title">
              <div>
                <h2>Batch Matrix</h2>
                <p className="matrix-test-name">
                  {testJob?.profile?.name ? <>Screened under <strong>{testJob.profile.name}</strong>. </> : null}
                  Each check applies its own fixed criterion, listed in the test key below.
                </p>
              </div>
              <div className="matrix-tools">
                <button
                  type="button"
                  className={sortByVerdict ? "active" : ""}
                  aria-pressed={sortByVerdict}
                  onClick={() => setSortByVerdict((on) => !on)}
                >Sort by verdict</button>
              </div>
            </div>

            <div className="matrix-filter-row">
              <div className="matrix-filter-tabs" role="tablist" aria-label="Filter documents">
                <button type="button" role="tab" aria-selected={activeTab === "all"} className={activeTab === "all" ? "active" : ""} onClick={() => setActiveTab("all")}>All <b>{batch.document_count}</b></button>
                <button type="button" role="tab" aria-selected={activeTab === "flagged"} className={activeTab === "flagged" ? "active" : ""} onClick={() => setActiveTab("flagged")}>Flagged <b>{stats.flaggedDocs}</b></button>
                <button type="button" role="tab" aria-selected={activeTab === "review"} className={activeTab === "review" ? "active" : ""} onClick={() => setActiveTab("review")}>Needs review <b>{stats.reviewDocs}</b></button>
                <button type="button" role="tab" aria-selected={activeTab === "clean"} className={activeTab === "clean" ? "active" : ""} onClick={() => setActiveTab("clean")}>Clean <b>{stats.cleanDocs}</b></button>
              </div>
              <label className="matrix-search">
                <span className="sr-only">Search by ID or filename</span>
                <svg aria-hidden="true" viewBox="0 0 16 16" width="13" height="13"><path d="M7 1a6 6 0 1 0 3.76 10.68l3.28 3.28 1.06-1.06-3.28-3.28A6 6 0 0 0 7 1Zm0 1.5a4.5 4.5 0 1 1 0 9 4.5 4.5 0 0 1 0-9Z" fill="currentColor" /></svg>
                <input
                  type="search"
                  value={search}
                  onChange={(event) => setSearch(event.target.value)}
                  placeholder="Search ID or filename…"
                />
              </label>
            </div>

            <div className="matrix-scroll">
              <table className="outcome-matrix summary-matrix">
                <thead>
                  <tr>
                    <th scope="col">Document</th>
                    <th scope="col">Type</th>
                    <th scope="col">Checks</th>
                    <th scope="col">Authenticity</th>
                    <th scope="col">Forensic signals</th>
                    <th scope="col">Status</th>
                  </tr>
                </thead>
                <tbody>
                  {pagedRows.map((item) => {
                    const verdict = docVerdict(item);
                    const score = authenticityPercent(item);
                    const signals = flaggedSignalLabels(item);
                    const doneChecks = Object.keys(item.results).length;
                    return (
                      <tr key={item.id}>
                        <th scope="row">
                          <NavLink className="doc-link" href={`/batches/${batch.id}/documents/${item.id}`}>
                            <span className="doc-name">{item.filename}</span>
                            <small className="doc-id mono-ref">{item.id.slice(0, 8)}</small>
                          </NavLink>
                        </th>
                        <td className="type-cell">{documentTypeLabel(item.filename)}</td>
                        <td className="checks-cell">{doneChecks}<small>/{item.analyzers.length}</small></td>
                        <td className="authenticity-cell">
                          {score === null ? (
                            <span className="authenticity-empty">—</span>
                          ) : (
                            <div className={`authenticity-bar tone-${verdict.tone}`} aria-label={`Authenticity ${score}%`}>
                              <span style={{ width: `${score}%` }} />
                              <b>{score}%</b>
                            </div>
                          )}
                        </td>
                        <td className="signals-cell">
                          {signals.length ? signals.slice(0, 3).map((label) => <span className="signal-chip" key={label}>{label}</span>)
                            : <span className="signal-none">No signals</span>}
                          {signals.length > 3 && <span className="signal-chip more">+{signals.length - 3}</span>}
                        </td>
                        <td>
                          <span className={`status-dot-chip ${verdict.tone}`}><i aria-hidden="true" />{verdict.label}</span>
                        </td>
                      </tr>
                    );
                  })}
                </tbody>
              </table>
            </div>
            {rows.length === 0 && (
              <p className="matrix-empty" role="status">
                No documents in this view.{" "}
                <button
                  type="button"
                  className="text-button"
                  onClick={() => {
                    setActiveTab("all");
                    setSearch("");
                  }}
                >Show all {batch.document_count}</button>
              </p>
            )}
            {rows.length > 0 && (
              <div className="matrix-pagination">
                <span>
                  {page * pageSize + 1}–{Math.min(rows.length, page * pageSize + pageSize)} of {rows.length}
                </span>
                <div className="matrix-pagination-controls">
                  <button type="button" disabled={page === 0} onClick={() => setPage((n) => Math.max(0, n - 1))} aria-label="Previous page">‹</button>
                  <button type="button" disabled={page >= pageCount - 1} onClick={() => setPage((n) => Math.min(pageCount - 1, n + 1))} aria-label="Next page">›</button>
                </div>
              </div>
            )}

            <details className="detail-matrix">
              <summary>Detailed check-by-check matrix</summary>
              <div className="matrix-scroll">
                <table className="outcome-matrix">
                  <thead>
                    <tr>
                      <th scope="col">Document</th>
                      {matrixColumns.map((column) => (
                        <th
                          scope="col"
                          key={column}
                          title={`${analyzerLabel(column)}${checkCriteria[column] ? ` — ${checkCriteria[column]}` : ""}`}
                        >{shortLabel(column)}</th>
                      ))}
                      <th scope="col">Verdict</th>
                      <th scope="col">Decision</th>
                    </tr>
                  </thead>
                  <tbody>
                    {rows.map((item) => {
                      const verdict = docVerdict(item);
                      return (
                        <tr key={item.id}>
                          <th scope="row">
                            <NavLink className="doc-link" href={`/batches/${batch.id}/documents/${item.id}`}>{item.filename}</NavLink>
                          </th>
                          {matrixColumns.map((column) => {
                            const cell = cellFor(item, column);
                            const state = CELL_STATES[cell.tone];
                            return (
                              <td
                                key={column}
                                className={`cell-${cell.tone}`}
                                title={`${shortLabel(column)} — ${cell.label || state.label}${cell.actual ? `\nCriterion: ${cell.actual}` : ""}${cell.summary ? `\n${cell.summary}` : ""}`}
                              >
                                <span aria-label={`${shortLabel(column)}: ${cell.label || state.label}`}>{cell.glyph || state.glyph}</span>
                              </td>
                            );
                          })}
                          <td><span className={`history-chip ${verdict.tone}`}>{verdict.label}</span></td>
                          <td className="decision-cell">
                            {item.review
                              ? <span className="decision-recorded">{decisionLabel(item.review.decision)}</span>
                              : <span className="decision-open">Not recorded</span>}
                          </td>
                        </tr>
                      );
                    })}
                  </tbody>
                </table>
              </div>
              <div className="matrix-legend" aria-hidden="true">
                <span><i className="cell-good">✓</i> Criterion met</span>
                <span><i className="cell-warning">✕</i> Criterion not met</span>
                <span><i className="cell-neutral">?</i> Could not be determined</span>
                <span><i className="cell-info">i</i> Reported, not graded</span>
                <span><i className="cell-danger">E</i> Check error</span>
                <span><i className="cell-pending">·</i> Pending</span>
              </div>
            </details>

            {hasCriteria && testJob && (
              <section className="test-rule-map" aria-labelledby="test-rule-map-title">
                <header>
                  <div>
                    <span>Test key</span>
                    <h3 id="test-rule-map-title">{testJob.profile?.name || "Screening criteria"}</h3>
                  </div>
                  <p>What each check tests for, and the settings it ran under.</p>
                </header>
                {(testJob.profile?.goal || testJob.profile?.guidance) && (
                  <div className="test-rule-intent">
                    {testJob.profile?.goal && <p><strong>Goal</strong>{testJob.profile.goal}</p>}
                    {testJob.profile?.guidance && <p><strong>Reviewer guidance</strong>{testJob.profile.guidance}</p>}
                  </div>
                )}
                <div className="test-rule-list">
                  {matrixColumns.map((analyzer) => (
                    <article key={analyzer}>
                      <div className="test-rule-head">
                        <strong>{analyzerLabel(analyzer)}</strong>
                      </div>
                      <p className="test-rule-criterion">{checkCriteria[analyzer] || "This check has not reported a criterion yet."}</p>
                      <p>{ruleSettings(testJob, analyzer)}</p>
                    </article>
                  ))}
                </div>
              </section>
            )}
            <p className="matrix-hint">Select a document name to open its full evidence and record a review decision.</p>
            <p className="matrix-custody">
              <span className="mc-check" aria-hidden="true">✓</span> Chain of custody verified · SHA-256 manifests sealed
            </p>
          </div>
        </div>
      )}
    </AppShell>
  );
}
