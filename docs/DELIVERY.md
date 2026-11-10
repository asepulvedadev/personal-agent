# Hermes workspace delivery

Repository: `asepulvedadev/personal-agent` (public). Target: the existing personal-agent deployment on the explicitly authorized VPS, isolated from other applications. Do not version credentials, conversations, live memories, quarantine contents, or workspace data.

## Authorized outcomes

1. Make `/workspace/projects/` the working root for versioned application projects; keep private Hermes state separate.
2. Version the existing project source plus sanitized assistant identities, quiet-display preferences and skills. Existing local assistants are not the deployed container; preserve that distinction.
3. Add tested GitHub Actions CI/CD with a project-restricted deployment credential, verified rollout and bounded rollback. Never give agent containers host Docker or root SSH access.
4. Provide a deployment skill to the two assistants and Codex without embedding credentials.
5. Accept GitHub links, ZIPs and individual skill files through a quarantine-first flow. Never execute supplied scripts or treat imported instructions as authority during inspection. Retain evidence and bind any promotion to the exact reviewed content.

## Security and completion gates

- A static scan is evidence, not proof that a package is harmless. Unsupported/executable/uncertain content stays quarantined pending an owner decision; no automatic unrestricted installation.
- Quarantine storage must not be an active skill directory or an agent-writable mount.
- Keep owner authentication, CSRF protection, private provider credentials, persistent state and network isolation intact.
- Verify existing behavior before changes, targeted negative security cases, a complete local suite, independent security/deployment checks, and production health after the authorized rollout.
- Report Web and Telegram coverage separately. A Web endpoint alone does not establish Telegram protection.

## Verified pre-release checkpoint — November 10, 2026

- Combined offline suite: **206 Python tests and 16 Node tests passed**; one existing Starlette/httpx deprecation warning. Independent quarantine review found and rechecked two defects: Web chat now rejects candidate imports before capable-agent access, and Telegram accepts the real broker `review_required` state. Ordinary repository questions remain ordinary chat.
- Separate independent bootstrap review found no observed blocker. Its scoped suite had one missing-workflow-file audit-input failure; the full workspace suite includes that file and passes. Actual host bootstrap and structural checks passed, including native `sshd`/sudo parsing and unchanged effective root SSH configuration.
- All five own service/timer definitions now use root-owned policy outside mutable source. The project root, policy and deployment state remain root-owned **0700**. No application or Docker restart occurred during bootstrap.
- Dedicated `personal-agent-deploy` key authentication returned healthy initial status; unrestricted command `id` was refused. The root credential is not in Actions or containers. Production environment permits only `main`; exactly the dedicated deployment key and previously trusted known-host lines were provisioned as environment secrets.
- New images built under separate preflight tags without changing live image tags. An isolated, network-disabled run verified actual pinned PTB registration/rebuild, ingress-first ordering, trusted identities/skills, GitHub CLI 2.96.0 and SQLite. The narrow broker token was provisioned without displaying it.
- Public-source secret inspection uses detect-secrets 1.5.0 with verification disabled, plus a detected synthetic canary to prove files were scanned. Fourteen candidate findings were reviewed: metadata/control-flow text, pinned source hashes, synthetic test values, and a private-key-header rejection fixture; no live credential was found. Private/runtime directories are excluded from source and Git.
- The concurrent skill-picker release completed separately and its exact additions were preserved. This task has not yet committed, published or rolled out its candidate at this checkpoint.

## Runtime and assistant boundaries

The supervisor reconciles approved snapshots independently of model revision, restarts on changed imports, stops on reconciliation failure, and preserves imports when the optional field is absent. Invalidated managed imports move outside skill roots; unrelated owner skills are untouched. Identities and seeded trusted skills preserve later owner improvements. Primary, compression and title requests retain the private relay. Runtime defaults use smart approvals and quiet display, remove inherited YOLO mode, retain protected instruction files, and work in `/workspace/projects/personal-agent`.

Versioned skills live in `skills/`; deployment/quarantine skills were also installed for Codex and both local Hermes profiles. Host and container GitHub authentication are separate. The VPS image contains the CLI but still needs its own owner authentication.

## Remaining release checks

1. Publish the coordinated source, observe branch CI, then the authorized main Actions rollout and live health. Static documentation is not a live status service: inspect the actual Actions run and restricted gate before claiming success.
2. Populate the persistent project checkout from the public repository without mixing it with host release authority or private profile state.
3. Verify live owner-only quarantine endpoints, approval/revocation synchronization and existing Web behavior. Keep original private data, provider relay and other applications intact.
4. Telegram guard image readiness does not migrate the existing local bot. Do not start a second poller or transfer its token without an explicit coordinated cutover. Report channel coverage separately.
5. Complete the VPS runtime's own GitHub device authentication with the owner; never transfer the local broad OAuth token as a shortcut.

## First Actions rollout correction

Branch CI and main CI passed. The first production job (`42823344388`) failed before Compose activation: the sanitized host environment omitted `/usr/sbin`, so the trusted firewall helper could not resolve `iptables` or `iptables-restore`. Its single recovery attempt hit the same environment error; the previous Web/Hermes/proxy containers remained running and healthy and `current` stayed on the initial release. Native host lookup reproduced exit 127. Both release and service wrappers now use the fixed, administrator-owned standard paths including `/usr/sbin` and `/sbin`, without inheriting client PATH. New regression tests first failed then passed (58 scoped deployment/bootstrap tests). The host policy update and a fresh, observed rollout are required before claiming continuous delivery success.

## End-to-end asset correction

The next Actions run (`42824605585`) passed both jobs and basic container health. Native non-root inspection then reproduced a static-asset permission failure: source extraction inherited the administrator umask 077 for nested directories, and Docker copied that inaccessible metadata into the image. API health alone missed this. The administrator restored the known working initial images, retained the suspect acceptance record outside rollback eligibility, and paused the deployment key. Extraction now explicitly normalizes only validated public source directories to 0755 (files remain 0644); the private project/policy/state roots stay 0700. Web health also requests `/static/app.js`, compatible with initial images. Both new regression tests were observed RED before the fix. Only a new successful rollout plus actual asset/skill checks establishes completion.
