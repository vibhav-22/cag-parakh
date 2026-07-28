from __future__ import annotations

import csv
import io
from html import escape
from typing import Any

from .version import APP_VERSION, DETECTORS_VERSION


# The reviewer's decision is the product's output, so every exported report
# carries the same statement of what the machine result does and does not mean.
# Efficacy is unmeasured against known-forged documents; a report that hid that
# would let a flag read as proof.
CALIBRATION_NOTICE = [
    "Parakh reports signals, not conclusions. A flagged check is a reason to look at the "
    "document, not evidence that it was forged.",
    "Detector accuracy has not been measured against known-forged documents. The "
    "true-positive rate of this system is unknown.",
    "Several checks report raw signal rather than a tuned threshold, so their flags carry "
    "no calibrated probability.",
    "A clear result is not a certificate of authenticity. It means no check in the "
    "selected set produced a signal.",
    "The recorded human decision is the verdict of record. The machine result is input to "
    "that decision, never a replacement for it.",
]

DECISION_LABELS = {
    "verified": "Verified",
    "needs_investigation": "Needs investigation",
    "inconclusive": "Inconclusive",
    "escalated": "Escalated to a second reviewer",
}

# Mirrors `analyzerLabel` in the frontend. A report that named a check
# differently from the screen the reviewer read it on would be a different
# document as far as anyone auditing the case is concerned.
_ANALYZER_LABELS = {
    "tamper_scan": "Whitener Detection",
    "photo_detection": "Document Photo",
    "qr_presence": "QR Presence",
}


def analyzer_label(analyzer_id: str) -> str:
    return _ANALYZER_LABELS.get(analyzer_id, analyzer_id.replace("_", " ").title())


_VERDICT_LABELS = {
    "review": "Needs review",
    "error": "Check errors",
    "clear": "Clear",
    "inconclusive": "Inconclusive",
    "pending": "Screening incomplete",
    "unanalyzable": "Marked unanalyzable",
}


def machine_verdict(job: dict[str, Any]) -> str:
    """Collapse a job's analyzer outcomes into one machine reading.

    Mirrors `docVerdict` in the frontend. Kept in step deliberately: an exported
    report that disagreed with the screen the reviewer looked at would be worse
    than no export at all.
    """

    if job.get("unanalyzable"):
        return "unanalyzable"
    if job.get("status") != "completed":
        return "pending"
    # `info` checks (metadata provenance) state facts and take no position, so
    # they neither clear a document nor flag it.
    outcomes = [
        str(result.get("outcome")) for result in job.get("results", {}).values()
        if str(result.get("outcome")) != "info"
    ]
    if not outcomes:
        return "inconclusive"
    if "review" in outcomes:
        return "review"
    if "error" in outcomes:
        return "error"
    if all(outcome == "clear" for outcome in outcomes):
        return "clear"
    return "inconclusive"


def verdict_label(verdict: str) -> str:
    return _VERDICT_LABELS.get(verdict, "Inconclusive")


def decision_label(decision: str | None) -> str:
    if not decision:
        return "Not yet recorded"
    return DECISION_LABELS.get(decision, decision.replace("_", " ").capitalize())


def case_report(job: dict[str, Any], batch_id: str | None = None) -> dict[str, Any]:
    """The full, machine-readable record of one screened document."""

    results = job.get("results", {})
    review = job.get("review") if isinstance(job.get("review"), dict) else None
    checks = []
    for analyzer in job.get("analyzers", []):
        result = results.get(analyzer)
        run = job.get("analyzer_runs", {}).get(analyzer, {})
        verdict = (result or {}).get("check") or {}
        checks.append({
            "analyzer_id": analyzer,
            "status": run.get("status", "queued"),
            "outcome": (result or {}).get("outcome"),
            "risk": (result or {}).get("risk"),
            "summary": (result or {}).get("summary"),
            "findings_count": (result or {}).get("findings_count", 0),
            "error": run.get("error"),
            # The check's own rule and how this document met it. Carried into the
            # export so a case file records why a tick was a tick.
            "result": verdict.get("status"),
            "criterion": verdict.get("criterion"),
            "reason": verdict.get("reason"),
            "facts": verdict.get("facts", []),
        })

    evidence = [
        {**region, "analyzer_id": analyzer}
        for analyzer, result in results.items()
        for region in result.get("regions", [])
    ]

    verdict = machine_verdict(job)
    profile = job.get("profile") if isinstance(job.get("profile"), dict) else None
    return {
        "case_id": job.get("id"),
        "batch_id": batch_id,
        "filename": job.get("filename"),
        "sha256": job.get("sha256"),
        "profile": profile,
        "screened_at": job.get("created_at"),
        "completed_at": job.get("completed_at"),
        "app_version": APP_VERSION,
        "detectors_version": DETECTORS_VERSION,
        "analysis_settings": job.get("settings", {}),
        "machine_verdict": verdict,
        "machine_verdict_label": verdict_label(verdict),
        "unanalyzable": bool(job.get("unanalyzable")),
        "unanalyzable_reason": job.get("unanalyzable_reason"),
        "checks": checks,
        "evidence_regions": evidence,
        "review": review,
        "review_label": decision_label((review or {}).get("decision")),
        "calibration_notice": CALIBRATION_NOTICE,
    }


_REPORT_CSS = """
  :root { color-scheme: light; }
  * { box-sizing: border-box; }
  body { margin: 0; padding: 40px; background: #ffffff; color: #16161c;
         font: 15px/1.55 "Inter", "Segoe UI", system-ui, sans-serif; }
  main { max-width: 920px; margin: 0 auto; }
  h1 { font-size: 24px; margin: 0 0 4px; letter-spacing: -0.01em; }
  h2 { font-size: 15px; text-transform: uppercase; letter-spacing: 0.08em;
       color: #6b6b7a; margin: 32px 0 12px; }
  .eyebrow { font-size: 12px; letter-spacing: 0.12em; text-transform: uppercase;
             color: #6b6b7a; margin: 0 0 16px; }
  .verdict { display: flex; align-items: baseline; gap: 12px; padding: 16px 18px;
             border: 1px solid #d9d9e3; border-left-width: 4px; border-radius: 6px; margin: 18px 0 0; }
  .verdict.clear { border-left-color: #1a8f86; }
  .verdict.review { border-left-color: #b8791d; }
  .verdict.error, .verdict.unanalyzable { border-left-color: #c4405f; }
  .verdict.inconclusive, .verdict.pending { border-left-color: #6b6b7a; }
  .verdict strong { font-size: 18px; }
  .verdict span { color: #5a5a68; font-size: 13px; }
  .scroll { overflow-x: auto; }
  table { width: 100%; border-collapse: collapse; font-size: 14px; }
  th, td { text-align: left; padding: 8px 10px; border-bottom: 1px solid #e6e6ee; vertical-align: top; }
  th { font-weight: 600; color: #4a4a58; }
  dl.meta { display: grid; grid-template-columns: repeat(2, minmax(0, 1fr)); gap: 10px 24px; margin: 0; }
  dl.meta div { display: flex; justify-content: space-between; gap: 16px;
                border-bottom: 1px solid #eeeef4; padding-bottom: 6px; }
  dl.meta dt { color: #6b6b7a; font-size: 13px; }
  dl.meta dd { margin: 0; font-size: 13px; text-align: right; word-break: break-all; }
  .pill { display: inline-block; padding: 2px 8px; border-radius: 999px; font-size: 12px;
          border: 1px solid #d9d9e3; }
  .pill.clear { border-color: #1a8f86; color: #14706a; }
  .pill.review { border-color: #b8791d; color: #8d5c13; }
  .pill.error { border-color: #c4405f; color: #a02d49; }
  .pill.inconclusive { border-color: #9a9aa8; color: #5a5a68; }
  td small { display: block; color: #6b6b7a; font-size: 11px; margin-top: 3px; }
  ul.facts { margin: 6px 0 0; padding-left: 16px; color: #4a4a58; font-size: 12px; }
  ul.facts b { font-weight: 600; color: #6b6b7a; }
  .auth-bar { position: relative; width: 96px; height: 8px; border-radius: 4px; background: #e6e6ee; }
  .auth-bar > span { display: block; height: 100%; border-radius: 4px; background: #9b3834; }
  .auth-bar.clear > span { background: #1a8f86; }
  .auth-bar.review > span, .auth-bar.inconclusive > span { background: #b8791d; }
  .auth-cell { display: flex; align-items: center; gap: 8px; white-space: nowrap; }
  .auth-cell b { font-size: 12px; font-variant-numeric: tabular-nums; }
  .tick-grid { text-align: center; font-size: 13px; }
  .tick-grid.pass { color: #14706a; }
  .tick-grid.fail { color: #a02d49; }
  .tick-grid.pending { color: #9a9aa8; }
  .notes { white-space: pre-wrap; border: 1px solid #e6e6ee; border-radius: 6px; padding: 12px 14px; }
  ul.notice { padding-left: 18px; color: #4a4a58; font-size: 13px; }
  ul.notice li { margin-bottom: 6px; }
  footer { margin-top: 36px; padding-top: 14px; border-top: 1px solid #e6e6ee;
           color: #6b6b7a; font-size: 12px; }
  @media (max-width: 720px) {
    body { padding: 24px 18px; }
    dl.meta { grid-template-columns: 1fr; }
    .verdict { flex-direction: column; gap: 4px; }
  }
  @media print {
    body { padding: 0; }
    h2 { break-after: avoid; }
    table { break-inside: auto; }
    .scroll { overflow-x: visible; }
  }
"""


def _document(title: str, body: str) -> str:
    return (
        "<!doctype html><html lang=\"en\"><head><meta charset=\"utf-8\">"
        f"<title>{escape(title)}</title><style>{_REPORT_CSS}</style></head>"
        f"<body><main>{body}</main></body></html>"
    )


def _meta_rows(pairs: list[tuple[str, Any]]) -> str:
    cells = "".join(
        f"<div><dt>{escape(label)}</dt><dd>{escape(str(value) if value not in (None, '') else '—')}</dd></div>"
        for label, value in pairs
    )
    return f"<dl class=\"meta\">{cells}</dl>"


# How a check result prints. `info` is not a middle grade between pass and fail
# — it is a check that reports facts and grades nothing.
_RESULT_LABELS = {
    "pass": ("Pass", "clear"),
    "fail": ("Fail", "review"),
    "inconclusive": ("Inconclusive", "inconclusive"),
    "info": ("Reported", "inconclusive"),
    "error": ("Check error", "error"),
}


def _result_label(check: dict[str, Any]) -> str:
    result = str(check.get("result") or "")
    if result in _RESULT_LABELS:
        return _RESULT_LABELS[result][0]
    return str(check.get("outcome") or check.get("status") or "—").title()


def _result_class(check: dict[str, Any]) -> str:
    return _RESULT_LABELS.get(str(check.get("result") or ""), ("", ""))[1]


def _facts_html(facts: Any) -> str:
    """The detector's own values, printed under the reason."""

    if not isinstance(facts, list) or not facts:
        return ""
    rows = "".join(
        f"<li><b>{escape(str(fact.get('label')))}:</b> {escape(str(fact.get('value')))}</li>"
        for fact in facts
        if isinstance(fact, dict) and fact.get("label")
    )
    return f"<ul class=\"facts\">{rows}</ul>" if rows else ""


def _notice_html() -> str:
    items = "".join(f"<li>{escape(line)}</li>" for line in CALIBRATION_NOTICE)
    return f"<h2>What this screening does not claim</h2><ul class=\"notice\">{items}</ul>"


def render_case_html(report: dict[str, Any]) -> str:
    """A printable single-case report. Print to PDF is the export path."""

    verdict = str(report["machine_verdict"])
    review = report.get("review") or {}

    checks = "".join(
        "<tr>"
        f"<td>{escape(analyzer_label(str(check['analyzer_id'])))}"
        f"<br><small>{escape(str(check.get('criterion') or ''))}</small></td>"
        f"<td><span class=\"pill {escape(_result_class(check))}\">"
        f"{escape(_result_label(check))}</span></td>"
        f"<td>{escape(str(check.get('reason') or check['summary'] or check['error'] or '—'))}"
        f"{_facts_html(check.get('facts'))}</td>"
        f"<td>{escape(str(check['findings_count']))}</td>"
        "</tr>"
        for check in report["checks"]
    )

    evidence = report["evidence_regions"]
    evidence_html = ""
    if evidence:
        rows = "".join(
            "<tr>"
            f"<td>{escape(str(region.get('page')))}</td>"
            f"<td>{escape(str(region.get('label')))}</td>"
            f"<td>{escape(str(region.get('severity', '')).title())}</td>"
            f"<td>{escape(str(region.get('message')))}</td>"
            "</tr>"
            for region in evidence
        )
        evidence_html = (
            "<h2>Located evidence</h2><div class=\"scroll\"><table><thead><tr><th>Page</th>"
            f"<th>Marker</th><th>Severity</th><th>Detail</th></tr></thead>"
            f"<tbody>{rows}</tbody></table></div>"
        )

    if review:
        decision_html = "<h2>Reviewer decision</h2>" + _meta_rows([
            ("Decision", report["review_label"]),
            ("Recorded", review.get("reviewed_at")),
            ("Reviewer", review.get("reviewer")),
            ("Assigned to", review.get("assigned_to")),
        ])
        if review.get("notes"):
            decision_html += f"<p class=\"notes\">{escape(str(review['notes']))}</p>"
    else:
        decision_html = (
            "<h2>Reviewer decision</h2><p class=\"notes\">No decision has been recorded for this "
            "document. This report is incomplete as a case record.</p>"
        )

    profile = report.get("profile") or {}
    profile_html = ""
    if profile:
        profile_html = "<h2>Screening test</h2>" + _meta_rows([
            ("Test", profile.get("name")),
            ("Goal", profile.get("goal")),
        ])
        if profile.get("guidance"):
            profile_html += (
                "<p class=\"notes\"><strong>Decision rule</strong><br>"
                f"{escape(str(profile['guidance']))}</p>"
            )

    unanalyzable_html = ""
    if report.get("unanalyzable"):
        unanalyzable_html = (
            "<h2>Marked unanalyzable</h2><p class=\"notes\">"
            f"{escape(str(report.get('unanalyzable_reason') or 'No reason given.'))}</p>"
        )

    body = (
        "<p class=\"eyebrow\">Parakh · Document screening case report</p>"
        f"<h1>{escape(str(report['filename']))}</h1>"
        f"<div class=\"verdict {escape(verdict)}\"><strong>{escape(str(report['machine_verdict_label']))}</strong>"
        f"<span>Machine reading · reviewer decision: {escape(str(report['review_label']))}</span></div>"
        "<h2>Case record</h2>"
        + _meta_rows([
            ("Case id", report["case_id"]),
            ("Batch id", report["batch_id"]),
            ("Screened at", report["screened_at"]),
            ("Completed at", report["completed_at"]),
            ("File digest (SHA-256)", report["sha256"]),
            ("App version", report["app_version"]),
            ("Detectors version", report["detectors_version"]),
        ])
        + profile_html
        + unanalyzable_html
        + "<h2>Checks run</h2><div class=\"scroll\"><table><thead><tr>"
          "<th>Check &amp; criterion</th><th>Result</th><th>Why</th><th>Findings</th>"
          "</tr></thead>"
          f"<tbody>{checks}</tbody></table></div>"
        + evidence_html
        + decision_html
        + _notice_html()
        + f"<footer>Generated by Parakh {escape(APP_VERSION)} · detectors "
          f"{escape(DETECTORS_VERSION)} · this report reproduces the analysis settings above.</footer>"
    )
    return _document(f"Case report — {report['filename']}", body)


def batch_csv(batch: dict[str, Any]) -> str:
    """One row per document: the spreadsheet a case file actually needs."""

    buffer = io.StringIO()
    writer = csv.writer(buffer, lineterminator="\n")
    writer.writerow([
        "document", "case_id", "sha256", "screening_test", "machine_verdict",
        "reviewer_decision", "reviewer", "assigned_to", "flagged_checks", "checks_run",
        "reviewed_at", "notes", "app_version", "detectors_version",
    ])
    for job in batch.get("jobs", []):
        review = job.get("review") if isinstance(job.get("review"), dict) else {}
        profile = job.get("profile") if isinstance(job.get("profile"), dict) else {}
        results = job.get("results", {})
        flagged = [
            analyzer for analyzer, result in results.items()
            if result.get("outcome") in ("review", "error")
        ]
        writer.writerow([
            job.get("filename"),
            job.get("id"),
            job.get("sha256") or "",
            profile.get("name") or "",
            verdict_label(machine_verdict(job)),
            decision_label(review.get("decision")),
            review.get("reviewer") or "",
            review.get("assigned_to") or "",
            "; ".join(sorted(flagged)),
            len(results),
            review.get("reviewed_at") or "",
            (review.get("notes") or "").replace("\r\n", " ").replace("\n", " "),
            APP_VERSION,
            DETECTORS_VERSION,
        ])
    return buffer.getvalue()


def _batch_profile(batch: dict[str, Any]) -> str | None:
    """The screening test a batch ran under, when every document shares one."""

    names = {
        (job.get("profile") or {}).get("name")
        for job in batch.get("jobs", [])
        if isinstance(job.get("profile"), dict)
    }
    names.discard(None)
    if len(names) == 1:
        return next(iter(names))
    if len(names) > 1:
        return "Mixed"
    return None


def _authenticity_percent(job: dict[str, Any]) -> int | None:
    """Share of completed checks that came back clear. A plain count, not a
    probability — mirrors `authenticityPercent` in the frontend so the report
    never disagrees with the screen the reviewer read it on."""

    graded = [
        result for result in job.get("results", {}).values()
        if result.get("outcome") != "info"
    ]
    if not graded:
        return None
    clear = sum(1 for result in graded if result.get("outcome") == "clear")
    return round((clear / len(graded)) * 100)


def _analyzer_columns(batch: dict[str, Any]) -> list[str]:
    columns: list[str] = []
    for job in batch.get("jobs", []):
        for analyzer in job.get("analyzers", []):
            if analyzer not in columns:
                columns.append(analyzer)
    return columns


# Five distinct marks, because collapsing "could not run" into a cross is the
# thing that makes a check matrix unreadable.
_TICK_GLYPHS = {
    "pass": ("✓", "pass"),
    "fail": ("✕", "fail"),
    "inconclusive": ("?", "pending"),
    "info": ("i", "pending"),
    "error": ("E", "fail"),
}


def _authenticity_chart_html(batch: dict[str, Any], columns: list[str]) -> str:
    """The tick/cross check matrix plus an authenticity bar per document —
    the same signal the batch matrix screen shows, reproduced for the printed
    report so a case file carries the chart, not just the verdict pill."""

    if not columns:
        return ""

    header_cells = "".join(f"<th>{escape(analyzer_label(column))}</th>" for column in columns)
    rows = ""
    for job in batch.get("jobs", []):
        results = job.get("results", {})
        analyzers = set(job.get("analyzers", []))
        cells = ""
        for column in columns:
            if column not in analyzers:
                cells += "<td class=\"tick-grid\">—</td>"
                continue
            result = results.get(column)
            if not result:
                cells += "<td class=\"tick-grid pending\">·</td>"
                continue
            glyph, tone = _TICK_GLYPHS.get(
                str((result.get("check") or {}).get("status") or ""),
                ("✓", "pass") if result.get("outcome") == "clear" else ("✕", "fail"),
            )
            cells += f"<td class=\"tick-grid {tone}\">{glyph}</td>"
        score = _authenticity_percent(job)
        verdict = machine_verdict(job)
        if score is None:
            auth_cell = "—"
        else:
            auth_cell = (
                f"<div class=\"auth-cell\"><div class=\"auth-bar {escape(verdict)}\">"
                f"<span style=\"width:{score}%\"></span></div><b>{score}%</b></div>"
            )
        rows += (
            "<tr>"
            f"<td>{escape(str(job.get('filename')))}</td>"
            f"<td>{auth_cell}</td>"
            f"{cells}"
            "</tr>"
        )

    return (
        "<h2>Authenticity &amp; check chart</h2>"
        "<div class=\"scroll\"><table><thead><tr><th>Document</th><th>Authenticity</th>"
        f"{header_cells}</tr></thead><tbody>{rows}</tbody></table></div>"
        "<p style=\"color:#6b6b7a;font-size:11px;margin:8px 0 0;\">✓ criterion met · "
        "✕ criterion not met · ? could not be determined · i reported, not graded · "
        "E check error · · pending · — not requested for that document</p>"
    )


def render_batch_html(batch: dict[str, Any]) -> str:
    """A printable batch summary: what got screened, what a human decided."""

    rows = ""
    counts = {"clear": 0, "review": 0, "error": 0, "inconclusive": 0, "pending": 0, "unanalyzable": 0}
    undecided = 0
    for job in batch.get("jobs", []):
        verdict = machine_verdict(job)
        counts[verdict] = counts.get(verdict, 0) + 1
        review = job.get("review") if isinstance(job.get("review"), dict) else {}
        if not review:
            undecided += 1
        rows += (
            "<tr>"
            f"<td>{escape(str(job.get('filename')))}</td>"
            f"<td><span class=\"pill {escape(verdict)}\">{escape(verdict_label(verdict))}</span></td>"
            f"<td>{escape(decision_label(review.get('decision')))}</td>"
            f"<td>{escape(str(review.get('assigned_to') or '—'))}</td>"
            f"<td>{escape(str(job.get('id'))[:12])}</td>"
            "</tr>"
        )

    chart_html = _authenticity_chart_html(batch, _analyzer_columns(batch))

    body = (
        "<p class=\"eyebrow\">Parakh · Batch screening report</p>"
        f"<h1>Batch {escape(str(batch.get('id'))[:8])}</h1>"
        f"<div class=\"verdict {'review' if counts['review'] else 'clear'}\">"
        f"<strong>{len(batch.get('jobs', []))} documents screened</strong>"
        f"<span>{counts['review']} need review · {counts['error']} check errors · "
        f"{undecided} without a recorded decision</span></div>"
        "<h2>Batch record</h2>"
        + _meta_rows([
            ("Batch id", batch.get("id")),
            ("Created at", batch.get("created_at")),
            ("Status", str(batch.get("status", "")).title()),
            ("Documents", batch.get("document_count")),
            ("Screening test", _batch_profile(batch)),
            ("App version", APP_VERSION),
            ("Detectors version", DETECTORS_VERSION),
        ])
        + "<h2>Documents</h2><div class=\"scroll\"><table><thead><tr><th>Document</th>"
          "<th>Machine verdict</th><th>Reviewer decision</th><th>Assigned to</th>"
          f"<th>Case id</th></tr></thead><tbody>{rows}</tbody></table></div>"
        + chart_html
        + _notice_html()
        + f"<footer>Generated by Parakh {escape(APP_VERSION)} · detectors {escape(DETECTORS_VERSION)}.</footer>"
    )
    return _document(f"Batch report — {batch.get('id')}", body)
