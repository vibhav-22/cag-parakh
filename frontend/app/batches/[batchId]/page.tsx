"use client";

import { useEffect, useMemo, useState } from "react";
import NavLink from "../../components/nav-link";
import { useParams, useRouter } from "next/navigation";
import AppShell from "../../components/app-shell";
import RouteHeader from "../../components/route-header";
import {
  CELL_STATES,
  analyzerLabel,
  cellFor,
  docVerdict,
  isFlagged,
  shortLabel,
  titleCase,
} from "../../lib/format";
import { useBatch } from "../../lib/use-batch";

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
    return {
      totalRuns,
      doneRuns,
      completedDocs: jobs.filter((item) => item.status === "completed").length,
      flaggedDocs: jobs.filter((item) => Object.values(item.results).some((result) => result.outcome === "review")).length,
      errorDocs: jobs.filter((item) => Object.values(item.results).some((result) => result.outcome === "error")).length,
      percent: totalRuns ? Math.round((doneRuns / totalRuns) * 100) : 0,
    };
  }, [batch]);

  // A 40-document batch is triaged flagged-first, so the table has to be able
  // to float the rows that need a human to the top.
  const [flaggedOnly, setFlaggedOnly] = useState(false);
  const [sortByVerdict, setSortByVerdict] = useState(false);

  const rows = useMemo(() => {
    const all = batch?.jobs || [];
    const filtered = flaggedOnly ? all.filter(isFlagged) : all;
    if (!sortByVerdict) return filtered;
    const rank = (tone: string) => (tone === "danger" ? 0 : tone === "warning" ? 1 : tone === "neutral" ? 2 : tone === "pending" ? 3 : 4);
    return [...filtered].sort((a, b) => rank(docVerdict(a).tone) - rank(docVerdict(b).tone));
  }, [batch, flaggedOnly, sortByVerdict]);

  const matrixColumns = useMemo(() => {
    const columns: string[] = [];
    for (const item of batch?.jobs || []) {
      for (const analyzer of item.analyzers) if (!columns.includes(analyzer)) columns.push(analyzer);
    }
    return columns;
  }, [batch]);

  const ready = loadState === "ready" && batch && !soloJobId;

  // This route's identity band: a live telemetry strip. Not the generic
  // four-tile metrics row that used to appear on every screen — the counts are
  // folded into the progress readout so the table can start higher.
  const header = ready ? (
    <RouteHeader
      eyebrow={<>Batch <span className="mono-ref">{batch.id.slice(0, 8)}</span></>}
      title={`${batch.document_count} documents screened together`}
      sub={batch.status === "completed"
        ? "Batch analysis complete. Open any document for its full evidence."
        : "Screening in progress. Results fill in as each document completes."}
      aside={<span className={`status-pill ${batch.status}`}><span className="status-copy" key={batch.status}>{batch.status === "running" ? "Analyzing" : titleCase(batch.status)}</span></span>}
      below={
        <div className="batch-telemetry">
          <div className="bt-track" aria-label={`${stats.percent}% complete`}>
            <span style={{ width: `${Math.max(2, stats.percent)}%` }} />
          </div>
          <dl className="bt-stats">
            <div><dt>Complete</dt><dd>{stats.completedDocs}<small>/{batch.document_count}</small></dd></div>
            <div className="bt-flagged"><dt>Flagged</dt><dd>{stats.flaggedDocs}</dd></div>
            <div className="bt-errors"><dt>Check errors</dt><dd>{stats.errorDocs}</dd></div>
            <div><dt>Checks run</dt><dd>{stats.doneRuns}<small>/{stats.totalRuns}</small></dd></div>
          </dl>
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

          <div className="matrix-card">
            <div className="panel-title">
              <h2>Results overview</h2>
              <div className="matrix-tools">
                <button
                  type="button"
                  className={flaggedOnly ? "active" : ""}
                  aria-pressed={flaggedOnly}
                  onClick={() => setFlaggedOnly((on) => !on)}
                >Flagged only <b>{stats.flaggedDocs}</b></button>
                <button
                  type="button"
                  className={sortByVerdict ? "active" : ""}
                  aria-pressed={sortByVerdict}
                  onClick={() => setSortByVerdict((on) => !on)}
                >Sort by verdict</button>
              </div>
            </div>
            <div className="matrix-scroll">
              <table className="outcome-matrix">
                <thead>
                  <tr>
                    <th scope="col">Document</th>
                    {matrixColumns.map((column) => <th scope="col" key={column} title={analyzerLabel(column)}>{shortLabel(column)}</th>)}
                    <th scope="col">Verdict</th>
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
                            <td key={column} className={`cell-${cell.tone}`} title={`${shortLabel(column)} — ${cell.summary || state.label}`}>
                              <span aria-label={`${shortLabel(column)}: ${state.label}`}>{state.glyph}</span>
                            </td>
                          );
                        })}
                        <td><span className={`history-chip ${verdict.tone}`}>{verdict.label}</span></td>
                      </tr>
                    );
                  })}
                </tbody>
              </table>
            </div>
            {rows.length === 0 && (
              <p className="matrix-empty" role="status">
                Nothing is flagged in this batch. Every document came back clear.{" "}
                <button type="button" className="text-button" onClick={() => setFlaggedOnly(false)}>Show all {batch.document_count}</button>
              </p>
            )}
            <div className="matrix-legend" aria-hidden="true">
              <span><i className="cell-good">✓</i> Clear</span>
              <span><i className="cell-warning">!</i> Needs review</span>
              <span><i className="cell-neutral">?</i> Inconclusive</span>
              <span><i className="cell-danger">×</i> Check error</span>
              <span><i className="cell-pending">·</i> Pending</span>
            </div>
            <p className="matrix-hint">Select a document name to open its full evidence and record a review decision.</p>
          </div>
        </div>
      )}
    </AppShell>
  );
}
