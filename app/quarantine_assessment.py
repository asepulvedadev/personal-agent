"""Human-facing static assessment. Candidate claims never reach the capable agent."""
import re
import unicodedata


def import_failure(status):
    """Only fixed local explanations cross the chat/broker boundary, never exceptions."""
    reason = {
        504: 'Se agotó el tiempo de descarga de GitHub. Puedes volver a intentarlo más tarde.',
        413: 'La fuente supera el tamaño permitido. Adjunta una skill o un ZIP original de hasta 5 MiB.',
    }.get(status, 'Comprueba que sea un enlace HTTPS público y canónico de GitHub, sin parámetros ni redirecciones, y compatible con los límites de cuarentena.')
    text = ('Revisión compartida · Hermes Web y Carpediev\n'
            'No se pudo completar la revisión del enlace de GitHub.\n' + reason + '\n'
            'Esta solicitud no creó una referencia ni instaló o ejecutó contenido. '
            'Escribe «cuarentena» para consultar las referencias de solicitudes anteriores.')
    return {'version':1, 'scope':'shared', 'text':text}


def claim(value):
    value = ''.join(c for c in str(value) if not unicodedata.category(c).startswith('C'))
    value = re.sub(r'https?://\S+', '[enlace omitido]', value, flags=re.I)
    return ' '.join(value.split())[:260]


def report(item):
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
