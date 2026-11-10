"""Verified Responses/streaming catalog; intersected with the owner's live API inventory.

Effort vocabularies: official individual OpenAI model pages, checked 2026-11-10.
GPT-5.5 Pro is omitted because streaming is not advertised on its model page.
"""
FULL=('none','low','medium','high','xhigh','max')
THINKING=('low','medium','high','xhigh','max')
STANDARD=('none','low','medium','high','xhigh')
CATALOG={
    'gpt-6.1-sol':(THINKING,'medium',False),
    'gpt-6.1-terra':(FULL,'medium',False),
    'gpt-6-astra':(THINKING,'medium',False),
    'gpt-6-sol':(FULL,'medium',False),
    'gpt-6-luna':(FULL,'medium',False),
    'gpt-5.6-sol':(FULL,'medium',False),
    'gpt-5.6-terra':(FULL,'medium',False),
    'gpt-5.6-luna':(FULL,'medium',False),
    'gpt-5.5':(STANDARD,'medium',False),
    'gpt-5.4':(STANDARD,'none',False),
    'gpt-5.4-mini':(STANDARD,'none',False),
    'gpt-5.4-pro':(('medium','high','xhigh'),'medium',False),
    'gpt-5.2':(STANDARD,'none',False),
    'gpt-5.2-pro':(('medium','high','xhigh'),'medium',False),
    'gpt-5.1':(('none','low','medium','high'),'none',True),
    'gpt-5.4-nano':(STANDARD,'none',True),
    'gpt-4.1':((),None,True),
    'gpt-4.1-mini':((),None,True),
}


def selection(vault):
    model=vault['model']
    efforts,default,_=CATALOG.get(model,((),None,False))
    # Existing installations used Hermes' medium default; preserve it on first upgrade.
    effort=vault.get('reasoning_effort','medium' if 'medium' in efforts else default)
    return {'model':model,'reasoning_effort':effort}


def enforce_selection(payload,path,selected):
    # The relay, not agent/provider heuristics, is authoritative for model and effort.
    payload['model']=selected['model']
    effort=selected['reasoning_effort']
    if path=='responses':
        payload.pop('reasoning_effort',None)
        if effort is None:payload.pop('reasoning',None)
        else:
            reasoning=payload.get('reasoning')
            payload['reasoning']={**(reasoning if isinstance(reasoning,dict) else {}),'effort':effort}
    else:
        payload.pop('reasoning',None)
        if effort is None:payload.pop('reasoning_effort',None)
        else:payload['reasoning_effort']=effort
