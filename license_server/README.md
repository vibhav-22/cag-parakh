# Parakh authorization service

This small service controls who may use installed Parakh applications and how many laptops each account may activate. It receives account credentials and a random device ID. It does not receive documents, extracted text, detector results, or report files.

## Production requirements

- Run this service on a separate machine behind HTTPS.
- Keep `PARAKH_SIGNING_SECRET` only on that server. Use a random value of at least 32 bytes and store it in the hosting platform's secret manager.
- Back up `PARAKH_LICENSE_DB` and restrict filesystem access to the service account.
- Put a managed reverse proxy or API gateway in front of the service for TLS, request limits, and operational logs.
- Do not package the database or signing secret inside the desktop installer.

## Start locally

```powershell
$env:PARAKH_SIGNING_SECRET = "replace-with-a-random-secret-of-at-least-32-bytes"
$env:PARAKH_LICENSE_DB = "license-data/licenses.db"
python -m uvicorn license_server.app:app --host 127.0.0.1 --port 8100
```

Connect a local Parakh backend with:

```powershell
$env:PARAKH_AUTH_URL = "http://127.0.0.1:8100"
python -m uvicorn backend.app:app --host 127.0.0.1 --port 8000
```

Non-local authorization URLs must use HTTPS.

## Approve and revoke people

Run management commands on the authorization server with the same database and signing-secret environment variables:

```powershell
python -m license_server.manage add-user person@example.com --name "Person Name" --max-devices 1
python -m license_server.manage list-users
python -m license_server.manage list-devices person@example.com
python -m license_server.manage disable-device person@example.com DEVICE_ID
python -m license_server.manage disable-user person@example.com
python -m license_server.manage enable-user person@example.com
```

Disabling an account or device invalidates it on the next permission check, no later than five minutes for a running local session. Passwords are stored with salted `scrypt` hashes. Authorization tokens expire after eight hours and are bound to both the account and device.
