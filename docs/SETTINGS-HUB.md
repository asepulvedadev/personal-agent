# Workspace settings

The chat gear opens `/settings`. The existing editor is now the Agents subview, not the entire settings interface. The page keeps the workspace theme and supports a narrow mobile navigation row.

## Agents

All live persistent Hermes profiles are discovered through the existing fixed-file service. Web and Carpediev remain distinct. The `app-config` profile appears as **Configurador**. Identity, preferences and supported nonsecret settings retain explicit saves, revision conflicts, private backups and dirty-draft protection. Temporary delegated runs are not persistent agents.

## Integrations

The owner may create and edit application API records at `/api/integrations`. Credentials are write-only in the existing encrypted vault. Enablement publishes `APP_<ID>_API_KEY` and `APP_<ID>_BASE_URL` through the existing runtime-variable channel; disabling removes both while retaining the encrypted key for later re-enablement. A separate checkbox removes the stored key. New records refuse collisions with preexisting environment names. The fixed provider credential remains in Secrets.

Records are **not verified connections**. Saving does not call a third party, implement provider-specific authentication, install an SDK, modify the egress allowlist, or grant application permissions. OAuth flows and provider-specific connectors are not implemented here. Restrict base URLs to public HTTPS domains without userinfo, query parameters or fragments. A required variable's availability does not prove the API is reachable. The page expressly discloses that enabled integration variables are available to the assistants. Secrets must not be pasted into prompts or description fields.

API mutations require owner authentication and same-origin CSRF, check for an active main-agent response, and use revision comparisons. Bodies, counts and text are bounded. No background retries or automatic requests are issued. Variables and integration metadata share one encrypted transaction, avoiding partially saved secret references.

Integration-owned variable names cannot be changed or removed through the older generic Secrets writer. Rotate or remove those credentials in Integrations. Effective and retained credentials are checked against public metadata before saving, including when a password field is blank or omitted.

## Customization

See [Customization](CUSTOMIZATION.md). The owner can inspect skill files and edit trusted project or owner-created skills. Built-in and exact-hash approved-import skills remain read-only; create a separate authored copy to adapt one. Tools, plugins and scripts are editable text **drafts outside discovery roots**. This is not a toolset/SDK installer, compiled-tool editor, or plugin activation UI. Imported external content still uses the shared chat quarantine. Nothing in this page approves imports or executes drafts.

## Configuration assistance

See [Configuration assistant](CONFIGURATION-ASSISTANT.md). A visible panel sends the owner's typed request plus the current section/profile/item name to the isolated native Configurador conversation. It does not automatically transmit open file bodies, vault values or unsent integration credentials. It can explain configuration and use native tools within existing authorization, including owner-requested workspace edits. It cannot bypass the owner-only vault API or automatically connect applications. The owner enters credentials in protected fields and explicitly saves changes.

## Checks

Python tests cover integration authentication, credential redaction, runtime variables, conflicts, collision refusal and invalid URLs; native and proxy customization tests cover safe paths, backups and revision checks; specialist tests cover scoped keys, sessions and final-answer validation. Node tests cover resource draft preservation, stale reads, read-only files and existing configuration-editor behavior. Real gateway inference and a successful deployed image are separate checks, not proven by unit tests.
