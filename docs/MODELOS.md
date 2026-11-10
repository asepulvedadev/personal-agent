# Selector de modelo y esfuerzo

El botón situado abajo a la izquierda del chat sustituye al texto «Hermes · Espacio aislado». Muestra el modelo actual y su esfuerzo. Al pulsarlo se abre un selector en español.

## Funcionamiento

- La lista cruza el inventario de tu clave de OpenAI con un catálogo de modelos compatibles con Responses, herramientas y transmisión de texto. Actualmente aparecen **18 modelos** en esta cuenta.
- Solo aparecen los esfuerzos documentados para cada modelo: sin razonamiento, bajo, medio, alto, muy alto o máximo, cuando corresponda. Los modelos sin razonamiento muestran «No aplica».
- Los nombres de modelo son los identificadores reales de la API. Los modelos anteriores se marcan como tales. Las versiones fechadas no se repiten en la lista; se usan los alias estables.
- GPT-5.5 Pro no se ofrece: su ficha no anuncia compatibilidad con la transmisión requerida por esta integración. Otros modelos de audio, imágenes o embeddings tampoco pertenecen a este selector de chat.
- Aplicar guarda la preferencia en la configuración cifrada. No modifica la clave API, no borra el historial y no necesita reiniciar Hermes.
- No se puede cambiar durante una respuesta. El selector también se desactiva mientras se prepara un archivo o se dicta audio.

Un esfuerzo mayor puede consumir más y tardar más. La lista de modelos de la API no garantiza saldo, cuota o disponibilidad de generación en todo momento. El inventario se conserva cinco minutos en memoria y se invalida al cambiar la clave.

## Aplicación real en Hermes

Cada mensaje incluye un bloqueo nativo confirmado (`require_model_lock`) y la selección de modelo y esfuerzo. Así se evita que un modelo antiguo guardado en la sesión prevalezca sobre la nueva elección.

El intermediario de confianza fuerza el modelo y esfuerzo elegidos por el propietario en cada solicitud; un agente o una llamada auxiliar no puede elegir otro. Esto también evita que heurísticas antiguas de Hermes reduzcan «máximo» a otro nivel. Para modelos sin razonamiento se elimina el parámetro, en lugar de enviar un valor incompatible.

La selección se fija durante toda la respuesta, incluidos sus pasos con herramientas. Las llamadas auxiliares utilizan la misma selección. La revisión de ejecución solo cambia con la configuración que requiere reinicio, como claves o variables; elegir un modelo desde el chat no la modifica.

## Verificación

- 43 pruebas de Python y dos de JavaScript aprobadas, con una advertencia de dependencia no bloqueante.
- Revisión independiente aprobada. Dos pruebas adicionales de concurrencia quedaron incorporadas: respuesta iniciada durante la consulta de modelos y rotación de clave durante esa consulta.
- Consulta real de la API: 18 modelos compatibles y esfuerzos de GPT-6.1 Sol `low`, `medium`, `high`, `xhigh`, `max`.
- Interfaz real: cambio a GPT-6 Luna / Bajo; GPT-4.1 desactivó el esfuerzo con «No aplica» sin aplicar esa elección; restauración a GPT-6.1 Sol / Medio.
- Dos respuestas reales en la misma sesión: Luna / Bajo recordó `PRUEBA-8462`; después Sol / Medio recordó el mismo código. Ambos eventos finales confirmaron el modelo solicitado y el bloqueo nativo.
- El PID y la hora de inicio del proceso de Hermes permanecieron iguales tras cambiar el modelo y esfuerzo. Se conservó el historial.

## Fuentes del catálogo

Capacidades y esfuerzos comprobados el 10 de noviembre de 2026 en las fichas oficiales individuales de [OpenAI Models](https://developers.openai.com/api/docs/models), además de la [guía de GPT-6](https://developers.openai.com/api/docs/guides/latest-model). El catálogo verificado vive en `app/models.py`; no se infieren esfuerzos nuevos a partir del nombre de un modelo desconocido.

Los valores medios usados por la aplicación en GPT-6 Astra y GPT-5.2 Pro son una política explícita de la interfaz, no una afirmación sobre un valor predeterminado que sus fichas individuales no documentan. La preferencia previa de una instalación se conserva cuando era compatible.
