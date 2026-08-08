# Parakh administrator guide

## Offline authorization

Keep the encrypted Ed25519 private key outside this repository, installer, and
employee laptops. Build with the matching public PEM. On the controlled admin
workstation, build the standalone administrator utility once, then run it from
an elevated shell. The employee installer never contains this utility.

```powershell
powershell -File build\make-admin-tool.ps1 -BuildPythonExe C:\Path\To\Python313\python.exe
cd release\admin
```

```powershell
.\Parakh-Authorization-Manager.exe generate-key --private-key D:\ParakhKeys\authorization-private.pem --public-key D:\ParakhKeys\authorization-public.pem --protected-passphrase-file D:\ParakhKeys\authorization-passphrase.dpapi
.\Parakh-Authorization-Manager.exe init --file .\authorization.json --private-key D:\ParakhKeys\authorization-private.pem --protected-passphrase-file D:\ParakhKeys\authorization-passphrase.dpapi --expires 2027-08-01T00:00:00Z
.\Parakh-Authorization-Manager.exe add-user --file .\authorization.json --private-key D:\ParakhKeys\authorization-private.pem --protected-passphrase-file D:\ParakhKeys\authorization-passphrase.dpapi employee@example.com --name "Employee Name"
```

The protected passphrase file is tied to the Windows account that created it. Keep
the private key and its recovery passphrase in a separate offline backup; copying
the DPAPI file to another computer is not a recovery mechanism.

Commands also include `remove-user`, `reset-password`, `set-user-expiry`,
`set-user-devices`, `set-expiry`, `list-users`, and `sign`. Deploy only the
newly signed `authorization.json` to
`%LOCALAPPDATA%\Parakh\authorization\`; never deploy the private key.

## Build

```powershell
powershell -File build\stage-vendor.ps1
powershell -File build\make-installer.ps1 `
  -PythonEmbedZip tmp\packaging-inputs\python-3.13.14-embed-amd64.zip `
  -BuildPythonExe C:\Path\To\Python313\python.exe `
  -PublicKeyFile D:\ParakhKeys\authorization-public.pem
```

For controlled releases supply an offline `-Wheelhouse`. The script verifies
the pinned Python hash, matching x64 Python 3.13 ABI, native dependencies,
tests, forbidden user/private material, required resources, and installer hash.
Outputs are under `release\windows`.

## Code signing

Electron-builder supports `CSC_LINK` and `CSC_KEY_PASSWORD` for a PFX, or a
Windows certificate-store identity. Keep credentials in CI secrets, timestamp
the signature using the certificate provider's RFC3161 service, and build with
`-RequireSigned`. Verify the installer and installed `Parakh.exe` with
`Get-AuthenticodeSignature`. Until a certificate is available, label the output
**unsigned internal pilot** and distribute its hash separately.
