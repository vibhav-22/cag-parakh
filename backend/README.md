# Backend API

This FastAPI service queues PDF screening jobs and runs each detector in a separate Python process.

## Run

```powershell
# From the repository root:
python -m pip install -r backend/requirements.txt
python -m uvicorn backend.app:app --reload

# Or, when your current directory is backend/:
python -m uvicorn --app-dir .. backend.app:app --reload
```

Use `http://127.0.0.1:8000/docs` for the API interface. Submit a PDF to `POST /api/v1/jobs`, then poll `GET /api/v1/jobs/{job_id}`.

Every job includes an `analyzer_runs` entry for each requested check. A run moves through `queued`, `running`, and either `completed` or `failed`, with timestamps, a normalized result, and an execution error when applicable. Completed results are also indexed by analyzer ID in `results` and use one stable shape:

```json
{
  "analyzer_id": "metadata",
  "outcome": "clear | review | inconclusive | error",
  "risk": "low | medium | high | unknown",
  "summary": "Low risk",
  "findings_count": 0,
  "artifacts": [],
  "exit_code": 0,
  "raw": {}
}
```

`raw` retains the analyzer's original report for detailed evidence and forward compatibility.

The web review surface uses `GET /api/v1/jobs/{job_id}/document/manifest` and
`GET /api/v1/jobs/{job_id}/document/pages/{page}.png` to render the PDF inside
the page and place normalized `regions` over the relevant areas. The original
PDF endpoint uses inline content disposition, so opening it does not force a
download.

The API exposes these single-PDF screening checks: metadata, QR presence, fonts, moire, scanner noise, same-phone consistency, composite tamper scan, and readability. Reference/batch tools and the standalone ink-analysis utilities remain command-line workflows.
