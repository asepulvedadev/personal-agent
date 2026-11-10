# Conversaciones guardadas e iconos

Las conversaciones se guardan automáticamente en la base de datos privada de Hermes. El historial no depende del navegador: el almacenamiento local solo recuerda cuál estaba abierta.

## Uso

- **+**: inicia una conversación. Si hay un borrador sin enviar, pide confirmación antes de descartarlo.
- **Reloj**: abre el historial en un panel temporal. Selecciona el título para retomar un chat; la conversación actual queda identificada.
- **Flecha hacia abajo**: carga más conversaciones. La doble flecha superior carga mensajes anteriores de una conversación larga.
- **Papelera de una fila**: elimina esa conversación y sus mensajes del historial activo, después de confirmar.
- **Papelera de la cabecera del historial**: elimina todo el historial del portal. Exige escribir `ELIMINAR TODO` y confirmar.

Todos los botones de la interfaz usan iconos con nombres accesibles, ayudas al pasar el cursor y foco visible. Los títulos, campos y textos explicativos se mantienen en español. Los iconos son Heroicons bajo licencia MIT, incluida junto a los recursos estáticos.

## Qué significa eliminar

No se archiva ni se oculta el chat: se utiliza la eliminación nativa de Hermes. Se borran los registros del chat y sus mensajes, junto con sus continuaciones por compresión y las transcripciones que administra Hermes. Una corrección mínima sobre la versión fijada de Hermes activa `include_compression_chain=True` únicamente en su operación de eliminación.

No es un borrado forense de todas las copias. Las copias de seguridad anteriores conservan su política existente; tampoco se borran la memoria global del agente, los archivos de trabajo, los recibos mínimos de reintento nativos ni la retención del proveedor. El diálogo muestra esta separación antes de confirmar. No se purgan respaldos ni proyectos del usuario.

La eliminación requiere sesión del propietario, origen válido, token CSRF y confirmación de alcance. Se impide mientras hay una respuesta en curso. Los identificadores expuestos por el portal se restringen a raíces `owner_`; las continuaciones internas se resuelven mediante el identificador estable de su conversación. La eliminación global toma primero una lista completa y paginada de hasta 10.000 registros, sin escribir si supera ese límite. Si una operación parcial falla, informa cuántos chats fueron eliminados y solicita recargar, sin afirmar un éxito total.

## Comprobaciones

Las pruebas locales usan servicios simulados y datos temporales; nunca borran conversaciones reales. La prueba `tests/native_history_probe.py` se ejecuta dentro de Hermes contra una base temporal en `/tmp`: crea una raíz comprimida, una continuación y un chat independiente; elimina la primera por la API y comprueba que solo desaparecen sus registros y transcripciones. La revisión visual comprueba iconos, historial, recuperación de mensajes y cancelación de los diálogos; no confirma la eliminación de chats del usuario.

### Resultado observado

- 51 pruebas de Python y tres de JavaScript aprobadas; una advertencia de dependencia no bloqueante.
- Regresión de paginación reproducida primero: después de un turno nuevo se repetían mensajes al cargar páginas anteriores. La interfaz ahora recarga la página canónica antes de habilitar la navegación; prueba y revisión independiente acotada aprobadas.
- La prueba nativa de raíz comprimida falló antes de la corrección y pasó en la imagen desplegada; el chat independiente permaneció intacto. Sigue apareciendo la limitación de FTS5 ya conocida, ajena a este cambio.
- HTTPS real: creación y eliminación de una conversación vacía y desechable; validación de CSRF y confirmación. Las cinco conversaciones anteriores permanecieron sin cambios. No se ejecutó el borrado global sobre el historial real.
- Navegador: recuperación de chats anteriores, persistencia tras recargar, iconos y diálogo de eliminación global con botón bloqueado hasta escribir la confirmación. El diálogo se canceló sin borrar chats del usuario.
- Solo se recrearon los servicios web y Hermes del asistente. Los 16 contenedores ajenos al proyecto conservaron identidad, imagen, inicio y estado.
