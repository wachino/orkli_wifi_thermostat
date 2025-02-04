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

### HACS (Home Assistant Community Store)
1. Go to the HACS section in your Home Assistant.
1. Click on **Integrations**.
1. Click the three dots in the top-right corner and select **Custom repositories**.
1. Add this repository URL: `https://github.com/wachino/orkli_wifi_thermostat`.
1. Search for "Mi Orkli Thermostat" and install the integration.
1. Restart Home Assistant.

### Manual Installation
1. Download the `orkli_wifi_thermostat` folder from this repository.
1. Copy the folder to the `custom_components` directory in your Home Assistant configuration.
1. Restart Home Assistant.
1. Add the integration via the Home Assistant UI:
   - Go to **Settings > Devices & Services > Integrations**.
   - Click **Add Integration** and search for "Mi Orkli Thermostat".
   - Follow the setup instructions.

---

## Configuration
After installing the integration, add your Mi Orkli Thermostat to Home Assistant using the following steps:
1. Go to **Settings > Devices & Services > Integrations**.
1. Click **Add Integration** and search for "Mi Orkli Thermostat".
1. Enter your thermostat's IP address and any required credentials.
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
- Switch between heating modes.
- Create automations using the thermostat's state and attributes.

---

## Contributing
Contributions are welcome! If you'd like to contribute, please:
1. Fork the repository.
1. Create a new branch for your feature or bugfix.
1. Submit a pull request.

---

## License
This project is licensed under the MIT License. See the LICENSE file for details.

Disclaimer
THE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND, EXPRESS OR IMPLIED, INCLUDING BUT NOT LIMITED TO THE WARRANTIES OF MERCHANTABILITY, FITNESS FOR A PARTICULAR PURPOSE, AND NONINFRINGEMENT. IN NO EVENT SHALL THE AUTHORS OR COPYRIGHT HOLDERS BE LIABLE FOR ANY CLAIM, DAMAGES, OR OTHER LIABILITY, WHETHER IN AN ACTION OF CONTRACT, TORT, OR OTHERWISE, ARISING FROM, OUT OF, OR IN CONNECTION WITH THE SOFTWARE OR THE USE OR OTHER DEALINGS IN THE SOFTWARE.

By using this software, you agree to these terms. The author(s) of this project are not responsible for any damages, malfunctions, or other issues that may arise from the use of this integration.

---

## Support
If you encounter any issues or have questions, please [open an issue](https://github.com/wachino/orkli_wifi_thermostat/issues) on GitHub.

