#!/usr/bin/env python3
"""Read-only quarantine entry point for an explicit source file, ZIP or GitHub URL."""
import argparse
import asyncio
import hashlib
import json
from pathlib import PurePosixPath
import sys

# Keep the installed skill read-only even when Python bytecode caching is enabled.
sys.dont_write_bytecode = True

import audit
import source


async def review(value):
    if value.startswith(('https://','http://')):
        data, provenance = await source.load_github(value, source.cli_fetch)
        filename = provenance.get('filename','github.zip')
        files = audit.read_package(data, filename)
        if filename=='github.zip':
            roots={path.split('/')[0] for path in files}
            if len(roots)!=1:
                raise source.SourceError('invalid_github_archive')
            root=next(iter(roots))+'/'
            files={path[len(root):]:raw for path,raw in files.items()}
            selected=provenance['path'].rstrip('/')
            if selected:
                files={path[len(selected)+1:] if path.startswith(selected+'/') else PurePosixPath(path).name:raw
                       for path,raw in files.items() if path==selected or path.startswith(selected+'/')}
        if not files:
            raise source.SourceError('empty_selected_source')
        provenance['source_sha256']=hashlib.sha256(data).hexdigest()
    else:
        files=audit.read_input(value)
        # Do not publish private absolute filesystem paths in the report.
        provenance={'kind':'local','filename':PurePosixPath(value).name}
    provenance={key:audit.clean_text(value) if isinstance(value,str) else value for key,value in provenance.items()}
    result = audit.audit_files(files,provenance)
    if 'selection' in provenance:
        provenance['selection'] = result['coverage']['acquisition']
    return {**result,'provenance':provenance}


def main(argv=None):
    parser=argparse.ArgumentParser(description='Audit skills/tools without executing, installing or activating them. Accepts an explicit local file, ZIP, or canonical public HTTPS GitHub URL; never reuses credentials, follows redirects or writes candidate files.')
    parser.add_argument('source',help='Local source file/ZIP, GitHub repository, tree or blob URL. Large repositories automatically select skill source with explicit coverage. Retained source: 5 MiB input, 8 MiB expanded, 512 members; single remote source files ≤512 KiB.')
    parser.add_argument('--format',choices=('json','text'),default='text')
    args=parser.parse_args(argv)
    try:
        result=asyncio.run(review(args.source))
        print(json.dumps(result,ensure_ascii=True,indent=2) if args.format=='json' else audit.render_text(result))
        return 0
    except source.SourceError as error:
        print('Quarantine intake refused: '+error.code+'. Provide a smaller original source file/ZIP or a canonical public GitHub source.',file=sys.stderr)
        return 2
    except (OSError,ValueError):
        print('Quarantine intake refused: invalid, unavailable or oversized source. No candidate content was executed or installed.',file=sys.stderr)
        return 2


if __name__=='__main__':
    raise SystemExit(main())
