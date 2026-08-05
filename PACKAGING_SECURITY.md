# Packaging security status

## Completed foundation

- Individual approved accounts replace the shared access code.
- Each laptop registers a random device identity and device limits are enforced centrally.
- Accounts and devices can be disabled without redistributing the installer.
- The frontend receives only an opaque local session cookie, not passwords or central tokens.
- Installed mode fails closed when authorization is missing.
- Installed mode stores mutable documents and results under `%LOCALAPPDATA%/Parakh/data`, outside the program files.
- The local API allows only loopback development origins by default and reviewer decisions are stamped with the signed-in account.

## Required before a public installer

1. Package the local backend and frontend into a Windows desktop shell that binds only to `127.0.0.1`.
2. Host the authorization service behind HTTPS with managed secrets, database backups, monitoring, and rate limiting at the edge.
3. Add a signed Windows installer and signed automatic updates.
4. Add retention controls, secure deletion, and optional at-rest encryption for local documents.
5. Add crash recovery and resumable queues for very large batches.
6. Run a packaging-specific security review and pilot on a small set of approved laptops.

Local administrators ultimately control their own laptop and can modify installed software. Central permission checks, code signing, short-lived sessions, and update signatures make bypass harder and revocation practical, but no desktop-only check can make client code impossible to alter.
