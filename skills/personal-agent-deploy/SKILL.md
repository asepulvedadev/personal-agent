---
name: personal-agent-deploy
description: Inspect or perform owner-authorized GitHub Actions deployment and rollback for asepulvedadev/personal-agent. Not for other applications or unrestricted VPS administration.
---

# Personal-agent deployment

The versioned application belongs in `/workspace/projects/personal-agent`; future applications belong in sibling project directories. Private profiles, conversations, credentials and quarantine evidence stay outside Git. A working checkout is not host deployment authority.

Read the current project's `docs/DELIVERY.md` and `docs/DEPLOYMENT.md` before deployment. Bootstrap is pending until independently verified; never infer readiness from a workflow file. Use `gh auth status` and explicit `--repo asepulvedadev/personal-agent`. Host authentication does not imply container authentication.

For read-only status, use `gh run list` and `gh run view`. Implementation alone does not authorize publishing or deploying. When the owner authorizes release, verify the exact diff, secret scan and offline tests, then use the established repository delivery policy. The main workflow deploys only after CI passes; do not dispatch a second concurrent rollout.

A manual operation uses `gh workflow run workspace.yml --repo asepulvedadev/personal-agent --ref main -f operation=deploy -f sha=EXACT_MAIN_SHA`. Rollback requires explicit owner authorization and an exact previously accepted SHA with `operation=rollback`. Read the run's conclusion and verify production health; a dispatch receipt is not success. Stop on ambiguous outcome or failed recovery rather than retrying blindly.

Never obtain host root/Docker credentials, bypass the forced deployment gate, modify another application, prune images, or restore data automatically. Keep retained rollback evidence. Report a meaningful failure plainly without raw secrets or infrastructure chatter.
