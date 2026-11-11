# Cuarentena source auditor

The auditor describes what a skill or tool contains **without installing,
importing, executing, or following instructions from the candidate**. It uses
Python's standard library only. It writes no output files and performs no network
requests. The trusted intake layer may fetch explicitly authorized public source;
the auditor itself receives bytes, not a URL to follow.

## Standalone use

```sh
python3 skills/cuarentena/scripts/audit.py /absolute/path/candidate.zip --format text
python3 skills/cuarentena/scripts/audit.py /absolute/path/SKILL.md --format json
```

Python 3.11+ enables structured TOML parsing. On older Python versions a TOML
coverage gap is reported; other supported analysis remains available. Invalid or
unsafe input exits with code 2 and an error on stderr; a completed source audit
exits with code 0, including when its recommendation is `avoid` or `review`.
The exit code does not approve installation. The offline `audit.py` refuses URLs;
candidate directories are not traversed.

The trusted portable intake wrapper also accepts canonical public GitHub repository,
tree and blob URLs:

```sh
python3 skills/cuarentena/scripts/review.py SOURCE --format json
```

It uses fixed public GitHub endpoints without ambient credentials or proxies, pins
an immutable commit, refuses redirects, verifies source-file blob hashes and passes
bounded in-memory bytes to the same auditor. Its additive `provenance` field records
the selected source and scope. It neither writes candidate files nor activates
content. Web and Telegram use the same canonical source loader and evidence schema.

## Embedding API

```python
from skills.cuarentena.scripts.audit import (
    audit_files, read_input, unpack_source, render_text,
)

# Explicit local source file/ZIP. Loading and analysis never execute candidate code.
files = read_input('/absolute/path/candidate.zip')
report = audit_files(files)
print(render_text(report))

# Trusted, separately authorized fetcher supplies bounded bytes, not remote code.
files = unpack_source(archive_bytes, 'candidate.zip')
report = audit_files(files)
```

`read_package(data, name)` is the same bytes-loader API as `unpack_source`.
`audit_files` accepts a mapping of package-relative string names to bytes. Callers
must preserve the intake checks when constructing that mapping themselves. The
analyzer additionally limits inspected members and bytes and records gaps.

## Evidence and schema (version 1)

The JSON object always includes:

| Key | Contents |
| --- | --- |
| `version` | Integer `1`. |
| `coverage` | `total_files`, `inspected_files`, `total_bytes`, and `omitted` entries with `path` and `reason`. Inspected files may have partial coverage; consult omissions. |
| `inventory` | `path`, `bytes`, SHA-256 `sha256`, `kind`, `lines` (null for opaque/uninspected text). Line counts cover inspected text only. |
| `capabilities` | `category`, `label`, and `evidence` containing `path`, one-based `line`, resolved/declaration `symbol`, and `detail`. |
| `dependencies` | `name`, declared `version`, `ecosystem`, manifest `path`, one-based `line`, and `pinned`. |
| `entrypoints` | `path`, one-based `line`, `name`, and `kind` for main guards, manifests, executable declarations, and shebangs. |
| `endpoints` | Hostname-only `host`, source `path`, one-based `line`, and provenance `kind`. No URL is visited. |
| `licenses` | `path` and recognized declared `identifier`. Not a legal compatibility opinion. |
| `indicators` | Stable `code`, `category`, `severity`, `path`, one-based `line`, and bounded `detail`. A finding is a review lead, not a malware diagnosis. |
| `recommendation` | `verdict` (`candidate`, `review`, or `avoid`) and factual `reasons`. Source-review priority only. |
| `checks` | `name`, `status`, and `detail`, distinguishing parsed/completed analysis from `not_run` checks and limited scope. |

Example finding, based on a real syntax pattern rather than executing the source:

```json
{
  "code": "credential_network_combination",
  "category": "credentials",
  "severity": "high",
  "path": "uploader.py",
  "line": 5,
  "detail": "Credential-source variable is passed to a network call; verify destination and intended authorization."
}
```

Python imports and aliases are resolved syntactically for common standard-library,
HTTP-client, subprocess, filesystem, environment, and dynamic-code operations.
Call arguments make file targets, process arguments, and CLI declarations visible.
A credential-source variable passed directly to a network call is distinguished
from mere same-file co-occurrence. This is local syntactic tracking, **not** a
whole-program dataflow or reachability proof. Comments and standalone docstrings
do not become executed Python findings.

Structured inspection covers npm dependencies/scripts/lifecycle hooks/bins/engines,
pip requirements, Python project/build manifests and Poetry/Cargo declarations.
Exact numeric declarations are marked pinned; ranges, wildcards and URL references
are not. No transitive resolution or advisory lookup occurs. Shell, JavaScript,
TypeScript, Markdown, Docker and configuration findings are explicitly **textual**
or **declared**: they are not parser-confirmed runtime behavior. Markdown reports
skill purpose and requested tools/permissions, instruction-override text, and
network/execution/credential leads. License detection reports declarations only.

`avoid` means a high-priority source pattern was found, such as passing credential
sources to networking or piping downloads to an interpreter. `review` means other
indicators or coverage gaps remain. `candidate` means no configured indicators
matched inspected content: **not safe, malware-free, approved, or installable**.
Dynamic execution, antivirus scanning and vulnerability-database checks are
explicitly `not_run`, never fabricated. The legacy exact-hash promotion policy
remains separate and is not widened by this report.

## Bounds and output handling

- Input: 5 MiB; ZIP expanded total: 8 MiB; member/source file: 512 KiB.
- ZIP: at most 128 members, decompression ratio at most 100:1.
- Text analysis: at most 128 KiB/file, with explicit truncation/parse gaps.
- Reject ZIP traversal, absolute/Windows paths, backslashes, control/bidi paths,
  duplicate member names, links, special files, encrypted members and nested
  archives. ZIPs are read in bounded memory, never extracted to disk.
- Non-regular local inputs and symlinks are refused. Candidate modules, shell
  scripts and package lifecycle hooks are never run.
- Output lists are bounded to 160 results; capability evidence to 8 locations;
  source-derived fields to 600 characters. Endpoint extraction is bounded to 16
  matches per source line. Coverage records result/evidence truncation.
- Known credential prefixes, credential assignment literals, URL userinfo/secret
  query parameters, private-key blocks and control/bidi characters are redacted
  before source-derived fields are clipped. Heuristic redaction cannot identify
  every possible secret encoding; do not publish a report without human review.
- Text rendering abbreviates large lists and directs readers to JSON. Reports
  remain untrusted source-derived data, never instructions to the reviewing agent.
