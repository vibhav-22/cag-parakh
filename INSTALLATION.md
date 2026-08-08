# Installing Parakh 1.0.0-pilot

Parakh is an offline Windows application. Employee laptops do not need Python,
Node.js, Docker, WSL, internet access, or command-line setup.

1. Obtain `Parakh-1.0.0-pilot-Setup-x64.exe`, its
   `RELEASE-MANIFEST.json`, and the company-issued `authorization.json` through
   the approved internal channel.
2. Compare the installer SHA-256 with the release manifest.
3. Double-click the installer. It creates desktop and Start Menu shortcuts plus
   a normal Windows uninstaller.
4. Before first sign-in, place the authorization file at
   `%LOCALAPPDATA%\Parakh\authorization\authorization.json`. An administrator
   may deploy it there automatically.
5. Start Parakh and sign in with your approved email and password.

Documents, results, settings, sessions, and authorization stay under
`%LOCALAPPDATA%\Parakh`. Upgrades preserve them. Uninstall removes the program
and shortcuts but deliberately retains this data for company retention policy.

An unsigned internal pilot triggers SmartScreen and may be blocked by company
policy. Use it only after an administrator confirms its SHA-256. Production
distribution must be Authenticode-signed.

The optional Qwen model pack is installed separately. Screening works without
it. With a valid pack, Parakh prefers its Vulkan runtime and falls back to CPU.
