"use client";

import { useEffect, useState } from "react";
import { API_URL } from "../lib/format";
import type { DocumentAnswer, VLMStatus } from "../lib/types";

/**
 * Visual Q&A scoped to one screened document.
 *
 * Deliberately not a general chat surface. The model is only ever asked about
 * the pages of the case in front of the reviewer, and the backend hands it the
 * detector results so it looks at the pages that carry evidence. A freeform
 * assistant with no document in context has nothing grounded to answer, and its
 * confident guesses would read as forensic findings.
 */
const SUGGESTIONS = [
  "What is the certificate number and issuing authority?",
  "Do the dates on this document agree with each other?",
  "Describe the seal or stamp and where it sits on the page.",
];

export default function AskDocumentPanel({
  jobId,
  currentPage,
  onCitePage,
}: {
  jobId: string;
  currentPage: number;
  onCitePage?: (page: number) => void;
}) {
  const [status, setStatus] = useState<VLMStatus | null>(null);
  const [question, setQuestion] = useState("");
  const [answer, setAnswer] = useState<DocumentAnswer | null>(null);
  const [asking, setAsking] = useState(false);
  const [error, setError] = useState("");

  useEffect(() => {
    let cancelled = false;
    fetch(`${API_URL}/api/v1/vlm/status`)
      .then((response) => (response.ok ? response.json() : null))
      .then((payload: VLMStatus | null) => { if (!cancelled) setStatus(payload); })
      .catch(() => { if (!cancelled) setStatus(null); });
    return () => { cancelled = true; };
  }, []);

  // A new document invalidates the previous answer. Leaving it on screen would
  // attach one document's answer to another document's evidence.
  const [askedFor, setAskedFor] = useState(jobId);
  if (askedFor !== jobId) {
    setAskedFor(jobId);
    setAnswer(null);
    setQuestion("");
    setError("");
  }

  async function ask(text: string) {
    const cleaned = text.trim();
    if (cleaned.length < 2 || asking) return;
    setAsking(true);
    setError("");
    try {
      const response = await fetch(`${API_URL}/api/v1/jobs/${jobId}/questions`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ question: cleaned, current_page: currentPage }),
      });
      const payload = await response.json();
      if (!response.ok) throw new Error(payload?.detail || "The model could not answer.");
      setAnswer(payload as DocumentAnswer);
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : "The model could not answer.");
    } finally {
      setAsking(false);
    }
  }

  const unavailable = status !== null && !status.configured;

  return (
    <section className="ask-doc" aria-labelledby="ask-doc-heading">
      <div className="ask-doc-head">
        <h3 id="ask-doc-heading">Ask about this document</h3>
        <span>{status?.model ? status.model : status === null ? "Checking model" : unavailable ? "Unavailable" : "Ready"}</span>
      </div>

      {unavailable ? (
        <p className="ask-doc-note" role="status">{status?.message
          || "The visual model is not configured on this machine, so questions cannot be answered here."}</p>
      ) : (
        <>
          <form
            onSubmit={(event) => { event.preventDefault(); void ask(question); }}
            className="ask-doc-form"
          >
            <label className="sr-only" htmlFor="ask-doc-input">Ask a question about this document</label>
            <textarea
              id="ask-doc-input"
              value={question}
              rows={2}
              maxLength={2000}
              placeholder={`Ask about page ${currentPage} or the document as a whole…`}
              onChange={(event) => setQuestion(event.target.value)}
              onKeyDown={(event) => {
                if (event.key === "Enter" && !event.shiftKey) {
                  event.preventDefault();
                  void ask(question);
                }
              }}
            />
            <button type="submit" disabled={asking || question.trim().length < 2}>
              {asking ? "Reading pages…" : "Ask"}
            </button>
          </form>

          {!answer && !asking && (
            <div className="ask-doc-suggestions" aria-label="Example questions">
              {SUGGESTIONS.map((item) => (
                <button type="button" key={item} onClick={() => { setQuestion(item); void ask(item); }}>{item}</button>
              ))}
            </div>
          )}

          {error && <p className="ask-doc-note error" role="alert">{error}</p>}

          {answer && (
            <div className="ask-doc-answer" aria-live="polite">
              <p className="ask-doc-text">{answer.answer}</p>
              <div className="ask-doc-meta">
                <span className={`finding-chip ${answer.confidence === "high" ? "good" : answer.confidence === "medium" ? "neutral" : "warning"}`}>
                  {answer.confidence} confidence
                </span>
                <span className="finding-chip neutral">
                  Read {answer.retrieved_pages.length} {answer.retrieved_pages.length === 1 ? "page" : "pages"}
                </span>
              </div>
              {answer.citations.length > 0 && (
                <ul className="ask-doc-citations">
                  {answer.citations.map((citation, index) => (
                    <li key={`${citation.page}-${index}`}>
                      <button type="button" onClick={() => onCitePage?.(citation.page)}>Page {citation.page}</button>
                      <span>{citation.evidence}</span>
                    </li>
                  ))}
                </ul>
              )}
              {answer.limitations.length > 0 && (
                <ul className="ask-doc-limits">
                  {answer.limitations.map((limit) => <li key={limit}>{limit}</li>)}
                </ul>
              )}
              <p className="ask-doc-note">
                A model reading of the pixels, not a screening result. It carries no weight in the
                verdict on record.
              </p>
            </div>
          )}
        </>
      )}
    </section>
  );
}
