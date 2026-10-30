# Estabilidad TCP: parser, reconexión y cierre

Descripción de las correcciones de recepción TCP, reconexión y cierre.
La evolución posterior del transporte de escritura se describe en
[la revisión de envíos TCP](tcp-write-review.md).

## Base y rama de trabajo

- Repositorio: `wachino/orkli_wifi_thermostat`.
- Base local: `6a4c9d8eaca7c3551f2647a3944809e265990151`.
- Rama de trabajo: `fix/tcp-and-climate`, destinada a integrarse en `main`.

## Fallos confirmados en la versión original

1. **Bucle sin progreso.** En `PushAPI.async_update_devices`, un checksum inválido
   o una trama incompleta provoca `continue` sin retirar bytes de `arr_data`.
   El mismo candidato se valida indefinidamente. Como ese recorrido no contiene
   un `await`, también puede bloquear el bucle de eventos de Home Assistant.
2. **Falta de estado entre lecturas.** `arr_data` se crea de nuevo en cada `recv`.
   No existe un buffer para reconstruir una trama repartida entre varias lecturas.
   En la implementación original, el primer fragmento con cabecera ya reproduce
   el bloqueo anterior.
3. **Recuperación ante ruido insuficiente.** Si el primer candidato de siete bytes
   es inválido, el parser no llega a una trama válida posterior, aunque su
   cabecera esté dentro de ese candidato.

Las tramas completas concatenadas sí funcionan en la versión original; se
incluyen como prueba de compatibilidad.

Estos fallos ofrecen una explicación reproducible para bloqueos de recepción.
Sin registros del incidente ni una captura real no se puede atribuirles todos
los fallos observados en la instalación.

## Procesamiento de tramas

`PacketStreamParser.feed()` separa el procesamiento de bytes de la red:

- Descarta ruido anterior a `0x3B`.
- Espera hasta disponer de siete bytes, conservando el fragmento entre lecturas.
- Aplica el checksum existente: suma de los bytes 1 a 5, módulo 256.
- Consume siete bytes de una trama válida y devuelve un `Packet` inmutable en
  cuanto a su contenido `message`, almacenado como `bytes`.
- Ante un candidato inválido consume un byte y vuelve a buscar cabecera.
- Al terminar conserva como máximo seis bytes pendientes. Cada vuelta consume
  bytes o termina, por lo que no puede repetir indefinidamente el mismo candidato.
- Limpia el buffer al conectar y desconectar, evitando unir fragmentos de
  conexiones distintas.

El receptor sigue entregando al callback únicamente paquetes con destino `1`.
Una cabecera en los datos o en el checksum de una trama válida no la divide.
No se añaden filtros de comandos, direcciones ni interpretaciones del modo.

`API.isValidMessage()` mantiene su interfaz y delega en la misma validación usada
por el parser. La recepción es el único consumidor nuevo de esa validación.

## Conexión, reconexión y descarga

- Las llamadas concurrentes a `async_connect()` se serializan y reutilizan el
  socket y la tarea receptora existentes.
- La conexión de `PushAPI` usa un socket no bloqueante y `loop.sock_connect()`
  con un límite de diez segundos, sin bloquear Home Assistant. La variante
  síncrona de `API.connect()` también limita la espera a diez segundos.
- El receptor conserva su identidad al reconectar por EOF, reinicio del peer o
  desconexión causada por el transporte de envío. Espera cinco segundos tras EOF,
  errores de recepción o intentos fallidos antes de volver a intentarlo.
- La falta temporal de datos mantiene la pausa existente de un segundo. Incluso
  con datos continuos, el receptor cede el control para permitir el cierre.
- `async_disconnect()` marca la instancia como detenida, cancela la conexión y
  el receptor pendientes y espera su finalización. También cierra sockets que
  todavía no se habían publicado y evita fugas si se cancela justo al conectar.
- El cierre explícito es definitivo para esa instancia: posteriores conexiones
  lanzan `APIConnectionError`. Una recarga crea una instancia nueva. El método
  síncrono `disconnect()`, usado por el transporte ante errores, cierra solo la
  conexión y permite que el receptor la recupere.
- Los errores de transporte se presentan como `APIConnectionError`, conservando
  su causa, en vez de atribuirlos a credenciales inválidas.
- Los errores del callback se registran con su traza y no cierran el socket ni
  impiden procesar el siguiente paquete. La cancelación se propaga normalmente.
- Al descargarse correctamente las plataformas, `async_unload_entry()` espera
  `coordinator.async_shutdown()` antes de retirar el listener y los datos.
  Si la descarga de plataformas falla, conserva la conexión y el listener.

El puerto, las tramas, los comandos enviados y la interpretación de estados
permanecen iguales. No se reenvían automáticamente órdenes tras un fallo de envío.

## Validación realizada

Con Python 3.13.1, sin instalar dependencias:

```bash
python3 -B -m unittest discover -s tests -v
git diff --cached --check
```

- Antes del cambio: de las ocho pruebas del receptor, dos pasan y seis
  reproducen el bloqueo, con once errores contando los casos de fragmentación.
  El límite de ejecución de las pruebas interrumpe el bucle sin colgar el equipo.
- La suite completa contiene **62 pruebas**: 19 de framing/recepción, 22 de ciclo
  de conexión/descarga y 21 de climatización. Todas pasan.
- Se prueban las 64 particiones posibles de una trama de siete bytes y cada
  punto de corte de un flujo de cuatro tramas concatenadas.
- También se cubren recepción byte a byte, checksum incorrecto, ruido, cabeceras
  dentro de datos y checksum, candidatos solapados, cola incompleta, `EAGAIN`,
  consumo de otros destinos, entradas repetidas y limpieza entre conexiones.
- Las pruebas nuevas cubren llamadas concurrentes, reutilización del lector tras
  EOF y errores sin argumentos, reintentos fallidos con pausa, conexión lenta,
  cierre durante conexión/callback/reintento, cancelación justo al publicar el
  socket, llamadas en espera durante el cierre y limpieza de fragmentos al
  reconectar. También verifican que un flujo continuo permite el cierre.
- Las pruebas de descarga ejecutan la función real `async_unload_entry` extraída
  del código con objetos simulados de Home Assistant. Comprueban tanto una
  descarga correcta como una descarga rechazada por las plataformas.
- Los receptores usan sockets simulados. La creación accidental de conexiones
  reales hace fallar las pruebas; no se envían comandos a dispositivos.
- Se compara literalmente el código de envío de `PushAPI.async_send_command` con la base y
  se comprueba que `coordinator.py` permanece idéntico. El cambio adicional en
  `climate.py` y sus pruebas se describen en [la revisión de climatización](climate-control-review.md).

La validación automática no ejecuta Home Assistant, hassfest ni hardware real.
El usuario ha comunicado que la prueba manual en Home Assistant parece funcionar
correctamente; no se dispone de una matriz exhaustiva de pruebas con hardware.
Las tramas de las pruebas son vectores sintéticos con checksum comprobable,
no capturas del termostato. La compatibilidad con Python 3.10 se deduce de la
sintaxis utilizada; la ejecución realizada corresponde a Python 3.13.1.

## Limitaciones y siguientes mejoras

- El transporte de escritura se conserva literalmente, incluido su tratamiento
  de envíos parciales o errores. No se introduce ninguna política de reenvío.
- Un callback que no termina todavía puede detener la entrega de actualizaciones.
  Un callback síncrono que bloquee el bucle de eventos también impide la
  cancelación cooperativa; estas pruebas usan callbacks asíncronos controlados.
- El cierre de una integración que falla antes de terminar su configuración y
  la ejecución completa del ciclo de vida de Home Assistant no se han probado.
- Se ha añadido `climate.turn_on` mediante la orden de zona existente. Confirmar
  si el modo informado es central o por zona y añadir sensores de diagnóstico
  siguen pendientes. No se modifica la interpretación del protocolo.

El checksum es débil y el protocolo procede de ingeniería inversa: cierto ruido
puede coincidir con una trama válida. Se conserva el criterio existente; no se
introducen reglas adicionales sin evidencia del protocolo.

## Revisión y eventual despliegue

La rama contiene las mejoras TCP y de climatización, las pruebas y esta
documentación. El diff de la PR permite revisar conjuntamente esos cambios.

Antes de instalarlo, revisar el diff completo, incluidos el ciclo de conexión y
la descarga. Las pruebas verifican el comportamiento con red y entorno simulados;
no demuestran por sí solas el funcionamiento en una instalación real.

Solo después de aprobar una versión concreta: guardar una copia de la integración
instalada, comprobar que coincide con la base prevista y aplicar esa versión en
Home Assistant. El seguimiento inicial debe observar actualizaciones y registros,
sin modificar consignas, encender zonas ni cambiar el modo para probar el parser.
Incluso una validación observacional en Home Assistant ejecutaría sus consultas
habituales; requiere la aprobación del usuario para conectar con los dispositivos.
La reversión consiste en restaurar la copia anterior y reiniciar Home Assistant.

Las pruebas automatizadas no realizan conexiones TCP/FTP, consultas ni escrituras
contra dispositivos reales.
