# Home Assistant Custom Integration for Orkli Wi-Fi Thermostat (TERMOWIFI TW501B)

_Please, notice: **This is NOT an official integration**._

This custom integration allows you to control and monitor the Orkli Wi-Fi Thermostat (Model TERMOWIFI TW501B) in Home Assistant. With this integration, you can manage your thermostat directly from your Home Assistant dashboard, create automations, and monitor your heating system's status.


![termostato-on](https://github.com/user-attachments/assets/b5dc8e05-227a-4cc0-9995-57b5241ce21b)
![termostato-off](https://github.com/user-attachments/assets/a6957119-6c36-4020-884a-00f15f5d293d)


---

## Features
- **Thermostat Control**: Set and adjust the target temperature.
- **Real-Time Monitoring**: View current temperature, humidity, and thermostat status.
- **Automation Support**: Integrate with other Home Assistant devices and services for advanced automation.

---

## Installation

[![Open your Home Assistant instance and open a repository inside the Home Assistant Community Store.](https://my.home-assistant.io/badges/hacs_repository.svg)](https://my.home-assistant.io/redirect/hacs_repository/?owner=wachino&category=Integration&repository=https%3A%2F%2Fgithub.com%2Fwachino%2Forkli_wifi_thermostat)

### HACS (Home Assistant Community Store)
1. Go to the HACS section in your Home Assistant.
1. Click on **Integrations**.
1. Click the three dots in the top-right corner and select **Custom repositories**.
1. Add this repository URL: `https://github.com/wachino/orkli_wifi_thermostat`.
1. Search for "Orkli Wifi Thermostat" and install the integration.
1. Restart Home Assistant.

### Manual Installation
1. Download the `orkli_wifi_thermostat` folder from this repository.
1. Copy the folder to the `custom_components` directory in your Home Assistant configuration.
1. Restart Home Assistant.

---

## Configuration
After installing the integration, add your Orkli Wifi Thermostat to Home Assistant using the following steps:
1. Go to **Settings > Devices & Services > Integrations**.
1. Click **Add Integration** and search for "Orkli Wifi Thermostat".
1. Enter your thermostat's IP address and the required credentials.
1. Click **Submit** to complete the setup.

---

## Visualization

By using this integration you can create a dashboard to look like:
![dashboard](https://github.com/user-attachments/assets/127b6032-7bbe-4051-a30d-25e0b9f2d900)

Where each widget will show the temperature and humidity:
![dashboard-widget](https://github.com/user-attachments/assets/1483b408-8c31-4d5a-ba0c-4ecbc2e69b55)

Additionally you can look for the humidity and temperature history:
![dashboard-temp-history](https://github.com/user-attachments/assets/58235611-5500-45c2-9a59-df7feca8514a)
![dashboard-humidity](https://github.com/user-attachments/assets/4bfdddc3-76e1-4dd1-81c9-70dd35e5d630)

---

## Usage
Once configured, the thermostat will appear as an entity in Home Assistant. You can:
- View the current temperature and humidity.
- Adjust the target temperature.
- Create automations using the thermostat's state and attributes.

### Zone power and HVAC mode

The climate entities support `climate.turn_on` and `climate.turn_off` using the
existing zone power command. Turning a zone on preserves its heating/cooling
mode; it does not select the central system's mode.

`climate.set_hvac_mode` accepts `off` and the zone's currently cached `heat` or
`cool` mode. Selecting that known mode enables the zone. The opposite mode is
rejected because this integration has no verified command for changing it.
The mode list follows the cached device mode. If that mode is unknown, direct
on/off commands still work without guessing a heating/cooling mode.

An accepted send is not a device acknowledgement: the displayed state changes
when device feedback arrives. Failed power commands raise an action error.
Stored climate states no longer overwrite coordinator power or mode at startup.
See [the climate control review](docs/climate-control-review.md) for examples,
compatibility notes, and offline validation limits.

### TCP writes

Commands are serialized and sent with `loop.sock_sendall()` on nonblocking
sockets, with a ten-second write timeout. Short writes and temporary socket
backpressure no longer discard the rest of a frame. A failed, timed-out, or
cancelled write closes that connection without automatically replaying the
command; commands queued on the closed connection are also rejected.

A successful send confirms delivery to the local socket, not execution by the
thermostat. The protocol bytes are unchanged. See
[the TCP write review](docs/tcp-write-review.md) for cancellation, reconnect,
and test details.

---

## Contributing
Contributions are welcome! If you'd like to contribute, please:
1. Fork the repository.
1. Create a new branch for your feature or bugfix.
1. Submit a pull request.

### Offline stability and climate tests

From the repository root, run with Python 3.10 or newer:

```bash
python3 -B -m unittest discover -s tests -v
```

The tests use only the Python standard library and fake sockets. They do not
require Home Assistant, credentials, or a thermostat connection. Coverage includes
fragmentation, concatenated frames, noise, invalid checksums, resynchronization,
connection retries, concurrent callers, timeouts, cancellation, integration
unloading, climate action hooks, and exact command bytes. A deterministic
execution budget catches non-yielding receiver loops without hanging the test
runner. Climate tests use Home Assistant stand-ins, not a running instance.
Write tests also exercise the real asyncio send-all implementation with a local
Unix socket pair that forces short writes; no TCP connection to hardware is made.

See [the TCP parser review](docs/tcp-parser-review.md) for the patch scope,
validation results, connection lifecycle behavior, and remaining limitations.

---

## License
This project is licensed under the MIT License. See the LICENSE file for details.

Disclaimer
THE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND, EXPRESS OR IMPLIED, INCLUDING BUT NOT LIMITED TO THE WARRANTIES OF MERCHANTABILITY, FITNESS FOR A PARTICULAR PURPOSE, AND NONINFRINGEMENT. IN NO EVENT SHALL THE AUTHORS OR COPYRIGHT HOLDERS BE LIABLE FOR ANY CLAIM, DAMAGES, OR OTHER LIABILITY, WHETHER IN AN ACTION OF CONTRACT, TORT, OR OTHERWISE, ARISING FROM, OUT OF, OR IN CONNECTION WITH THE SOFTWARE OR THE USE OR OTHER DEALINGS IN THE SOFTWARE.

By using this software, you agree to these terms. The author(s) of this project are not responsible for any damages, malfunctions, or other issues that may arise from the use of this integration.

---

## Support
If you encounter any issues or have questions, please [open an issue](https://github.com/wachino/orkli_wifi_thermostat/issues) on GitHub.

