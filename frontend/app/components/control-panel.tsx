"use client";

import { FormEvent, useEffect, useState } from "react";
import { useRouter } from "next/navigation";
import AdvancedSettings, { AnalysisSettings, DEFAULT_ANALYSIS_SETTINGS } from "../advanced-settings";
import { API_URL, MAX_BATCH_FILES, analyzerLabel, initialAnalysisSettings, storedDefaultAnalyzers } from "../lib/format";
import { useSession } from "../lib/session";
import type { Analyzer, Batch } from "../lib/types";

/**
 * The body of the /new route: upload, presets, check selection, and the
 * per-run overrides disclosure. It is a form, not a sidebar — the page owns
 * the shell and the header band, this owns the fields.
 *
 * Setup progress is reported upward so the route header can light the right
 * step; the stored-batch list has moved to /history.
 */
export type SetupProgress = { files: number; checks: number; analyzers: number };

const SUPPORTED_DOCUMENT_EXTENSIONS = [".pdf", ".jpg", ".jpeg", ".png", ".webp", ".tif", ".tiff"];

function isSupportedDocument(file: File) {
  const name = file.name.toLowerCase();
  return SUPPORTED_DOCUMENT_EXTENSIONS.some((extension) => name.endsWith(extension));
}

export default function ControlPanel({ onProgress }: { onProgress?: (progress: SetupProgress) => void }) {
  const router = useRouter();
  const { handleUnauthorized, setServiceStatus } = useSession();
  const [analyzers, setAnalyzers] = useState<Analyzer[]>([]);
  const [selected, setSelected] = useState<string[]>([]);
  const [files, setFiles] = useState<File[]>([]);
  const [error, setError] = useState("");
  const [submitting, setSubmitting] = useState(false);
  const [dragOver, setDragOver] = useState(false);
  const [overridesOpen, setOverridesOpen] = useState(false);
  const [analysisSettings, setAnalysisSettings] = useState<AnalysisSettings>(initialAnalysisSettings);

  // Deliberately no persistence here. These are per-run overrides for THIS
  // batch, seeded from the saved defaults and never written back — which is
  // what the disclosure copy promises the user. Writing them back would also
  // clobber `default_analyzers`, since `initialAnalysisSettings()` allow-lists
  // detector keys and drops everything else on the round trip.

  useEffect(() => {
    fetch(`${API_URL}/api/v1/analyzers`)
      .then((response) => {
        if (response.status === 401) throw new Error("unauthorized");
        if (!response.ok) throw new Error("Backend is unavailable");
        return response.json();
      })
      .then((data: { analyzers: Analyzer[] }) => {
        setAnalyzers(data.analyzers);
        // Seed the run from the user's saved default checks. Fall back to
        // everything on when no defaults are saved, or when the saved set no
        // longer matches any analyzer the backend offers.
        const ids = data.analyzers.filter((item) => item.available !== false).map((item) => item.id);
        const defaults = storedDefaultAnalyzers();
        const seeded = defaults ? ids.filter((id) => defaults.includes(id)) : ids;
        setSelected(seeded.length ? seeded : ids);
        setServiceStatus("online");
      })
      .catch((cause) => {
        if (cause instanceof Error && cause.message === "unauthorized") return handleUnauthorized();
        setError("The screening service is unreachable. Start the backend, then refresh this page.");
        setServiceStatus("offline");
      });
  }, [handleUnauthorized, setServiceStatus]);

  // The header band lives on the page, so the step indicator has to be told
  // what the form knows. Counts only, and only when they actually change.
  useEffect(() => {
    onProgress?.({ files: files.length, checks: selected.length, analyzers: analyzers.length });
  }, [files.length, selected.length, analyzers.length, onProgress]);

  async function submit(event: FormEvent) {
    event.preventDefault();
    if (files.length === 0 || selected.length === 0) return;
    setError("");
    setSubmitting(true);
    const body = new FormData();
    for (const item of files) body.append("files", item);
    body.append("settings", JSON.stringify(analysisSettings));
    try {
      const response = await fetch(`${API_URL}/api/v1/batches?analyzers=${encodeURIComponent(selected.join(","))}`, { method: "POST", body });
      if (response.status === 401) return handleUnauthorized();
      const payload: Batch = await response.json();
      if (!response.ok) throw new Error((payload as unknown as { detail?: string }).detail || "Unable to start analysis");
      setFiles([]);
      router.push(`/batches/${payload.id}`);
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : "Unable to start analysis");
    } finally {
      setSubmitting(false);
    }
  }

  function acceptFiles(list: FileList | null) {
    if (!list || list.length === 0) return;
    const incoming = Array.from(list);
    const rejected = incoming.filter((item) => !isSupportedDocument(item));
    const accepted = incoming.filter(isSupportedDocument);
    setFiles((current) => {
      const merged = [...current];
      for (const item of accepted) {
        if (!merged.some((existing) => existing.name === item.name && existing.size === item.size)) merged.push(item);
      }
      return merged.slice(0, MAX_BATCH_FILES);
    });
    setError(rejected.length
      ? `Skipped ${rejected.length} unsupported ${rejected.length === 1 ? "file" : "files"}. Use PDF, JPG, PNG, WebP, or TIFF.`
      : "");
  }

  function removeFile(target: File) {
    setFiles((current) => current.filter((item) => item !== target));
  }

  function toggleAnalyzer(id: string) {
    if (analyzers.find((item) => item.id === id)?.available === false) return;
    setSelected((current) => current.includes(id) ? current.filter((item) => item !== id) : [...current, id]);
  }

  function applyPreset(preset: "quick" | "standard" | "deep") {
    const available = analyzers.filter((item) => item.available !== false);
    if (preset === "deep") setSelected(available.map((item) => item.id));
    else if (preset === "quick") setSelected(available.slice(0, Math.min(3, available.length)).map((item) => item.id));
    else setSelected(available.slice(0, Math.max(1, Math.ceil(available.length * 0.65))).map((item) => item.id));
  }

  const totalUploadMb = files.reduce((sum, item) => sum + item.size, 0) / 1024 / 1024;
  const overridden = JSON.stringify(analysisSettings) !== JSON.stringify(DEFAULT_ANALYSIS_SETTINGS);

  return (
    <form className="setup-form" aria-label="New document review" onSubmit={submit}>
      <section className="setup-block" aria-labelledby="setup-documents">
        <div className="setup-block-head">
          <span className="setup-ordinal" aria-hidden="true">1</span>
          <div>
            <h2 id="setup-documents">Select the documents</h2>
            <p>Everything stays on this machine. Nothing is uploaded to a third party.</p>
          </div>
        </div>

        <div className="field-label"><span>Documents</span><small>PDF or image, up to 25 MB each</small></div>
        <label
          className={`dropzone ${files.length ? "has-file" : ""} ${dragOver ? "drag-over" : ""}`}
          onDragOver={(event) => { event.preventDefault(); setDragOver(true); }}
          onDragLeave={() => setDragOver(false)}
          onDrop={(event) => { event.preventDefault(); setDragOver(false); acceptFiles(event.dataTransfer.files); }}
        >
          <input
            type="file"
            accept="application/pdf,image/jpeg,image/png,image/webp,image/tiff,.pdf,.jpg,.jpeg,.png,.webp,.tif,.tiff"
            multiple
            onChange={(event) => { acceptFiles(event.target.files); event.target.value = ""; }}
          />
          <span className="upload-icon" aria-hidden="true">+</span>
          {files.length
            ? <><strong>{files.length} {files.length === 1 ? "document" : "documents"} ready</strong><small>{totalUploadMb.toFixed(2)} MB total — add more or remove below</small></>
            : <><strong>Choose PDFs or images</strong><small>JPG, PNG, WebP, or TIFF — screen up to {MAX_BATCH_FILES} at once</small></>}
        </label>
        {files.length > 0 && (
          <ul className="file-list" aria-label="Documents selected for screening">
            {files.map((item) => (
              <li key={`${item.name}-${item.size}`}>
                <span className="file-name">{item.name}</span>
                <small>{(item.size / 1024 / 1024).toFixed(2)} MB</small>
                <button type="button" aria-label={`Remove ${item.name}`} onClick={() => removeFile(item)}>×</button>
              </li>
            ))}
            <li className="file-list-actions">
              <button type="button" className="text-button" onClick={() => setFiles([])}>Clear all</button>
            </li>
          </ul>
        )}
        {submitting && <div className="upload-feedback" role="status"><div><span>Uploading {files.length === 1 ? "document" : `${files.length} documents`}</span><small>Preparing secure analysis</small></div><i><b /></i></div>}
      </section>

      <section className="setup-block" aria-labelledby="setup-checks">
        <div className="setup-block-head">
          <span className="setup-ordinal" aria-hidden="true">2</span>
          <div>
            <h2 id="setup-checks">Choose the checks</h2>
            <p>Start from a preset, then add or drop individual detectors for this case.</p>
          </div>
        </div>

        <div className="preset-heading"><span>Investigation preset</span><small>Choose a starting point, then refine checks below.</small></div>
        <div className="preset-options" role="group" aria-label="Investigation presets">
          <button type="button" onClick={() => applyPreset("quick")}><strong>Quick</strong><span>Fast triage</span></button>
          <button type="button" onClick={() => applyPreset("standard")}><strong>Standard</strong><span>Recommended</span></button>
          <button type="button" onClick={() => applyPreset("deep")}><strong>Deep</strong><span>Full review</span></button>
        </div>

        <div className="analyzer-heading">
          <div><h3>Screening checks</h3><span>{selected.length} of {analyzers.length} selected</span></div>
          <button type="button" className="text-button" onClick={() => {
            const available = analyzers.filter((item) => item.available !== false).map((item) => item.id);
            setSelected(selected.length === available.length ? [] : available);
          }}>{selected.length === analyzers.filter((item) => item.available !== false).length ? "Clear all" : "Select all"}</button>
        </div>
        <div className="analyzer-list">
          {analyzers.length === 0 && !error && Array.from({ length: 5 }, (_, index) => <div className="analyzer-skeleton" aria-hidden="true" key={index}><i /><span><b /><small /></span></div>)}
          {analyzers.map((analyzer) => (
            <label className={`analyzer-row ${analyzer.available === false ? "unavailable" : ""}`} key={analyzer.id} title={analyzer.availability_message || undefined}>
              <input type="checkbox" checked={selected.includes(analyzer.id)} disabled={analyzer.available === false} onChange={() => toggleAnalyzer(analyzer.id)} />
              <span className="checkmark">✓</span>
              <span><strong>{analyzerLabel(analyzer.id)}</strong><small>{analyzer.available === false ? analyzer.availability_message : analyzer.description}</small></span>
            </label>
          ))}
        </div>
      </section>

      <details
        className="run-overrides"
        open={overridesOpen}
        onToggle={(event) => setOverridesOpen(event.currentTarget.open)}
      >
        <summary>
          <span className="run-overrides-copy">
            <strong>Run overrides</strong>
            <small>Advanced settings for this batch only, seeded from your saved defaults.</small>
          </span>
          <span className={`run-overrides-state ${overridden ? "custom" : ""}`}>{overridden ? "Modified" : "Defaults"}</span>
          <span className="run-overrides-chevron" aria-hidden="true">›</span>
        </summary>
        <div className="run-overrides-body">
          <AdvancedSettings
            analyzers={analyzers}
            selected={selected}
            settings={analysisSettings}
            onToggleAnalyzer={toggleAnalyzer}
            onChange={setAnalysisSettings}
            onReset={() => setAnalysisSettings(DEFAULT_ANALYSIS_SETTINGS)}
            onBack={() => setOverridesOpen(false)}
          />
        </div>
      </details>

      {error && <div className="feedback-toast error" role="alert"><span aria-hidden="true">!</span><p><strong>Unable to continue</strong>{error}</p></div>}

      <div className="setup-submit">
        <button className="primary-button" disabled={files.length === 0 || selected.length === 0 || submitting}>
          {submitting ? "Submitting..." : files.length > 1 ? `Screen ${files.length} documents` : "Run document analysis"}
          <span aria-hidden="true">→</span>
        </button>
        <p className="setup-submit-note">
          {files.length === 0
            ? "Add at least one PDF or image to begin."
            : selected.length === 0
              ? "Select at least one screening check to begin."
              : `${files.length} ${files.length === 1 ? "document" : "documents"} · ${selected.length} ${selected.length === 1 ? "check" : "checks"} · results open in a case you can return to.`}
        </p>
      </div>
    </form>
  );
}
