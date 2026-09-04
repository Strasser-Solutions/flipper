# Changelog

## Unreleased

- horizontal (left-right) swing toggle; vertical and horizontal share one 3-bit field, so
  both axes are now written together instead of one clearing the other
- the two bottom toggles show their on/off state in the icon
- WindFree toggle: sets the FanSpecial field, and moves fan to auto and swing off with it,
  because the unit only honours WindFree in that combination
- changing fan speed or turning swing on drops back out of WindFree, matching the unit
- the setting persists alongside the others; files written before it are still loaded

## 1.0

- initial release
- power, mode (cool/heat/dry/fan/auto), temperature 16-30 °C, fan speed and vertical swing
- full state frame retransmitted on every press, dual-section checksum per the Samsung protocol
- settings persist between launches, out-of-range values are repaired on load
