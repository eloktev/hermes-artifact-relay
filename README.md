# Hermes Artifact Relay

A standalone, standard-library-only Hermes Agent plugin that publishes private Markdown or standalone HTML and reads original source through a bearer-authenticated Artifact Relay API.

**Artifact Relay service:** [source, self-hosting, and ⭐ Star on GitHub](https://github.com/eloktev/artifact-relay) · [project page](https://eloktev.github.io/artifact-relay/)

Use the managed beta for an isolated hosted instance, or run the MIT-licensed service yourself. The plugin and service remain separate so you can inspect, deploy, and upgrade each independently.

## Features

- Two gated native tools: `artifact_publish` and `artifact_read`
- Same-origin enforcement for viewer URL reads
- Hosted device authorization with credential-safe local storage
- Configurable service origin through Hermes plugin settings
- Best-effort Hermes session provenance with graceful fallback
- Bundled generic long-result publishing skill
- No runtime Python dependencies

## Service contract

The configured service must support:

- `POST /api/artifacts` using `multipart/form-data`, returning a JSON object with an artifact `id` and viewer `url`
- `GET /api/artifacts/<id>` using bearer authentication, returning a JSON object whose `content` is the original Markdown or HTML source
- Viewer URLs shaped as `<base_url>/a/<id>`

Artifact IDs must contain 22–64 URL-safe letters, digits, underscores, or hyphens. Configure
an origin only (no path, userinfo, query, or fragment). Plain HTTP is accepted only for
`localhost` or a loopback IP; every remote publisher requires HTTPS.

## Install

This plugin is distributed through Git repositories and Hermes' Git installer. **No PyPI or
standalone wheel distribution is supported; `uv build` intentionally fails to prevent publishing
an incomplete or misleading Python package.** Install from the repository root (the installer
does not accept a plugin subdirectory):

```bash
hermes plugins install eloktev/hermes-artifact-relay --no-enable
```

For reproducible production installation, pin an audited commit:

```bash
hermes plugins install eloktev/hermes-artifact-relay \
  --ref <full-40-character-commit-sha> --no-enable
```

Installation does not require a token or service URL. Enable the plugin, then connect to the
hosted control plane (`https://relay.lok-labs.com`) with its device authorization flow:

```bash
hermes plugins enable artifact-relay
hermes artifact-relay setup
```

The command displays the verification link and user code, polls until authorization completes,
stores the service URL in Hermes config, and stores the API token only in the active profile's
private `.env` (new files use mode `0600`; existing permissions are preserved). It then publishes
a small verification artifact through the new tenant and returns that non-secret URL. It never prints
the token or places it in config, command arguments, or model-facing output. The setup
command does not restart the gateway; start a new session (and restart any long-running Hermes
process externally) to activate the tools.

Check readiness without revealing credentials:

```bash
hermes artifact-relay status
```

`--control-plane` exists only for loopback/local testing; normal hosted setup should use the
default. `--timeout` changes the authorization wait limit, for example `--timeout 900`.

For a local clone during development:

```bash
hermes plugins doctor /absolute/path/to/hermes-artifact-relay --ci
```

The manifest uses installer-compatible version 1 while retaining additive `api_version` and `config_schema` metadata understood by current Hermes runtimes.

For a self-hosted/manual deployment, configure the non-secret service URL with Hermes config:

```bash
hermes config set plugins.entries.artifact-relay.settings.base_url https://publisher.example
```

Provide the bearer token only through the secret environment used to launch Hermes. For a
one-shot shell session:

**POSIX shells (Linux/macOS):**

```bash
export ARTIFACT_RELAY_API_TOKEN='replace-with-token'
hermes plugins enable artifact-relay
```

**PowerShell (Windows):**

```powershell
$env:ARTIFACT_RELAY_API_TOKEN = 'replace-with-token'
hermes plugins enable artifact-relay
```

Hosted setup is preferred for a gateway, desktop app, or other persistent launch. Manual tokens
must still live only in the active Hermes profile's secret `.env`. Restart long-running Hermes
processes after changing the secret. Never put the token in `config.yaml` or plugin settings.

### Optional macOS Keychain launch pattern

The plugin itself remains cross-platform and reads only `ARTIFACT_RELAY_API_TOKEN`. macOS users may populate that environment variable from Keychain before launching Hermes:

```bash
export ARTIFACT_RELAY_API_TOKEN="$(security find-generic-password -a hermes -s artifact-relay-api-token -w)"
hermes
```

This is an optional shell integration, not a plugin runtime dependency.

## Usage

The plugin registers:

- `artifact_publish(title, content, summary?, format?, expires_days?)`
- `artifact_read(url)`

The bundled skill is available as `artifact-relay:artifact-publishing`. It teaches Hermes to publish one long logical result automatically and return a concise link response without assuming a fixed chat platform or service domain.

### Optional provenance

Provenance export is **off by default**. To opt in:

```bash
hermes config set plugins.entries.artifact-relay.settings.include_provenance true
```

When enabled, each publish may send the Hermes session ID/title, platform, chat display name,
thread/topic ID and name, and—only when no session title exists—the current user task. These
fields go to the configured publisher alongside the artifact. Leave the setting false when the
publisher is shared or when chat metadata should remain local.

Tools remain unavailable until both the plugin `base_url` setting and `ARTIFACT_RELAY_API_TOKEN` are present. Handler errors contain safe remediation and never expose response bodies or bearer tokens.

## Development

```bash
uv sync
uv run pytest -q
uv run ruff check .
hermes plugins doctor . --ci
```

The test suite uses a loopback HTTP server and never contacts a real publisher.

## Security

See [SECURITY.md](SECURITY.md). Do not publish secrets in artifact content, titles, summaries, or provenance.

## License

MIT — see [LICENSE](LICENSE).
