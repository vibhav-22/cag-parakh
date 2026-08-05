"use client";

import { useEffect, useState } from "react";
import AppShell from "../components/app-shell";
import RouteHeader from "../components/route-header";
import ControlPanel, { type SetupProgress } from "../components/control-panel";

/**
 * /new — the setup room. Deliberately the narrowest and calmest screen in the
 * app: one column, one job, one button. Its identity signal is the three-step
 * track in the header band, which tracks the real state of the form below.
 */
const STEP_LABELS = ["Upload", "Configure", "Process", "Review"] as const;

function StepIndicator({ progress }: { progress: SetupProgress }) {
  // The active step is simply the first one that is not satisfied yet.
  const active = progress.files === 0 ? 1 : progress.checks === 0 ? 2 : 2;
  const hints = [
    progress.files === 0 ? "Nothing selected" : `${progress.files} ${progress.files === 1 ? "document" : "documents"}`,
    progress.analyzers === 0 ? "Loading detectors" : `${progress.checks} of ${progress.analyzers} selected`,
    "Runs after submission",
    "Evidence and decisions",
  ];

  return (
    <ol className="step-track" aria-label="Review setup progress">
      {STEP_LABELS.map((label, index) => {
        const step = index + 1;
        const state = step < active ? "done" : step === active ? "active" : "todo";
        return (
          <li key={label} className={`step-node ${state}`} aria-current={state === "active" ? "step" : undefined}>
            <span className="step-mark" aria-hidden="true">{state === "done" ? "✓" : step}</span>
            <span className="step-copy">
              <strong>{label}</strong>
              <small>{hints[index]}</small>
            </span>
          </li>
        );
      })}
    </ol>
  );
}

export default function NewReviewPage() {
  const [progress, setProgress] = useState<SetupProgress>({ files: 0, checks: 0, analyzers: 0 });
  const [projectId, setProjectId] = useState<string | null>(null);

  // Read once on mount. `useSearchParams` would force this route into a
  // Suspense boundary for a value that only ever seeds the form, and it is
  // unverified whether that builds cleanly under this app's vinext runtime.
  useEffect(() => {
    setProjectId(new URLSearchParams(window.location.search).get("project"));
  }, []);

  return (
    <AppShell
      route="new"
      chrome="rail"
      header={
        <RouteHeader
          eyebrow="Forensic intake / New case"
          title="New screening"
          sub="Submit source documents for integrity, metadata, and manipulation analysis."
          below={<StepIndicator progress={progress} />}
        />
      }
    >
      <ControlPanel projectId={projectId} onProgress={setProgress} />
    </AppShell>
  );
}
