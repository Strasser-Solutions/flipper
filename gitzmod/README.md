# 5RC Gitzmod — Samsung A/C remote for Flipper Zero

An infrared remote for Samsung air conditioners, running as a native app on the
[Flipper Zero](https://flipperzero.one/). Power, mode, temperature, fan speed, vertical
**and horizontal** swing, and **WindFree**.

![Overview](docs/screenshots/app-overview.png)

Unlike replaying recorded `.ir` files, this app assembles the Samsung state frame itself. That
means settings no capture happens to contain — WindFree above all — are reachable, and any
combination of settings can be sent.

---

## Install

**You need:** a Flipper Zero on **official firmware 1.4.3 (API 87.1)**. Check with
`device_info` on the serial CLI, or in `Settings → About`.

### The easy way

1. Grab `samsung_ac_remote.fap` (build it, see below, or take it from a release)
2. Copy it to the SD card into `apps/Infrared/` — via [qFlipper](https://flipperzero.one/update)
   or a card reader
3. On the Flipper: **Apps → Infrared → 5RC Gitzmod**

![Apps/Infrared](docs/screenshots/menu-infrared-folder.png)

### Building it yourself

With [uFBT](https://github.com/flipperdevices/flipperzero-ufbt), which fetches SDK and
toolchain on its own:

```bash
pip install --upgrade ufbt
cd gitzmod
ufbt              # builds the .fap
ufbt launch       # builds, uploads and starts it on a connected Flipper
```

The result lands in `dist/samsung_ac_remote.fap`.

> If `update.flipperzero.one` is unreachable from your network, uFBT cannot fetch the SDK.
> [`docs/rebuild.md`](docs/rebuild.md) describes a complete route that uses only GitHub as a
> source.

**Trouble uploading?** The serial port has to be free. qFlipper, an open terminal, or — easy
to overlook — a browser tab with a Flipper web tool will hold it. Web Serial grabs the port
again automatically after every replug.

---

## Using it

The display is in portrait orientation. Navigate with the D-pad, act with **OK**. The selected
button is drawn inverted.

| Button | What it does |
|---|---|
| **POWER** | Turn the unit on / off |
| **MODE** | Cycle: cool → heat → dry → fan → auto |
| **+ / −** | Target temperature, 16–30 °C |
| **FAN** | Cycle: auto → low → medium → high |
| **SWING** | Vertical swing (vanes up/down) |
| **↔** | Horizontal swing (left/right) |
| **WF** | WindFree |

The two buttons in the bottom row show their state: while the setting is on, the icon carries
a bar under the glyph.

| ↔ off | ↔ on | WF off | WF on |
|---|---|---|---|
| ![off](docs/screenshots/app-overview.png) | ![on](docs/screenshots/swing-h-on.png) | ![off](docs/screenshots/app-overview.png) | ![on](docs/screenshots/windfree-on.png) |

**While power is off, nothing is transmitted.** The other buttons only change what the app
shows, so you can set everything up first and then switch on with a single press of POWER.

**WindFree only works with fan on auto and vertical swing off** — that is what the unit
accepts. The app moves those along when you enable it, and drops back out of WindFree when
you change the fan or turn vertical swing on. Horizontal swing is independent.

> **If the unit ignores you, check the temperature first.** The app offers 16–30 °C because
> that is the protocol's range. Many units are narrower — the one this was developed against
> only cools from 18 °C and silently discards anything below. It then looks as if it were
> only running the fan.

Full walkthrough with all modes and fan speeds: [`docs/usage.md`](docs/usage.md).

---

## Documentation

| Document | Contents |
|---|---|
| [`docs/usage.md`](docs/usage.md) | Every button and state, with screenshots |
| [`docs/rebuild.md`](docs/rebuild.md) | Protocol, build environment and the complete source, annotated |
| [`docs/changelog.md`](docs/changelog.md) | What changed |
| [`tools/`](tools/) | Icon generator, u8g2 text measurement, RPC screenshot capture |

---

## Credits and provenance

This app is a fork. Being precise about what came from where:

### Taken from others

| Project | Author | License | What is used |
|---|---|---|---|
| [samsung-ac-remote-flipper-app](https://github.com/dappermint/samsung-ac-remote-flipper-app) | [@dappermint](https://github.com/dappermint) | MIT | **The app this forks.** Scene structure, the `hvac_samsung` protocol library, the icon set, the build harness |
| [flipperzero-midea-ac-remote](https://github.com/xakep666/flipperzero-midea-ac-remote) | [@xakep666](https://github.com/xakep666) | MIT | The panel widget `views/ac_remote_panel.*`, inherited through the project above |
| [IRremoteESP8266](https://github.com/crankyoldgit/IRremoteESP8266) | [@crankyoldgit](https://github.com/crankyoldgit) | LGPL-2.1 | The Samsung protocol itself: field layout, timings, reset template, checksum, and the captured frames used to verify this implementation. Reverse engineered in [#1538](https://github.com/crankyoldgit/IRremoteESP8266/issues/1538) and [#1062](https://github.com/crankyoldgit/IRremoteESP8266/issues/1062) |
| [flipperzero-firmware](https://github.com/flipperdevices/flipperzero-firmware) | [Flipper Devices](https://github.com/flipperdevices) | GPL-3.0 | SDK, headers and API table to build against |
| [u8g2](https://github.com/olikraus/u8g2) | [@olikraus](https://github.com/olikraus) | BSD 2-clause | Font data, read at build time only to measure text widths |
| [arm-none-eabi-gcc-xpack](https://github.com/xpack-dev-tools/arm-none-eabi-gcc-xpack) | [xPack](https://github.com/xpack-dev-tools) | MIT packaging | The build toolchain |

Nothing from the firmware SDK, u8g2 or the toolchain ends up inside the `.fap` — the app
resolves the firmware API when it is loaded on the device.

### Added in this fork

Everything below is the work of **5RC**, [Strasser-Solutions](https://github.com/Strasser-Solutions/flipper):

- **WindFree support.** `hvac_samsung_set_windfree()`, writing the `FanSpecial` field, plus
  the state coupling the unit requires (fan to auto, vertical swing off, and dropping back
  out when either changes).
- **Horizontal swing.** Both swing axes share one 3-bit protocol field, so
  `hvac_samsung_set_swing()` was reworked to take and write both axes together — writing one
  alone silently cleared the other.
- **On/off state in the icons.** Four generated icons per toggle (off/on, each with a hover
  variant), so a setting's state is visible whether or not the button is selected.
- **The heading** in two font weights, sized against the actual u8g2 font metrics rather than
  by trial and error.
- **A bug fix**: `ac_remote_store_settings()` returned `true` when the final write *failed* —
  the return value was inverted.
- **Documentation and tooling**: the rebuild guide, the screenshot pipeline over the Flipper's
  RPC screen stream, the icon generator and the font measurement script.

The upstream README states the panel code came from a GPL project. That is not correct —
`flipperzero-midea-ac-remote` is MIT licensed, as is this project. Corrected here.

---

## License

MIT. See [`LICENSE`](LICENSE), which carries both copyright lines and the provenance notes.
