"""Large repository intake keeps the skill, not an unrelated website archive."""
import asyncio
import hashlib
import io
import json
import stat
import zipfile

import pytest
from test_portal import portal, login
from test_quarantine import SAFE
from skills.cuarentena.scripts import source


def repository(entries):
    output = io.BytesIO()
    with zipfile.ZipFile(output, 'w', zipfile.ZIP_STORED) as archive:
        for path, data in entries:
            archive.writestr('repo-commit/' + path, data)
    return output.getvalue()


def fetcher(data):
    calls = []
    async def fetch(url, limit):
        calls.append((url, limit))
        if '/commits/' in url:
            return b'a' * 40
        if 'codeload.github.com' not in url:
            return b'{"private":false,"default_branch":"main"}'
        if len(data) > limit:
            raise ValueError('source_size_limit')
        return data
    return fetch, calls


def large_skill():
    return repository([
        ('site/large-image.jpg', b'x' * (5 * 1024**2)),
        ('package.json', b'{"scripts":{"postinstall":"node skills/demo/scripts/run.mjs"}}'),
        ('skills/demo/SKILL.md', SAFE),
        ('skills/demo/scripts/run.mjs', b'fetch("https://example.test/collect");'),
        ('skills/demo/references/preview.jpg', b'not executable image fixture'),
        *[(f'skills/demo/references/ref-{n}.md', f'Reference {n}.'.encode()) for n in range(137)],
    ])


def test_large_repository_selects_complete_skill_source_instead_of_failing():
    original = large_skill()
    fetch, calls = fetcher(original)
    data, provenance = asyncio.run(source.load_github('https://github.com/owner/repo', fetch))
    from skills.cuarentena.scripts.audit import read_package, audit_files
    files = read_package(data, 'github.zip')
    assert len(files) == 140
    assert any(p.endswith('scripts/run.mjs') for p in files)
    assert not any('/site/' in p or p.endswith('.jpg') for p in files)
    assert provenance['selection']['roots'] == ['skills/demo']
    assert provenance['selection']['omitted_files'] == 1
    assert provenance['selection']['excluded_repository_files'] == 1
    assert provenance['upstream_sha256'] == hashlib.sha256(original).hexdigest()
    assert len(audit_files(files)['inventory']) == 140
    assert calls[-1][1] > 5 * 1024**2


def test_original_small_repository_download_is_unchanged():
    original = repository([('README.md', b'Hello source.')])
    fetch, calls = fetcher(original)
    data, provenance = asyncio.run(source.load_github('https://github.com/owner/repo', fetch))
    assert data == original and 'selection' not in provenance
    assert len(calls) == 3


def test_source_member_is_inspected_beyond_previous_text_cutoff():
    from skills.cuarentena.scripts.audit import audit_files
    content = b'// source context\n' * 12000 + b'\nchild_process.exec("node worker.js");\n'
    result = audit_files({'scripts/large.mjs':content})
    assert any(e['symbol'].endswith(':textual') and e['line']>12000
               for c in result['capabilities'] if c['category']=='process' for e in c['evidence'])
    assert not any('Text truncated' in x['reason'] for x in result['coverage']['omitted'])


def test_explicit_tree_does_not_include_sibling_skills():
    original = repository([('skills/a/SKILL.md', SAFE), ('skills/b/SKILL.md', SAFE),
                           ('site/large.jpg', b'x' * (5 * 1024**2))])
    fetch, _ = fetcher(original)
    data, provenance = asyncio.run(source.load_github('https://github.com/owner/repo/tree/main/skills/a', fetch))
    with zipfile.ZipFile(io.BytesIO(data)) as package:
        assert package.namelist() == ['r/skills/a/SKILL.md']
    assert provenance['path'] == 'skills/a'


def test_small_transport_with_oversized_unrelated_member_also_selects_source():
    original = repository([('skills/demo/SKILL.md',SAFE), ('site/data.txt',b'x'*600000)])
    fetch, calls = fetcher(original)
    data, provenance = asyncio.run(source.load_github('https://github.com/owner/repo',fetch))
    assert provenance['selection']['received_files']==1
    assert provenance['selection']['excluded_repository_files']==1
    assert len(calls)==3
    with zipfile.ZipFile(io.BytesIO(data)) as package:
        assert package.namelist()==['r/skills/demo/SKILL.md']


def test_retained_package_budgets_filename_and_zip_header_bytes():
    original = repository([('skills/demo/SKILL.md',SAFE), *[
        ('skills/demo/references/'+'x'*180+f'-{n:04}.md',b'x'*10000) for n in range(511)]])
    data, selection = source.select_archive(original,'')
    assert len(data)<=source.SOURCE_BYTES
    assert 0<selection['received_files']<512 and selection['omitted_files']>0
    from skills.cuarentena.scripts.audit import read_package
    assert len(read_package(data,'github.zip'))==selection['received_files']


def test_many_skill_roots_do_not_block_the_acquisition_deadline():
    import os
    import subprocess
    import sys
    program = '''import io,zipfile
from skills.cuarentena.scripts.source import select_archive
data=io.BytesIO()
with zipfile.ZipFile(data,'w') as package:
    for n in range(20000):
        package.writestr(f'repo/skills/s{n:05}/SKILL.md',b'# Source only')
result,coverage=select_archive(data.getvalue(),'')
assert coverage['scoped_files']==20000 and coverage['received_files']==512
assert coverage['roots_total']==20000
'''
    result = subprocess.run([sys.executable,'-B','-c',program],capture_output=True,text=True,
                            timeout=10,env={**os.environ,'PYTHONDONTWRITEBYTECODE':'1'})
    assert result.returncode==0, result.stderr


@pytest.mark.parametrize('path,mode', [('skills/demo/../bad.py', stat.S_IFREG),
                                    ('skills/demo/link.py', stat.S_IFLNK)])
def test_selected_archive_keeps_path_and_link_guards(path, mode):
    output = io.BytesIO()
    with zipfile.ZipFile(output, 'w') as package:
        item = zipfile.ZipInfo('root/' + path)
        item.external_attr = (mode | 0o644) << 16
        package.writestr(item, b'not executed')
    with pytest.raises(source.SourceError):
        source.select_archive(output.getvalue(), '')


def test_partial_selection_returns_evidence_and_never_enables_promotion(portal, monkeypatch):
    import app.quarantine as quarantine
    fetch, _ = fetcher(large_skill())
    monkeypatch.setattr(quarantine, 'fetch_public', fetch)
    mod, client = portal
    headers = login(client)
    response = client.post('/api/quarantine/github', headers=headers,
                           json={'url':'https://github.com/owner/repo'})
    assert response.status_code == 200
    item = response.json()
    assert len(item['manifest']) == 140 and not item['eligible']
    assert item['audit']['coverage']['acquisition']['omitted_files'] == 1
    assert any(x['code'] == 'selected_github_source' for x in item['findings'])
    result = client.get('/api/quarantine/' + item['id']).json()['report']['text']
    assert 'skills/demo' in result
    assert 'repositorio completo recibido' not in result
    assert 'Selección de fuente' in result and '1 recurso(s)' in result
    assert not list((mod.CONTROL/'quarantine'/item['id']).glob('approved.json'))
