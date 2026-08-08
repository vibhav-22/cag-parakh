# Offline authorization administration

The former network license service is retired. Parakh uses a signed local
`authorization.json` file and never contacts a cloud authentication service.

Use `Parakh-Authorization-Manager.exe` from an elevated administrator terminal to
create and update the file. Keep the Ed25519 private key outside the application
repository and installed application. Only the matching public key is packaged
with Parakh.

See [`../ADMINISTRATION.md`](../ADMINISTRATION.md) for key generation, DPAPI
passphrase protection, user management, deployment, rotation, and recovery.
