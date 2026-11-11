# Skill quarantine

## Trust boundary

All imported bytes remain in `CONTROL_DIR/quarantine/<opaque-id>/`, never under
`/workspace`, a live skill root, or an agent mount. The Web service owns this
storage. Each import creates private, exclusive, fsynced `source.bin` and
`record.json` evidence. The original input is retained for blocked packages too.
No imported script, hook, shell template, module, package manager or ignore rule
is executed. The scanner uses bounded UTF-8 inspection, regex and Python AST
parsing only. It does not call Hermes install/inspect/scanner functions.

Static analysis is **not a malware-free certificate**. Owner review remains
necessary. Detection cannot establish the intent or safety of arbitrary natural
language. Script packages, unsupported content, additional resources, unfamiliar
frontmatter, external/code references and uncertain inputs stay in quarantine.
The report includes a bounded review/reimplementation plan, not an automatic
permission to create or execute everything in a package.

## Shared chat workflow

Send a canonical GitHub skill/repository link or use the normal chat attachment clip for original ZIP or skill files. There is no separate quarantine toolbar button or dialog. Binary files first use the bounded owner-authenticated upload endpoint; chat receives an opaque receipt, not extracted package text. Explicit skill-file text from API clients is inspected as source, never forwarded to the capable agent. A string pretending to contain a ZIP is refused.

Trusted `/api/chat` interception precedes skill expansion, provider checks and agent execution. Reports explain the package's **unverified declared purpose**, observed structural/integrity/pattern findings, supported-format usability and conditional benefits. This is bounded static assessment, not an exhaustive security or effectiveness audit. No LLM with tools investigates the raw package.

Both Hermes Web and Carpediev use the same Web-service-owned quarantine and the same versioned report schema. Type `cuarentena` for the ten most recent receipts, or `ver skill RECEIPT_ID` to review a receipt from either chat. Global review does **not** merge identities, conversations or memories, and does not install into either profile automatically.

Web reports have a separate owner-only display transcript under `CONTROL_DIR/quarantine-chat/<owner-session>/`. They survive reload and merge chronologically with native history without changing native message offsets. They never enter Hermes model history. Each of at most 100 review conversations retains at most 100 report turns; full storage refuses new reports without silently deleting evidence. Existing confirmed chat deletion removes only that chat's display transcript, not quarantine evidence.

### Interfaces and approval boundary

- `POST /api/quarantine/upload?name=<filename>`: original bytes; owner + CSRF.
- `POST /api/quarantine/github`: canonical public source URL; owner + CSRF.
- `POST /api/chat`: link, skill text or attachment `{name, quarantine_id}`; owner + CSRF. Returns ordinary assistant-style SSE without invoking a model for quarantine requests.
- `GET /api/quarantine/chat/<owner-session>`: owner-only display transcript.
- `GET /api/quarantine` and `/api/quarantine/<id>`: existing owner-only inspection API. Detail previews remain explicitly untrusted; chat uses only the bounded assessment, never raw body previews.
- Internal ingest, list and report endpoints require the dedicated narrow bearer credential and have **no approval authority**.

Existing `POST /api/quarantine/<id>/approve` remains an owner + CSRF administrative interface with `{expected_sha256, profile, skill_path}`. Only one self-contained supported `SKILL.md` with no findings can receive immutable exact-hash approval. Profiles remain exactly `default` and `carpediev`. Chat review, a vague “yes”, an agent response, or the narrow ingest token does not approve anything. Replacements and shared installation require a separately reviewed owner workflow.

## Public GitHub handling

Only credential-free `https://github.com` URLs without query strings, fragments,
encoded path segments or redirects are accepted. Downloads explicitly use the operator's
`HTTPS_PROXY`, as the portal's other external requests do; ambient proxy exclusions,
credentials and redirect routing remain disabled. The isolated Web container has no
direct Internet route. Fixed official GitHub API and
codeload HTTPS endpoints first verify public repository metadata, then resolve a
40-character immutable commit before downloading its archive. No SSH, git hooks,
submodules or Git LFS actions run. Redirects, private/unavailable sources and
oversized streamed responses fail closed. The fetch helper is injectable in tests;
the tests make no real source/provider calls.

The whole archive must pass limits before the selected subtree is inspected.
This deliberately rejects large repositories even when their selected directory
is small. Failed remote fetches provide no imported bytes to retain; successfully
received archive bytes retain commit and path provenance even if analysis blocks.

Metadata, commit resolution and download share a single 15-second deadline, below
Telegram's 20-second broker request timeout. Failed GitHub imports produce a fixed,
secret-safe explanation shared by both channels, distinguishing timeout, size limits
and invalid/unavailable sources. Web retains this explanation in its display-only
transcript across reload; no new quarantine reference or approval is created for a
failed download. Internal chat consumers receive `{kind: "quarantine-error", report}`
with HTTP 200 for this completed failure report, not an import-success receipt. Owner
administrative import APIs retain their non-2xx failure status. A report must never be
interpreted as approval or as evidence that an unreceived package was inspected.

## Limits

- Input: 5 MiB; expanded archive: 8 MiB; each member: 512 KiB.
- At most 128 archive entries, depth 8, 240-character ASCII paths, ratio 100:1.
- Text analysis: 128 KiB per file; larger text requires additional review.
- At most 100 imports and 100 MiB storage, with a conservative record reservation.
  Full storage refuses new intake without deleting evidence. No automatic purge.
- Absolute/traversal/Windows paths, device names, trailing dots/spaces, duplicate
  or case-colliding paths, file/directory collisions, encrypted/special/symlink
  entries, nested archives, ambiguous names and corrupt CRCs are rejected.
- There is no archive extraction to arbitrary filesystem paths (`extractall` is
  never used). Synchronous evidence writes and quota checks contain no await points in the single-process Web deployment; route locks bound asynchronous intake and chat history operations.

## Trusted runtime read interface

`app.quarantine.approved_skills(control_dir, profile=None)` is a pure read accessor
returning records with `id`, `profile`, `name`, `content`, `sha256`,
`source_sha256`, `skill_path` and `approved_at`. It rechecks original source hash,
current static analysis, selected content hash, metadata, profile binding and
approved artifact content on every read. Changed evidence/artifacts disappear
from the returned approved set and owner reports show `blocked`.

The existing authenticated `GET /internal/runtime-config` includes this optional
`approved_skills` list without changing its configuration revision semantics.
Only the trusted runtime supervisor may consume these approved instructions.
Synchronizers must poll the list independently of revision changes, use a
non-replacing approved-import namespace, preserve existing skills/configuration,
and never copy unapproved resources. This module itself performs no live skill
installation or runtime synchronization.

## Dedicated internal intake interface

For a separately deployed Telegram/MCP bridge:

- `POST /internal/quarantine/upload?name=<filename>&profile=<profile>`: raw bytes.
- `POST /internal/quarantine/github`: JSON `{ "url": "...", "profile": "default" }`.
- `GET /internal/quarantine/<id>`: structured report only.

All three require a constant-time checked bearer token from
`QUARANTINE_INGEST_TOKEN_FILE` (default `/run/runtime-auth/quarantine-token`).
Missing/empty token files return 503; invalid tokens return 401. Owner cookies,
the provider token and the runtime-config token are not substitutes. These routes
cannot approve, read settings, or retrieve raw imported/approved content. Reports
contain IDs/hashes, provenance, manifest, finding codes, state, eligibility, plan
and `untrusted: true`, never content previews or frontmatter descriptions.
Names and paths in reports are still untrusted data. Original profile binding
prevents approving an internal import for a different profile.

These routes establish the intake boundary only. Telegram/MCP event interception,
connector registration, secret provisioning, runtime synchronization and
production rollout are separate integration work; this Web unit alone does not
claim Telegram coverage.

## Server-side chat boundary

Before skill expansion or any capable-agent call, `/api/chat` refuses bare GitHub links, explicit GitHub skill-import requests, skill/archive/source attachments and skill-shaped frontmatter with HTTP 409 directing the owner to the dedicated quarantine intake. This applies to direct API clients too; browser attachment routing is not the enforcement boundary. Ordinary repository discussions and ordinary document analysis keep their previous path. Intent recognition does not prove all paraphrases safe and is not a general natural-language security filter.
