"use client";

import { FormEvent, useCallback, useEffect, useState } from "react";
import { useParams, useRouter } from "next/navigation";
import AppShell from "../../components/app-shell";
import NavLink from "../../components/nav-link";
import RouteHeader from "../../components/route-header";
import { API_URL, relativeDay } from "../../lib/format";
import { useSession } from "../../lib/session";
import type { BatchSummary, Project } from "../../lib/types";

type LoadState = "loading" | "ready" | "missing" | "error";

/** A plain read of a batch's outcome for this list — no lazy hydration here.
 *  The history route's HYDRATE_LIMIT/HYDRATE_CONCURRENCY exist to enable
 *  filename search, which this page does not offer. */
function verdict(item: BatchSummary): { label: string; tone: string } {
  if (item.status !== "completed") return { label: "In progress", tone: "pending" };
  if (item.flagged_documents > 0) return { label: `${item.flagged_documents} flagged`, tone: "warning" };
  return { label: "All clear", tone: "good" };
}

export default function ProjectDetailPage() {
  const params = useParams<{ projectId: string }>();
  const projectId = params?.projectId;
  const router = useRouter();
  const { handleUnauthorized } = useSession();

  const [project, setProject] = useState<Project | null>(null);
  const [batches, setBatches] = useState<BatchSummary[]>([]);
  const [loadState, setLoadState] = useState<LoadState>("loading");
  const [now, setNow] = useState(0);

  const [renaming, setRenaming] = useState(false);
  const [renameDraft, setRenameDraft] = useState("");
  const [confirmingDelete, setConfirmingDelete] = useState(false);
  const [error, setError] = useState("");

  const load = useCallback(async () => {
    if (!projectId) return;
    setLoadState("loading");
    setNow(Date.now());
    try {
      const [projectResponse, batchesResponse] = await Promise.all([
        fetch(`${API_URL}/api/v1/projects/${projectId}`),
        fetch(`${API_URL}/api/v1/projects/${projectId}/batches`),
      ]);
      if (projectResponse.status === 401 || batchesResponse.status === 401) return handleUnauthorized();
      if (projectResponse.status === 404) { setLoadState("missing"); return; }
      if (!projectResponse.ok || !batchesResponse.ok) throw new Error("unavailable");
      const projectPayload: Project = await projectResponse.json();
      const batchesPayload: BatchSummary[] = await batchesResponse.json();
      setProject(projectPayload);
      setBatches(batchesPayload);
      setRenameDraft(projectPayload.name);
      setLoadState("ready");
    } catch {
      setLoadState("error");
    }
  }, [projectId, handleUnauthorized]);

  // eslint-disable-next-line react-hooks/set-state-in-effect
  useEffect(() => { load(); }, [load]);

  async function submitRename(event: FormEvent) {
    event.preventDefault();
    if (!project) return;
    const name = renameDraft.trim();
    if (!name || name === project.name) { setRenaming(false); return; }
    setError("");
    try {
      const response = await fetch(`${API_URL}/api/v1/projects/${project.id}/rename`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ name }),
      });
      if (response.status === 401) return handleUnauthorized();
      const payload = await response.json();
      if (!response.ok) throw new Error(payload?.detail || "Unable to rename the project");
      setProject(payload);
      setRenaming(false);
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : "Unable to rename the project");
    }
  }

  async function confirmDelete() {
    if (!project) return;
    setError("");
    try {
      const response = await fetch(`${API_URL}/api/v1/projects/${project.id}`, { method: "DELETE" });
      if (response.status === 401) return handleUnauthorized();
      if (!response.ok && response.status !== 404) throw new Error("Unable to delete the project");
      router.push("/projects");
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : "Unable to delete the project");
      setConfirmingDelete(false);
    }
  }

  if (loadState === "missing") {
    return (
      <AppShell route="projects" chrome="rail" header={<RouteHeader eyebrow="Workspace" title="Project" />}>
        <div className="route-message error" role="alert">
          <strong>This project is not on this device</strong>
          <span>Projects are stored locally by the machine that created them, so a link opened elsewhere will not resolve.</span>
          <NavLink className="text-button" href="/projects">Back to Projects</NavLink>
        </div>
      </AppShell>
    );
  }

  return (
    <AppShell
      route="projects"
      chrome="rail"
      header={
        <RouteHeader
          eyebrow={<NavLink href="/projects">Projects</NavLink>}
          title={project?.name || "Loading…"}
          sub={project ? `${project.batch_count} ${project.batch_count === 1 ? "test" : "tests"} filed` : undefined}
          aside={project && (
            <div className="project-actions">
              <NavLink className="primary-button" href={`/new?project=${project.id}`}>New test</NavLink>
              <button type="button" className="text-button" onClick={() => { setRenaming(true); setRenameDraft(project.name); }}>Rename</button>
              <button type="button" className="text-button" onClick={() => setConfirmingDelete(true)}>Delete</button>
            </div>
          )}
          below={
            <>
              {renaming && (
                <form className="project-create" onSubmit={submitRename}>
                  <input
                    type="text"
                    value={renameDraft}
                    aria-label="Project name"
                    maxLength={80}
                    autoFocus
                    onChange={(event) => setRenameDraft(event.target.value)}
                  />
                  <button type="submit" className="primary-button">Save</button>
                  <button type="button" className="text-button" onClick={() => setRenaming(false)}>Cancel</button>
                </form>
              )}
              {confirmingDelete && project && (
                <div className="project-delete-confirm" role="alertdialog">
                  <p>
                    Delete <strong>{project.name}</strong>? The {project.batch_count}{" "}
                    {project.batch_count === 1 ? "test" : "tests"} inside stay in your Batches library.
                  </p>
                  <button type="button" className="primary-button" onClick={confirmDelete}>Delete project</button>
                  <button type="button" className="text-button" onClick={() => setConfirmingDelete(false)}>Cancel</button>
                </div>
              )}
            </>
          }
        />
      }
    >
      {error && <div className="feedback-toast error" role="alert">{error}</div>}

      {loadState === "loading" && (
        <div className="route-message" role="status" aria-live="polite">Loading project…</div>
      )}

      {loadState === "error" && (
        <div className="route-message error" role="alert">
          <strong>The project could not be loaded</strong>
          <span>The screening service did not answer. Confirm the backend is running, then retry.</span>
          <button type="button" className="text-button" onClick={load}>Retry</button>
        </div>
      )}

      {loadState === "ready" && batches.length === 0 && (
        <div className="project-empty">
          <strong>No tests yet</strong>
          <p>Every screening run you file into this project will show up here.</p>
          {project && <NavLink className="primary-button" href={`/new?project=${project.id}`}>New test</NavLink>}
        </div>
      )}

      {loadState === "ready" && batches.length > 0 && (
        <ul className="project-list" aria-label="Tests in this project">
          {batches.map((item) => {
            const tone = verdict(item);
            return (
              <li key={item.id}>
                <NavLink className="project-row" href={`/batches/${item.id}`}>
                  <span className="project-name" title={item.name}>{item.name}</span>
                  <span className="project-count">
                    <span className={`history-chip ${tone.tone}`}>{tone.label}</span>
                  </span>
                  <span className="project-activity">{relativeDay(item.created_at, now)}</span>
                  <span className="project-go" aria-hidden="true">→</span>
                </NavLink>
              </li>
            );
          })}
        </ul>
      )}
    </AppShell>
  );
}
