"use client";

import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import AppShell from "../components/app-shell";
import NavLink from "../components/nav-link";
import RouteHeader from "../components/route-header";
import { API_URL, docVerdict, formatWhen, titleCase } from "../lib/format";
import { useSession } from "../lib/session";
import type { Batch, BatchSummary } from "../lib/types";

type LoadState = "loading" | "ready" | "error";
type VerdictFilter = "all" | "flagged" | "clear" | "running" | "reviewed";
type RangeFilter = "all" | "7" | "30";
type SortKey = "newest" | "flagged";

/** What batch detail adds on top of the list endpoint: filenames to search and
 *  the decisions a reviewer already recorded. */
type CaseDetail = {
  filenames: string[];
  decided: number;
  documents: number;
  decisions: string[];
  hasErrors: boolean;
};

// The list endpoint carries no filenames and no review decisions, so detail is
// fetched lazily behind the rendered list. Three at a time keeps the route
// responsive without ever fanning out one request per batch at once.
const HYDRATE_LIMIT = 60;
const HYDRATE_CONCURRENCY = 3;

const VERDICT_FILTERS: Array<{ id: VerdictFilter; label: string }> = [
  { id: "all", label: "All cases" },
  { id: "flagged", label: "Flagged" },
  { id: "clear", label: "All clear" },
  { id: "running", label: "In progress" },
  { id: "reviewed", label: "Decision recorded" },
];

function summarize(batch: Batch): CaseDetail {
  const decisions: string[] = [];
  let decided = 0;
  let hasErrors = false;
  for (const job of batch.jobs) {
    if (job.review?.decision) {
      decided += 1;
      const label = titleCase(job.review.decision);
      if (!decisions.includes(label)) decisions.push(label);
    }
    if (docVerdict(job).tone === "danger") hasErrors = true;
  }
  return {
    filenames: batch.jobs.map((job) => job.filename),
    decided,
    documents: batch.jobs.length,
    decisions,
    hasErrors,
  };
}

/** Human day label above the exact timestamp, so a scan down the column reads
 *  as time passing rather than as ten identical dates. */
function relativeDay(iso: string, now: number): string {
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

function caseVerdict(item: BatchSummary, detail?: CaseDetail): { label: string; tone: string } {
  if (item.status !== "completed") return { label: "In progress", tone: "pending" };
  if (item.flagged_documents > 0) {
    return { label: `${item.flagged_documents} flagged`, tone: "warning" };
  }
  if (detail?.hasErrors) return { label: "Check errors", tone: "danger" };
  return { label: "All clear", tone: "good" };
}

export default function HistoryPage() {
  const { handleUnauthorized } = useSession();
  const [cases, setCases] = useState<BatchSummary[]>([]);
  const [details, setDetails] = useState<Record<string, CaseDetail>>({});
  const [loadState, setLoadState] = useState<LoadState>("loading");
  const [indexing, setIndexing] = useState(false);
  const [query, setQuery] = useState("");
  const [verdict, setVerdict] = useState<VerdictFilter>("all");
  const [range, setRange] = useState<RangeFilter>("all");
  const [sort, setSort] = useState<SortKey>("newest");
  // "Now" is sampled when the list loads rather than during render, so relative
  // dates and the period filter stay stable across re-renders.
  const [now, setNow] = useState(0);
  const hydrated = useRef(new Set<string>());

  const load = useCallback(async () => {
    setLoadState("loading");
    setNow(Date.now());
    try {
      const response = await fetch(`${API_URL}/api/v1/batches`);
      if (response.status === 401) return handleUnauthorized();
      if (!response.ok) throw new Error("unavailable");
      const payload: BatchSummary[] = await response.json();
      setCases(payload);
      setLoadState("ready");
    } catch {
      setLoadState("error");
    }
  }, [handleUnauthorized]);

  // The list resolves asynchronously, so no state is set during render.
  // eslint-disable-next-line react-hooks/set-state-in-effect
  useEffect(() => { load(); }, [load]);

  // Enrich behind the already-rendered list: filenames and recorded decisions
  // only exist on batch detail, and this route is never blocked on them.
  useEffect(() => {
    const queue = cases.slice(0, HYDRATE_LIMIT).map((item) => item.id).filter((id) => !hydrated.current.has(id));
    if (queue.length === 0) return;
    let cancelled = false;
    setIndexing(true);

    async function worker() {
      while (!cancelled) {
        const id = queue.shift();
        if (!id) return;
        hydrated.current.add(id);
        try {
          const response = await fetch(`${API_URL}/api/v1/batches/${id}`);
          if (response.status === 401) { cancelled = true; handleUnauthorized(); return; }
          if (!response.ok) continue;
          const batch: Batch = await response.json();
          if (cancelled) return;
          setDetails((current) => ({ ...current, [id]: summarize(batch) }));
        } catch {
          hydrated.current.delete(id);
        }
      }
    }

    Promise.all(Array.from({ length: HYDRATE_CONCURRENCY }, worker)).finally(() => {
      if (!cancelled) setIndexing(false);
    });
    return () => { cancelled = true; };
  }, [cases, handleUnauthorized]);

  const filtered = useMemo(() => {
    const needle = query.trim().toLowerCase();
    const cutoff = range === "all" || !now ? 0 : now - Number(range) * 86_400_000;
    const rows = cases.filter((item) => {
      const detail = details[item.id];
      if (cutoff) {
        const created = new Date(item.created_at).getTime();
        if (Number.isNaN(created) || created < cutoff) return false;
      }
      if (verdict === "flagged" && !(item.status === "completed" && item.flagged_documents > 0)) return false;
      if (verdict === "clear" && !(item.status === "completed" && item.flagged_documents === 0)) return false;
      if (verdict === "running" && item.status === "completed") return false;
      if (verdict === "reviewed" && !(detail && detail.decided > 0)) return false;
      if (needle) {
        const haystack = [
          item.id,
          formatWhen(item.created_at),
          ...(detail?.filenames || []),
          ...(detail?.decisions || []),
        ].join(" ").toLowerCase();
        if (!haystack.includes(needle)) return false;
      }
      return true;
    });
    rows.sort((a, b) => {
      if (sort === "flagged" && a.flagged_documents !== b.flagged_documents) {
        return b.flagged_documents - a.flagged_documents;
      }
      return new Date(b.created_at).getTime() - new Date(a.created_at).getTime();
    });
    return rows;
  }, [cases, details, query, verdict, range, sort, now]);

  const filtersActive = query.trim() !== "" || verdict !== "all" || range !== "all";
  function clearFilters() {
    setQuery("");
    setVerdict("all");
    setRange("all");
  }

  const totalDocuments = cases.reduce((sum, item) => sum + item.document_count, 0);
  const subline = loadState === "ready"
    ? cases.length === 0
      ? "Nothing screened on this device yet."
      : `${cases.length} ${cases.length === 1 ? "case" : "cases"} · ${totalDocuments} documents screened on this device.`
    : "Every batch screened on this device, newest first.";

  const controls = (
    <div className="hist-controls">
      <div className="hist-search">
        <svg viewBox="0 0 16 16" aria-hidden="true" focusable="false">
          <circle cx="7" cy="7" r="4.6" fill="none" stroke="currentColor" strokeWidth="1.6" />
          <path d="M10.6 10.6 14 14" fill="none" stroke="currentColor" strokeWidth="1.6" strokeLinecap="round" />
        </svg>
        <input
          type="search"
          value={query}
          placeholder="Search by document name, batch reference, or date"
          aria-label="Search past cases"
          onChange={(event) => setQuery(event.target.value)}
        />
        {query && (
          <button type="button" className="hist-search-clear" aria-label="Clear search" onClick={() => setQuery("")}>×</button>
        )}
      </div>

      <div className="hist-selects">
        <label>
          <span>Period</span>
          <select value={range} aria-label="Filter by period" onChange={(event) => setRange(event.target.value as RangeFilter)}>
            <option value="all">All time</option>
            <option value="7">Last 7 days</option>
            <option value="30">Last 30 days</option>
          </select>
        </label>
        <label>
          <span>Sort</span>
          <select value={sort} aria-label="Sort cases" onChange={(event) => setSort(event.target.value as SortKey)}>
            <option value="newest">Newest first</option>
            <option value="flagged">Most flagged</option>
          </select>
        </label>
      </div>

      <div className="hist-segments" role="group" aria-label="Filter by outcome">
        {VERDICT_FILTERS.map((option) => (
          <button
            key={option.id}
            type="button"
            className={verdict === option.id ? "active" : ""}
            aria-pressed={verdict === option.id}
            onClick={() => setVerdict(option.id)}
          >
            {option.label}
          </button>
        ))}
        {filtersActive && (
          <button type="button" className="hist-reset" onClick={clearFilters}>Clear filters</button>
        )}
      </div>
    </div>
  );

  return (
    <AppShell
      route="history"
      chrome="rail"
      header={
        <RouteHeader
          eyebrow="Case library"
          title="Case history"
          sub={subline}
          below={controls}
        />
      }
    >
      {loadState === "loading" && (
        <div className="hist-loading" role="status" aria-live="polite">
          <span className="hist-loading-copy">Loading stored cases…</span>
          {Array.from({ length: 6 }, (_, index) => (
            <div className="hist-skeleton" key={index} aria-hidden="true"><i /><i /><i /></div>
          ))}
        </div>
      )}

      {loadState === "error" && (
        <div className="hist-state error" role="alert">
          <strong>Case history could not be loaded</strong>
          <p>The screening service did not answer. Confirm the backend is running on this machine, then try again — nothing has been lost.</p>
          <button type="button" className="hist-action" onClick={load}>Retry</button>
        </div>
      )}

      {loadState === "ready" && cases.length === 0 && (
        <div className="hist-state empty">
          <span className="hist-state-mark" aria-hidden="true">◵</span>
          <strong>No cases yet — this is where your work will live</strong>
          <p>Every batch you screen on this device is kept here, with its verdict and the decision you recorded. Screen your first documents and this page becomes your working record.</p>
          <NavLink className="hist-action primary" href="/new">Start a new review<span aria-hidden="true"> →</span></NavLink>
        </div>
      )}

      {loadState === "ready" && cases.length > 0 && (
        <section className="hist-results" aria-label="Past cases">
          <div className="hist-count" role="status" aria-live="polite">
            <span>
              Showing <strong>{filtered.length}</strong> of {cases.length} {cases.length === 1 ? "case" : "cases"}
            </span>
            {indexing && <em>Indexing document names…</em>}
          </div>

          {filtered.length === 0 ? (
            <div className="hist-state empty narrow">
              <span className="hist-state-mark" aria-hidden="true">⌕</span>
              <strong>No cases match these filters</strong>
              <p>
                {indexing
                  ? "Document names are still being indexed, so a filename search may not be complete yet. Widen the filters or try again in a moment."
                  : "Nothing in your stored cases matches that search and filter combination. Widen the period or clear the filters to see everything again."}
              </p>
              <button type="button" className="hist-action" onClick={clearFilters}>Clear filters</button>
            </div>
          ) : (
            <ul className="hist-rows">
              {filtered.map((item) => {
                const detail = details[item.id];
                const tone = caseVerdict(item, detail);
                const percent = item.document_count
                  ? Math.round((item.completed_documents / item.document_count) * 100)
                  : 0;
                return (
                  <li key={item.id}>
                    <NavLink className="case-row" href={`/batches/${item.id}`}>
                      <span className="case-when">
                        <strong>{relativeDay(item.created_at, now)}</strong>
                        <small>{formatWhen(item.created_at)}</small>
                      </span>
                      <span className="case-docs">
                        <strong>{item.document_count}</strong>
                        <small>{item.document_count === 1 ? "document" : "documents"}</small>
                      </span>
                      <span className="case-signal">
                        <span className={`history-chip ${tone.tone}`}>{tone.label}</span>
                        {item.status !== "completed" && (
                          <span className="case-progress" aria-label={`${percent}% complete`}>
                            <i style={{ width: `${Math.max(6, percent)}%` }} />
                          </span>
                        )}
                      </span>
                      <span className="case-review">
                        {detail
                          ? detail.decided > 0
                            ? <><strong>{detail.decisions.join(", ")}</strong><small>{detail.decided} of {detail.documents} decided</small></>
                            : <small className="case-muted">No decision recorded</small>
                          : <small className="case-muted">—</small>}
                      </span>
                      <span className="case-ref" title={item.id}>{item.id.slice(0, 8)}</span>
                      <span className="case-go" aria-hidden="true">→</span>
                    </NavLink>
                  </li>
                );
              })}
            </ul>
          )}
        </section>
      )}
    </AppShell>
  );
}
