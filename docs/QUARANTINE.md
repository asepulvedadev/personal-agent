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

## Owner Web workflow

Open **Cuarentena de skills** beside chat history. Upload ZIP, `SKILL.md`, Markdown
or plaintext, or explicitly submit a public GitHub repository/path URL. ZIP and
`SKILL.md` selected in the chat attachment picker are diverted here rather than
sent as document text. The document API rejects `SKILL.md`; ordinary Markdown
notes keep their existing document behavior. Ordinary GitHub discussions do not
silently trigger downloads. Individual files named differently from `SKILL.md`
are retained for review, not automatically converted into skills.

- `POST /api/quarantine/upload?name=<filename>`: raw bytes, owner and CSRF.
- `POST /api/quarantine/github`: JSON `{ "url": "https://github.com/owner/repo" }`,
  owner and CSRF. `tree/<ref>/<path>` and Markdown/text `blob/<ref>/<path>` are
  accepted. References containing `/` are deliberately unsupported.
- `GET /api/quarantine` and `GET /api/quarantine/<id>`: owner-only reports.
  Detail contains at most eight 4 KiB untrusted text previews. The UI uses
  `textContent`, not HTML/Markdown rendering. No imported text enters chat or the
  fully capable agent's system context.
- `POST /api/quarantine/<id>/approve`: owner and CSRF; JSON
  `{ "expected_sha256": "<source hash>", "profile": "default", "skill_path": "SKILL.md" }`.
  Profiles are exactly `default` and `carpediev`.

States are `quarantined`, `review_required`, `blocked` and `approved`. Only one
self-contained `SKILL.md` with the narrow scalar `name`/`description` frontmatter
and no findings can be explicitly approved. The owner must read the report.
Approval creates exclusive `approved.json`, does not install or replace a live
skill, and cannot change its profile afterward. Names already approved for the
same profile are reserved; replacement needs a separate reviewed workflow.
Protected security/configuration/identity names are refused.

## Public GitHub handling

Only credential-free `https://github.com` URLs without query strings, fragments,
encoded path segments or redirects are accepted. Fixed official GitHub API and
codeload HTTPS endpoints first verify public repository metadata, then resolve a
40-character immutable commit before downloading its archive. No SSH, git hooks,
submodules or Git LFS actions run. Redirects, private/unavailable sources and
oversized streamed responses fail closed. The fetch helper is injectable in tests;
the tests make no real source/provider calls.

The whole archive must pass limits before the selected subtree is inspected.
This deliberately rejects large repositories even when their selected directory
is small. Failed remote fetches provide no imported bytes to retain; successfully
received archive bytes retain commit and path provenance even if analysis blocks.

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
  never used). A single intake lock serializes Web/internal intake and approvals
  in the single-process Web deployment.

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
