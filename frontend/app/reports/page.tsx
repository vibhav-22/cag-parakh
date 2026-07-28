"use client";

import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import {
  ArrowRight,
  CalendarBlank,
  DownloadSimple,
  File,
  Files,
  Flag,
  ShieldCheck,
  Timer,
} from "@phosphor-icons/react";
import AppShell from "../components/app-shell";
import { API_URL, analyzerLabel, checkStatusOf } from "../lib/format";
import { useSession } from "../lib/session";
import type { Batch, BatchSummary, Job } from "../lib/types";

type LoadState = "loading" | "ready" | "error";
type RangeDays = "30" | "90" | "365" | "all";

type VolumePoint = {
  label: string;
  clean: number;
  flagged: number;
};

type ReasonPoint = {
  id: string;
  label: string;
  value: number;
  color: string;
};

type TypeRow = {
  label: string;
  screened: number;
  clean: number;
  flagged: number;
  averageSeconds: number;
  share: number;
};

const RANGE_LABELS: Record<RangeDays, string> = {
  "30": "Last 30 days",
  "90": "Last 90 days",
  "365": "Last 12 months",
  all: "All time",
};

const REASON_COLORS = ["#dc302f", "#c55c09", "#3364d7", "#8794a4", "#7254c8", "#147d43"];

function durationSeconds(job: Job): number {
  const values = Object.values(job.analyzer_runs || {}).flatMap((run) => {
    if (!run.started_at || !run.completed_at) return [];
    const start = new Date(run.started_at).getTime();
    const end = new Date(run.completed_at).getTime();
    return Number.isFinite(start) && Number.isFinite(end) && end >= start ? [(end - start) / 1000] : [];
  });
  return values.length ? values.reduce((sum, value) => sum + value, 0) / values.length : 0;
}

function isJobFlagged(job: Job) {
  return !job.unanalyzable && Object.values(job.results || {}).some((result) => {
    const status = checkStatusOf(result);
    return status === "fail" || status === "error";
  });
}

function fileType(filename: string) {
  const extension = filename.split(".").pop()?.trim().toUpperCase();
  if (!extension || extension === filename.toUpperCase()) return "Other files";
  if (extension === "JPG" || extension === "JPEG") return "JPEG images";
  if (extension === "TIF" || extension === "TIFF") return "TIFF images";
  if (extension === "PNG") return "PNG images";
  if (extension === "WEBP") return "WebP images";
  if (extension === "PDF") return "PDF documents";
  return `${extension} files`;
}

function formatDuration(seconds: number) {
  if (!seconds) return "—";
  if (seconds < 60) return `${seconds.toFixed(seconds < 10 ? 1 : 0)}s`;
  return `${(seconds / 60).toFixed(1)}m`;
}

function formatDateRange(range: RangeDays, now: number) {
  if (!now || range === "all") return "ALL SCREENING HISTORY";
  const start = new Date(now - Number(range) * 86_400_000);
  const end = new Date(now);
  const format = new Intl.DateTimeFormat("en", { day: "2-digit", month: "short", year: "numeric" });
  return `${format.format(start)} — ${format.format(end)}`.toUpperCase();
}

function roundedRect(
  context: CanvasRenderingContext2D,
  x: number,
  y: number,
  width: number,
  height: number,
  radius: number,
) {
  context.beginPath();
  context.roundRect(x, y, width, height, radius);
}

function VolumeChart({ points }: { points: VolumePoint[] }) {
  const ref = useRef<HTMLCanvasElement | null>(null);

  useEffect(() => {
    const canvas = ref.current;
    if (!canvas) return;
    const draw = () => {
      const bounds = canvas.getBoundingClientRect();
      const ratio = Math.min(window.devicePixelRatio || 1, 2);
      canvas.width = Math.max(1, Math.round(bounds.width * ratio));
      canvas.height = Math.max(1, Math.round(bounds.height * ratio));
      const context = canvas.getContext("2d");
      if (!context) return;
      context.scale(ratio, ratio);
      context.clearRect(0, 0, bounds.width, bounds.height);

      const chartTop = 20;
      const chartBottom = bounds.height - 28;
      const chartHeight = Math.max(80, chartBottom - chartTop);
      const slot = bounds.width / Math.max(points.length, 1);
      const barWidth = Math.min(28, Math.max(13, slot * 0.34));
      const max = Math.max(1, ...points.map((point) => point.clean + point.flagged));

      points.forEach((point, index) => {
        const total = point.clean + point.flagged;
        const fullHeight = Math.max(4, (total / max) * chartHeight);
        const flaggedHeight = total ? (point.flagged / total) * fullHeight : 0;
        const cleanHeight = Math.max(0, fullHeight - flaggedHeight);
        const x = slot * index + (slot - barWidth) / 2;
        const y = chartBottom - fullHeight;

        context.fillStyle = "#16833f";
        roundedRect(context, x, y + flaggedHeight, barWidth, cleanHeight, 2);
        context.fill();
        if (flaggedHeight > 0) {
          context.fillStyle = "#d92f2f";
          roundedRect(context, x, y, barWidth, Math.max(3, flaggedHeight), 2);
          context.fill();
        }

        context.fillStyle = "#768394";
        context.font = "9px ui-monospace, SFMono-Regular, Consolas, monospace";
        context.textAlign = "center";
        context.fillText(point.label, x + barWidth / 2, bounds.height - 8);
      });
    };

    draw();
    const observer = new ResizeObserver(draw);
    observer.observe(canvas);
    return () => observer.disconnect();
  }, [points]);

  return <canvas ref={ref} className="reports-volume-canvas" aria-label="Screening volume over time" role="img" />;
}

function ReasonsChart({ points, total }: { points: ReasonPoint[]; total: number }) {
  const ref = useRef<HTMLCanvasElement | null>(null);

  useEffect(() => {
    const canvas = ref.current;
    if (!canvas) return;
    const draw = () => {
      const bounds = canvas.getBoundingClientRect();
      const size = Math.min(bounds.width, bounds.height);
      const ratio = Math.min(window.devicePixelRatio || 1, 2);
      canvas.width = Math.max(1, Math.round(bounds.width * ratio));
      canvas.height = Math.max(1, Math.round(bounds.height * ratio));
      const context = canvas.getContext("2d");
      if (!context) return;
      context.scale(ratio, ratio);
      context.clearRect(0, 0, bounds.width, bounds.height);
      const centerX = bounds.width / 2;
      const centerY = bounds.height / 2;
      const radius = Math.max(24, size * 0.38);
      const lineWidth = Math.max(12, radius * 0.38);
      let angle = -Math.PI / 2;

      if (!total) {
        context.strokeStyle = "#e4eaf1";
        context.lineWidth = lineWidth;
        context.beginPath();
        context.arc(centerX, centerY, radius - lineWidth / 2, 0, Math.PI * 2);
        context.stroke();
      } else {
        points.forEach((point) => {
          const slice = (point.value / total) * Math.PI * 2;
          context.strokeStyle = point.color;
          context.lineWidth = lineWidth;
          context.beginPath();
          context.arc(centerX, centerY, radius - lineWidth / 2, angle, angle + slice);
          context.stroke();
          angle += slice;
        });
      }

      context.fillStyle = "#15202d";
      context.textAlign = "center";
      context.font = "500 18px var(--font-geist-sans), system-ui";
      context.fillText(total.toLocaleString(), centerX, centerY + 1);
      context.fillStyle = "#7a8797";
      context.font = "8px ui-monospace, SFMono-Regular, Consolas, monospace";
      context.fillText(total === 1 ? "TOTAL FLAG" : "TOTAL FLAGS", centerX, centerY + 15);
    };

    draw();
    const observer = new ResizeObserver(draw);
    observer.observe(canvas);
    return () => observer.disconnect();
  }, [points, total]);

  return <canvas ref={ref} className="reports-reasons-canvas" aria-label="Distribution of flag reasons" role="img" />;
}

export default function ReportsPage() {
  const { handleUnauthorized } = useSession();
  const [summaries, setSummaries] = useState<BatchSummary[]>([]);
  const [details, setDetails] = useState<Record<string, Batch>>({});
  const [loadState, setLoadState] = useState<LoadState>("loading");
  const [range, setRange] = useState<RangeDays>("30");
  const [now, setNow] = useState(0);

  const load = useCallback(async () => {
    setLoadState("loading");
    setNow(Date.now());
    try {
      const response = await fetch(`${API_URL}/api/v1/batches`);
      if (response.status === 401) return handleUnauthorized();
      if (!response.ok) throw new Error("unavailable");
      setSummaries(await response.json());
      setLoadState("ready");
    } catch {
      setLoadState("error");
    }
  }, [handleUnauthorized]);

  // eslint-disable-next-line react-hooks/set-state-in-effect
  useEffect(() => { void load(); }, [load]);

  const activeSummaries = useMemo(() => {
    if (range === "all" || !now) return summaries;
    const cutoff = now - Number(range) * 86_400_000;
    return summaries.filter((item) => new Date(item.created_at).getTime() >= cutoff);
  }, [summaries, range, now]);

  useEffect(() => {
    const missing = activeSummaries.slice(0, 80).filter((item) => !details[item.id]);
    if (!missing.length) return;
    let cancelled = false;
    Promise.all(missing.map(async (item) => {
      try {
        const response = await fetch(`${API_URL}/api/v1/batches/${item.id}`);
        if (response.status === 401) { handleUnauthorized(); return null; }
        return response.ok ? await response.json() as Batch : null;
      } catch {
        return null;
      }
    })).then((items) => {
      if (cancelled) return;
      setDetails((current) => {
        const next = { ...current };
        items.forEach((item) => { if (item) next[item.id] = item; });
        return next;
      });
    });
    return () => { cancelled = true; };
  }, [activeSummaries, details, handleUnauthorized]);

  const detailsLoading = activeSummaries.some((item) => !details[item.id]);
  const jobs = useMemo(
    () => activeSummaries.flatMap((summary) => details[summary.id]?.jobs || []),
    [activeSummaries, details],
  );

  const totalDocuments = activeSummaries.reduce((sum, item) => sum + item.document_count, 0);
  const flaggedDocuments = activeSummaries.reduce((sum, item) => sum + item.flagged_documents, 0);
  const cleanDocuments = Math.max(0, totalDocuments - flaggedDocuments);
  const cleanRate = totalDocuments ? (cleanDocuments / totalDocuments) * 100 : 0;
  const reasonCounts = useMemo(() => {
    const counts = new Map<string, number>();
    jobs.forEach((job) => Object.entries(job.results || {}).forEach(([id, result]) => {
      const status = checkStatusOf(result);
      if (status === "fail" || status === "error") counts.set(id, (counts.get(id) || 0) + 1);
    }));
    return [...counts.entries()].sort((a, b) => b[1] - a[1]);
  }, [jobs]);
  const totalFlags = reasonCounts.reduce((sum, [, value]) => sum + value, 0) || flaggedDocuments;
  const averageTurnaround = jobs.length
    ? jobs.reduce((sum, job) => sum + durationSeconds(job), 0) / jobs.length
    : 0;

  const volume = useMemo<VolumePoint[]>(() => {
    const count = range === "30" ? 5 : range === "90" ? 8 : 10;
    const bucketDays = range === "30" ? 7 : range === "90" ? 14 : range === "365" ? 42 : 30;
    const end = now;
    const buckets = Array.from({ length: count }, (_, index) => {
      const bucketEnd = end - (count - index - 1) * bucketDays * 86_400_000;
      const bucketStart = bucketEnd - bucketDays * 86_400_000;
      return { start: bucketStart, end: bucketEnd, clean: 0, flagged: 0 };
    });
    activeSummaries.forEach((item) => {
      const time = new Date(item.created_at).getTime();
      const bucket = buckets.find((candidate) => time > candidate.start && time <= candidate.end);
      if (!bucket) return;
      bucket.flagged += item.flagged_documents;
      bucket.clean += Math.max(0, item.document_count - item.flagged_documents);
    });
    return buckets.map((bucket) => ({
      label: new Intl.DateTimeFormat("en", { day: "2-digit", month: "short" }).format(new Date(bucket.end)),
      clean: bucket.clean,
      flagged: bucket.flagged,
    }));
  }, [activeSummaries, range, now]);

  const reasons = useMemo<ReasonPoint[]>(() => {
    const source = reasonCounts.length ? reasonCounts : [["review_required", flaggedDocuments] as [string, number]];
    return source.filter(([, value]) => value > 0).slice(0, 5).map(([id, value], index) => ({
      id,
      label: id === "review_required" ? "Review required" : analyzerLabel(id),
      value,
      color: REASON_COLORS[index % REASON_COLORS.length],
    }));
  }, [reasonCounts, flaggedDocuments]);

  const typeRows = useMemo<TypeRow[]>(() => {
    const groups = new Map<string, { jobs: Job[]; flagged: number; seconds: number }>();
    jobs.forEach((job) => {
      const label = fileType(job.filename);
      const group = groups.get(label) || { jobs: [], flagged: 0, seconds: 0 };
      group.jobs.push(job);
      group.flagged += isJobFlagged(job) ? 1 : 0;
      group.seconds += durationSeconds(job);
      groups.set(label, group);
    });
    return [...groups.entries()].map(([label, group]) => ({
      label,
      screened: group.jobs.length,
      clean: group.jobs.length ? ((group.jobs.length - group.flagged) / group.jobs.length) * 100 : 0,
      flagged: group.flagged,
      averageSeconds: group.jobs.length ? group.seconds / group.jobs.length : 0,
      share: jobs.length ? (group.jobs.length / jobs.length) * 100 : 0,
    })).sort((a, b) => b.screened - a.screened);
  }, [jobs]);

  function exportReport() {
    const headers = ["File type", "Screened", "Clean rate", "Flagged", "Average time", "Share"];
    const rows = typeRows.map((row) => [
      row.label,
      row.screened,
      `${row.clean.toFixed(1)}%`,
      row.flagged,
      formatDuration(row.averageSeconds),
      `${row.share.toFixed(1)}%`,
    ]);
    const csv = [headers, ...rows].map((row) => row.map((cell) => `"${String(cell).replaceAll('"', '""')}"`).join(",")).join("\n");
    const url = URL.createObjectURL(new Blob([csv], { type: "text/csv;charset=utf-8" }));
    const anchor = document.createElement("a");
    anchor.href = url;
    anchor.download = `parakh-reports-${range}-days.csv`;
    anchor.click();
    URL.revokeObjectURL(url);
  }

  const kpis = [
    { label: "Documents screened", value: totalDocuments.toLocaleString(), icon: <Files weight="regular" />, tone: "green" },
    { label: "Clean rate", value: `${cleanRate.toFixed(1)}%`, icon: <ShieldCheck weight="regular" />, tone: "green" },
    { label: "Flags raised", value: totalFlags.toLocaleString(), icon: <Flag weight="regular" />, tone: "red" },
    { label: "Avg. turnaround", value: formatDuration(averageTurnaround), icon: <Timer weight="regular" />, tone: "blue" },
  ];

  return (
    <AppShell
      route="reports"
      chrome="rail"
      header={
        <header className="reports-header">
          <div>
            <p>SCREENING ANALYTICS <span>/</span> {formatDateRange(range, now)}</p>
            <h1>Reports &amp; Analytics</h1>
          </div>
          <div className="reports-header-actions">
            <label>
              <CalendarBlank aria-hidden="true" />
              <span className="sr-only">Reporting period</span>
              <select value={range} onChange={(event) => setRange(event.target.value as RangeDays)}>
                {Object.entries(RANGE_LABELS).map(([value, label]) => <option value={value} key={value}>{label}</option>)}
              </select>
            </label>
            <button type="button" onClick={exportReport} disabled={!typeRows.length}>
              <DownloadSimple aria-hidden="true" weight="bold" />
              Export report
            </button>
          </div>
        </header>
      }
    >
      {loadState === "loading" && (
        <div className="reports-state" role="status">
          <strong>Building the report…</strong>
          <span>Reading screening history from this device.</span>
        </div>
      )}

      {loadState === "error" && (
        <div className="reports-state error" role="alert">
          <strong>Reports could not be loaded</strong>
          <span>The screening service did not answer. Nothing has been changed.</span>
          <button type="button" onClick={load}>Retry</button>
        </div>
      )}

      {loadState === "ready" && (
        <div className="reports-page">
          <section className="reports-kpis" aria-label="Screening overview">
            {kpis.map((item) => (
              <article className="reports-kpi" key={item.label}>
                <div className={`reports-kpi-icon ${item.tone}`}>{item.icon}</div>
                <span>{item.label}</span>
                <strong>{item.value}</strong>
              </article>
            ))}
          </section>

          {totalDocuments === 0 ? (
            <div className="reports-empty">
              <Files aria-hidden="true" />
              <strong>No screenings in this period</strong>
              <p>Choose a wider date range or run a new screening to populate this report.</p>
            </div>
          ) : (
            <>
              <section className="reports-chart-grid">
                <article className="reports-panel reports-volume">
                  <header>
                    <div><h2>Screening Volume</h2><p>Documents processed over time</p></div>
                    <div className="reports-legend"><span className="clean">Clean</span><span className="flagged">Flagged</span></div>
                  </header>
                  <VolumeChart points={volume} />
                </article>

                <article className="reports-panel reports-reasons">
                  <header><div><h2>Flag Reasons</h2><p>Distribution of raised flags</p></div></header>
                  <div className="reports-reasons-body">
                    <ReasonsChart points={reasons} total={totalFlags} />
                    <ul>
                      {reasons.length ? reasons.map((reason) => (
                        <li key={reason.id}>
                          <i style={{ backgroundColor: reason.color }} />
                          <span>{reason.label}</span>
                          <strong>{totalFlags ? Math.round((reason.value / totalFlags) * 100) : 0}%</strong>
                        </li>
                      )) : <li className="reports-no-flags">No flags raised</li>}
                    </ul>
                  </div>
                </article>
              </section>

              <section className="reports-panel reports-table-panel">
                <header>
                  <div><h2>Performance by File Type</h2><p>How each document format moves through screening</p></div>
                  <a href="/history">View full breakdown <ArrowRight aria-hidden="true" /></a>
                </header>
                {detailsLoading && !typeRows.length ? (
                  <div className="reports-table-loading" role="status">Grouping document performance…</div>
                ) : (
                  <div className="reports-table-scroll">
                    <table>
                      <thead><tr><th>File type</th><th>Screened</th><th>Clean rate</th><th>Flagged</th><th>Avg. time</th><th>Share</th></tr></thead>
                      <tbody>
                        {typeRows.map((row) => (
                          <tr key={row.label}>
                            <th scope="row"><span className="reports-file-icon"><File weight="regular" /></span>{row.label}</th>
                            <td>{row.screened.toLocaleString()}</td>
                            <td className="reports-clean-rate">{row.clean.toFixed(1)}%</td>
                            <td className="reports-flag-count">{row.flagged.toLocaleString()}</td>
                            <td>{formatDuration(row.averageSeconds)}</td>
                            <td>
                              <span className="reports-share"><i style={{ width: `${Math.max(4, row.share)}%` }} /></span>
                              <small>{row.share.toFixed(0)}%</small>
                            </td>
                          </tr>
                        ))}
                      </tbody>
                    </table>
                  </div>
                )}
              </section>
            </>
          )}
        </div>
      )}
    </AppShell>
  );
}
