# Álvaro's Personal Agent — Implementation Plan

Build one private assistant for Álvaro Sepúlveda Vélez's web development, marketing, and AI work. Run Hermes Agent on one VPS and access it through a minimal web chat and a private Telegram bot. Start with a useful assistant, not an agency platform.

**Status:** planning only; no application, VPS deployment, or integrations implemented.
**Date:** November 9, 2026. **Repository:** [asepulvedadev/personal-agent](https://github.com/asepulvedadev/personal-agent), private. Repository creation and plan upload were authorized by Álvaro.

## 1. The decision in one minute

- One owner, one Hermes profile, one gateway, and one configured model provider.
- Reuse Hermes for reasoning, tools, Telegram, sessions, memory, and skills.
- Build only a small web chat and a thin server-side bridge to Hermes.
- Use native persistence and plain project files before adding databases or memory services.
- Give the agent limited permissions; keep publishing, spending, and production changes under human control.

Success means the assistant reliably helps with real work without becoming another product to maintain. “Perfect” is an iteration goal, not a promise of unlimited capability or autonomy.

## 2. What the assistant should help with

Confirmed owner context: Álvaro is a web developer specializing in marketing and artificial intelligence. No client names, budgets, current projects, or existing infrastructure are assumed.

| Work area | First useful outcome | Boundary |
| --- | --- | --- |
| Projects | Summarize project status, blockers, decisions, and next actions | Use supplied or explicitly connected sources |
| Development | Explain code, propose implementation steps, review changes, draft tests | No production access by default |
| Marketing | Research competitors, draft landing-page copy, prepare campaign briefs | Never invent metrics or publish automatically |
| AI and automation | Assess repetitive workflows and propose practical automations | Prefer the smallest useful solution |
| Personal work management | Turn a request into a short, prioritized action list | No separate task-management product |

Start by testing three recurring workflows: a daily project briefing, a development task breakdown, and a marketing brief. Expand based on actual usage rather than installing every available tool.

## 3. MVP scope

### Web: a conversation, not a dashboard

The initial screen contains only:

- A small agent name and connection indicator.
- One conversation transcript with readable Markdown and code blocks.
- A message composer, Send, and Stop controls.
- A discreet New conversation action and a way to resume the last conversation.

No sidebar, navigation menus, analytics dashboard, settings area, agent catalog, project boards, or model picker. Responsive layout, keyboard access, visible errors, and reconnect behavior are part of the minimum experience. Cross-device web history should come from Hermes, not only browser storage.

Use one lightweight HTML/CSS/JavaScript frontend, served by the web bridge. A small Python/FastAPI bridge is the proposed implementation choice; no React application or frontend build pipeline is needed initially. The bridge must not implement a second agent loop.

### Telegram: the mobile entry point

Use Hermes' native Telegram integration, not a custom bot implementation. Create a bot through BotFather, store its token outside Git, and allow only Álvaro's numeric Telegram user ID. Start with direct-message text chat and long polling. Voice notes are a later addition if useful, not an MVP dependency. [Telegram integration](https://hermes-agent.nousresearch.com/docs/user-guide/messaging/telegram)

### One identity does not mean one live transcript

Both channels should use the same Hermes profile and project sources. Keep channel conversations separate initially: do not promise that a web conversation and a Telegram chat automatically become the same thread.

Native memory is curated and bounded; its snapshot is loaded at session start. A fact saved in one conversation may require a fresh session before it appears in another. Explicitly test this behavior rather than describing it as instantaneous synchronization. [Persistent memory](https://hermes-agent.nousresearch.com/docs/user-guide/features/memory)

Provide continuity through saved project notes and session search first. Add explicit cross-channel transcript continuation only if using separate conversations proves inconvenient.

## 4. Minimal architecture

```text
Álvaro's browser -- private network --> Web chat + thin Python bridge
                                                  |
                                      authenticated local HTTP
                                                  |
Telegram <------ outbound long polling ------> Hermes gateway
                                                  |
                                  Model provider + permitted tools
                                                  |
                                    Native state + project files
```

All application components run on the same VPS. The Python bridge serves static assets and forwards permitted chat/session operations. Hermes remains the only agent runtime.

Hermes exposes authenticated session creation, history, and chat streaming endpoints. Prefer that native surface; detect capabilities against the pinned version before implementation. The browser never receives `API_SERVER_KEY`, model credentials, or unrestricted access to Hermes endpoints. [API server](https://hermes-agent.nousresearch.com/docs/user-guide/features/api-server)

**Proposed web access:** a private Tailscale network restricted to Álvaro's devices. Bind the bridge to the private interface, not the public internet; keep the Hermes API on loopback. This avoids building a public login system. The tradeoff is that each device needs private-network access. A public domain is a separate future decision requiring HTTPS, authentication, and a security review before exposure.

Do not expose the Hermes administrative dashboard as the product UI. It has configuration and secret-management capabilities that this chat does not need. Keep it disabled or reachable only through a separate administrative access path. [Dashboard security](https://hermes-agent.nousresearch.com/docs/user-guide/features/web-dashboard)

## 5. Personalization and project knowledge

Create these only during implementation; the current deliverable is this plan:

| Artifact | Purpose |
| --- | --- |
| Hermes `SOUL.md` | Assistant role, tone, working principles, and autonomy limits |
| Hermes-managed `USER.md` | Álvaro's confirmed profile and durable preferences |
| Hermes-managed `MEMORY.md` | Concise verified facts, not an unlimited document archive |
| `workspace/projects/<slug>/PROJECT.md` | Project objective, sources, status, blockers, and next action |
| `workspace/projects/<slug>/DECISIONS.md` | Important decisions and their rationale, when needed |
| A small set of Hermes skills | Reusable procedures proven useful in real tasks |

Project facts should link to their source and distinguish confirmed information from assumptions. Update notes after meaningful work, not after every chat message. Start with three reusable procedures matching the workflows in section 2; do not create a separate agent per specialty.

The repository should contain sanitized templates and application source, not real client files, live memory, conversations, credentials, or production configuration. Runtime project knowledge stays in the private workspace and its protected backups.

## 6. Autonomy and safety

The initial agent may research public sources, read authorized project files, draft documents, and organize notes inside its assigned workspace.

Require explicit approval for external messages, publishing, pushes, deployments, advertising changes, paid API operations beyond an agreed budget, deletions, and modifications outside the assigned workspace. Treat content from websites and documents as untrusted input, never as permission to act.

Enforce limits through tool access and operating-system permissions, not only instructions. Do not give the agent root, production credentials, the host Docker socket, or unrestricted SSH access. Begin without general-purpose terminal execution; enable development execution only after an isolated workspace and its restrictions are tested. There is no need to expose dangerous capabilities merely to display an approval button.

Choose a version-pinned Hermes release after compatibility testing. Verify actual approval behavior in both channels; if a capability cannot be safely approved or denied through a channel, leave it disabled there. Only one gateway should own the Telegram bot token.

## 7. Deployment and operating model

- **VPS:** Linux host selected later. A provisional sizing target is 2 vCPU and 4 GB RAM for an API-backed model and light tool use, not a measured minimum or a purchase recommendation. Reassess after testing; no local model hosting in the MVP.
- **Processes:** one supervised Hermes gateway and one supervised web bridge. Use systemd with a dedicated non-root service account; no Kubernetes or additional worker platform.
- **Model:** one provider and one capable tool-calling model initially. Select it using the three real workflows and an agreed monthly spending limit; do not lock in a model or claim a fixed price now.
- **Persistence:** Hermes' native state plus project files on persistent storage. No Postgres, Redis, vector database, or external memory platform initially.
- **Recovery:** encrypted backups of runtime state and project files, outside the VPS, with restricted access. Use a supported SQLite backup procedure or quiesce the service; do not blindly copy an active database. Test a restore before relying on it.
- **Maintenance:** pinned versions, a rollback path, useful logs without secrets, and a brief manual update procedure. Avoid a monitoring stack until basic service checks are insufficient.

The cost model is VPS + model usage + optional tool usage + backup storage. Set a provider-side limit where supported; otherwise disable paid tools or enforce a tested limit before enabling unattended work. A chat instruction alone is not a spending control.

## 8. Build order and completion gates

| Phase | What to create | Exit condition |
| --- | --- | --- |
| 0. Foundation | Private repository, this plan, confirmed identity/access inputs | Scope and access boundaries are clear; no secrets committed |
| 1. Core assistant | Pinned Hermes installation, one provider, profile, workspace, Telegram allowlist | Álvaro can complete a useful task in Telegram; another account is rejected |
| 2. Minimal web | Static chat, Python bridge, private access, native session integration | Send, stream, stop, reconnect, and resume history work on desktop and mobile |
| 3. Work context | One real project, compact profile, three initial procedures | The assistant produces useful development and marketing outputs from supplied context |
| 4. Reliability | Safe restart, protected backup, restore test, usage limits | The system recovers without losing required state or repeating external actions |
| 5. Measured expansion | Only integrations justified by repeated use | Each new integration has a clear benefit, minimal permissions, and a tested failure path |

Telegram is the first validation channel, not a substitute for delivering the web UI. Do not start additional features before the two requested channels and core safety checks work.

## 9. MVP acceptance checklist

- [ ] Álvaro can open the web interface and immediately chat without navigating menus.
- [ ] The web experience is usable from both desktop and mobile devices.
- [ ] The Telegram bot responds to Álvaro and rejects an unauthorized account.
- [ ] Both channels use the intended profile and project sources without claiming automatic transcript merging.
- [ ] A saved preference is recovered in a fresh session; project context survives a restart.
- [ ] Web history resumes after reload; streaming errors and cancellation are displayed honestly.
- [ ] Sending from both channels does not corrupt state; execution follows Hermes' supported admission behavior.
- [ ] An unknown request outcome is investigated, not blindly retried if it could repeat a side effect.
- [ ] The agent cannot publish, spend, deploy, or reach production using ungranted capabilities.
- [ ] Credentials never appear in Git, browser-delivered source, or routine logs.
- [ ] The native state and project workspace can be restored from a tested backup.
- [ ] The three initial workflows are useful enough for regular work, with limitations documented.

## 10. Explicitly out of scope

No multi-agent agency, customer portal, multi-tenancy, billing, CRM, visual workflow builder, vector search, automatic social publishing, WhatsApp integration, autonomous ad buying, or broad MCP catalog. No fork of Hermes unless a verified requirement cannot be met through its supported interfaces.

GitHub project access, calendar, email, marketing platforms, voice, and scheduled briefings can follow later, one at a time. Prefer read-only integration first. The criterion is demonstrated value, not technical availability.

## 11. Inputs needed before deployment

Confirm these progressively, not through a long setup questionnaire:

1. Repository name, visibility, and the authorized GitHub session for creation/upload.
2. The exact VPS destination, permitted operations, and credential/session to use.
3. Model provider, credentials supplied through a secure path, and spending limit.
4. Telegram bot token and Álvaro's numeric user ID, never committed or pasted into the plan.
5. Approval of private-network web access and one real project to validate the assistant.

The present request authorizes the plan and repository creation, not VPS provisioning, account purchases, deployment, or production integration. Ask only for the next consequential missing decision.

## 12. Evidence and next action

Official Hermes documentation was checked for this plan. The API, Telegram integration, bounded memory, and dashboard are existing capabilities; the minimal web UI and its private-access architecture are proposed work. Recheck the exact contracts against the version selected for deployment.

**Next action:** confirm the VPS destination and authorized access, then configure one working Telegram assistant before building the web surface. The initial repository delivery contains only this plan, with no credentials or client data.
