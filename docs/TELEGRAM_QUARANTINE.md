# Telegram quarantine ingress

## Boundary

The trusted image-owned guard consumes import candidates before Hermes caches attachments,
creates an agent event, or runs tools. It never installs a skill, executes uploaded content,
approves activation, or forwards package instructions to the capable agent. Its only broker
capability is quarantine ingestion. Owner approval remains a separate portal operation.

`runtime/patch_quarantine.py` verifies the exact SHA-256 of the Telegram adapter from upstream
commit `3b421578588243f920886e6fe79a6faa87ea026e`, then inserts one fixed-path load of
`/opt/runtime/quarantine_ingress.py` at the beginning of `_register_handlers`. Build fails on
source drift. The seam runs on first connection and application rebuild/reconnect. Reapplying
the exact patch is idempotent only when it recovers the pinned original bytes.
The second pinned seam validates ordering after late native plugin registration;
an unsafe addition is rolled back to the prior handler table before the error propagates.

Unlike optional plugin factories, guard setup failures propagate before native handler
registration or polling. The guard occupies an earlier blocking handler group, refuses
conflicting pre-ingress handlers, and stops every matched update even when download,
broker delivery, metadata parsing or acknowledgement fails. Unrelated chat continues normally.

This protects this intake path; it is not an OS sandbox or a guarantee that an otherwise
capable agent can never independently download public code. Do not treat scanner output as
proof that code is harmless.

## Image integration

The project Dockerfile must copy both trusted scripts into `/opt/runtime`, then run:

```sh
python /opt/runtime/patch_quarantine.py /opt/hermes
```

Keep these scripts on the read-only image filesystem. Do not place them in the agent's
writable profile/plugin/skill directories, make the patch optional, or catch its failures.

## Runtime configuration

Set trusted values in the supervisor's environment before Telegram startup:

| Variable | Contract |
| --- | --- |
| `QUARANTINE_OWNER_IDS` | Required comma-separated positive numeric Telegram owner IDs. Falls back to `TELEGRAM_ALLOWED_USERS`; wildcard/names/empty entries are rejected. |
| `QUARANTINE_BOT_ID` | Expected positive numeric receiving-bot ID. Explicit configuration is recommended under multiplexing; fallback is the numeric prefix of `TELEGRAM_BOT_TOKEN`. |
| `QUARANTINE_TARGET_PROFILE` | Destination profile for the receipt, default `carpediev`. It does not change bot ownership or native profile routing. |
| `QUARANTINE_BROKER_URL` | Fixed trusted broker origin, default `http://web:8080`; no credentials, path, query, fragment, redirects or proxy-environment routing. |
| `QUARANTINE_INGEST_TOKEN_FILE` | Absolute path to a dedicated, read-only narrow ingest credential. |
| `QUARANTINE_INGEST_TOKEN` | Alternative dedicated ingest credential; takes precedence over the file. Never give this credential activation authority. |
| `QUARANTINE_REVIEW_URL` | Optional operator-configured HTTPS owner review page. Never derived from incoming content. |

All candidate authorization happens before download or broker access. The sender must be
an allowed human, in their own private chat, through the exact bound application and
expected receiving bot. Group posts, channel posts, missing senders and other bots are rejected.

## Candidate policy

- Standalone canonical HTTPS GitHub repository URLs are intake candidates.
- GitHub URLs embedded in an explicit skill/import/study request are intake candidates.
- Ordinary code discussion containing a GitHub URL is not intercepted.
- Hidden Telegram text-link entities are considered for explicit intake requests.
- Multiple candidate URLs are rejected as ambiguous, not partially ingested.
- ZIP, `.skill`, Markdown, scripts and text/config source attachments are candidates.
- Unknown/suspicious document extensions are consumed and refused, never passed through.
- Existing PDF/DOCX/TXT/CSV and ordinary media-document flows remain unchanged unless an
  explicit intake caption is present. A renamed file is not considered trustworthy; the
  broker must inspect actual bytes independently.

Acceptable uploaded source extensions are declared in `runtime/quarantine_ingress.py`.
Unsupported archive formats must be repackaged as ZIP; no archive is extracted by the guard.
Display filenames cannot contain separators, control characters or traversal entries.

Telegram documents must declare a positive size no larger than 5 MiB. The guard obtains
the file from the receiving Telegram SDK only, uses a bounded in-memory output sink, checks
actual bytes again, and enforces download deadlines. No attachment is written to disk or
Hermes cache by this path. PTB may buffer the trusted Telegram HTTP response internally
before writing to the sink; this is not a streaming-memory guarantee for an untrusted
arbitrary HTTP server. Broker resource/ZIP-expansion limits remain mandatory.

## Narrow broker protocol

All calls run off the event loop, carry `Authorization: Bearer <dedicated-ingest-token>`,
have an overall deadline, forbid redirects, and cap metadata responses at 64 KiB.

```text
POST /internal/quarantine/github
Content-Type: application/json
{"url": "https://github.com/owner/repository", "profile": "carpediev"}

POST /internal/quarantine/upload?name=SKILL.md&profile=carpediev
Content-Type: application/octet-stream
<raw bounded bytes>
```

Success responses use HTTP 200/201 and an object with an opaque `id` (8–96 ASCII
letters/digits/hyphens/underscores), a recognized `state`, and optional `findings` list/dict.
Only the opaque ID, a fixed localized state label, finding count and configured owner
review page are shown. No package instructions, snippets, filenames, arbitrary URLs,
broker errors or suggested commands are reflected. The guard has no approve endpoint.

If an acknowledgement is uncertain, the owner is directed to inspect the portal before
retrying. Rejected or failed candidates never fall back to the ordinary agent path.

## Offline verification

```sh
PYTHONDONTWRITEBYTECODE=1 /tmp/personal-agent-testenv/bin/python -m pytest \
  -p no:cacheprovider tests/test_quarantine_ingress.py
```

Tests use fake PTB types and broker responses, not live Telegram or GitHub credentials.
Before release, verify the patch against the exact build source, run a pinned-container
import/registration probe without polling, and check the parent deployment's real owner
route and broker authorization separately. Do not infer live delivery from offline tests.

## Cross-component receipt regression

The broker uses `review_required` for retained material requiring further review. Telegram accepts this exact state and renders only its fixed localized label, receipt ID and finding count. A regression passes a real broker summary to the receipt renderer; package text is never reflected. This offline check does not establish that the image patch or Telegram cutover is deployed.
