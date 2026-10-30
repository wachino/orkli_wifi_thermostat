# Envíos TCP completos y serializados

Esta mejora parte de `f0575473fce5d6ac1f9d24cbb31540c9a770331b` y se prepara en
`fix/async-tcp-send`. No modifica la construcción ni el contenido de las tramas.

## Problema y comportamiento

Un `socket.send()` en modo no bloqueante puede escribir solo parte de una trama
o indicar que no se puede escribir temporalmente. El transporte anterior trataba
un resultado distinto de siete como un fallo y cerraba la conexión, sin terminar
de enviar los bytes restantes.

`async_send_command()` utiliza ahora `loop.sock_sendall()` y un bloqueo por
instancia de `PushAPI`. Cada comando ocupa la conexión hasta terminar de escribir
todos sus bytes o detectar un error. No se mezclan escrituras concurrentes.

- La escritura tiene un límite de diez segundos (`SEND_TIMEOUT`). El tiempo de
  espera del bloqueo no forma parte de ese límite.
- `True` significa que terminó la escritura en el socket local. No confirma que
  el controlador recibiera o ejecutara la orden.
- Ante error, timeout o cancelación de una escritura activa se cierra la conexión
  afectada, porque no se puede saber qué prefijo de la trama llegó a enviarse.
- No se reintenta ni se reproduce automáticamente el comando sobre otra conexión.
  Los comandos que esperaban el bloqueo del socket cerrado también devuelven
  `False`. Una nueva petición explícita puede usar una conexión nueva.
- Cancelar una petición que solo esperaba su turno no cierra el socket del
  comando activo. Cancelar la escritura activa propaga `CancelledError` a su
  llamador después de cancelar la operación de I/O y cerrar el socket.
- Un cierre interno cancela la escritura y devuelve `False` a su llamador, sin
  cancelarlo como si se hubiera cancelado toda la acción de Home Assistant.
- Los errores de conexión anteriores al envío mantienen su contrato existente:
  `APIConnectionError` se propaga al llamador.

## Relación con reconexión y descarga

El transporte conserva el socket seleccionado antes de esperar el bloqueo y
verifica su identidad al obtenerlo. Una petición en espera nunca se traslada
silenciosamente a un socket diferente.

Los cambios de conexión esperan el mismo bloqueo para que termine la limpieza
del envío anterior antes de crear otro socket. Así se evita reutilizar un
descriptor mientras asyncio todavía está retirando su operación de escritura.
Los remitentes conectan antes de adquirir el bloqueo de escritura, evitando
invertir el orden de los bloqueos.

La descarga cancela los trabajos de conexión/recepción y la escritura pendiente,
espera su limpieza y deja salir las peticiones que esperaban el socket antiguo.
Se cubre también el caso en que el propio callback de recepción esperaba un envío.
Un error de una escritura antigua no cierra un socket de reemplazo.

## Validación

```bash
python3 -B -m unittest discover -s tests -v
```

Pasan **80 pruebas** con Python 3.13.1. Las 18 nuevas cubren escritura completa,
serialización, errores sin argumentos, timeout, cancelación antes de iniciar el
trabajo y durante la escritura, cancelación de un solicitante en espera,
reconexión, descarga y conservación del orden de las tramas. Se adapta también
la prueba de recuperación tras fallo de envío al nuevo transporte.

Dos pruebas usan el `sock_sendall()` real con un par de sockets Unix locales y
una envoltura que fuerza escrituras de dos bytes y un primer `BlockingIOError`.
Verifican los bytes recibidos y que dos comandos concurrentes no se intercalan.
Las demás usan sockets/operaciones simuladas. No se abre ninguna conexión TCP/FTP
contra dispositivos, ni se ejecuta una instancia completa de Home Assistant.

Se comprueba que `coordinator.py`, `climate.py` y la clase `Packet` mantienen su
código respecto a la base. Las pruebas de bytes literales para encendido,
apagado y temperatura siguen pasando. La versión del manifiesto no cambia:
publicar una nueva release es un paso posterior a la revisión.

El contrato de `sock_sendall()` está documentado en la
[referencia de Python 3.12](https://docs.python.org/3.12/library/asyncio-eventloop.html#asyncio.loop.sock_sendall).
