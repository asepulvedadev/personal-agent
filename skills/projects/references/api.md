# Shared-project native API (v1)

`project_manager` reaches only the private Web service using its dedicated runtime credential. No token belongs in prompts, skill files, URLs, output, or command arguments. Full application contract: `docs/PROJECTS.md` in the personal-agent source repository.

## Reads and command envelope

- `{"mode":"list"}` → `{contacts:[],projects:[]}`. Contacts include their current revision; project summaries include ID and current revision.
- `{"mode":"get","project_id":"<32-lowercase-hex>"}` → `{project:...}` with groups, tasks, documents, revision, and audit.
- `{"mode":"download_document","project_id":"<selected-id>","document_id":"<document-id>"}` downloads an authorized stored attachment into `/workspace/projects/<project-id>/documents/<document-id>.<safe-extension>`. Optional `destination` names an explicit absolute new file under `/workspace/projects`; existing files/symlinks and traversal are rejected. Binary transfers are bounded to 10 MiB and never included inline in the tool output. Returned fields: `path`, `name`, `media_type`, `bytes`, `sha256`, `project_id`, `document_id`. PDF/DOCX extraction uses separate local file tools. The server filename never determines the filesystem path.
- Command: `{"mode":"command","action":"add_task","project_id":"<selected-id>","revision":1,"operation_id":"<new-32-hex-UUID>","data":{"title":"Owner-requested task"}}`.

Every intended command requires a unique `operation_id`; retries preserve the exact operation ID and payload. Existing-project writes require explicit `project_id` and latest positive integer `revision`. `create_contact`, `update_contact`, and `create_project` do not accept top-level `project_id` or `revision`. Successful writes return `{project}` or `{contact}`; use the returned revision for subsequent changes. API validation errors do not authorize bypassing validators.

## Action fields

Optional fields may be omitted; required fields are noted. IDs are server-generated 32-lowercase-hex identities.

| Action | `data` |
|---|---|
| `create_contact` | `name` required; `email`, `phone`, `company`, `notes`, `status` (`contact`, `client`, `archived`) |
| `update_contact` | `contact_id`, contact `revision` required; editable contact fields |
| `create_project` | `name` required; `description`, nullable `client_id`, `status` (`draft`, `active`, `paused`, `finished`, `archived`); optional source-import `groups`, `tasks` |
| `update_project` | `name`, `description`, nullable `client_id`, project `status`, `internal_notes` |
| `add_group` | `name` required |
| `update_group` | `group_id`, `name` |
| `add_task` | `title` required; task fields below |
| `update_task` | `task_id` required; editable task fields |
| `add_comment` | `task_id`, `text`; `kind` (`comment`, `question`, `answer`), boolean `visible` |
| `add_time` | `task_id`, `date` (YYYY-MM-DD), positive integer `minutes`; `note`, boolean `visible` |
| `start_timer` | `task_id`; `note` |
| `stop_timer` | `task_id` (server elapsed time; one active timer per actor) |
| `add_document` | nullable `task_id`, `name`, `media_type`, actual `content_base64`, boolean `visible` |
| `set_visibility` | `kind` (`document`, `comment`, `time`), project-local entry `id`, boolean `visible`; requires explicit owner instruction |

Task fields: `code`, nullable `group_id`, `why`, `benefit`, `proposal`, `resolution`, `evidence`, `priority` (`high`, `normal`, `low`), `approval` (`pending`, `approved`), `status` (`todo`, `doing`, `blocked`, `done`), `blocker`, `blocker_owner`, `next_step`, nullable integer `estimated_minutes`, boolean `visible` (default false), `internal_notes`. `done` requires nonempty `resolution` and `evidence`; `blocked` requires `blocker` and `next_step`. Comments and time entries retain service attribution.

`create_share` and `revoke_share` are owner-only Web actions and are not exposed by this tool. Attaching or tracking a document does not publish it, authorize external actions, or authorize making hidden records client-visible.

## Failure recovery

- Authentication/credential unavailable: ask the owner to repair the runtime; do not search for alternate credentials.
- 409: stale revision or reused operation ID with changed input. Read current state; do not overwrite silently.
- Unknown write outcome (timeout, broken connection, invalid response, server failure): retain original ID and payload, inspect current state, and only retry that exact command if authorized. A new ID can duplicate the mutation.
- No redirects or environment proxies are followed. Errors omit raw server diagnostics. A failure is not proof that nothing committed.
