"use client";

import { useCallback, useEffect, useRef, useState } from "react";
import { API_URL } from "./format";
import { useSession } from "./session";
import type { Batch, Job } from "./types";

export type BatchLoadState = "loading" | "ready" | "missing" | "error";

type BatchView = {
  id: string | undefined;
  batch: Batch | null;
  loadState: BatchLoadState;
  pollStalled: boolean;
};

const emptyView = (id: string | undefined): BatchView => ({
  id,
  batch: null,
  loadState: "loading",
  pollStalled: false,
});

/**
 * Hydrates a batch from its id in the URL and keeps polling until every
 * document finishes. This is what makes /batches/[batchId] survive a refresh:
 * the URL is the state, not React.
 */
export function useBatch(batchId: string | undefined) {
  const { handleUnauthorized } = useSession();
  const [view, setView] = useState<BatchView>(() => emptyView(batchId));
  const pollFailures = useRef(0);

  // Navigating to a different case resets during render rather than in an
  // effect, so the stale batch never paints for a frame.
  if (view.id !== batchId) {
    setView(emptyView(batchId));
  }

  const fetchBatch = useCallback(async () => {
    const response = await fetch(`${API_URL}/api/v1/batches/${batchId}`);
    if (response.status === 401) {
      handleUnauthorized();
      return;
    }
    if (response.status === 404) {
      setView((current) => (current.id === batchId ? { ...current, loadState: "missing" } : current));
      return;
    }
    if (!response.ok) throw new Error("Batch could not be loaded");
    const payload: Batch = await response.json();
    pollFailures.current = 0;
    setView((current) => (current.id === batchId
      ? { ...current, batch: payload, loadState: "ready", pollStalled: false }
      : current));
  }, [batchId, handleUnauthorized]);

  const markFailure = useCallback(() => {
    pollFailures.current += 1;
    if (pollFailures.current >= 4) {
      setView((current) => (current.id === batchId ? { ...current, pollStalled: true } : current));
    }
  }, [batchId]);

  useEffect(() => {
    if (!batchId) return;
    pollFailures.current = 0;
    // Every setView here runs in a promise continuation, not synchronously.
    // eslint-disable-next-line react-hooks/set-state-in-effect
    fetchBatch().catch(() => {
      setView((current) => (current.id === batchId && current.loadState === "loading"
        ? { ...current, loadState: "error" }
        : current));
    });
  }, [batchId, fetchBatch]);

  const status = view.batch?.status;
  useEffect(() => {
    if (!batchId || !status || status === "completed") return;
    const timer = window.setInterval(() => { fetchBatch().catch(markFailure); }, 1800);
    return () => window.clearInterval(timer);
  }, [batchId, status, fetchBatch, markFailure]);

  const retryPoll = useCallback(async () => {
    try {
      await fetchBatch();
    } catch {
      markFailure();
    }
  }, [fetchBatch, markFailure]);

  const patchJob = useCallback((jobId: string, patch: Partial<Job>) => {
    setView((current) => (current.batch
      ? {
          ...current,
          batch: {
            ...current.batch,
            jobs: current.batch.jobs.map((item) => (item.id === jobId ? { ...item, ...patch } : item)),
          },
        }
      : current));
  }, []);

  return {
    batch: view.batch,
    loadState: view.loadState,
    pollStalled: view.pollStalled,
    retryPoll,
    patchJob,
  };
}
