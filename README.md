# Personal agent workspace

An owner-authenticated Web interface and isolated Hermes Agent runtime for personal software, marketing and AI work. Application source is public; credentials, conversations, client materials, live memories and quarantine evidence are not.

## Development

Use Python 3.12 for the portal test suite and Node for browser-unit tests. Production images use the separately pinned Hermes runtime.

```sh
python -m pip install --require-hashes -r requirements.lock
python -m pip install -r requirements-dev.txt
python -m pytest tests -q -p no:cacheprovider --basetemp="$(mktemp -d)"
node --test tests/*.cjs
```

Do not run live probes against production without explicit target/session authorization. Store test caches and scratch data outside the checkout.

## Runtime layout

- Persistent application projects: `/workspace/projects/<project>/`; this repository: `/workspace/projects/personal-agent`.
- Hermes Web: `/workspace/.hermes`; Carpediev: its separate `profiles/carpediev` home.
- Owner-only quarantine: Web control storage, outside agent mounts and active skills.
- Host release authority: root-owned policy outside agent-writable project checkouts.

See [delivery status](docs/DELIVERY.md), [deployment](docs/DEPLOYMENT.md), [quarantine](docs/QUARANTINE.md), and [Telegram guard](docs/TELEGRAM_QUARANTINE.md). A workflow file or passing offline test is not proof of a live deployment. External skills require quarantine and explicit hash-bound approval; static analysis cannot guarantee harmlessness.

Versioned agent skills live in `skills/`. The image seeds trusted copies without overwriting later owner improvements. GitHub authentication is per runtime: never copy host tokens into repository files or assume a host login authenticates a container.
