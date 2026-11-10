"""Installed skill metadata and explicit dollar mentions, without reading skill bodies."""
import json
import re
from fastapi import HTTPException

NAME = re.compile(r'[A-Za-z][A-Za-z0-9_./-]{0,99}\Z')
MENTION = re.compile(r'(?<!\S)\$([A-Za-z][A-Za-z0-9_./-]{0,99})(?![A-Za-z0-9_./-])')
MARKER = '\n\nSkills solicitadas por el propietario:\n'


def safe_name(value):
    return isinstance(value,str) and NAME.fullmatch(value) and '..' not in value and not value.lower().startswith(('sk-','ghp_','bearer'))


def metadata(payload):
    rows=payload.get('data') if isinstance(payload,dict) else None
    if not isinstance(rows,list) or len(rows)>1000:
        raise HTTPException(502,'No se pudo leer el catálogo de skills de Hermes.')
    skills={}
    for row in rows:
        if not isinstance(row,dict) or not safe_name(row.get('name')):continue
        name=row['name']
        def text(key,limit):
            value=row.get(key)
            return ' '.join(value.split())[:limit] if isinstance(value,str) else ''
        skills.setdefault(name,{'name':name,'description':text('description',500),'category':text('category',100)})
    return sorted(skills.values(),key=lambda row:row['name'].casefold())


async def catalog(native):
    return metadata(await native('GET','/v1/skills'))


async def requested(message,native):
    names=list(dict.fromkeys(MENTION.findall(message)))
    if not names:return message
    installed={row['name'] for row in await catalog(native)}
    selected=[name for name in names if name in installed]
    if len(selected)>5:raise HTTPException(400,'Selecciona un máximo de cinco skills por mensaje.')
    if not selected:return message
    calls='\n'.join('• skill_view(name='+json.dumps(name,ensure_ascii=False)+')' for name in selected)
    return message+MARKER+'Antes de responder, consulta estas skills instaladas mediante sus nombres exactos:\n'+calls+'\nAplica sus instrucciones pertinentes a la solicitud. No ejecutes acciones adicionales que el propietario no haya pedido.'
