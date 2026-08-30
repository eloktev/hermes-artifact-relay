---
name: artifact-publishing
description: Publish long results as private linked artifacts.
version: 1.1.0
author: Egor Loktev (eloktev), Hermes Agent
license: MIT
platforms: [linux, macos, windows]
metadata:
  hermes:
    tags: [artifacts, markdown, html, publishing]
    related_skills: []
---

# Artifact Publishing

Use the plugin's two native tools to deliver long results as private links and to read original source from existing links. The service origin is configured by the operator; never assume a domain or messaging platform.

## When to Use

Publish automatically when a final result is likely to exceed about 1,200 characters, has more than five bullets or two substantive sections, or contains a report, audit, plan, research, comparison table, citations, sizeable code/configuration, or a diagram. Treat one logical deliverable as one artifact.

Keep the complete result in chat when it is short, needed for immediate copy/paste troubleshooting, contains sensitive raw configuration, or the user explicitly asks for inline text. Never publish secrets.

Use `artifact_read` whenever the user supplies a link from the configured publisher origin and asks about its contents. Do not use an anonymous viewer shell in place of the authenticated source API.

## Prerequisites

- The plugin is enabled and both `base_url` and `ARTIFACT_RELAY_API_TOKEN` are configured.
- The publisher implements `POST /api/artifacts` and `GET /api/artifacts/<id>`.

If the user asks to connect or configure the hosted relay, install/enable the plugin and run
`hermes artifact-relay setup` through the terminal. Relay the verification URL and user code to
the user, then let the command poll. Never request, read, repeat, or place the resulting token in
chat or config. The command saves it to the active profile's secret environment and publishes a
small verification artifact; return that non-secret URL. It does not restart a gateway; explain
that a new session (and an external restart for a long-running Hermes process) is required. Use
`hermes artifact-relay status` for a credential-safe readiness check.

## Procedure

1. Decide whether the output is one large logical deliverable. Completion criterion: there is one title, one short summary, and one complete body.
2. Review title, summary, and body for credentials, tokens, personal data, and sensitive configuration. Completion criterion: no secret will be sent.
3. Call `artifact_publish` with the complete Markdown or standalone HTML source. Completion criterion: the tool returns `success: true` and an artifact URL.
4. Return a concise chat message with the title, three to five conclusions, and the single link. Do not repeat the full artifact body.
5. If publication fails, state the safe error and remediation. Do not silently replace the artifact with a very long chat response unless the user asks.

## Reading

Call `artifact_read` with an artifact ID or same-origin viewer URL. Foreign-origin URLs are intentionally rejected before bearer authentication. Completion criterion: the result contains original source in `artifact.content`.

## Pitfalls

- A successful API response proves upload, not visual rendering quality.
- Standalone HTML may execute code under the publisher's sandbox; prefer Markdown unless interactivity is needed.
- Titles and summaries may appear in link previews. Keep them non-sensitive.
- Provenance is best-effort. Missing session metadata must not block publication.
- Never work around a foreign-origin rejection by extracting an ID from an untrusted URL.

## Verification

- The returned viewer URL uses the configured publisher origin.
- `artifact_read` returns the expected original source for the new ID.
- The final chat response remains concise and includes only one artifact link.
