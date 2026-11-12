# Shared project workspace

Owner-authorized module for contacts, optionally client-linked projects, work groups,
tasks, documents, questions, time entries and revocable client views. This does not
activate or modify GoHighLevel. Original client documents and live data are private,
never part of the public repository.

## API contract (v1)

Owner: GET `/api/projects` -> `{contacts:[],projects:[]}`; GET
`/api/projects/{id}` -> `{project:...}`; POST `/api/projects/command`.
Service: GET `/internal/projects`, GET `/internal/projects/{id}`, POST
`/internal/projects/command`; only a dedicated projects bearer token, not database
credentials. Owner writes require existing owner cookie, CSRF and exact Origin.
Service actor uses header `X-Project-Actor: hermes-web|carpediev`; audit records this
as service attribution, not proof of a human identity. Owner-only sharing actions.

Commands: `{action, project_id?, revision?, operation_id, data:{...}}`.
`operation_id` is a unique 32-hex UUID per intended operation, reused on network
retry. Existing-project writes require its current integer `revision`. Stale writes
return 409 with no mutation. An operation ID reused with different input fails.
Successful project commands return `{project}` (plus share data when relevant).
Contact commands return `{contact}`. IDs are 32-hex server-generated identities.
All dates are ISO-8601 UTC, time entry work dates ISO YYYY-MM-DD.

Actions and data:
- `create_contact`: name(required), email, phone, company, notes, status(contact|client|archived).
- `update_contact`: contact_id, revision, any editable contact fields.
- `create_project`: name(required), description, client_id(nullable), status(draft|active|paused|finished|archived), optional groups and tasks for private source import.
- `update_project`: name, description, client_id(nullable), status, internal_notes.
- `add_group`: name. `update_group`: group_id, name.
- `add_task`: title(required), code, group_id(nullable), why, benefit, proposal,
  resolution, evidence, priority(high|normal|low), approval(pending|approved),
  status(todo|doing|blocked|done), blocker, blocker_owner, next_step,
  estimated_minutes(nullable integer), visible(boolean default false), internal_notes.
- `update_task`: task_id plus editable task fields above. Done requires nonempty
  resolution and evidence; blocked requires blocker and next_step. Unknown original
  hours remain unknown, not invented historical entries.
- `add_comment`: task_id, text, kind(comment|question|answer), visible(boolean).
- `add_time`: task_id, date, minutes(positive integer), note, visible(boolean).
- `start_timer`: task_id, note. `stop_timer`: task_id; uses actual server elapsed
  time, one active timer per actor, no invented historic time.
- `add_document`: task_id(nullable), name, media_type, content_base64, visible.
  Private stored attachment; download only after project/task/share authorization.
- `set_visibility`: kind(document|comment|time), id, visible. Change an existing project-local entry; clients cannot use this action.
- `create_share`: label. Owner-only, returns additional `{share:{id,token}}` once.
  `revoke_share`: share_id. Full owner project includes share metadata, never tokens.

Project shape: id, name, description, client_id, status, internal_notes, revision,
created_at, updated_at, groups[{id,name}], tasks[{id,...task fields,comments:[],
time_entries:[],timer:null|{actor,started_at,note}}], documents[{id,task_id,name,
media_type,size,sha256,visible,created_at}], shares[{id,label,created_at,revoked_at}],
audit[{action,actor,at,revision}]. Comments/time entries include id, actor, created_at
and their declared fields. Index projects include task_count, done_count,
tracked_minutes, estimated_minutes(nullable), latest update.

Client portal: GET `/project` serves a public empty shell. Token stays in fragment
`/project#TOKEN`, never query/path/referrer. GET `/api/project-view` with
Authorization Bearer TOKEN -> `{project}`. POST `/api/project-view/comments` with
same token and exact Origin, JSON `{task_id,text,kind,operation_id}`. Only visible
tasks/documents/comments/time entries; never internal notes, full contacts, audit,
shares, actor/service credentials or hidden tasks/counts. Client may comment/ask/
answer on visible tasks, not change status/hours. Revocation affects every request.
Documents: GET `/api/projects/{project_id}/documents/{document_id}` (owner),
GET `/internal/projects/{project_id}/documents/{document_id}` (scoped service), or
GET `/api/project-view/documents/{document_id}` (client bearer). Downloads attachment
only with nosniff, sanitized filename, no inline active content. UI fetches Blob for
client downloads; no token in URL. Read errors never expose DSN, SQL or traceback.

## Persistence and isolation

PostgreSQL holds contacts, project aggregates, operation deduplication, scoped
share token hashes and append-only audit records. Project updates lock one row and
validate revision in one transaction. The server connection config is a read-only
private file mounted only into Web. Documents live in private control storage.
Agents operate through the shared API; separate conversations remain separate.
Database runs in a dedicated persistent service/network with no published port.
Backups include logical pg_dump plus private document storage and a restore probe.

## Initial source import

Ranchos de América / Optimización de GoHighLevel: preserve the original Word and
three diagrams privately, import exactly 40 proposals in eight source groups,
retain original A1–H3 codes and priority. All source proposals require approval and
start Por ejecutar; no invented resolution, hours, tariff, contract or dates.
Missing reference documents remain dependencies, not fabricated attachments.

## Administrator rollout and recovery

`deploy/projects_admin.py` is an explicit administrator operation, never a release
hook. It refuses changed baseline policy, pre-existing provisioning, overlapping
networks and names; holds the existing deployment lock; saves the three original
policy files in root-private deployment state. It creates a dedicated non-root
PostgreSQL 17 container (official digest pinned), private internal network and
application role. Only Web mounts its connection JSON. The native project tool
uses a different narrow bearer token, mode 0400 for UID 10001; Web has a separate
copy for UID 10002. Neither credential is public source or a tool response.

No existing app service is restarted during provisioning. The normal restricted
Actions rollout later connects Web to the private DB network. Ordinary source
rollback retains the database and private storage; it is NOT data rollback.
Never drop a populated database or restore an old dump automatically. Schema v1
is additive; future incompatible schemas need an explicit data migration plan.

Backups retain three root-private archives and now include `projects.pg_dump`,
connection/recovery material, and control-backed immutable documents alongside
existing workspace/auth state. Each dump is checked with `pg_restore --list`;
this is structural validation, not a restore test. Verify an actual restore in a
separate disposable database after import. To recover a running deployment,
restore only into an isolated database first, check counts/hashes, then obtain
explicit owner authorization before replacing live data. Preserve the checkpoint
and accepted image SHA. If administrator provisioning fails, stop and inspect;
never rerun blindly or globally prune Docker/network state.

## Verification checkpoint before application rollout

- 605 Python tests passed with an explicit isolated PostgreSQL 17 test database;
  78 Node tests passed, then the client-fragment skip-navigation regression raised
  the focused UI suite to 18 passing tests. One pre-existing Starlette/httpx
  deprecation warning remains.
- Both Web (Python 3.14/psycopg binary) and native Hermes Docker images built.
  Native registry/toolset discovery succeeded inside the built Hermes image.
- Independent live PostgreSQL/HTTP verification found and rechecked one defect:
  an idempotent client-comment replay could return an obsolete visibility snapshot.
  Replay now locks and projects current state, without another mutation. Revoked
  access, cross-project documents and local download confinement were rechecked.
- Source secret scan: 47 findings reviewed, including three public policy SHA-256
  pins and a synthetic database-error test; no live credential in the candidate.
- Local browser verified owner login, project creation without client, task
  creation, and bearer-only client view. Production/channel checks remain separate
  and must be observed after the restricted rollout.

Rollback boundaries: infrastructure provisioning retains private DB data and its
policy checkpoint; the UI/domain unit can be removed without deleting that data;
native project-tool enablement is separate from existing image/quarantine tools.
No data restoration is automatic. Risk: data/auth/concurrency and deployment.

## Direct-release checkpoint

The owner explicitly requested direct VPS deployment instead of relying on Actions.
The preceding run 43100399185 completed successfully and accepted release 0120c26;
Web, Hermes and the new database were healthy and the deployment lock was free.
Direct activation still uses the same root-managed release gate, fixed Compose,
mandatory backup and recovery logic. It does not use candidate host hooks or alter
other applications. A commit-scoped skip instruction avoids a duplicate automatic
Actions rollout; it does not disable future CI globally.

The final upload correction reuses the exact endpoint guards before buffering
project command bodies. Unauthenticated uploads cannot force the larger project
body allocation. Observed RED then GREEN; independent probes checked eight
negative owner/service/Origin/CSRF/actor combinations plus retained authorized
uploads. Full local verification: 607 Python and 80 Node tests passed. No live
project data, source attachments or credentials are included in this repository.

## Task video evidence

Task-linked MP4, WebM and Ogg video attachments offer **Ver video** and
**Descargar** in both owner and client views. Preview loads only on request,
with native playback/seek/volume/fullscreen controls and no autoplay. Unsupported
formats remain download-only; playback failures keep the download available.
The existing authorized attachment endpoint is reused, including client bearer
headers and task/document visibility checks. A local Blob URL contains no share
token. No new public media endpoint or change to download response headers is
needed. The project HTML shells alone permit `blob:` in CSP `media-src`;
script and default source policies stay unchanged. Closing a task, hiding the video or leaving the view releases the player
and its Blob URL, including cancelled in-flight previews. Downloaded/cached bytes
cannot be recalled after access is revoked; subsequent requests are reauthorized.
The existing 10 MiB attachment limit still applies.

Validation checkpoint (2026-11-11): 22 focused UI tests and all 83 Node tests
passed; 116 portal/media/project API/runtime tests passed with one existing
Starlette/httpx deprecation warning. Real G1 (46.4 s) and D4 (42 s) MP4 files
played to completion in a local client-view fixture; preview also played with
the scoped CSP enabled. Client download bytes matched the source SHA-256 hashes.
These checks do not establish production rollout; inline playback is pending
owner-authorized deployment and verification with an existing client access.

Release validation (2026-11-12): rebased onto current main without changing other
features; 608 Python tests passed, three optional integration tests skipped, and
83 Node tests passed. The first local full-suite attempt hit two skill-seeding
failures caused by generated Python bytecode; the clean source archive with
`PYTHONDONTWRITEBYTECODE=1` passed, matching CI. Independent security review found
no observed blocker. Secret scanning detected its synthetic canary; six existing
portal test-fixture findings were inspected, with no live credential found.
Risk: item 2 (project-only CSP media allowance); existing download authorization
and script/default restrictions are unchanged. Production verification remains a
separate release step.
