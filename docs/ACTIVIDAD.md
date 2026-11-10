# Actividad visible del agente

Cada respuesta que utiliza herramientas incluye un bloque **Actividad del agente**, abierto de forma predeterminada y plegable si se desea. No se añade un menú permanente ni cambia el compositor de iconos.

## Qué muestra

- El nombre de la herramienta, con una descripción breve en español para las más habituales: terminal, búsqueda web, lectura de archivos, memoria, delegación o consulta de skills.
- El identificador de una skill cuando Hermes llama a `skill_view` con un nombre reconocible. `skills_list` solo se muestra como listado: no significa que se hayan cargado todas las skills disponibles.
- Estados observados: **En curso**, **Finalizada**, **Con error**, **Detenida** o **Sin confirmación**.
- Al reabrir un chat: reconstrucción desde las llamadas y resultados que Hermes ya guarda. **Resultado registrado** significa que existe un resultado, no que la ejecución haya tenido éxito.

No muestra comandos completos, argumentos, archivos de referencia, resultados internos, previews ni razonamiento privado. Los datos de nombres se validan y se insertan como texto, nunca como HTML. Esto limita la visualización; no altera la retención ni la proyección de datos de las APIs nativas existentes.

## Límites de observabilidad

La API de chat de la versión fijada emite `tool.started`, `tool.completed` y `tool.failed`, con nombres y argumentos, pero no siempre identifica cada llamada. Si dos llamadas simultáneas a la misma herramienta no tienen identificadores, la interfaz no asigna arbitrariamente el resultado a una skill concreta: conserva la incertidumbre. Las llamadas sin cierre confirmado tampoco se presentan como correctas.

La API de historial conserva las llamadas y los identificadores, pero omite metadatos explícitos de error. Por eso, una recarga sustituye el estado de ejecución en directo por la evidencia disponible en el historial. Las páginas antiguas pueden comenzar en mitad de una secuencia; si falta la llamada original, solo se muestra el nombre de herramienta que Hermes conservó, sin inventar la skill.

Se conserva como máximo una lista de 200 acciones por bloque; se señala con `+` si se supera. Los eventos de razonamiento y progreso interno no se convierten en actividad visible. Una respuesta sin herramientas no genera una actividad ficticia.

## Comprobaciones locales

- 51 pruebas de Python y nueve de JavaScript aprobadas; una advertencia de dependencia no bloqueante.
- Pruebas de ambas formas de eventos, consulta frente a listado de skills, privacidad del contenido visible, historial, llamadas simultáneas, cancelación/desconexión y DOM de solo texto.
- Revisión independiente de estos límites y de las regresiones de historial y audio: aprobada.

## Verificación desplegada

Se recreó únicamente el servicio web, sin reiniciar Hermes. Los 16 contenedores ajenos al proyecto conservaron identidad, imagen, inicio y estado. Una prueba real en la UI creó una conversación nueva y pidió únicamente `skills_list` y `skill_view`, sin comandos ni cambios de archivos. La respuesta fue `ACTIVIDAD_OK`; el bloque visible mostró **Listar skills** y **Skill · popular-web-designs**. Las dos acciones se reconstruyeron desde el historial nativo.
