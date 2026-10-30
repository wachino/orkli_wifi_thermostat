# Control de zona con los métodos estándar de Home Assistant

Cambios de la rama `fix/tcp-and-climate`, sobre la base
`6a4c9d8eaca7c3551f2647a3944809e265990151`. Incluye las correcciones TCP ya
preparadas. El usuario ha comunicado que su prueba manual en Home Assistant
parece funcionar correctamente. La validación automática usa un entorno simulado.
La evolución posterior del transporte, conservando los mismos paquetes, se
describe en [la revisión de envíos TCP](tcp-write-review.md).

## Comportamiento

| Acción | Resultado |
| --- | --- |
| `climate.turn_on` | Envía únicamente la orden de encender la zona. Conserva el modo. |
| `climate.turn_off` | Envía únicamente la orden de apagar la zona. Conserva el modo. |
| `climate.set_hvac_mode: off` | Envía la misma orden de apagar la zona. |
| `climate.set_hvac_mode: heat` con modo almacenado de calefacción | Envía la orden de encender la zona. |
| `climate.set_hvac_mode: cool` con modo almacenado de refrigeración | Envía la orden de encender la zona. |
| Solicitar el modo contrario o un modo no admitido | Error de validación sin enviar bytes. |
| Modo almacenado desconocido | Solo se ofrece `off` en la lista de modos; `turn_on` y `turn_off` siguen usando el encendido explícito de zona. |

`turn_on` es inequívoco a nivel del protocolo conocido: activa una zona y no
envía ninguna selección de calefacción/refrigeración. Puede activar la zona
aunque no se conozca el modo; su estado de clima dependerá después de la
telemetría. No se atribuye a la integración la capacidad de cambiar el modo
central de la geotermia.

La integración conserva la interpretación existente de `device.mode` (`0` para
calefacción y `1` para refrigeración). Ese dato puede proceder de la configuración
guardada o de los paquetes recibidos; no se añade una garantía de frescura ni se
afirma que represente el modo central. Confirmar ese significado requiere más
evidencia del protocolo.

## Corrección de la interfaz

La entidad implementa `async_set_hvac_mode` y deja de sobrescribir
`async_handle_set_hvac_mode_service`, que pertenece a la validación de Home
Assistant. Declara `TARGET_TEMPERATURE`, `TURN_ON` y `TURN_OFF` e implementa los
métodos de encendido y apagado explícitamente.

La lista `hvac_modes` contiene `off` y el modo almacenado del dispositivo. La
validación se mantiene tanto en el servicio base como en el método de la entidad,
por si se invoca directamente. Esto evita mostrar una capacidad de conmutar entre
calefacción y refrigeración que las órdenes actuales no implementan.

Tras un envío aceptado no se modifican `device.on` ni `device.mode`. El estado se
publica al recibir feedback por el coordinador. Se evita confundir la entrega
al socket con una confirmación del termostato. Un envío fallido o un error de
conexión genera `HomeAssistantError`; las peticiones de modo no admitidas generan
`ServiceValidationError`. Los mensajes nuevos tienen traducciones en inglés y
español. La cancelación de la acción se propaga normalmente.

Al recuperar el estado tras el arranque se siguen restaurando las temperaturas
como antes, pero no se sobrescriben el encendido ni el modo del coordinador con
un estado de clima antiguo. Esto también evita deducir refrigeración simplemente
porque el estado guardado era `off`.

## Compatibilidad

Las automatizaciones que solicitan siempre `heat` o siempre `cool` fallarán con
un error de validación cuando el modo almacenado sea el contrario. Para activar
la zona independientemente de la temporada se debe usar `climate.turn_on`.
El modo ofrecido en la interfaz seguirá los datos almacenados o recibidos.

La interfaz puede seguir mostrando el estado anterior hasta recibir telemetría.
No hay un tiempo máximo de confirmación ni una nueva consulta de estado.
También se conserva el tratamiento actual del coordinador de los valores cero;
esta propuesta no revisa la semántica de bits o el significado de esos valores.

Ejemplos de validación manual en Home Assistant, sustituyendo
`climate.tu_termostato` por la entidad real:

```yaml
action: climate.turn_on
target:
  entity_id: climate.tu_termostato
```

```yaml
action: climate.turn_off
target:
  entity_id: climate.tu_termostato
```

## Pruebas y alcance

```bash
python3 -B -m unittest discover -s tests -v
```

Con Python 3.13.1 pasan las **62 pruebas**, incluidas **21 de climatización**.
Se ejecutan las clases reales de entidad y coordinador con bases mínimas que
simulan Home Assistant. No se instala ni arranca Home Assistant; no se prueba
su registro completo de servicios ni la interfaz visual.

Las pruebas cubren los métodos estándar, las capacidades declaradas, la
validación de modos, errores de envío, cancelación, cambios del estado a partir
del feedback, restauración y los bytes exactos enviados. Para la zona de prueba
con dirección 8 e identificador 2 se comprueban estas tramas literales:

- Encendido: `3b 08 ff 04 08 03 16`.
- Apagado: `3b 08 ff 04 08 02 15`.
- Temperatura de 21,5 °C: `3b 08 ff 04 0a 2b 40`, seguida de la lectura existente
  `3b 08 ff 04 0b 00 16`.

`coordinator.py` y `PushAPI.async_send_command` conservan literalmente su código
original. No se añaden órdenes, reintentos de escritura ni consultas a dispositivos
para validar esta propuesta. El protocolo y las consignas permanecen iguales.

Referencias oficiales utilizadas para comprobar el contrato de la entidad y
el tratamiento de errores:

- [Entidad de clima y métodos de encendido/apagado](https://contributors.home-assistant.io/core/entities/climate/).
- [Implementación del validador de servicios de ClimateEntity](https://github.com/home-assistant/core/blob/dev/homeassistant/components/climate/entity.py).
- [Errores de acciones](https://contributors.home-assistant.io/core/quality-scale/action-exceptions/).
