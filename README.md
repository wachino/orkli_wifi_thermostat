# Home Assistant Custom Integration for Mi Orkli Wi-Fi Thermostat (TERMOWIFI TW501B)

This custom integration allows you to control and monitor the Mi Orkli Wi-Fi Thermostat (Model TERMOWIFI TW501B) in Home Assistant. With this integration, you can manage your thermostat directly from your Home Assistant dashboard, create automations, and monitor your heating system's status.

---

## Features
- **Thermostat Control**: Set and adjust the target temperature.
- **Mode Selection**: Switch between heating modes (e.g., Comfort, Eco, Anti-freeze).
- **Real-Time Monitoring**: View current temperature, humidity, and thermostat status.
- **Automation Support**: Integrate with other Home Assistant devices and services for advanced automation.

---

## Installation

### HACS (Home Assistant Community Store)
1. Go to the HACS section in your Home Assistant.
2. Click on **Integrations**.
3. Click the three dots in the top-right corner and select **Custom repositories**.
4. Add this repository URL: `https://github.com/wachino/orkli_wifi_thermostat`.
5. Search for "Mi Orkli Thermostat" and install the integration.
6. Restart Home Assistant.

### Manual Installation
1. Download the `mi_orkli_thermostat` folder from this repository.
2. Copy the folder to the `custom_components` directory in your Home Assistant configuration.
3. Restart Home Assistant.
4. Add the integration via the Home Assistant UI:
   - Go to **Settings > Devices & Services > Integrations**.
   - Click **Add Integration** and search for "Mi Orkli Thermostat".
   - Follow the setup instructions.

---

## Configuration
After installing the integration, add your Mi Orkli Thermostat to Home Assistant using the following steps:
1. Go to **Settings > Devices & Services > Integrations**.
2. Click **Add Integration** and search for "Mi Orkli Thermostat".
3. Enter your thermostat's IP address and any required credentials.
4. Click **Submit** to complete the setup.

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
2. Create a new branch for your feature or bugfix.
3. Submit a pull request.

---

## License
This project is licensed under the MIT License. See the LICENSE file for details.

Disclaimer
THE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND, EXPRESS OR IMPLIED, INCLUDING BUT NOT LIMITED TO THE WARRANTIES OF MERCHANTABILITY, FITNESS FOR A PARTICULAR PURPOSE, AND NONINFRINGEMENT. IN NO EVENT SHALL THE AUTHORS OR COPYRIGHT HOLDERS BE LIABLE FOR ANY CLAIM, DAMAGES, OR OTHER LIABILITY, WHETHER IN AN ACTION OF CONTRACT, TORT, OR OTHERWISE, ARISING FROM, OUT OF, OR IN CONNECTION WITH THE SOFTWARE OR THE USE OR OTHER DEALINGS IN THE SOFTWARE.

By using this software, you agree to these terms. The author(s) of this project are not responsible for any damages, malfunctions, or other issues that may arise from the use of this integration.

---

## Support
If you encounter any issues or have questions, please [open an issue](https://github.com/wachino/orkli_wifi_thermostat/issues) on GitHub.

