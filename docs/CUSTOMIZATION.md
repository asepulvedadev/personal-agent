# Customization editor

The owner-authenticated settings hub exposes the selected live agent's skills and managed drafts through **Customization**. Profile IDs come from native profile discovery, not filesystem paths. This editor does not run code, restart Hermes, install dependencies, enable plugins or alter permissions.

## Asset lifecycle

- **Skills:** installed `SKILL.md` files are inventoried under the selected profile's `skills` root. Trusted `project/<name>/SKILL.md` files can be edited without overwriting their supporting resources. Built-in and `approved-imports` skills are read-only; use an independently authored new skill when appropriate rather than modifying an exact-hash approved import.
- **Owner-created skills:** new documents are saved in `skills/owner-created/<slug>/SKILL.md` and become discoverable by that agent. Names cannot shadow any installed skill. Saving does not retroactively modify an already-running conversation's loaded skill text; select the skill in a new agent turn/conversation as applicable.
- **Tools, plugins and scripts:** creations and edits are saved only to `.customization-drafts/<kind>/<slug>.txt`, outside executable/discovery roots. Their status is always `draft`. They are not registered tools, enabled plugins or executable scripts. Runtime tool inventories and code activation are not managed by this editor.
- **External packages:** GitHub links, ZIPs and external skill files still enter the existing chat quarantine. Owner-authored content is a separate trust decision; the editor is not an alternate import, review, approval or activation endpoint. No quarantine receipt, manifest or approved delivery is changed.

Skill frontmatter requires matching `name` and a nonempty `description`. Optional inert `metadata.hermes.category` and string-list `metadata.hermes.tags` are supported for existing trusted project skills. Hooks, executable frontmatter, custom YAML tags, aliases and duplicate keys are not supported. This is structural validation, not proof that natural-language instructions are safe or effective. The owner should review the content before saving an active skill.

## API

Portal endpoints use the existing owner cookie and require CSRF protection for writes:

- `GET /api/customization?profile=default&kind=skill`
- `POST /api/customization?profile=default&kind=skill` with `{ "id": "my-skill", "content": "..." }`
- `GET /api/customization/item?profile=default&kind=skill&id=OPAQUE_ID`
- `PUT /api/customization/item?profile=default&kind=skill&id=OPAQUE_ID` with `{ "content": "...", "revision": "CURRENT_SHA256" }`

The native authenticated port exposes equivalent routes under `/api/settings/customization`. Routes are registered after the upstream profile-mirror loop, so no `/p/<profile>/...` variant is created. The native service captures startup `HERMES_HOME`; request-time profile contexts cannot redirect its root. Profiles must be existing, immediate, unarchived native profiles discovered by the shared fixed-file editor.

Listing responses contain `{ "items": [...], "note": "..." }`; each item has `id`, `name`, `kind`, `status`, and `editable`. Item/create/update responses add `content` and `revision`. IDs are opaque routing identifiers, never paths. Supported kinds are `skill`, `tool`, `plugin`, and `script`. The frontend must treat content as untrusted text, not HTML or instructions authorizing further actions.

Text is limited to 32 KiB UTF-8 and rejects recognizable credential strings. Inventory is capped at 512 items, 2048 directories and ten path components; reaching a cap fails explicitly rather than silently truncating. Responses contain no raw configuration, credential files or host paths. Native errors are mapped to fixed portal messages.

All filesystem traversal uses directory descriptors with no-follow opens. Symlink, hardlink and nonregular file content cannot be edited. Writes use a profile-local cooperative lock, content-hash compare-and-swap, private atomic replacement and the existing five-version private backup helper. A conflict returns HTTP 409 without replacing the target. Administrator/root writers can ignore the cooperative lock; the final reread narrows but cannot eliminate that threat model. No deletion endpoint is provided.

## Verification

Run the focused contracts and fixed-file regressions offline:

```sh
python -m pytest tests/test_customization.py tests/test_customization_proxy.py tests/test_config_files.py tests/test_settings.py -q -p no:cacheprovider --basetemp="$(mktemp -d)"
```

Tests cover authentication/CSRF, fixed profile routing, strict proxy shapes and error redaction, CAS conflicts, active owner skills, editable project skills, immutable approved imports, inert draft storage, frontmatter and secret rejection, path/link refusal, inventory overflow and a noncooperating writer before final replacement.
