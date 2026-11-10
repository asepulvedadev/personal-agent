# Focused replies for Hermes

`focused-replies` is an independently authored, conversation-scoped output-style skill for Hermes Web and Carpediev. It provides direct answers, short actionable sequences, meaningful progress, and fewer distractions. It changes neither identity nor permissions and does not infer a diagnosis.

## Use

- **Web:** select `focused-replies` in the existing skill picker, or include `$focused-replies` in your message.
- **Telegram:** invoke `/focused-replies` in a fresh conversation, or ask Carpediev to use the `focused-replies` skill.
- Ask for `normal mode`, `stop focus mode`, or `stop adhd mode` to return to ordinary formatting. Select the skill again in a new conversation; there is no always-on hook.
- Request a full explanation whenever needed. Brevity must not remove material facts, necessary caveats, or required consent.

The existing image-owned skill seeder delivers the same source to each profile's own `skills/project/focused-replies/SKILL.md`. It never replaces later owner edits. The skill contains one Markdown file and no plugin, installer, dependency, executable helper, or external resource. Existing SOUL, configuration, approved-import manifests, and shared quarantine are unchanged.

## Source and REA evidence

The owner requested a similar adaptation after reviewing [ayghri/i-have-adhd](https://github.com/ayghri/i-have-adhd). Its documentation also describes a native [Hermes skill route](https://github.com/ayghri/i-have-adhd/blob/main/INSTALL.md). This implementation re-expresses the general communication ideas; it does not install or patch the upstream plugin.

The retained original Markdown had SHA-256 `4a3e5f5fd8f671dcf9d920c376c2e14b8da4d3c48ff1a3128edc78cdfc6874fa`. REA inspected an **analyst-created ZIP containing only those unchanged bytes**, not the complete upstream repository. Carrier SHA-256: `f5019af7313776584e27cfef8bc1f37331df844aded04772027c5fe87e19872a`. Its static inventory identified one 7,200-byte Markdown member, non-executable and unencrypted, with verified integrity and no inventory contradictions.

REA inventory is structural evidence, not semantic review, a malware-free certificate, or proof of runtime behavior. No upstream instructions, hooks, installers, or examples were executed. The upstream quarantine item stays blocked; no approval record or scanner exception is created. The new skill follows the owner's separate authorization to build a similar response-formatting capability.

## Verification

`tests/test_focused_replies.py` checks the public quarantine creation interface accepts the standalone new file without findings or approval, and that normal seeding reaches both profiles while preserving identity, configuration, approval state, and later owner edits. Structural tests do not prove model behavior; validate discovery and a real reply separately after deployment.
