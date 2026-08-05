"use client";

export type AnalysisSettings = {
  metadata: { dpi: number };
  qr_presence: { dpi: number; min_codes: number; max_pages: number; rotations: string; stop_after_first: boolean; effort: "low" | "medium" | "high" };
  same_phone: { dpi: number; max_analysis_side: number };
  tamper_scan: { dpi: number; review_threshold: number; annotate_all: boolean };
  readability: { dpi: number; noise_threshold: number; sharpness_threshold: number };
  photo_detection: { effort: "low" | "medium" | "high" };
};

export const DEFAULT_ANALYSIS_SETTINGS: AnalysisSettings = {
  metadata: { dpi: 200 },
  qr_presence: { dpi: 300, min_codes: 1, max_pages: 0, rotations: "0,90", stop_after_first: false, effort: "medium" },
  same_phone: { dpi: 180, max_analysis_side: 1800 },
  tamper_scan: { dpi: 200, review_threshold: 0.35, annotate_all: false },
  readability: { dpi: 150, noise_threshold: 8, sharpness_threshold: 500 },
  photo_detection: { effort: "medium" },
};

type Analyzer = { id: string; description: string; available?: boolean; availability_message?: string | null };

type Props = {
  analyzers: Analyzer[];
  selected: string[];
  settings: AnalysisSettings;
  onToggleAnalyzer: (id: string) => void;
  onChange: (settings: AnalysisSettings) => void;
  onReset: () => void;
  onBack: () => void;
};

const LABELS: Record<string, string> = {
  metadata: "Metadata",
  qr_presence: "QR detection",
  font_analysis: "Font analysis",
  moire: "Moire detection",
  same_phone: "Same-phone consistency",
  tamper_scan: "Whitener detection",
  readability: "Readability",
  photo_detection: "Document photo",
};

const SUMMARIES: Record<string, string> = {
  metadata: "Raster and file history checks",
  qr_presence: "Search effort and expected QR count",
  font_analysis: "Typeface, embedding, and OCR-layer rules",
  moire: "Adaptive frequency-pattern screening",
  same_phone: "Cross-page camera fingerprint comparison",
  tamper_scan: "Correction-fluid probability and regions",
  readability: "OCR, image noise, and sharpness limits",
  photo_detection: "Search effort for passport-style face extraction",
};

function numberValue(value: string, fallback: number) {
  const parsed = Number(value);
  return Number.isFinite(parsed) ? parsed : fallback;
}

function SettingField({ label, hint, value, min, max, step = 1, suffix, onChange }: {
  label: string;
  hint: string;
  value: number;
  min: number;
  max: number;
  step?: number;
  suffix?: string;
  onChange: (value: number) => void;
}) {
  return (
    <label className="setting-field">
      <span><strong>{label}</strong><small>{hint}</small></span>
      <span className="setting-input-wrap">
        <input
          type="number"
          value={value}
          min={min}
          max={max}
          step={step}
          onChange={(event) => onChange(numberValue(event.target.value, value))}
        />
        {suffix && <i>{suffix}</i>}
      </span>
    </label>
  );
}

function SettingSwitch({ label, hint, checked, onChange }: {
  label: string;
  hint: string;
  checked: boolean;
  onChange: (checked: boolean) => void;
}) {
  return (
    <label className="setting-switch-row">
      <span><strong>{label}</strong><small>{hint}</small></span>
      <input type="checkbox" checked={checked} onChange={(event) => onChange(event.target.checked)} />
      <span className="switch-control" aria-hidden="true"><i /></span>
    </label>
  );
}

export default function AdvancedSettings({ analyzers, selected, settings, onToggleAnalyzer, onChange, onReset, onBack }: Props) {
  const available = new Set(analyzers.map((item) => item.id));
  const orderedIds = ["metadata", "qr_presence", "font_analysis", "moire", "same_phone", "tamper_scan", "readability", "photo_detection"]
    .filter((id) => available.has(id));
  const customized = JSON.stringify(settings) !== JSON.stringify(DEFAULT_ANALYSIS_SETTINGS);

  const patch = <K extends keyof AnalysisSettings>(key: K, value: Partial<AnalysisSettings[K]>) => {
    onChange({ ...settings, [key]: { ...settings[key], ...value } });
  };

  return (
    <div className="advanced-settings">
      <div className="settings-intro">
        <div>
          <span className={`settings-profile ${customized ? "customized" : ""}`}>{customized ? "Custom profile" : "Balanced profile"}</span>
          <h2>Advanced settings</h2>
          <p>Tune detector effort and decision thresholds. Changes apply to every document in the next batch.</p>
        </div>
        <button type="button" className="text-button" onClick={onReset} disabled={!customized}>Reset defaults</button>
      </div>

      <div className="settings-notice" role="note">
        <strong>Use with care</strong>
        <span>Higher DPI and broader searches improve coverage but take longer to complete.</span>
      </div>

      <div className="settings-list">
        {orderedIds.map((id) => {
          const enabled = selected.includes(id);
          const analyzer = analyzers.find((item) => item.id === id);
          const selectable = analyzer?.available !== false;
          return (
            <section className={`setting-group ${enabled ? "enabled" : "disabled"} ${selectable ? "" : "unavailable"}`} key={id}>
              <header>
                <div><strong>{LABELS[id]}</strong><small>{selectable ? SUMMARIES[id] : analyzer?.availability_message || "Model service unavailable"}</small></div>
                <label className="analyzer-switch" aria-label={`${enabled ? "Disable" : "Enable"} ${LABELS[id]}`}>
                  <input type="checkbox" checked={enabled} disabled={!selectable} onChange={() => onToggleAnalyzer(id)} />
                  <span aria-hidden="true"><i /></span>
                </label>
              </header>

              {id === "metadata" && <div className="setting-fields">
                <SettingField label="Render DPI" hint="Image inspection resolution" value={settings.metadata.dpi} min={96} max={400} suffix="dpi" onChange={(dpi) => patch("metadata", { dpi })} />
              </div>}

              {id === "qr_presence" && <div className="setting-fields">
                <label className="setting-field">
                  <span><strong>Search effort</strong><small>How hard to look before reporting no QR code</small></span>
                  <select value={settings.qr_presence.effort} onChange={(event) => patch("qr_presence", { effort: event.target.value as AnalysisSettings["qr_presence"]["effort"] })}>
                    <option value="low">Low — fastest, one pass</option>
                    <option value="medium">Medium — widen only on a miss</option>
                    <option value="high">High — widest search, slowest</option>
                  </select>
                </label>
                <SettingField label="Search DPI" hint="Starting resolution for the first pass" value={settings.qr_presence.dpi} min={96} max={600} suffix="dpi" onChange={(dpi) => patch("qr_presence", { dpi })} />
                <SettingField label="Minimum QR codes" hint="Flag documents with fewer matches" value={settings.qr_presence.min_codes} min={0} max={20} onChange={(min_codes) => patch("qr_presence", { min_codes })} />
                <SettingField label="Page limit" hint="0 scans every page" value={settings.qr_presence.max_pages} min={0} max={100} onChange={(max_pages) => patch("qr_presence", { max_pages })} />
                <label className="setting-field">
                  <span><strong>Rotations</strong><small>Angles attempted during decoding</small></span>
                  <select value={settings.qr_presence.rotations} onChange={(event) => patch("qr_presence", { rotations: event.target.value })}>
                    <option value="0">0° only</option>
                    <option value="0,90">0° and 90°</option>
                    <option value="0,90,180,270">All rotations</option>
                  </select>
                </label>
                <SettingSwitch label="Stop after first" hint="Faster when only presence matters" checked={settings.qr_presence.stop_after_first} onChange={(stop_after_first) => patch("qr_presence", { stop_after_first })} />
              </div>}

              {id === "same_phone" && <div className="setting-fields">
                <SettingField label="Render DPI" hint="Capture fingerprint resolution" value={settings.same_phone.dpi} min={96} max={400} suffix="dpi" onChange={(dpi) => patch("same_phone", { dpi })} />
                <SettingField label="Analysis size" hint="Maximum page side" value={settings.same_phone.max_analysis_side} min={800} max={4000} step={100} suffix="px" onChange={(max_analysis_side) => patch("same_phone", { max_analysis_side })} />
              </div>}

              {id === "tamper_scan" && <div className="setting-fields">
                <SettingField label="Render DPI" hint="Whitener detection resolution" value={settings.tamper_scan.dpi} min={96} max={400} suffix="dpi" onChange={(dpi) => patch("tamper_scan", { dpi })} />
                <SettingField label="Review threshold" hint="Probability that triggers review" value={Math.round(settings.tamper_scan.review_threshold * 100)} min={10} max={90} suffix="%" onChange={(value) => patch("tamper_scan", { review_threshold: value / 100 })} />
                <SettingSwitch label="Annotate every page" hint="Keep clean-page annotations too" checked={settings.tamper_scan.annotate_all} onChange={(annotate_all) => patch("tamper_scan", { annotate_all })} />
              </div>}

              {id === "readability" && <div className="setting-fields">
                <SettingField label="OCR render DPI" hint="Resolution used for visual tests" value={settings.readability.dpi} min={96} max={300} suffix="dpi" onChange={(dpi) => patch("readability", { dpi })} />
                <SettingField label="Maximum image noise" hint="Lower is stricter" value={settings.readability.noise_threshold} min={1} max={50} step={0.5} onChange={(noise_threshold) => patch("readability", { noise_threshold })} />
                <SettingField label="Minimum sharpness" hint="Higher is stricter" value={settings.readability.sharpness_threshold} min={50} max={5000} step={50} onChange={(sharpness_threshold) => patch("readability", { sharpness_threshold })} />
              </div>}

              {id === "photo_detection" && <div className="setting-fields">
                <label className="setting-field">
                  <span><strong>Search effort</strong><small>How hard to look before reporting the photos found are all of them</small></span>
                  <select value={settings.photo_detection.effort} onChange={(event) => patch("photo_detection", { effort: event.target.value as AnalysisSettings["photo_detection"]["effort"] })}>
                    <option value="low">Low — embedded images only, fastest</option>
                    <option value="medium">Medium — render pages only on a miss</option>
                    <option value="high">High — always render every page, slowest</option>
                  </select>
                </label>
                <p className="managed-setting">
                  <strong>Low</strong> reads only the images already stored inside the PDF and never
                  renders a page, so a photo printed into a page scan is missed. A &ldquo;no photo&rdquo;
                  result is reported as inconclusive rather than a fail, because the page was
                  never looked at. <em>About 2–6 seconds per document.</em>
                  <br /><br />
                  <strong>Medium</strong> is the default and what this detector has always done: read the
                  embedded images, and render every page only if that finds nothing.
                  <em>About 3–9 seconds per document.</em>
                  <br /><br />
                  <strong>High</strong> always renders every page at 400 dpi and sweeps it with a denser
                  window at a lower confidence floor, even when photos were already found — this is
                  what finds a second portrait beside the first, which matters for face matching
                  across a batch. It also surfaces more false positives.
                  <em>About 8–35 seconds for a short document; a 43-page file measured 166 seconds.
                  Documents beyond roughly 90 pages risk hitting the 6-minute detector timeout.</em>
                  <br /><br />
                  Timings measured on this machine over five documents from the current corpus; they
                  scale with page count and with how many faces each page contains.
                </p>
              </div>}

              {(id === "font_analysis" || id === "moire") && <p className="managed-setting">This detector uses calibrated adaptive rules. Enable or disable it here; no manual threshold is required.</p>}
            </section>
          );
        })}
      </div>

      <div className="settings-footer">
        <span>{selected.length} of {analyzers.length} checks enabled</span>
        <button type="button" className="primary-button" onClick={onBack}>Back to review setup <span aria-hidden="true">→</span></button>
      </div>
    </div>
  );
}
