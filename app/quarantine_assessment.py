"""Human-facing static assessment. Candidate claims never reach the capable agent."""
import re
import unicodedata
from skills.cuarentena.scripts.audit import clean_text


def import_failure(status):
    """Only fixed local explanations cross the chat/broker boundary, never exceptions."""
    reason = {
        504: 'Se agotó el tiempo de descarga de GitHub. Puedes volver a intentarlo más tarde.',
        413: 'La fuente supera los límites de revisión: 5 MiB por entrada y 512 KiB por archivo. Adjunta una fuente más pequeña o selecciona un archivo concreto de GitHub.',
    }.get(status, 'Comprueba que sea un enlace HTTPS público y canónico de GitHub, sin parámetros ni redirecciones, y compatible con los límites de cuarentena.')
    text = ('Revisión compartida · Hermes Web y Carpediev\n'
            'No se pudo completar la revisión del enlace de GitHub.\n' + reason + '\n'
            'Esta solicitud no creó una referencia ni instaló o ejecutó contenido. '
            'Escribe «cuarentena» para consultar las referencias de solicitudes anteriores.')
    return {'version':1, 'scope':'shared', 'text':text}


def claim(value):
    value = ''.join(c for c in clean_text(value) if not unicodedata.category(c).startswith('C'))
    value = re.sub(r'https?://\S+', '[enlace omitido]', value, flags=re.I)
    return ' '.join(value.split())[:260]


def _legacy_report(item):
    approved = item['state'] == 'approved'
    blocked = item['state'] == 'blocked'
    eligible = item.get('eligible') and not blocked
    metadata = list(item.get('metadata', {}).values())[:3]
    purposes = [claim(entry['description']) for entry in metadata]
    purpose = '; '.join('«' + text + '»' for text in purposes) or 'El formato no permite identificar un propósito declarado fiable.'
    usable = ('Aprobación registrada para su perfil original; esta revisión no comprueba la entrega al agente.' if approved else
              'Bloqueada: no debe activarse.' if blocked else
              'Formato compatible con aprobación humana; esta revisión no la activa.' if eligible else
              'No es utilizable directamente: requiere revisión o adaptación independiente.')
    benefit = ('Podría aportar un procedimiento reutilizable para la capacidad declarada y reducir trabajo repetitivo. '
               'Es un beneficio potencial, no comprobado ejecutando el paquete.' if purposes else
               'No puedo atribuir beneficios concretos sin identificar antes su capacidad y revisar sus recursos.')
    declared=' '.join(purposes).lower()
    examples=[(r'product|copy|redact|writ','Podría dar consistencia a borradores y descripciones basados en los datos que aportes.'),
              (r'github|repository|repositorio|pull request','Podría ordenar tareas de repositorios, incidencias y revisiones de cambios.'),
              (r'research|investig|search','Podría estructurar búsquedas, contrastar fuentes y organizar resultados.'),
              (r'design|diseñ|frontend','Podría aportar criterios reutilizables para revisar interfaces y mantener consistencia visual.')]
    for pattern,example in examples:
        if re.search(pattern,declared):
            benefit=example+' Depende de comprobar que el contenido realmente cumple lo que declara.'
            break
    findings = item.get('findings', [])
    resources = len(item.get('manifest', []))
    text = (f"Revisión compartida · Hermes Web y Carpediev\nReferencia: {item['id']}\n"
            f"Qué dice que hace (declaración no confiable): {purpose}\n"
            f"¿Se puede usar?: {usable}\nBeneficios potenciales: {benefit}\n"
            f"Verificación: {resources} archivo(s); {len(findings)} hallazgo(s). "
            "Se comprobaron estructura, límites, integridad y patrones de riesgo; no se ejecutó contenido.\n")
    if findings:
        codes = sorted({f['code'] for f in findings})[:8]
        text += 'Hallazgos: ' + ', '.join(codes) + '.\n'
    text += ('Esta revisión no instala ni ejecuta contenido. La evidencia se conserva en cuarentena. Analizar no equivale a instalar ni aprobar. '
             'El análisis estático no demuestra ausencia de malware. '
             'Puedes escribir «cuarentena» o «ver skill ' + item['id'] + '» desde cualquiera de los dos chats.')
    return {'version':1, 'scope':'shared', 'text':text}


def _evidence_lines(item):
    audit = item['audit']
    coverage = audit['coverage']
    recommendation = audit['recommendation']
    verdict = {'avoid':'No activar: revisar los indicadores de alto riesgo.',
               'review':'Requiere revisión antes de adoptar.',
               'candidate':'Candidato para una prueba controlada; no es una aprobación.'}
    lines = ['Dictamen: '+verdict.get(recommendation['verdict'], 'Revisión requerida.')]
    lines.extend('Motivo: '+claim(reason) for reason in recommendation['reasons'])
    origin = item.get('provenance', {})
    if origin.get('repository'):
        lines.append('Origen: '+claim(origin['repository'])+' · commit '+claim(origin.get('commit','')))
        lines.append('Alcance: '+claim(origin.get('path') or 'repositorio completo recibido'))
    lines.append(f"Cobertura: {coverage['inspected_files']}/{coverage['total_files']} archivos inspeccionados; {coverage['total_bytes']} bytes recibidos.")
    for purpose in list(item.get('metadata', {}).values())[:3]:
        lines.append('Declara (declaración no confiable): '+claim(purpose['description']))
    lines.append('Indicadores y evidencia:')
    if not audit['indicators']:
        lines.append('Sin indicadores detectados por los controles ejecutados; consulta la cobertura y las pruebas pendientes.')
    for finding in audit['indicators']:
        lines.append(f"- {claim(finding['severity'])} · {claim(finding['path'])}:{finding['line']} · {claim(finding['code'])}: {claim(finding['detail'])}")
    lines.append('Qué hace / Beneficios potenciales según la fuente:')
    if not audit['capabilities']:
        lines.append('No se identificó una operación ejecutable; el material puede contener instrucciones o documentación.')
    for capability in audit['capabilities']:
        labels = {'network':'Operaciones o referencias de red', 'credentials':'Acceso o referencias a credenciales/entorno',
                  'process':'Creación de procesos o comandos', 'filesystem_write':'Modificación de archivos',
                  'filesystem_read':'Lectura de archivos', 'dynamic_code':'Evaluación dinámica de código',
                  'declared_purpose':'Propósito declarado por el paquete', 'permissions':'Permisos declarados',
                  'lifecycle':'Scripts del ciclo de instalación', 'build_backend':'Sistema de construcción declarado'}
        label = labels.get(capability['category'], capability['label'])
        lines.append('- '+claim(label))
        for evidence in capability['evidence']:
            lines.append(f"  {claim(evidence['path'])}:{evidence['line']} · {claim(evidence['symbol'])} · {claim(evidence['detail'])}")
    if audit['entrypoints']:
        lines.append('Puntos de entrada:')
        for entry in audit['entrypoints']:
            lines.append(f"- {claim(entry['path'])}:{entry['line']} · {claim(entry['kind'])} {claim(entry['name'])}")
    lines.append('Dependencias y versiones:')
    if not audit['dependencies']:
        lines.append('No se encontraron dependencias declaradas en los formatos inspeccionados.')
    for dependency in audit['dependencies']:
        pin = 'fijada' if dependency['pinned'] else 'no fijada'
        lines.append(f"- {claim(dependency['name'])} {claim(dependency['version'])} ({claim(dependency['ecosystem'])}, {pin}) · {claim(dependency['path'])}:{dependency['line']}")
    if audit['endpoints']:
        lines.append('Destinos externos encontrados (no contactados):')
        for endpoint in audit['endpoints']:
            lines.append(f"- {claim(endpoint['host'])} · {claim(endpoint['path'])}:{endpoint['line']} · {claim(endpoint['kind'])}")
    if audit['licenses']:
        lines.append('Licencias declaradas:')
        lines.extend('- '+claim(license['identifier'])+' · '+claim(license['path']) for license in audit['licenses'])
    lines.append('Inventario y huellas:')
    for resource in audit['inventory']:
        lines.append(f"- {claim(resource['path'])}: {resource['bytes']} bytes, {claim(resource['kind'])}, SHA-256 {resource['sha256'][:16]}")
    for omitted in coverage['omitted']:
        lines.append('No inspeccionado: '+claim(omitted['path'])+' · '+claim(omitted['reason']))
    lines.append('Controles y pruebas:')
    for check in audit['checks']:
        status={'completed':'completado','not_run':'no realizado','limited':'alcance limitado'}.get(check['status'],check['status'])
        lines.append(f"- {claim(check['name'])}: {claim(status)} · {claim(check['detail'])}")
    if item['state']=='approved':
        lines.append('Aprobación registrada para su perfil original; este análisis no comprueba su entrega.')
    elif item.get('eligible'):
        lines.append('Formato compatible con revisión humana. No se concedió aprobación.')
    else:
        codes=', '.join(sorted({finding['code'] for finding in item.get('findings', [])}))
        status = 'Bloqueada' if item['state']=='blocked' else 'Estado: requiere revisión'
        lines.append(status+' para activación por la política de formato: '+claim(codes))
    return lines


def report(item, page=1):
    """Paginate every bounded evidence row; Telegram and Web receive identical text."""
    if not item.get('audit'):
        return _legacy_report(item)
    pages, current = [], []
    length = 0
    for line in _evidence_lines(item):
        # Rows are data, never Markdown/HTML interpreted by either chat transport.
        line = line[:700]
        if length + len(line) + 1 > 2900 and current:
            pages.append('\n'.join(current))
            current, length = [], 0
        current.append(line)
        length += len(line) + 1
    if current:
        pages.append('\n'.join(current))
    page = page if isinstance(page, int) and not isinstance(page, bool) else 1
    prefix = f"Revisión compartida · Hermes Web y Carpediev\nReferencia: {item['id']} · Cuarentena de fuente · Página {page}/{len(pages)}\n"
    content = pages[page-1] if 1 <= page <= len(pages) else f'Página no disponible. Consulta una página entre 1 y {len(pages)}.'
    footer = '\nNo se ejecutó ni instaló el paquete.'
    if page < len(pages):
        footer += f"\nMás evidencia: $cuarentena informe {item['id']} {page+1}"
    return {'version':1, 'scope':'shared', 'text':prefix+content+footer}
