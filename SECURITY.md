# Security Policy

## Supported versions

Security fixes are applied to the latest release on the default branch.

## Reporting a vulnerability

Please report suspected vulnerabilities privately to the repository maintainer rather than opening a public issue. Include affected versions, reproduction steps, impact, and any suggested mitigation. Allow reasonable time for investigation before public disclosure.

## Security model

- `ARTIFACT_RELAY_API_TOKEN` is the only required secret. The plugin never reads a token from Hermes config or command arguments.
- `base_url` is non-secret and is read from the plugin's namespaced Hermes settings.
- Artifact viewer URLs are accepted only when their scheme and authority exactly match the configured service origin. IDs may also be supplied directly.
- Remote publishers require HTTPS; plain HTTP is accepted only on loopback. Authenticated requests never follow redirects.
- Bearer tokens are added only after same-origin validation.
- Provenance export is disabled by default and requires an explicit `include_provenance` opt-in.
- Model-facing network errors omit server response bodies to reduce accidental secret disclosure.
- Publish content is limited to 5 MiB and expiration to 0–3650 days.
- Best-effort provenance reads are local, read-only, and nonessential. Database errors degrade to the supplied session ID.

Users remain responsible for protecting artifact contents and configuring TLS for non-loopback production services. Do not put credentials, private keys, or sensitive raw configuration in titles, summaries, or artifact bodies.
