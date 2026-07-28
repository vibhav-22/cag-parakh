"use client";

import { useCallback, useEffect, useMemo, useState } from "react";
import { useRouter } from "next/navigation";
import {
  ArrowRight,
  CheckCircle,
  Clock,
  FileText,
  Flag,
  MagnifyingGlass,
  Scan,
  ShieldCheck,
} from "@phosphor-icons/react";
import AppShell from "./components/app-shell";
import NavLink from "./components/nav-link";
import { API_URL, docVerdict, formatWhen } from "./lib/format";
import { useSession } from "./lib/session";
import type { Batch, BatchSummary, Job } from "./lib/types";

type LoadState = "loading" | "ready" | "error";

type RecentDocument = {
  batchId: string;
  job: Job;
  createdAt: string;
};

function scoreFor(job: Job) {
  const results = Object.values(job.results);
  if (!results.length) return job.status === "completed" ? 0 : null;
  const review = results.filter((result) => result.outcome === "review").length;
  const errors = results.filter((result) => result.outcome === "error").length;
  const clear = results.filter((result) => result.outcome === "clear").length;
  return Math.max(0, Math.min(100, Math.round(((clear + (results.length - review - errors) * 0.5) / results.length) * 100)));
}

export default function DashboardPage() {
  const router = useRouter();
  const { handleUnauthorized, serviceStatus } = useSession();
  const [samplePending, setSamplePending] = useState(false);
  const [sampleError, setSampleError] = useState("");
  const [summaries, setSummaries] = useState<BatchSummary[]>([]);
  const [recent, setRecent] = useState<RecentDocument[]>([]);
  const [loadState, setLoadState] = useState<LoadState>("loading");
  const [query, setQuery] = useState("");

  const load = useCallback(async () => {
    setLoadState("loading");
    try {
      const response = await fetch(`${API_URL}/api/v1/batches`);
      if (response.status === 401) return handleUnauthorized();
      if (!response.ok) throw new Error("unavailable");
      const items = (await response.json()) as BatchSummary[];
      setSummaries(items);

      const details = await Promise.all(
        items.slice(0, 8).map(async (item) => {
          const detailResponse = await fetch(`${API_URL}/api/v1/batches/${item.id}`);
          if (!detailResponse.ok) return null;
          return (await detailResponse.json()) as Batch;
        }),
      );

      const documents = details
        .filter((batch): batch is Batch => Boolean(batch))
        .flatMap((batch) => batch.jobs.map((job) => ({ batchId: batch.id, job, createdAt: batch.created_at })))
        .slice(0, 8);
      setRecent(documents);
      setLoadState("ready");
    } catch {
      setLoadState("error");
    }
  }, [handleUnauthorized]);

  useEffect(() => { void load(); }, [load]);

  // First run. A reviewer who lands on an empty dashboard has no way to learn
  // what a verdict looks like without committing a real document, so offer a
  // generated sample instead. It is screened for real — the result is a genuine
  // reading of a synthetic file, not a mock-up.
  async function runSampleCase() {
    if (samplePending) return;
    setSamplePending(true);
    setSampleError("");
    try {
      const response = await fetch(`${API_URL}/api/v1/samples/case`, { method: "POST" });
      if (response.status === 401) return handleUnauthorized();
      const payload = await response.json();
      if (!response.ok) throw new Error(payload?.detail || "The sample case could not be started.");
      router.push(`/batches/${(payload as Batch).id}`);
    } catch (cause) {
      setSampleError(cause instanceof Error ? cause.message : "The sample case could not be started.");
      setSamplePending(false);
    }
  }

  const metrics = useMemo(() => {
    const screened = summaries.reduce((sum, item) => sum + item.document_count, 0);
    const flagged = summaries.reduce((sum, item) => sum + item.flagged_documents, 0);
    const processing = summaries.reduce((sum, item) => sum + Math.max(0, item.document_count - item.completed_documents), 0);
    return { screened, flagged, clean: Math.max(0, screened - flagged), processing };
  }, [summaries]);

  const visibleRows = useMemo(() => {
    const needle = query.trim().toLowerCase();
    if (!needle) return recent;
    return recent.filter(({ batchId, job }) => `${batchId} ${job.id} ${job.filename}`.toLowerCase().includes(needle));
  }, [query, recent]);

  const reviewQueue = recent.filter(({ job }) => {
    const tone = docVerdict(job).tone;
    return tone === "warning" || tone === "danger";
  }).slice(0, 4);

  return (
    <AppShell route="home" chrome="rail">
      <div className="pen-dashboard">
        <header className="dashboard-head">
          <div>
            <p className="pen-eyebrow">Operations overview</p>
            <h1>Dashboard</h1>
          </div>
          <div className="dashboard-actions">
            <label className="dashboard-search">
              <MagnifyingGlass aria-hidden="true" />
              <span className="sr-only">Search cases or documents</span>
              <input value={query} onChange={(event) => setQuery(event.target.value)} placeholder="Search case or document" />
            </label>
            <NavLink className="pen-primary" href="/new"><Scan weight="bold" aria-hidden="true" />New screening</NavLink>
          </div>
        </header>

        {loadState === "ready" && summaries.length === 0 && (
          <section className="first-run" aria-labelledby="first-run-heading">
            <div>
              <p className="pen-eyebrow">First run</p>
              <h2 id="first-run-heading">See a verdict before you commit a real document</h2>
              <p>
                Parakh generates a synthetic sample certificate, screens it for real, and opens the
                result. Every check that runs is the same one your own documents get — only the
                document is fabricated, and every copy of it is watermarked SAMPLE.
              </p>
              {sampleError && <p className="first-run-error" role="alert">{sampleError}</p>}
            </div>
            <div className="first-run-actions">
              <button type="button" className="pen-primary" onClick={() => void runSampleCase()} disabled={samplePending}>
                {samplePending ? "Screening the sample…" : "Run the sample case"}
              </button>
              <NavLink href="/new">Skip and upload my own<ArrowRight aria-hidden="true" /></NavLink>
            </div>
          </section>
        )}

        <section className="metric-grid" aria-label="Screening overview">
          <article>
            <span>Documents screened</span>
            <FileText aria-hidden="true" />
            <strong>{metrics.screened.toLocaleString()}</strong>
            <small>Across {summaries.length} batches</small>
          </article>
          <article>
            <span>Clean</span>
            <CheckCircle aria-hidden="true" />
            <strong>{metrics.clean.toLocaleString()}</strong>
            <small>{metrics.screened ? `${Math.round((metrics.clean / metrics.screened) * 100)}% of total` : "No results yet"}</small>
          </article>
          <article>
            <span>Flagged</span>
            <Flag aria-hidden="true" />
            <strong>{metrics.flagged.toLocaleString()}</strong>
            <small>{reviewQueue.length ? `${reviewQueue.length} ready for review` : "Queue is clear"}</small>
          </article>
          <article>
            <span>Processing</span>
            <Clock aria-hidden="true" />
            <strong>{String(metrics.processing).padStart(2, "0")}</strong>
            <small>{serviceStatus === "online" ? "Analysis service live" : "Service unavailable"}</small>
          </article>
        </section>

        <div className="dashboard-grid">
          <section className="dashboard-panel recent-panel">
            <header>
              <div><h2>Recent screenings</h2><p>Latest forensic analysis across all batches</p></div>
              <NavLink href="/history">View history<ArrowRight aria-hidden="true" /></NavLink>
            </header>

            <div className="dashboard-table-head" aria-hidden="true">
              <span>Document</span><span>Case ID</span><span>Result</span><span>Score</span><span>Screened</span>
            </div>

            {loadState === "loading" && <div className="dashboard-state" role="status">Loading recent screenings…</div>}
            {loadState === "error" && (
              <div className="dashboard-state error" role="alert">
                <span>The screening service is unavailable.</span>
                <button type="button" onClick={() => void load()}>Retry</button>
              </div>
            )}
            {loadState === "ready" && visibleRows.length === 0 && (
              <div className="dashboard-state">No screenings match this view.</div>
            )}

            <div className="dashboard-rows">
              {visibleRows.map(({ batchId, job, createdAt }) => {
                const verdict = docVerdict(job);
                const score = scoreFor(job);
                return (
                  <NavLink href={`/batches/${batchId}/documents/${job.id}`} className="dashboard-row" key={job.id}>
                    <span className="dashboard-document"><i><FileText aria-hidden="true" /></i><strong>{job.filename}</strong></span>
                    <span className="mono">{batchId.slice(0, 10)}</span>
                    <span><b className={`status-chip ${verdict.tone}`}>{verdict.label}</b></span>
                    <span className="score">{score === null ? "—" : score}</span>
                    <span className="mono">{formatWhen(createdAt)}</span>
                  </NavLink>
                );
              })}
            </div>
          </section>

          <aside className="dashboard-side">
            <section className="dashboard-panel queue-panel">
              <header><h2>Review queue</h2><span>{reviewQueue.length}</span></header>
              {reviewQueue.length === 0 ? (
                <div className="queue-empty"><ShieldCheck aria-hidden="true" /><p>No flagged documents waiting.</p></div>
              ) : reviewQueue.map(({ batchId, job }) => {
                const findings = Object.values(job.results).filter((result) => result.outcome === "review" || result.outcome === "error");
                return (
                  <NavLink href={`/batches/${batchId}/documents/${job.id}`} key={job.id}>
                    <i className={docVerdict(job).tone} aria-hidden="true" />
                    <span><strong>{job.filename}</strong><small>{findings[0]?.summary || "Requires investigator review"}</small></span>
                    <ArrowRight aria-hidden="true" />
                  </NavLink>
                );
              })}
            </section>
            <section className="integrity-panel">
              <header><h2>System integrity</h2><span><i className={serviceStatus} />{serviceStatus}</span></header>
              <dl>
                <div><dt>Analysis service</dt><dd>{serviceStatus === "online" ? "Verified" : "Unavailable"}</dd></div>
                <div><dt>Evidence store</dt><dd>Local</dd></div>
                <div><dt>Review model</dt><dd>Human-led</dd></div>
              </dl>
            </section>
          </aside>
        </div>
      </div>
    </AppShell>
  );
}
