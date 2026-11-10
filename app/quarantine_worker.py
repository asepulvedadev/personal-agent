"""Bounded static inspection of untrusted bytes. Never executes imported content."""
import ast
import re
from pathlib import PurePosixPath

MAX_TEXT = 128 * 1024
SAFE_NAME = re.compile(r'[a-z][a-z0-9-]{1,62}\Z')
PROTECTED = {'auth', 'config', 'security', 'system', 'soul', 'user', 'memory', 'agents',
             'approved-imports', 'default', 'carpediev', 'hermes', 'deploy', 'deployment'}
DANGERS = [
    ('instruction_override', r'ignore\s+(?:all\s+|the\s+)?(?:previous|system)|(?:override|bypass|disable)\s+.{0,35}(?:security|safety|guard|sandbox|approval)|(?:system|developer)\s+prompt'),
    ('credentials', r'(?:reveal|steal|exfiltrat|upload|send|read|print).{0,65}(?:secret|token|credential|password|\.env|private.key)|(?:api[_ -]?key|private[_ -]?key)\s*[:=]'),
    ('execution', r'\b(?:subprocess|os\.system|eval|exec|curl|wget|sudo|chmod|pip install|npm install|bash|powershell|cmd\.exe)\b|rm\s+-[rf]|/etc/|~/.hermes|/workspace|\$\(|```\s*(?:sh|bash|python|js|shell)'),
    ('template', r'\{\{|\{%|<script|javascript:|data:|!\['),
]


def inspect_files(files):
    """Return evidence and a narrow candidate; absence of findings is not safety proof."""
    findings = []
    candidates = []
    metadata = {}

    def finding(code, path, severity='review_required'):
        findings.append({'code': code, 'path': path, 'severity': severity})

    for path, raw in files.items():
        suffix = PurePosixPath(path).suffix.lower()
        if PurePosixPath(path).name.lower() == '.skillignore':
            finding('package_ignore_rules_not_honored', path)
        if suffix not in {'.md', '.txt'}:
            finding('executable_or_unsupported_resource', path)
            if suffix == '.py' and len(raw) <= MAX_TEXT:
                try:
                    tree = ast.parse(raw.decode('utf-8'))
                    if any(isinstance(node, (ast.Import, ast.ImportFrom, ast.Call)) for node in ast.walk(tree)):
                        finding('python_execution_constructs', path)
                except (UnicodeError, SyntaxError, ValueError, RecursionError):
                    finding('unparseable_source', path)
            continue
        if len(raw) > MAX_TEXT:
            finding('text_inspection_limit', path)
            continue
        try:
            text = raw.decode('utf-8')
        except UnicodeError:
            finding('invalid_utf8', path)
            continue
        if any(ord(char) < 32 and char not in '\n\r\t' for char in text):
            finding('binary_or_control_content', path)
            continue
        for code, pattern in DANGERS:
            if re.search(pattern, text, re.IGNORECASE):
                finding(code, path, 'blocked')
        if re.search(r'\]\((?!https://)[^)]+\)', text):
            finding('non_self_contained_reference', path)
        # Even HTTPS links and shell snippets need further human investigation.
        if re.search(r'https?://|`[^`]+`|```', text):
            finding('external_or_code_reference', path)
        if PurePosixPath(path).name != 'SKILL.md':
            finding('additional_resource', path)
            continue
        candidates.append(path)
        match = re.match(r'\A---\r?\n(.*?)\r?\n---\r?\n(.+)\Z', text, re.DOTALL)
        if not match:
            finding('invalid_frontmatter', path)
            continue
        # Deliberately small scalar grammar, not a YAML interpreter. Other YAML is review-only.
        values = {}
        for line in match[1].splitlines():
            scalar = re.fullmatch(r'(name|description): ([^\r\n]+)', line)
            if not scalar or scalar[1] in values:
                finding('unsupported_frontmatter', path)
                continue
            value = scalar[2].strip()
            if value[:1] in {'"', "'"} and value[-1:] == value[:1]:
                value = value[1:-1]
            if not value or any(char in value for char in '{}[]&*!|>'):
                finding('unsupported_frontmatter', path)
            values[scalar[1]] = value
        name = values.get('name', '')
        description = values.get('description', '')
        if not SAFE_NAME.fullmatch(name) or not 10 <= len(description) <= 500:
            finding('invalid_skill_metadata', path)
        elif name in PROTECTED or any(part in PROTECTED for part in name.split('-')):
            finding('protected_target', path, 'blocked')
        else:
            metadata[path] = {'name': name, 'description': description}
    if len(files) != 1 or len(candidates) != 1:
        finding('requires_self_contained_single_skill', '')
    return findings, candidates, metadata
