# Generación privada de imágenes

Hermes Web y Carpediev comparten el mismo servicio de generación con la clave de OpenAI guardada en **Secretos**. La clave real permanece en el almacén cifrado del portal; los asistentes reciben únicamente el token del relay. La selección y el bloqueo del modelo de conversación no cambian al generar imágenes.

## Alcance y coste

Esta versión permite **generar una imagen nueva a partir de texto**, no editar imágenes, importar imágenes remotas, usar multipart ni elegir modelos arbitrarios. El modelo permitido es `gpt-image-2`. Cada solicitud admite `n: 1`, tamaño `1024x1024`, `1536x1024` o `1024x1536`, y calidad `low`, `medium`, `high` o `auto`. La descripción debe tener entre 1 y 32.000 caracteres no vacíos. Todos esos controles son explícitos: un valor incompatible se rechaza, no se sustituye silenciosamente por otro.

La API de imágenes es **de pago** y depende del acceso, la facturación, el saldo y los límites de la cuenta de OpenAI. Configurar una clave o consultar modelos no garantiza acceso a la generación ni disponibilidad ilimitada. Se conserva el presupuesto compartido de solicitudes del proveedor y solo puede haber una generación en curso entre ambos asistentes.

## Uso en ambos canales

Pide, por ejemplo: «Genera una imagen cuadrada de una taza naranja sobre fondo negro». El agente dispone de la herramienta nativa `image_generate`; no necesita instalar scripts ni pedir la clave por chat. Los perfiles usan el nivel nativo `gpt-image-2-medium`, que envía el modelo real `gpt-image-2` con calidad `medium`. El tamaño se elige mediante orientación cuadrada, horizontal o vertical; los controles del contrato del relay no son parámetros adicionales del chat.

Hermes Web muestra la imagen privada y el control **Descargar imagen**. Carpediev usa el mismo relay y entrega el archivo mediante el transporte nativo de Telegram. La integración conserva conversaciones y ajustes ajenos a imágenes, y no inicia un segundo bot. Un despliegue saludable y las pruebas simuladas no demuestran una entrega real: cada canal debe comprobarse con su propia solicitud después del despliegue.

## Contrato del relay

El runtime autorizado envía un objeto JSON a `POST /provider/v1/images/generations` con su token de servicio existente. Solo se admiten los cinco campos indicados; no se envía `response_format`. El relay llama exclusivamente a la API oficial de generación de OpenAI, sin seguir redirecciones ni descargar URL de imágenes.

La respuesta compatible con OpenAI contiene `created` y un único elemento en `data`, con `b64_json` y `portal_url`. Este último tiene el formato `/api/images/<32 caracteres hexadecimales>` y permite a Hermes Web mostrar una vista previa y descargar el archivo. El runtime puede decodificar el mismo PNG para adjuntarlo en Telegram. No se devuelven mensajes sin filtrar, instrucciones revisadas, metadatos internos ni detalles de uso del proveedor.

## Almacenamiento y acceso

El portal verifica base64 estricto, tamaño máximo, cabecera, dimensiones, estructura, CRC y flujo de píxeles PNG antes de guardar. La respuesta JSON está limitada a 36 MiB y el PNG a 25 MiB. Solo se admiten los tamaños solicitados y PNG no entrelazados. Los archivos y sus registros se escriben de forma atómica bajo `CONTROL_DIR/generated-images`, con directorio privado `0700`, archivos `0600` e identificadores aleatorios. No se almacena la descripción enviada.

`GET /api/images/<id>` exige la sesión del propietario: el token del runtime no sirve para leer esta ruta. La respuesta es `image/png`, sin caché y con protección contra interpretación de otro contenido. No es un directorio público ni un endpoint para leer rutas arbitrarias; solo devuelve imágenes con un registro íntegro y rechaza enlaces simbólicos, enlaces duros y archivos alterados. Los PNG permanecen en el almacenamiento privado hasta que el propietario aplique su política de conservación; esta versión no incluye una galería ni borrado automático.

## Fallos y reintentos

Los fallos de clave ausente, clave rechazada, acceso al modelo, saldo/facturación, límite de solicitudes y tiempo agotado se comunican como errores reales, con código y estado seguros, sin mostrar la clave, la descripción ni el cuerpo bruto de OpenAI. La operación tiene un límite total de 300 segundos y **no realiza reintentos automáticos**. Un timeout o una desconexión pueden ocurrir después de que OpenAI haya procesado la solicitud: revisa el consumo antes de pedirla de nuevo. Tampoco se presenta una generación fallida como si hubiera producido un archivo.

Referencia: [Create image — OpenAI API](https://developers.openai.com/api/reference/resources/images/methods/generate).
