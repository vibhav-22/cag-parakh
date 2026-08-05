"use client";

import { FormEvent, useCallback, useEffect, useState } from "react";
import { useRouter } from "next/navigation";
import AppShell from "../components/app-shell";
import NavLink from "../components/nav-link";
import RouteHeader from "../components/route-header";
import { API_URL, relativeDay } from "../lib/format";
import { useSession } from "../lib/session";
import type { Project } from "../lib/types";

type LoadState = "loading" | "ready" | "error";

/**
 * The folder list — one level above /history. A project groups the batches
 * a reviewer wants kept together (an intake, a client, a sweep), the way a
 * ChatGPT project groups chats. Grouping only: no moving existing batches in,
 * no project-level presets, no rollup analytics. See control-panel.tsx for
 * how a run actually gets filed into one.
 */
export default function ProjectsPage() {
  const router = useRouter();
  const { handleUnauthorized } = useSession();
  const [projects, setProjects] = useState<Project[]>([]);
  const [loadState, setLoadState] = useState<LoadState>("loading");
  const [draftName, setDraftName] = useState("");
  const [creating, setCreating] = useState(false);
  const [error, setError] = useState("");
  // Sampled once per load, same reasoning as /history: relative dates must
  // stay stable across re-renders.
  const [now, setNow] = useState(0);

  const load = useCallback(async () => {
    setLoadState("loading");
    setNow(Date.now());
    try {
      const response = await fetch(`${API_URL}/api/v1/projects`);
      if (response.status === 401) return handleUnauthorized();
      if (!response.ok) throw new Error("unavailable");
      const payload: Project[] = await response.json();
      setProjects(payload);
      setLoadState("ready");
    } catch {
      setLoadState("error");
    }
  }, [handleUnauthorized]);

  // The list resolves asynchronously, so no state is set during render.
  // eslint-disable-next-line react-hooks/set-state-in-effect
  useEffect(() => { load(); }, [load]);

  async function createProject(event: FormEvent) {
    event.preventDefault();
    const name = draftName.trim();
    if (!name) return;
    setCreating(true);
    setError("");
    try {
      const response = await fetch(`${API_URL}/api/v1/projects`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ name }),
      });
      if (response.status === 401) return handleUnauthorized();
      const payload = await response.json();
      if (!response.ok) throw new Error(payload?.detail || "Unable to create the project");
      // Open what was just made — the ChatGPT behaviour, and it saves a click
      // before the reviewer starts the first test inside it.
      router.push(`/projects/${payload.id}`);
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : "Unable to create the project");
      setCreating(false);
    }
  }

  const totalBatches = projects.reduce((sum, item) => sum + item.batch_count, 0);
  const subline = loadState === "ready"
    ? projects.length === 0
      ? "Nothing grouped yet."
      : `${projects.length} ${projects.length === 1 ? "project" : "projects"} · ${totalBatches} ${totalBatches === 1 ? "test" : "tests"} filed.`
    : "Group the screening runs that belong together.";

  return (
    <AppShell
      route="projects"
      chrome="rail"
      header={
        <RouteHeader
          eyebrow="Workspace"
          title="Projects"
          sub={subline}
          below={
            <form className="project-create" onSubmit={createProject}>
              <input
                type="text"
                value={draftName}
                placeholder="Name a new project — e.g. Q3 passport intake"
                aria-label="New project name"
                maxLength={80}
                onChange={(event) => setDraftName(event.target.value)}
              />
              <button type="submit" className="primary-button" disabled={creating || !draftName.trim()}>
                {creating ? "Creating…" : "New project"}
              </button>
            </form>
          }
        />
      }
    >
      {error && <div className="feedback-toast error" role="alert">{error}</div>}

      {loadState === "loading" && (
        <div className="route-message" role="status" aria-live="polite">Loading projects…</div>
      )}

      {loadState === "error" && (
        <div className="route-message" role="alert">
          <strong>Projects could not be loaded</strong>
          <p>The screening service did not answer. Confirm the backend is running, then try again.</p>
          <button type="button" className="text-button" onClick={load}>Retry</button>
        </div>
      )}

      {loadState === "ready" && projects.length === 0 && (
        <div className="project-empty">
          <strong>No projects yet</strong>
          <p>Create one to keep a set of screening runs together — everything filed into it shows up here, and it still lives in your Batches library too.</p>
        </div>
      )}

      {loadState === "ready" && projects.length > 0 && (
        <ul className="project-list" aria-label="Projects">
          {projects.map((project) => (
            <li key={project.id}>
              <NavLink className="project-row" href={`/projects/${project.id}`}>
                <span className="project-name" title={project.name}>{project.name}</span>
                <span className="project-count">
                  {project.batch_count} {project.batch_count === 1 ? "test" : "tests"}
                </span>
                <span className="project-activity">
                  {project.last_activity_at ? relativeDay(project.last_activity_at, now) : "No tests yet"}
                </span>
                <span className="project-go" aria-hidden="true">→</span>
              </NavLink>
            </li>
          ))}
        </ul>
      )}
    </AppShell>
  );
}
