# Chat en español con documentos y audio

El chat permite adjuntar documentos, transcribir grabaciones y dictar texto en directo. Los controles del compositor son únicamente iconos: adjuntar, micrófono y enviar; al generar una respuesta aparece también el icono de detener.

## Cómo usarlo

- **Documentos:** pulsa el clip y selecciona PDF, DOCX, TXT, MD o CSV. Se muestra una etiqueta que puedes quitar antes de enviar. Puedes escribir una pregunta o enviar únicamente los documentos para que Hermes los analice.
- **Grabaciones:** selecciona un MP3, MP4, MPEG, MPGA, M4A, WAV o WebM desde el mismo clip. El audio se transcribe progresivamente en el cuadro de texto. Revisa y modifica el resultado antes de enviarlo.
- **Dictado:** pulsa el micrófono y permite su uso en el navegador. Verás texto mientras hablas. Pulsa otra vez para terminar, revisa el texto y envía cuando quieras. El dictado no envía automáticamente un mensaje a Hermes.

El audio se procesa con OpenAI y tiene consumo de API independiente del modelo del chat. Se mantiene `gpt-6.1-sol` para Hermes. El dictado usa `gpt-live-transcribe`; las grabaciones usan `gpt-transcribe`.

## Límites y privacidad

| Recurso | Límite |
|---|---|
| Documento | 5 MiB por archivo; tres adjuntos por mensaje |
| PDF | 50 páginas; debe contener texto seleccionable |
| Texto extraído | 40.000 caracteres por documento; mensaje completo máximo de 60.000 |
| Grabación | 10 MiB por archivo |
| Dictado | Una sesión simultánea; hasta tres minutos |
| Conexiones de dictado | Diez inicios por cada diez minutos |

Los PDF escaneados necesitan OCR previo. No se ejecutan macros ni instrucciones de los documentos. El servidor extrae texto con límites de memoria, CPU y tiempo. Esta separación de proceso contiene consumo de recursos, pero no constituye un sistema operativo aislado adicional.

Los originales y las grabaciones no se guardan como archivos en el VPS. El texto de los documentos se incorpora como contenido no confiable a la conversación de Hermes y permanece en su historial privado. El audio viaja a OpenAI para transcribirse. La clave API real permanece en el servicio de control: el navegador no la recibe.

El micrófono solo está permitido en el propio sitio. La conexión de audio exige sesión, origen válido y token CSRF, y utiliza el proxy de salida existente. No se expuso ningún puerto adicional ni se amplió el acceso del agente al VPS.

## Verificación realizada

- 34 pruebas de Python y dos de JavaScript aprobadas; una advertencia de dependencia no bloqueante.
- Revisión independiente de los límites, autenticación y contrato de audio; corrección del campo de idioma de grabaciones a `languages[]=es` comprobada con una prueba de regresión.
- Carga HTTPS de un documento de más de 3 MiB: superó correctamente el límite anterior del intermediario. El límite de Caddy se amplió únicamente en el dominio de este proyecto a 11 MB; los límites específicos del servidor siguen activos.
- Extracción real de texto de PDF y TXT comprobada. En el navegador, Hermes respondió `LUNA-5724`, el código contenido solo en el documento de prueba adjunto.
- Grabación de voz sintética en español transcrita correctamente mediante OpenAI; se verificaron eventos parciales y finales. La interfaz insertó el resultado como texto editable sin enviarlo.
- Dictado probado a través del WebSocket HTTPS real con voz sintética PCM16 mono a 24 kHz: recibió texto parcial a los 2,54 segundos para una grabación de 4,9 segundos y confirmó el texto final. No se grabó el micrófono físico del usuario.
- Los 16 contenedores anteriores al proyecto conservaron su identidad, imagen, inicio y estado; Caddy solo recibió una recarga gradual. Solo se reconstruyó el contenedor web para esta mejora.

## Pendiente de comprobación por el usuario

El permiso del navegador y el micrófono físico se verifican al pulsar el icono en su equipo. No se ha supuesto ni concedido ese permiso automáticamente.

El bloque informativo situado debajo del compositor se retiró a petición del usuario. Los límites, la transcripción y las protecciones descritas en este documento no cambiaron.
