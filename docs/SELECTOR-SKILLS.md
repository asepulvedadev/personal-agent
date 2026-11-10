# Selector de skills con $

Escribe **$** en el cuadro del chat para consultar las skills instaladas y disponibles en el Hermes de este espacio. Continúa escribiendo para filtrar por nombre, descripción o categoría.

- **↑ / ↓**: recorrer los resultados.
- **Enter / Tab**: insertar la skill seleccionada, sin enviar el mensaje.
- **Clic**: insertar una skill.
- **Esc**: cerrar el selector conservando el borrador.
- **Shift + Enter**: insertar una línea nueva, sin elegir una skill ni enviar.

La selección inserta `$nombre-de-la-skill` en el texto y conserva el contenido a ambos lados del cursor. Seleccionar una skill no la ejecuta ni inicia una respuesta. Al enviar el mensaje, el servidor comprueba los nombres contra el catálogo real y solicita a Hermes que consulte esas skills con `skill_view` antes de responder. La consulta efectiva aparece después en la actividad del agente; no se presenta la selección como ejecución confirmada.

Se admiten hasta cinco skills distintas por mensaje. Los precios como `$500` o `US$100` no activan la selección de skills ni modifican el mensaje. Las menciones que solo están en documentos adjuntos no se tratan como una selección del propietario. Los nombres desconocidos se conservan como texto, sin inventar una skill instalada.

## Catálogo y privacidad

El catálogo proviene de la API nativa autenticada de Hermes y respeta su resolución de skills disponibles, incluidos los filtros de desactivación y entorno. El navegador recibe únicamente nombre, descripción y categoría, no el contenido de `SKILL.md`, rutas internas ni credenciales. El selector muestra nombres canónicos y categorías en español, y conserva el foco en el cuadro de texto. El catálogo del navegador se actualiza al cabo de un minuto.

Se corrigió un fallo de la versión fijada de Hermes: `GET /v1/skills` devolvía HTTP500 porque pasaba `include_editorial=True` a una función que ya no acepta ese argumento. La corrección elimina únicamente ese argumento y exige coincidencia exacta durante la construcción de la imagen. No se amplían montajes, permisos ni acceso a otros proyectos.

## Verificación

El código del despliegue se preparó a partir del código actual del VPS en una copia privada aislada, para no publicar los cambios de cuarentena/Telegram que otra tarea estaba desarrollando en el mismo directorio. Pasaron 57 pruebas de Python y 16 de JavaScript; una advertencia de dependencia no bloqueante. Revisión independiente de autenticación, metadatos, expansión de menciones, teclado, borradores y carreras asíncronas aprobada.

El catálogo nativo pasó de HTTP500 a HTTP200 y devuelve 39 skills en este despliegue. Se comprobaron en el navegador el filtrado, clic, Enter, Tab, Esc y los precios. Una regresión detectada al repetir el mismo prefijo después de Esc se reprodujo con una prueba RED y se corrigió; la prueba y la revisión independiente acotada pasaron. No se desplegaron los cambios ajenos de cuarentena, Telegram o CI/CD.

La prueba real seleccionó `popular-web-designs`, la insertó con Tab sin enviar y, tras el envío explícito, Hermes hizo una única consulta de la skill y respondió `SKILL_SELECTOR_OK`. El historial visible conserva el mensaje original con `$nombre` y no muestra la instrucción interna añadida por el servidor. Se guardó una captura del selector filtrado por `$web`.
