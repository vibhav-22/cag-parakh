# Packaging security status

## Completed foundation

- Individual approved accounts replace the shared access code.
- Each laptop has a random device identity, usable for optional signed device binding.
- Access changes are distributed as a newly signed authorization file, not a new installer.
- The frontend receives only an opaque local session cookie, not password hashes.
- Installed mode fails closed when authorization is missing.
- Installed mode stores mutable documents and results under `%LOCALAPPDATA%/Parakh/data`, outside the program files.
- The local API allows only loopback development origins by default and reviewer decisions are stamped with the signed-in account.
- Passwords use per-user Argon2id salts. The installer contains only an Ed25519
  public key; release verification rejects private-key material.
- Packaged mode is anchored by an immutable resource marker as well as an
  environment flag. The source development bypass must be explicit and is
  disabled in packaged mode.

- The desktop shell binds every local service to `127.0.0.1` on ports allocated at launch, and ships its own Python, Tesseract, Poppler, and face-matching model so no check silently degrades on a machine that lacks them.

## Required before a public installer

1. Sign the NSIS installer and future updates. Unsigned internal pilot builds
   trigger SmartScreen and may be blocked by company policy.
2. Add retention controls, secure deletion, and optional at-rest encryption for local documents.
3. Add crash recovery and resumable queues for very large batches.
4. Run the packaging-specific security review and `build/PILOT.md` on approved
   laptops. Dependency diagnostics and golden-result comparison are blocking.

Local administrators ultimately control their own laptop and can modify installed software. Signed authorization, code signing, short-lived sessions, and update signatures make bypass harder, but no desktop-only check can make client code impossible to alter.
