# Project-restricted continuous delivery

CI runs offline Python tests and the Node voice tests on pushes, pull requests, and manual runs. Test jobs have no production environment or deployment secrets. Only successful main-branch push/manual runs in `asepulvedadev/personal-agent` can enter the production deployment job. Configure required production reviewers if manual owner approval is desired. Deployments serialize without cancelling an active rollout; GitHub may replace an older pending run. The host rejects stale commits, so superseded main runs fail closed.

The production runtime dependency lock is unchanged. `requirements-dev.txt` contains test dependencies only. CI does not run `live_smoke`, isolation probes, or native-history network checks. Official action commit pins were verified against GitHub's public tag-reference API; update pins through reviewed dependency changes.

## Administrator bootstrap (separate from rollout)

Run these commands only as the authorized administrator on the project host, after reviewing the local source and taking the normal host-state backup. Replace `/root/personal-agent-reviewed` with the absolute reviewed source directory; it must not use symlinked ancestors. No command below deploys an application release.

```sh
/usr/bin/python3 -I /root/personal-agent-reviewed/deploy/bootstrap.py --source /root/personal-agent-reviewed
/usr/bin/python3 -I /root/personal-agent-reviewed/deploy/bootstrap.py --source /root/personal-agent-reviewed --check
```

Installation requires `current` to point directly to preserved `releases/initial`. It refuses unexpected accounts/groups, non-private project permissions, unknown personal-agent units, or unreviewed unit drop-ins. It copies only the curated policy files, freezes the five existing service/timer definitions, and runs `systemctl daemon-reload`. It does **not** start/restart the application, Docker, workspace mount, or backup timer. Only already-active personal-agent firewall/edge-link helpers receive `try-restart`; edge-link does not pull the application service into an active state. Missing/inactive helpers require an explicit administrator decision, not an implicit application start.

**Keep `/srv/personal-agent` root-owned mode `0700`.** Existing mode-`0444` runtime-token files rely on that outer boundary. Policy/state are root-private `0700`; installed policy files are `0600`. The unprivileged SSH account instead uses the separate root-owned, publicly traversable `0755` forced gate `/usr/local/libexec/personal-agent-ssh-gate.py`. It still invokes only the fixed sudo target inside the private policy directory. The account has a root-owned home, locked password, its own system group only, `/bin/sh` for OpenSSH forced-command dispatch, no home `.ssh` authorization, no forwarding/TTY/user RC, and Ed25519 public-key authentication only. Its root-managed authorized-key file starts empty.

The only base SSH configuration change is this marked block appended at EOF, preserving all prior bytes, ownership and mode:

```text
# personal-agent restricted deployment
Match all
Include /etc/ssh/personal-agent-deploy.conf
```

The scoped project fragment lives outside the early `sshd_config.d` include. Bootstrap stores the original global configuration in root-private `/srv/personal-agent/deployment-state/sshd_config.before-personal-agent`, refuses conflicting markers/includes, validates sudoers and `sshd -t`, checks effective deployment-user restrictions, and verifies root's effective SSH settings are unchanged before reloading `ssh.service`. Validation failure restores prior global SSH bytes/metadata and project SSH/sudo fragments before any reload. Existing administrative SSH access is not broadened or replaced. Retain an independent administrator session during bootstrap.

`--check` performs structural/account/effective-configuration checks without writes or reloads; it is **not** a container-health or remote-login test. After independent review, successful readiness checks, owner-verified host identity, and provisioning a dedicated key outside agents, enable **only its public `.pub` file** in a separate step:

```sh
/usr/bin/python3 -I /root/personal-agent-reviewed/deploy/bootstrap.py --source /root/personal-agent-reviewed --enable-key /root/personal-agent-deploy.pub
```

This accepts one structurally valid Ed25519 public key, writes the restricted authorized-key entry, and never reads/prints a private key. Rerunning installation refuses a nonempty key file; disable it explicitly before policy replacement. Private-key creation, Actions secret provisioning, SSH login validation and production rollout remain separate authorized owner operations. Keep reviewed fixed policy separate from future application source.

## Owner bootstrap prerequisites

These operations belong to the authorized host administrator, not the assistants or workflow. Do not enable the credential until all prerequisites are complete.

1. Keep `/srv/personal-agent/{workspace,control,runtime-auth,portal-bootstrap}` unchanged. Source releases live at `releases/<40-character-sha>` outside the agent-writable workspace. `/workspace/projects/personal-agent` is a separate working checkout, not the deployment authority.
2. Create root-owned `deploy-policy` and `deployment-state` directories, inaccessible for modification by the deployment account or agents. Install reviewed `release_gate.py`, `ssh_gate.py`, `service_gate.py`, baseline `compose.yaml`, `network-guard.sh`, `backup.py`, and `edge-link.py` in `deploy-policy`. Gate scripts use only Python's standard library. Preserve the current `releases/initial` directory and its existing baseline image tags. Never overwrite those tags or prune retained images. The release gate creates root-private state, image-only overrides, accepted-release markers, a project lock, and secret-safe `events.jsonl` evidence in `deployment-state`.
3. Install the trusted policy copies **outside** source releases, owned by root with no group/other write access. Review all ancestor-directory ownership. Only the deployment administrator may replace this policy. Install Docker Compose and Python 3 on the host. Ensure `releases` and `backups` already exist and the initial deployment is healthy. Review the fixed Compose policy: it must not contain host lifecycle hooks, source-mounted host execution, agent Docker sockets, or deployment credentials. Candidate Compose files are never consulted.
4. Repoint **every** personal-agent service, backup timer/service, network guard, and edge-link unit away from `current/deploy/*`. Use only root-owned policy scripts. The application service must not run bare `docker compose` from `current`; it must select the fixed policy and the accepted release's image override as below. Do not alter unrelated units or global Docker/firewall state. The existing project guard modifies only its own chains/rules.
5. Create a dedicated `personal-agent-deploy` account with no password access, Docker-group membership, agent-accessible home, or root private key. Install its public key with OpenSSH `restrict,command="/usr/bin/python3 -I /usr/local/libexec/personal-agent-ssh-gate.py"`. Disable password, keyboard-interactive and forwarding access for this account with a scoped `Match User` SSH policy; enforce `ForceCommand` too so future keys cannot gain a shell. Keep account/authorized-key files root-managed. The forced command ignores client environment except `SSH_ORIGINAL_COMMAND`, permits only `status`, `deploy <sha>`, or `rollback <sha>`, closes stdin, and invokes fixed sudo argv without a shell.
6. Authorize only the fixed interpreter/script command in sudoers, e.g. `personal-agent-deploy ALL=(root) NOPASSWD: /usr/bin/python3 -I /srv/personal-agent/deploy-policy/release_gate.py *`. The script revalidates the entire argument list; do not authorize arbitrary Python, shells, Docker, sudo environment preservation, or agent-owned scripts. Validate sudo/SSH configuration before reloading it. Generate/provision the deployment key outside agent containers.
7. Create the GitHub `production` environment, restrict it to `main`, configure protection/review policy, and set exactly these environment secrets: `PERSONAL_AGENT_DEPLOY_KEY` (dedicated account's SSH private key) and `PERSONAL_AGENT_KNOWN_HOSTS` (owner-verified complete known_hosts line for `2.24.115.58`). Verify the host fingerprint through an independent trusted channel; do not trust runtime `ssh-keyscan`. No root SSH key or password belongs in Actions. Ensure production secrets are not also set at repository/org level where test jobs could receive them.

## Fixed Compose interface and host units

For an accepted SHA, use this argv shape (replace both `<sha>` instances with the root-managed accepted identity; never evaluate source-provided shell code):

```text
/usr/bin/docker compose --project-name personal-agent --project-directory /srv/personal-agent/releases/<sha> --env-file /dev/null -f /srv/personal-agent/deploy-policy/compose.yaml -f /srv/personal-agent/deployment-state/<sha>.images.json up -d --no-build --pull never
```

`initial` uses its preserved directory and baseline policy image tags, **without** an override. The installed root-owned `service_gate.py start|stop` wrapper resolves `current` only after validating that it points directly to `releases/initial` or a previously accepted 40-hex release. It must choose the corresponding fixed argv above. Never read a candidate Compose file, `.env`, hook, service unit, or helper script on the host. If adapting systemd, use a separately reviewed root-owned wrapper, not an `ExecStart` inside a source release. Gate `status` is an observation, not a boot/restart command. Keep the trusted edge-link service running so a recreated web container is reattached to its existing frontend.

## Deployment behavior

`deploy <sha>` checks the fixed public repository's current main via GitHub's anonymous API, downloads only the canonical codeload URL for that exact SHA, and checks current main again immediately before rollout. Redirects and arbitrary URLs are refused; transient API/rate-limit failure stops deployment. Compressed download is capped at 32 MiB, expanded tar at 128 MiB, each regular file at 16 MiB, and member count at 5,000. Extraction rejects traversal, duplicate names, links, devices, control characters, archive-root/commit mismatch, and private/runtime/cache paths including `.env*`, `.git`, workspace, quarantine, and runtime credentials. Source bundles containing such files are rejected, not merged with persistent state. No tar `extractall` or candidate host script runs.

The gate builds unique SHA-tagged images with fixed policy/context and an image-only override, takes a trusted backup, applies the trusted project network guard, then uses `up --no-build --pull never`. No `down`, global restart, or prune occurs. Web/Hermes must report Docker health `healthy`, and all three services must be running, within approximately 5 minutes (individual `ps` probes are capped at 10 seconds). Builds and `up` have a 10-minute per-command timeout. Backup timeout is 6 minutes. The serialized host lock rejects concurrent direct SSH requests.

Only healthy rollout promotes `current` atomically. Any activation/promotion failure attempts exactly one recovery to the prior accepted release (including initial), retaining the original nonzero result even if recovery works. Rollback failure is recorded as owner-action-required. Failed source/images remain for administrator inspection; neither persistent state nor unrelated applications are deleted. A backup does **not** guarantee application data compatibility: source changes must preserve persisted formats or include an explicitly reviewed data rollback plan. No automatic data restoration occurs.

Manual `rollback <sha>` accepts only retained, previously healthy SHA releases with root-managed acceptance records and image overrides; it never downloads or rebuilds. `initial` is available for automatic first-deploy recovery only; intentional manual restoration to initial is an administrator operation. Preserve accepted source/images and the fixed baseline policy. Changing policy can invalidate retained rollback assumptions, so review it separately. A healthy current SHA redeploy is idempotent and does not rebuild accepted images.

Owner invocation through the restricted account is `ssh personal-agent-deploy@2.24.115.58 'status'`, `'deploy <sha>'`, or `'rollback <sha>'`. Manual Actions runs must use the main branch; rollback requires an explicit retained SHA. Deploy defaults to that workflow's main SHA; if main has advanced, it fails rather than deploying stale source. Status exposes only release identity/health; SSH failure responses expose only error category. Inspect root-private event evidence and production health separately; CI success is not production-health proof.

## Bounded canonical-head lookup

The administrator gate uses the exact Git reference endpoint for `refs/heads/main`, validates the returned ref and commit object type and SHA, and caps metadata at 16 KiB. It does not download the full commit API response (which includes patches). A prior rollout of the owner configuration editor failed safely before activation because its full commit JSON exceeded the old 64 KiB head-check limit. The fix retains canonical repository-only URLs, redirect refusal, immutable commit archives, and both pre-build and pre-activation head checks. Frozen host policy must be updated by the authorized administrator; publishing the source helper alone does not update it.
