# Pin map and commissioning checklist

## Firmware default pin map

| Part | ESP32 pin | Notes |
|---|---|---|
| Capacitive soil sensor analog output | GPIO34 | ADC1 input; sensor output must remain at or below 3.3 V |
| Relay module control input | GPIO26 | Verify active-high/active-low logic with pump disconnected |
| DHT22 data | GPIO27 | Use correct module wiring; bare DHT22 generally requires a pull-up resistor |
| Water-level sensor analog output | GPIO35 | Firmware assumes analog output; adapt for a digital-only module |
| Sensor power/ground | Per module spec | Do not assume a sensor board accepts 5 V; check its output voltage too |

## Preflight checklist

- [ ] Pump voltage/current and adapter current are confirmed from labels/datasheets.
- [ ] 5 V supply is not mistakenly expected to become 6 V through the LM2596 buck converter.
- [ ] Pump power is not drawn from ESP32 GPIO/3.3 V.
- [ ] Relay contact rating is suitable for the pump's DC inductive load and startup current.
- [ ] ESP32 input pins cannot receive 5 V.
- [ ] Relay is OFF at boot and after Wi-Fi loss.
- [ ] Dry/wet soil ADC values have been measured and entered into firmware.
- [ ] Empty/full reservoir ADC values have been measured and entered into firmware.
- [ ] Pump OFF works even if moisture sensor is unplugged or reports invalid values.
- [ ] Low-water cutoff, maximum run-time cutoff, and overwatering cutoff have been tested with pump disconnected first.
- [ ] Tubing is secured, water cannot drip onto electronics, and a manual power disconnect is available.
- [ ] The system is observed through multiple full watering cycles before unattended use.
