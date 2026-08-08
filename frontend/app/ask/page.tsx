"use client";

import { FormEvent, useEffect, useState } from "react";
import AppShell from "../components/app-shell";
import AuthorizedImg from "../components/authorized-img";
import RouteHeader from "../components/route-header";
import { API_URL, titleCase } from "../lib/format";
import type { DocumentAnswer, DocumentManifest, VLMDocument, VLMStatus } from "../lib/types";

type AnswerEntry = { question: string; response: DocumentAnswer };

export default function AskDocumentsPage() {
  const [status, setStatus] = useState<VLMStatus | null>(null);
  const [document, setDocument] = useState<VLMDocument | null>(null);
  const [manifest, setManifest] = useState<DocumentManifest | null>(null);
  const [currentPage, setCurrentPage] = useState(1);
  const [question, setQuestion] = useState("");
  const [answers, setAnswers] = useState<AnswerEntry[]>([]);
  const [uploading, setUploading] = useState(false);
  const [asking, setAsking] = useState(false);
  const [error, setError] = useState("");

  useEffect(() => {
    fetch(`${API_URL}/api/v1/vlm/status`)
      .then(async (response) => {
        if (!response.ok) throw new Error("Model status is unavailable.");
        return response.json() as Promise<VLMStatus>;
      })
      .then(setStatus)
      .catch(() => setStatus({ enabled: false, configured: false, ready: false, model: null, message: "The visual model could not be reached." }));
  }, []);

  async function upload(file: File) {
    setUploading(true);
    setError("");
    const body = new FormData();
    body.append("file", file);
    try {
      const response = await fetch(`${API_URL}/api/v1/vlm/documents`, { method: "POST", body });
      const payload = await response.json() as VLMDocument & { detail?: string };
      if (!response.ok) throw new Error(payload.detail || "The document could not be opened.");
      const manifestResponse = await fetch(`${API_URL}/api/v1/vlm/documents/${payload.id}/manifest`);
      if (!manifestResponse.ok) throw new Error("The document preview could not be prepared.");
      setDocument(payload);
      setManifest(await manifestResponse.json() as DocumentManifest);
      setCurrentPage(1);
      setAnswers([]);
      setQuestion("");
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : "The document could not be opened.");
    } finally {
      setUploading(false);
    }
  }

  async function ask(event: FormEvent) {
    event.preventDefault();
    const submitted = question.trim();
    if (!document || !submitted || asking) return;
    setAsking(true);
    setError("");
    try {
      const response = await fetch(`${API_URL}/api/v1/vlm/documents/${document.id}/questions`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ question: submitted, current_page: currentPage }),
      });
      const payload = await response.json() as DocumentAnswer & { detail?: string };
      if (!response.ok) throw new Error(payload.detail || "The model could not answer this question.");
      setAnswers((current) => [...current, { question: submitted, response: payload }]);
      setQuestion("");
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : "The model could not answer this question.");
    } finally {
      setAsking(false);
    }
  }

  const page = manifest?.pages.find((item) => item.page === currentPage);

  return (
    <AppShell
      route="ask"
      chrome="rail"
      header={
        <RouteHeader
          eyebrow="Separate operation"
          title="Ask documents"
          sub="Open a PDF or image and ask questions about its contents. This does not run screening checks or affect a case verdict."
          aside={<span className={`ask-model-state ${status?.ready ? "ready" : "offline"}`}>{status === null ? "Checking model" : status.ready ? "Model ready" : "Model offline"}</span>}
        />
      }
    >
      {!document ? (
        <section className="ask-start" aria-labelledby="ask-upload-title">
          <div className="ask-start-copy">
            <h2 id="ask-upload-title">Choose a document to explore</h2>
            <p>The assistant can retrieve relevant pages from a large PDF, read visual details, answer questions, and cite the pages it used.</p>
          </div>
          <label className={`ask-dropzone ${uploading ? "busy" : ""}`}>
            <input
              type="file"
              accept="application/pdf,image/jpeg,image/png,image/webp,image/tiff,.pdf,.jpg,.jpeg,.png,.webp,.tif,.tiff"
              disabled={!status?.ready || uploading}
              onChange={(event) => {
                const file = event.target.files?.[0];
                if (file) void upload(file);
                event.target.value = "";
              }}
            />
            <span className="ask-upload-mark" aria-hidden="true">+</span>
            <strong>{uploading ? "Preparing document..." : "Choose a PDF or image"}</strong>
            <small>Up to 25 MB. Files remain on this machine.</small>
          </label>
          {status && !status.ready && <p className="ask-service-note" role="status">{status.message}</p>}
          {error && <p className="ask-error" role="alert">{error}</p>}
        </section>
      ) : (
        <div className="ask-workspace">
          <section className="ask-document" aria-label="Document preview">
            <div className="ask-document-bar">
              <div><strong>{document.filename}</strong><span>{manifest?.page_count || document.page_count} pages</span></div>
              <button type="button" onClick={() => { setDocument(null); setManifest(null); setAnswers([]); setError(""); }}>Change document</button>
            </div>
            <div className="ask-page-stage">
              {page ? <AuthorizedImg src={`${API_URL}/api/v1/vlm/documents/${document.id}/pages/${currentPage}.png?dpi=144`} alt={`Page ${currentPage} of ${document.filename}`} /> : <span>Preparing preview...</span>}
            </div>
            <div className="ask-page-controls">
              <button type="button" disabled={currentPage <= 1} onClick={() => setCurrentPage((value) => Math.max(1, value - 1))}>Previous</button>
              <span>Page <strong>{currentPage}</strong> of {manifest?.page_count || document.page_count}</span>
              <button type="button" disabled={!manifest || currentPage >= manifest.page_count} onClick={() => setCurrentPage((value) => Math.min(manifest?.page_count || value, value + 1))}>Next</button>
            </div>
          </section>

          <section className="ask-conversation" aria-labelledby="ask-conversation-title">
            <div className="ask-conversation-head">
              <h2 id="ask-conversation-title">Questions</h2>
              <p>Ask about names, totals, clauses, dates, tables, or what appears on a specific page.</p>
            </div>
            <div className="ask-answer-list" aria-live="polite">
              {answers.length === 0 ? (
                <div className="ask-empty">
                  <strong>What would you like to know?</strong>
                  <span>Try “Summarize this document” or “What is the total amount?”</span>
                </div>
              ) : answers.map((entry, index) => (
                <article className="ask-answer" key={`${entry.question}-${index}`}>
                  <p className="ask-question">{entry.question}</p>
                  <p>{entry.response.answer}</p>
                  <div className="ask-answer-meta">
                    <span>{titleCase(entry.response.confidence)} confidence</span>
                    {entry.response.citations.map((citation, citationIndex) => (
                      <button type="button" title={citation.evidence} onClick={() => setCurrentPage(citation.page)} key={`${citation.page}-${citationIndex}`}>Page {citation.page}</button>
                    ))}
                  </div>
                  {entry.response.limitations.length > 0 && <small>{entry.response.limitations.join(" ")}</small>}
                </article>
              ))}
            </div>
            <form className="ask-form" onSubmit={ask}>
              <label htmlFor="ask-question">Ask about this document</label>
              <textarea id="ask-question" value={question} onChange={(event) => { setQuestion(event.target.value); setError(""); }} placeholder="Type a question..." maxLength={2000} rows={3} />
              <div><span>Current page: {currentPage}</span><button type="submit" disabled={!question.trim() || asking}>{asking ? "Reading document..." : "Ask question"}</button></div>
            </form>
            {error && <p className="ask-error" role="alert">{error}</p>}
            <p className="ask-disclaimer">Answers are generated from the document and may require verification. This operation does not assess authenticity or fraud.</p>
          </section>
        </div>
      )}
    </AppShell>
  );
}
