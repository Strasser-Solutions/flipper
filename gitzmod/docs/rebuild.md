# 5RC Gitzmod — Rebuild guide

Everything needed to rebuild this Flipper Zero app from scratch, **including the complete
source**: an infrared remote for Samsung air conditioners with power, mode, temperature, fan
speed, vertical and horizontal swing, and WindFree.

Day-to-day usage is described in [`usage.md`](usage.md), screenshots live in
[`screenshots/`](screenshots/).

The starting point is
[dappermint/samsung-ac-remote-flipper-app](https://github.com/dappermint/samsung-ac-remote-flipper-app)
(MIT), whose panel code in turn comes from
[flipperzero-midea-ac-remote](https://github.com/xakep666/flipperzero-midea-ac-remote)
(also MIT). Added here: WindFree, horizontal swing, the on/off state indication on the bottom
buttons, and the heading. Full attribution in
[Licensing and provenance](#13-licensing-and-provenance).

---

## Contents

1. [Requirements](#1-requirements)
2. [The Samsung protocol](#2-the-samsung-protocol)
3. [Verifying against real captures](#3-verifying-against-real-captures)
4. [Build environment](#4-build-environment)
5. [Project layout](#5-project-layout)
6. [The protocol code](#6-the-protocol-code)
7. [The app](#7-the-app)
8. [The panel widget](#8-the-panel-widget)
9. [Icons and assets](#9-icons-and-assets)
10. [Building and installing](#10-building-and-installing)
11. [Taking screenshots](#11-taking-screenshots)
12. [Pitfalls](#12-pitfalls)
13. [Licensing and provenance](#13-licensing-and-provenance)

---

## 1. Requirements

**Hardware**

- Flipper Zero (target `f7`, hardware version 12 in this case)
- A Samsung air conditioner within infrared range

**Firmware on the device**

This app was built against **official firmware 1.4.3, API 87.1**. The API version has to
match between build and device, otherwise the loader refuses to start the app. Check it over
the serial CLI:

```
device_info
```

The interesting fields are `firmware_api_major` / `firmware_api_minor` and
`firmware_origin_fork`.

**On the build machine**

- Python 3 (3.13 here)
- The ARM toolchain `arm-none-eabi-gcc` **12.3.1** — the version the Flipper SDK is built
  with
- SCons plus a handful of Python packages, see [Build environment](#4-build-environment)

---

## 2. The Samsung protocol

### Stateful, not command based

Samsung air conditioners have **no single-shot commands** like "temperature +1". Every press
on the original remote transmits the **complete device state**. So the app has to send
everything currently set on every button press.

This is also why brute force gets you nowhere here: a random bit pattern is practically never
a valid frame with correct checksums.

### Frame format

The standard frame is **14 bytes**, split into **two sections of 7 bytes**. Each section
carries **its own checksum**.

```
carrier          38 kHz, 50 % duty cycle

header           mark  690 us   space 17844 us
per section      mark 3086 us   space  8864 us
                 56 bits, LSB first
                   bit mark      586 us
                   one space    1432 us
                   zero space    436 us
                 footer mark 586 us, gap 2886 us
```

That comes to 2 + 2 x (2 + 56x2 + 2) = **234 timing values** per transmission. The Flipper
infrared library caps out at `MAX_TIMINGS_AMOUNT` = 1024, so there is plenty of room.

### Field map

Byte offsets refer to the 14-byte frame:

| Byte | Bits | Field | Values |
|---|---|---|---|
| 1 | 4–7 | checksum 1, low nibble | |
| 2 | 0–3 | checksum 1, high nibble | |
| 6 | 4–5 | power 1 | `0b11` on, `0b00` off |
| 8 | 4–7 | checksum 2, low nibble | |
| 9 | 0–3 | checksum 2, high nibble | |
| 9 | 4–6 | swing | `0b111` off, `0b010` vertical, `0b011` horizontal, `0b100` both |
| 10 | 1–3 | fan special | `0b000` off, `0b011` powerful, `0b101` **WindFree**, `0b111` econo |
| 10 | 4 | display | |
| 11 | 4–7 | temperature | setpoint − 16, so `0`…`14` for 16–30 °C |
| 12 | 1–3 | fan | `0` auto, `2` low, `4` medium, `5` high |
| 12 | 4–6 | mode | `0` auto, `1` cool, `2` dry, `3` fan, `4` heat |
| 13 | 4–5 | power 2 | same as power 1 |

**Two traps** that are easy to miss when rebuilding this:

- **Power appears twice in the frame** (byte 6 and byte 13). Both have to be set.
- **Vertical and horizontal swing share a single 3-bit field.** Writing only one axis clears
  the other. The setter has to take both axes.

The unit only accepts **WindFree** together with **fan on auto** and **vertical swing off** —
the reference implementation checks exactly that combination in `getBreeze()`. The app sets
those along with it, and drops back out when fan or vertical swing change.

### Checksum

Per section: count the set bits, skipping the checksum nibbles themselves, then invert the
result bitwise.

```
sum      = popcount(section[0])
         + popcount(section[1] & 0x0F)     // the high nibble is the checksum
         + popcount((section[2] >> 4) & 0x0F)
         + popcount(section[3..6])
checksum = sum XOR 0xFF
```

The 8 bits are then split across two nibbles: the low nibble goes into the **high** nibble of
byte 1 of the section, the high nibble into the **low** nibble of byte 2.

### Sources

Timings, field layout and checksum come from the reverse engineering in
[IRremoteESP8266](https://github.com/crankyoldgit/IRremoteESP8266), file `ir_Samsung.cpp`,
originally documented in
[issue #1538](https://github.com/crankyoldgit/IRremoteESP8266/issues/1538) (checksum) and
[issue #1062](https://github.com/crankyoldgit/IRremoteESP8266/issues/1062) (WindFree).

---

## 3. Verifying against real captures

Rather than trusting that the implementation is right, it can be checked against real frames
captured from original remotes. The IRremoteESP8266 test suite contains such captures
together with their decoded meaning.

The following script builds the frames with the same logic as the C code and compares them
byte for byte:

```python
RESET = [0x02,0x92,0x0F,0x00,0x00,0x00,0xF0, 0x01,0x02,0xAE,0x71,0x00,0x15,0xF0]

def set_field(p, i, mask, shift, value):
    p[i] = (p[i] & ~mask) | ((value << shift) & mask)

def section_checksum(sec):
    pc = lambda v: bin(v).count('1')
    s = pc(sec[0]) + pc(sec[1] & 0x0F) + pc((sec[2] >> 4) & 0x0F)
    s += sum(pc(sec[i]) for i in range(3, 7))
    return s ^ 0xFF

def build(mode, temp, fan, swing_v, swing_h, windfree, power):
    p = list(RESET)
    set_field(p, 12, 0x70, 4, {'auto':0,'cool':1,'dry':2,'fan':3,'heat':4}[mode])
    set_field(p, 11, 0xF0, 4, temp - 16)
    set_field(p, 12, 0x0E, 1, {'auto':0,'low':2,'med':4,'high':5}[fan])
    sw = 0b100 if (swing_v and swing_h) else 0b010 if swing_v else 0b011 if swing_h else 0b111
    set_field(p, 9, 0x70, 4, sw)
    set_field(p, 10, 0x0E, 1, 0b101 if windfree else 0b000)
    set_field(p, 6,  0x30, 4, 0b11 if power else 0b00)
    set_field(p, 13, 0x30, 4, 0b11 if power else 0b00)
    for base, lo, hi in ((0, 1, 2), (7, 8, 9)):
        s = section_checksum(p[base:base+7])
        p[lo] = (p[lo] & 0x0F) | ((s & 0x0F) << 4)
        p[hi] = (p[hi] & 0xF0) | ((s >> 4) & 0x0F)
    return p

# capture from IRremoteESP8266 issue #505:
# "Power: On, Mode: Cool, Temp: 24C, Fan: Auto, Swing(V): Off, Swing(H): Off"
assert build('cool', 24, 'auto', False, False, False, True) == \
    [0x02,0x92,0x0F,0x00,0x00,0x00,0xF0, 0x01,0xE2,0xFE,0x71,0x80,0x11,0xF0]

# capture: "Power: On, Mode: Cool, Temp: 16C, Fan: Low, Swing(V): On"
assert build('cool', 16, 'low', True, False, False, True) == \
    [0x02,0x92,0x0F,0x00,0x00,0x00,0xF0, 0x01,0x02,0xAF,0x71,0x00,0x15,0xF0]

print("both frames match bit for bit")
```

Both comparisons pass, checksums included. What the app transmits is exactly what an original
remote transmits.

---

## 4. Build environment

### The normal way

Flipper apps are built with **uFBT**, which downloads SDK and toolchain itself:

```bash
pip install --upgrade ufbt
cd <app directory>
ufbt              # builds the .fap
ufbt launch       # builds, copies to the device and starts it
```

That is all it normally takes.

### When `update.flipperzero.one` is blocked

On a corporate network with a DNS filter (Cisco Umbrella in this case) the update domain is
blocked, so uFBT can fetch neither SDK nor toolchain:

```
HTTP/1.1 403 Forbidden
Server: Cisco Umbrella
```

The way around it has three parts, all sourced from GitHub:

**a) Firmware source tree instead of the SDK package.** The official release assets are only
served from the blocked domain, but the source is on GitHub, and it can be built against
directly:

```bash
git clone --depth 1 --branch 1.4.3 \
  https://github.com/flipperdevices/flipperzero-firmware.git fw
cd fw
git submodule update --init --recursive --depth 1 --jobs 8
```

> On Windows, clone to a **short path** (e.g. `C:\ffw`) and set
> `git config core.longpaths true`. Otherwise the mbedtls submodule fails with
> *"Filename too long"*.

**b) ARM toolchain from xPack.** Same version as the official one (GCC 12.3.1):

```
https://github.com/xpack-dev-tools/arm-none-eabi-gcc-xpack/releases/tag/v12.3.1-1.2
```

Extract it so that `<toolchain>/bin/arm-none-eabi-gcc` exists.

**c) Skip the toolchain download.** `FBT_NOENV=1` tells the build system the environment is
already set up, so it fetches nothing:

```powershell
$env:FBT_NOENV  = "1"
$env:FBT_NO_SYNC = "1"
$env:PATH = "<toolchain>\bin;$env:PATH"
cd C:\ffw
.\fbt.cmd fap_<appid>
```

The app is hooked into the firmware tree with a directory junction:

```powershell
New-Item -ItemType Junction -Path C:\ffw\applications_user\samsung_ac_remote `
         -Target <app directory>
```

### Python packages

The build system normally ships its own Python. Without that bundle, these modules have to be
available in whichever interpreter is used:

```bash
pip install scons ansi colorlog Pillow heatshrink2 protobuf jinja2 \
            pyserial python-dotenv pyelftools cxxheaderparser grpcio-tools
```

`grpcio-tools` is needed because nanopb otherwise looks for a `protoc` binary; with it
installed, nanopb uses the compiler that ships inside the package.

One spot in the SDK needs defusing. Generating the VS Code configuration calls
`WhereIs("openocd")` and `WhereIs("clangd")`. Without the bundled toolchain both are missing,
`WhereIs` returns `None`, and the build dies before compiling anything. In
`scripts/fbt/util.py`:

```python
    @staticmethod
    def fix_path(path):
        if path is None:      # openocd/clangd absent without the Flipper bundle
            return ""
        return str(PurePosixPath(Path(path).as_posix()))
```

### Does the build match the firmware?

Once built, you can check whether the app will load on the target at all. A `.fap` is an ELF
file; every undefined symbol in it has to appear in the firmware's API table
(`targets/f7/api_symbols.csv`):

```python
import csv
from elftools.elf.elffile import ELFFile

undef = set()
with open("samsung_ac_remote.fap", "rb") as f:
    for sec in ELFFile(f).iter_sections():
        if sec.header["sh_type"] in ("SHT_SYMTAB", "SHT_DYNSYM"):
            for s in sec.iter_symbols():
                if s["st_shndx"] == "SHN_UNDEF" and s.name:
                    undef.add(s.name)

api = set()
with open("targets/f7/api_symbols.csv", newline="") as f:
    for r in csv.DictReader(f):
        if r["entry"] in ("Function", "Variable") and r["status"] in ("+", "?"):
            api.add(r["name"])

print("missing:", sorted(undef - api))
```

An empty list means the app will start. This is exactly how it surfaced that a build against
a **fork SDK** (Momentum) drops four icons as "duplicates", because that fork ships them in
its firmware — on official firmware they are then missing and the app fails to load with
`MissingImports`.

---

## 5. Project layout

```
samsung_ac_remote/
├── application.fam                 app manifest
├── ac_remote.png                   app icon (10x10)
├── ac_remote_app.c/.h              entry point, lifecycle
├── ac_remote_app_i.h               internal struct, settings
├── ac_remote_custom_event.h        event encoding
├── scenes/
│   ├── ac_remote_scene.c/.h        scene plumbing (generic)
│   ├── ac_remote_scene_config.h    scene list
│   └── ac_remote_scene_samsung.c   the actual user interface
├── views/
│   └── ac_remote_panel.c/.h        button grid widget
├── lib/hvac_samsung/
│   └── hvac_samsung.c/.h           protocol: build and send the frame
└── assets/                         icons, compiled into C code
```

The structure follows the usual Flipper pattern: a `ViewDispatcher` with a `SceneManager`,
holding a `ViewStack` with a custom view widget.

---

## 6. The protocol code

This part is independent of the Flipper UI and can be tested on its own.


### `lib/hvac_samsung/hvac_samsung.h`

Public interface. Note that `hvac_samsung_set_swing()` takes **both** axes — they share one protocol field.

<sub>69 lines</sub>

```c
#pragma once

#include <infrared_transmit.h>
#include <infrared_worker.h>

#include "furi_hal.h"

// Standard (14-byte) Samsung A/C frame: two 7-byte sections, each with its
// own checksum. Field layout, timings and checksum reverse-engineered in
// IRremoteESP8266 (ir_Samsung.cpp), originally documented in issue #1538.
#define HVAC_SAMSUNG_PACKET_SIZE     14
#define HVAC_SAMSUNG_SECTION_SIZE    7
typedef uint8_t* HvacSamsungPacket;

HvacSamsungPacket hvac_samsung_create_packet(void);
void hvac_samsung_free_packet(HvacSamsungPacket packet);

typedef enum {
    HvacSamsungModeCool,
    HvacSamsungModeHeat,
    HvacSamsungModeDry,
    HvacSamsungModeFan,
    HvacSamsungModeAuto,
} HvacSamsungMode;
void hvac_samsung_set_mode(HvacSamsungPacket packet, HvacSamsungMode mode);

typedef enum {
    HvacSamsungFanAuto,
    HvacSamsungFanLow,
    HvacSamsungFanMed,
    HvacSamsungFanHigh,
} HvacSamsungFan;
void hvac_samsung_set_fan(HvacSamsungPacket packet, HvacSamsungFan fan);

typedef uint8_t HvacSamsungTemperature;
#define HVAC_SAMSUNG_TEMPERATURE_MIN     (HvacSamsungTemperature)16
#define HVAC_SAMSUNG_TEMPERATURE_MAX     (HvacSamsungTemperature)30
#define HVAC_SAMSUNG_TEMPERATURE_DEFAULT (HvacSamsungTemperature)24
void hvac_samsung_set_temperature(HvacSamsungPacket packet, HvacSamsungTemperature temperature);

void hvac_samsung_set_power(HvacSamsungPacket packet, bool on);
// Vertical and horizontal swing share a single 3-bit field, so both axes have
// to be written together - setting one on its own would clear the other.
void hvac_samsung_set_swing(HvacSamsungPacket packet, bool vertical, bool horizontal);

// WindFree ("Breeze"): the vanes close over the outlet so the cooled air seeps
// out without a direct draft. It shares the FanSpecial field with Powerful and
// Econo, and the unit only honours it with the fan on auto and swing off.
void hvac_samsung_set_windfree(HvacSamsungPacket packet, bool on);

#define HVAC_SAMSUNG_TRANSMIT_FREQUENCY  38000
#define HVAC_SAMSUNG_TRANSMIT_DUTY_CYCLE 0.5

#define HVAC_SAMSUNG_HDR_MARK      690
#define HVAC_SAMSUNG_HDR_SPACE     17844
#define HVAC_SAMSUNG_SECTION_MARK  3086
#define HVAC_SAMSUNG_SECTION_SPACE 8864
#define HVAC_SAMSUNG_BIT_MARK      586
#define HVAC_SAMSUNG_ONE_SPACE     1432
#define HVAC_SAMSUNG_ZERO_SPACE    436
#define HVAC_SAMSUNG_SECTION_GAP   2886

// header mark+space, then per section: section mark+space, 56 bits (mark+space
// each), footer mark+gap
#define HVAC_SAMSUNG_TIMINGS_LEN \
    (2 + (HVAC_SAMSUNG_PACKET_SIZE / HVAC_SAMSUNG_SECTION_SIZE) * (2 + 7 * 8 * 2 + 2))

// Recomputes both section checksums then transmits the frame once.
void hvac_samsung_send(HvacSamsungPacket packet);
```

### `lib/hvac_samsung/hvac_samsung.c`

Frame assembly, checksums, and the conversion into timings.

<sub>162 lines</sub>

```c
#include "hvac_samsung.h"

// Library default template. Carries the fixed bytes and sane defaults
// (cool, 24C, fan auto, swing off, power off). Checksums are recomputed
// before every transmission.
static const uint8_t hvac_samsung_reset[HVAC_SAMSUNG_PACKET_SIZE] = {
    0x02,
    0x92,
    0x0F,
    0x00,
    0x00,
    0x00,
    0xF0,
    0x01,
    0x02,
    0xAE,
    0x71,
    0x00,
    0x15,
    0xF0};

static void hvac_samsung_set_field(uint8_t* byte, uint8_t mask, uint8_t shift, uint8_t value) {
    *byte = (*byte & ~mask) | ((value << shift) & mask);
}

static uint8_t hvac_samsung_popcount(uint8_t value) {
    uint8_t count = 0;
    while(value) {
        count += value & 1;
        value >>= 1;
    }
    return count;
}

static uint8_t hvac_samsung_section_checksum(const uint8_t* section) {
    uint8_t sum = 0;
    sum += hvac_samsung_popcount(section[0]);
    sum += hvac_samsung_popcount(section[1] & 0x0F);
    sum += hvac_samsung_popcount((section[2] >> 4) & 0x0F);
    for(uint8_t i = 3; i < HVAC_SAMSUNG_SECTION_SIZE; i++) {
        sum += hvac_samsung_popcount(section[i]);
    }
    return sum ^ 0xFF;
}

static void hvac_samsung_checksum(HvacSamsungPacket packet) {
    uint8_t sum = hvac_samsung_section_checksum(packet);
    packet[1] = (packet[1] & 0x0F) | ((sum & 0x0F) << 4); // Sum1Lower
    packet[2] = (packet[2] & 0xF0) | ((sum >> 4) & 0x0F); // Sum1Upper

    sum = hvac_samsung_section_checksum(packet + HVAC_SAMSUNG_SECTION_SIZE);
    packet[8] = (packet[8] & 0x0F) | ((sum & 0x0F) << 4); // Sum2Lower
    packet[9] = (packet[9] & 0xF0) | ((sum >> 4) & 0x0F); // Sum2Upper
}

HvacSamsungPacket hvac_samsung_create_packet(void) {
    HvacSamsungPacket packet = malloc(sizeof(uint8_t) * HVAC_SAMSUNG_PACKET_SIZE);
    furi_assert(packet);
    memcpy(packet, hvac_samsung_reset, HVAC_SAMSUNG_PACKET_SIZE);
    return packet;
}

void hvac_samsung_free_packet(HvacSamsungPacket packet) {
    furi_assert(packet);
    free(packet);
}

void hvac_samsung_set_power(HvacSamsungPacket packet, bool on) {
    furi_assert(packet);
    uint8_t value = on ? 0b11 : 0b00;
    hvac_samsung_set_field(&packet[6], 0x30, 4, value); // Power1
    hvac_samsung_set_field(&packet[13], 0x30, 4, value); // Power2
}

void hvac_samsung_set_mode(HvacSamsungPacket packet, HvacSamsungMode mode) {
    furi_assert(packet);
    static const uint8_t raw[] = {
        [HvacSamsungModeCool] = 1,
        [HvacSamsungModeHeat] = 4,
        [HvacSamsungModeDry] = 2,
        [HvacSamsungModeFan] = 3,
        [HvacSamsungModeAuto] = 0,
    };
    hvac_samsung_set_field(&packet[12], 0x70, 4, raw[mode]); // Mode
}

void hvac_samsung_set_fan(HvacSamsungPacket packet, HvacSamsungFan fan) {
    furi_assert(packet);
    static const uint8_t raw[] = {
        [HvacSamsungFanAuto] = 0,
        [HvacSamsungFanLow] = 2,
        [HvacSamsungFanMed] = 4,
        [HvacSamsungFanHigh] = 5,
    };
    hvac_samsung_set_field(&packet[12], 0x0E, 1, raw[fan]); // Fan
}

void hvac_samsung_set_temperature(HvacSamsungPacket packet, HvacSamsungTemperature temperature) {
    furi_assert(packet);
    if(temperature < HVAC_SAMSUNG_TEMPERATURE_MIN) temperature = HVAC_SAMSUNG_TEMPERATURE_MIN;
    if(temperature > HVAC_SAMSUNG_TEMPERATURE_MAX) temperature = HVAC_SAMSUNG_TEMPERATURE_MAX;
    hvac_samsung_set_field(
        &packet[11], 0xF0, 4, temperature - HVAC_SAMSUNG_TEMPERATURE_MIN); // Temp
}

void hvac_samsung_set_swing(HvacSamsungPacket packet, bool vertical, bool horizontal) {
    furi_assert(packet);
    uint8_t value;
    if(vertical && horizontal) {
        value = 0b100;
    } else if(vertical) {
        value = 0b010;
    } else if(horizontal) {
        value = 0b011;
    } else {
        value = 0b111;
    }
    hvac_samsung_set_field(&packet[9], 0x70, 4, value); // Swing
}

void hvac_samsung_set_windfree(HvacSamsungPacket packet, bool on) {
    furi_assert(packet);
    hvac_samsung_set_field(&packet[10], 0x0E, 1, on ? 0b101 : 0b000); // FanSpecial
}

void hvac_samsung_send(HvacSamsungPacket packet) {
    furi_assert(packet);
    hvac_samsung_checksum(packet);

    uint32_t* timings = malloc(sizeof(uint32_t) * HVAC_SAMSUNG_TIMINGS_LEN);
    furi_assert(timings);
    furi_assert(HVAC_SAMSUNG_TIMINGS_LEN <= MAX_TIMINGS_AMOUNT);

    size_t idx = 0;
    timings[idx++] = HVAC_SAMSUNG_HDR_MARK;
    timings[idx++] = HVAC_SAMSUNG_HDR_SPACE;

    for(uint8_t offset = 0; offset < HVAC_SAMSUNG_PACKET_SIZE; offset += HVAC_SAMSUNG_SECTION_SIZE) {
        timings[idx++] = HVAC_SAMSUNG_SECTION_MARK;
        timings[idx++] = HVAC_SAMSUNG_SECTION_SPACE;

        for(uint8_t i = 0; i < HVAC_SAMSUNG_SECTION_SIZE; i++) {
            uint8_t byte = packet[offset + i];
            for(uint8_t mask = 1; mask > 0; mask <<= 1) {
                timings[idx++] = HVAC_SAMSUNG_BIT_MARK;
                timings[idx++] =
                    (byte & mask) ? HVAC_SAMSUNG_ONE_SPACE : HVAC_SAMSUNG_ZERO_SPACE;
            }
        }

        timings[idx++] = HVAC_SAMSUNG_BIT_MARK;
        timings[idx++] = HVAC_SAMSUNG_SECTION_GAP;
    }

    infrared_send_raw_ext(
        timings,
        HVAC_SAMSUNG_TIMINGS_LEN,
        true,
        HVAC_SAMSUNG_TRANSMIT_FREQUENCY,
        HVAC_SAMSUNG_TRANSMIT_DUTY_CYCLE);
    free(timings);
}
```

---

## 7. The app

### Manifest


### `application.fam`

`fap_private_libs` pulls the protocol code in as its own library, `fap_icon_assets` compiles the `assets/` folder into `<appid>_icons.h`.

<sub>30 lines</sub>

```python
App(
    appid="samsung_ac_remote",
    name="Samsung AC Remote",
    apptype=FlipperAppType.EXTERNAL,
    targets=["f7"],
    entry_point="ac_remote_app",
    cdefines=["APP_SAMSUNG_AC_REMOTE"],
    requires=[
        "storage",
        "gui",
        "infrared",
    ],
    stack_size=1 * 2048,
    order=90,
    fap_description="Samsung Electric Air Conditioner remote control",
    fap_version="1.0",
    fap_icon="ac_remote.png",
    fap_category="Infrared",
    fap_author="@dappermint",
    fap_weburl="https://github.com/dappermint/samsung-ac-remote-flipper-app",
    fap_icon_assets="assets",
    fap_private_libs=[
        Lib(
            name="hvac_samsung",
            sources=[
                "hvac_samsung.c",
            ],
        ),
    ],
)
```

### Entry point and lifecycle


### `ac_remote_app.h`

<sub>11 lines</sub>

```c
#pragma once

#ifdef __cplusplus
extern "C" {
#endif

typedef struct AC_RemoteApp AC_RemoteApp;

#ifdef __cplusplus
}
#endif
```

### `ac_remote_app_i.h`

This holds the settings struct. Every new feature gets a field here.

<sub>42 lines</sub>

```c
#pragma once

#include <gui/gui.h>
#include <gui/view.h>
#include <gui/view_stack.h>
#include <gui/view_dispatcher.h>
#include <gui/scene_manager.h>
#include <storage/storage.h>
#include <flipper_format/flipper_format.h>
#include <notification/notification_messages.h>
#include <hvac_samsung.h>

#include "ac_remote_app.h"
#include "scenes/ac_remote_scene.h"
#include "ac_remote_custom_event.h"
#include "views/ac_remote_panel.h"
#include "samsung_ac_remote_icons.h"

#define AC_REMOTE_APP_SETTINGS APP_DATA_PATH("settings.txt")

typedef struct {
    uint32_t power;
    uint32_t mode;
    uint32_t temperature;
    uint32_t fan;
    uint32_t swing;
    uint32_t swing_h;
    uint32_t windfree;
} ACRemoteAppSettings;

struct AC_RemoteApp {
    Gui* gui;
    ViewDispatcher* view_dispatcher;
    SceneManager* scene_manager;
    ViewStack* view_stack;
    ACRemotePanel* ac_remote_panel;
    ACRemoteAppSettings app_state;
};

typedef enum {
    AC_RemoteAppViewStack,
} AC_RemoteAppView;
```

### `ac_remote_app.c`

<sub>75 lines</sub>

```c
#include "ac_remote_app_i.h"

#include <furi.h>
#include <furi_hal.h>

static bool ac_remote_app_custom_event_callback(void* context, uint32_t event) {
    furi_assert(context);
    AC_RemoteApp* app = context;
    return scene_manager_handle_custom_event(app->scene_manager, event);
}

static bool ac_remote_app_back_event_callback(void* context) {
    furi_assert(context);
    AC_RemoteApp* app = context;
    return scene_manager_handle_back_event(app->scene_manager);
}

static void ac_remote_app_tick_event_callback(void* context) {
    furi_assert(context);
    AC_RemoteApp* app = context;
    scene_manager_handle_tick_event(app->scene_manager);
}

AC_RemoteApp* ac_remote_app_alloc() {
    AC_RemoteApp* app = malloc(sizeof(AC_RemoteApp));

    app->gui = furi_record_open(RECORD_GUI);

    app->view_dispatcher = view_dispatcher_alloc();
    app->scene_manager = scene_manager_alloc(&ac_remote_scene_handlers, app);
    view_dispatcher_set_event_callback_context(app->view_dispatcher, app);

    view_dispatcher_set_custom_event_callback(
        app->view_dispatcher, ac_remote_app_custom_event_callback);
    view_dispatcher_set_navigation_event_callback(
        app->view_dispatcher, ac_remote_app_back_event_callback);
    view_dispatcher_set_tick_event_callback(
        app->view_dispatcher, ac_remote_app_tick_event_callback, 100);

    view_dispatcher_attach_to_gui(app->view_dispatcher, app->gui, ViewDispatcherTypeFullscreen);

    app->view_stack = view_stack_alloc();
    view_dispatcher_add_view(
        app->view_dispatcher, AC_RemoteAppViewStack, view_stack_get_view(app->view_stack));

    app->ac_remote_panel = ac_remote_panel_alloc();

    scene_manager_next_scene(app->scene_manager, AC_RemoteSceneSamsung);
    return app;
}

void ac_remote_app_free(AC_RemoteApp* app) {
    furi_assert(app);

    // Views
    view_dispatcher_remove_view(app->view_dispatcher, AC_RemoteAppViewStack);

    // View dispatcher
    view_dispatcher_free(app->view_dispatcher);
    view_stack_free(app->view_stack);
    ac_remote_panel_free(app->ac_remote_panel);
    scene_manager_free(app->scene_manager);

    // Close records
    furi_record_close(RECORD_GUI);
    free(app);
}

int32_t ac_remote_app(void* p) {
    UNUSED(p);
    AC_RemoteApp* ac_remote_app = ac_remote_app_alloc();
    view_dispatcher_run(ac_remote_app->view_dispatcher);
    ac_remote_app_free(ac_remote_app);
    return 0;
}
```

### `ac_remote_custom_event.h`

Event type and payload are packed into a single `uint32_t`, because the ViewDispatcher only carries one numeric value.

<sub>38 lines</sub>

```c
#pragma once

#include <stdint.h>

enum AC_RemoteCustomEventType {
    AC_RemoteCustomEventTypeButtonPressed,
    AC_RemoteCustomEventTypeButtonLongPressed,
    AC_RemoteCustomEventTypeSendSettings,
    AC_RemoteCustomEventTypeSendCommand,
};

#pragma pack(push, 1)
typedef union {
    uint32_t packed_value;
    struct {
        uint16_t type;
        int16_t value;
    } content;
} AC_RemoteCustomEvent;
#pragma pack(pop)

static inline uint32_t ac_remote_custom_event_pack(uint16_t type, int16_t value) {
    AC_RemoteCustomEvent event = {.content = {.type = type, .value = value}};
    return event.packed_value;
}

static inline void
    ac_remote_custom_event_unpack(uint32_t packed_value, uint16_t* type, int16_t* value) {
    AC_RemoteCustomEvent event = {.packed_value = packed_value};
    if(type) *type = event.content.type;
    if(value) *value = event.content.value;
}

static inline uint16_t ac_remote_custom_event_get_type(uint32_t packed_value) {
    uint16_t type;
    ac_remote_custom_event_unpack(packed_value, &type, NULL);
    return type;
}
```

### Scene plumbing


### `scenes/ac_remote_scene.h`

<sub>29 lines</sub>

```c
#pragma once

#include <gui/scene_manager.h>

// Generate scene id and total number
#define ADD_SCENE(prefix, name, id) AC_RemoteScene##id,
typedef enum {
#include "ac_remote_scene_config.h"
    AC_RemoteSceneNum,
} AC_RemoteScene;
#undef ADD_SCENE

extern const SceneManagerHandlers ac_remote_scene_handlers;

// Generate scene on_enter handlers declaration
#define ADD_SCENE(prefix, name, id) void prefix##_scene_##name##_on_enter(void*);
#include "ac_remote_scene_config.h"
#undef ADD_SCENE

// Generate scene on_event handlers declaration
#define ADD_SCENE(prefix, name, id) \
    bool prefix##_scene_##name##_on_event(void* context, SceneManagerEvent event);
#include "ac_remote_scene_config.h"
#undef ADD_SCENE

// Generate scene on_exit handlers declaration
#define ADD_SCENE(prefix, name, id) void prefix##_scene_##name##_on_exit(void* context);
#include "ac_remote_scene_config.h"
#undef ADD_SCENE
```

### `scenes/ac_remote_scene.c`

<sub>30 lines</sub>

```c
#include "ac_remote_scene.h"

// Generate scene on_enter handlers array
#define ADD_SCENE(prefix, name, id) prefix##_scene_##name##_on_enter,
void (*const ac_remote_scene_on_enter_handlers[])(void*) = {
#include "ac_remote_scene_config.h"
};
#undef ADD_SCENE

// Generate scene on_event handlers array
#define ADD_SCENE(prefix, name, id) prefix##_scene_##name##_on_event,
bool (*const ac_remote_scene_on_event_handlers[])(void* context, SceneManagerEvent event) = {
#include "ac_remote_scene_config.h"
};
#undef ADD_SCENE

// Generate scene on_exit handlers array
#define ADD_SCENE(prefix, name, id) prefix##_scene_##name##_on_exit,
void (*const ac_remote_scene_on_exit_handlers[])(void* context) = {
#include "ac_remote_scene_config.h"
};
#undef ADD_SCENE

// Initialize scene handlers configuration structure
const SceneManagerHandlers ac_remote_scene_handlers = {
    .on_enter_handlers = ac_remote_scene_on_enter_handlers,
    .on_event_handlers = ac_remote_scene_on_event_handlers,
    .on_exit_handlers = ac_remote_scene_on_exit_handlers,
    .scene_num = AC_RemoteSceneNum,
};
```

### `scenes/ac_remote_scene_config.h`

<sub>1 lines</sub>

```c
ADD_SCENE(ac_remote, samsung, Samsung)
```

### The user interface

This is the central file: layout, state handling, persistence and transmission.

Three things are worth understanding here:

- **Nothing is transmitted while the unit is off.** The event handler only updates state and
  returns early. That lets you set everything up and then activate it with a single press of
  power.
- **The grid is sparse.** `ac_remote_panel_reserve(2, 4)` allocates 2x4 slots, seven are
  filled — navigation skips the empty ones.
- **Labels only store the pointer** to their text, not a copy. So only string literals or
  long-lived buffers may go in there.


### `scenes/ac_remote_scene_samsung.c`

<sub>441 lines</sub>

```c
#include "../ac_remote_app_i.h"

typedef enum {
    button_power,
    button_mode,
    button_temp_up,
    button_fan,
    button_temp_down,
    button_swing,
    button_swing_h,
    button_windfree,
    label_temperature,
    label_title,
    label_title_suffix,
} button_id;

const Icon* power[2][2] = {
    [0] = {&I_on_19x20, &I_on_hover_19x20},
    [1] = {&I_off_19x20, &I_off_hover_19x20},
};
const Icon* mode[5][2] = {
    [HvacSamsungModeCool] = {&I_cold_19x20, &I_cold_hover_19x20},
    [HvacSamsungModeHeat] = {&I_heat_19x20, &I_heat_hover_19x20},
    [HvacSamsungModeDry] = {&I_dry_19x20, &I_dry_hover_19x20},
    [HvacSamsungModeFan] = {&I_fan_19x20, &I_fan_hover_19x20},
    [HvacSamsungModeAuto] = {&I_auto_19x20, &I_auto_hover_19x20},
};
const Icon* fan[4][2] = {
    [HvacSamsungFanAuto] = {&I_fan_speed_auto_19x20, &I_fan_speed_auto_hover_19x20},
    [HvacSamsungFanLow] = {&I_fan_speed_1_19x20, &I_fan_speed_1_hover_19x20},
    [HvacSamsungFanMed] = {&I_fan_speed_2_19x20, &I_fan_speed_2_hover_19x20},
    [HvacSamsungFanHigh] = {&I_fan_speed_3_19x20, &I_fan_speed_3_hover_19x20},
};

// The two bottom toggles show their state: the "on" icons carry a bar under
// the glyph, and each state has its own hover variant.
const Icon* swing_h[2][2] = {
    [0] = {&I_swing_h_19x11, &I_swing_h_hover_19x11},
    [1] = {&I_swing_h_on_19x11, &I_swing_h_on_hover_19x11},
};
const Icon* windfree[2][2] = {
    [0] = {&I_windfree_19x11, &I_windfree_hover_19x11},
    [1] = {&I_windfree_on_19x11, &I_windfree_on_hover_19x11},
};

char buffer[4] = {0};

bool ac_remote_load_settings(ACRemoteAppSettings* app_state) {
    Storage* storage = furi_record_open(RECORD_STORAGE);
    FlipperFormat* ff = flipper_format_buffered_file_alloc(storage);
    FuriString* header = furi_string_alloc();

    uint32_t version = 0;
    bool success = false;
    do {
        if(!flipper_format_buffered_file_open_existing(ff, AC_REMOTE_APP_SETTINGS)) break;
        if(!flipper_format_read_header(ff, header, &version)) break;
        if(!furi_string_equal(header, "Samsung AC Remote") || (version != 1)) break;
        if(!flipper_format_read_uint32(ff, "Mode", &app_state->mode, 1)) break;
        if(app_state->mode > HvacSamsungModeAuto) break;
        if(!flipper_format_read_uint32(ff, "Temperature", &app_state->temperature, 1)) break;
        if(app_state->temperature < HVAC_SAMSUNG_TEMPERATURE_MIN ||
           app_state->temperature > HVAC_SAMSUNG_TEMPERATURE_MAX)
            break;
        if(!flipper_format_read_uint32(ff, "Fan", &app_state->fan, 1)) break;
        if(app_state->fan > HvacSamsungFanHigh) break;
        if(!flipper_format_read_uint32(ff, "Swing", &app_state->swing, 1)) break;
        if(app_state->swing > 1) break;
        // Written by versions without horizontal swing, so a missing key is not an error.
        if(!flipper_format_read_uint32(ff, "SwingH", &app_state->swing_h, 1)) {
            app_state->swing_h = 0;
        }
        if(app_state->swing_h > 1) break;
        if(!flipper_format_read_uint32(ff, "Power", &app_state->power, 1)) break;
        if(app_state->power > 1) break;
        // Written by versions without WindFree, so a missing key is not an error.
        if(!flipper_format_read_uint32(ff, "WindFree", &app_state->windfree, 1)) {
            app_state->windfree = 0;
        }
        if(app_state->windfree > 1) break;
        success = true;
    } while(false);
    furi_record_close(RECORD_STORAGE);
    furi_string_free(header);
    flipper_format_free(ff);
    return success;
}

bool ac_remote_store_settings(ACRemoteAppSettings* app_state) {
    Storage* storage = furi_record_open(RECORD_STORAGE);
    FlipperFormat* ff = flipper_format_file_alloc(storage);

    bool success = false;
    do {
        if(!flipper_format_file_open_always(ff, AC_REMOTE_APP_SETTINGS)) break;
        if(!flipper_format_write_header_cstr(ff, "Samsung AC Remote", 1)) break;
        if(!flipper_format_write_comment_cstr(ff, "")) break;
        if(!flipper_format_write_uint32(ff, "Mode", &app_state->mode, 1)) break;
        if(!flipper_format_write_uint32(ff, "Temperature", &app_state->temperature, 1)) break;
        if(!flipper_format_write_uint32(ff, "Fan", &app_state->fan, 1)) break;
        if(!flipper_format_write_uint32(ff, "Swing", &app_state->swing, 1)) break;
        if(!flipper_format_write_uint32(ff, "SwingH", &app_state->swing_h, 1)) break;
        if(!flipper_format_write_uint32(ff, "Power", &app_state->power, 1)) break;
        if(!flipper_format_write_uint32(ff, "WindFree", &app_state->windfree, 1)) break;
        success = true;
    } while(false);
    furi_record_close(RECORD_STORAGE);
    flipper_format_free(ff);
    return success;
}

void ac_remote_scene_universal_common_item_callback(void* context, uint32_t index) {
    AC_RemoteApp* ac_remote = context;
    uint32_t event = ac_remote_custom_event_pack(AC_RemoteCustomEventTypeButtonPressed, index);
    view_dispatcher_send_custom_event(ac_remote->view_dispatcher, event);
}

void ac_remote_displayed_temperature(
    const ACRemoteAppSettings* app_state,
    char* buffer,
    size_t buffer_size) {
    if(app_state->mode == HvacSamsungModeFan) {
        snprintf(buffer, buffer_size, "  ");
        return;
    }
    snprintf(buffer, buffer_size, "%ld", app_state->temperature);
}

void ac_remote_scene_samsung_on_enter(void* context) {
    AC_RemoteApp* ac_remote = context;
    ACRemotePanel* ac_remote_panel = ac_remote->ac_remote_panel;

    if(!ac_remote_load_settings(&ac_remote->app_state)) {
        ac_remote->app_state.power = 0;
        ac_remote->app_state.mode = HvacSamsungModeCool;
        ac_remote->app_state.fan = HvacSamsungFanAuto;
        ac_remote->app_state.temperature = HVAC_SAMSUNG_TEMPERATURE_DEFAULT;
        ac_remote->app_state.swing = 0;
        ac_remote->app_state.swing_h = 0;
        ac_remote->app_state.windfree = 0;
    }

    view_stack_add_view(ac_remote->view_stack, ac_remote_panel_get_view(ac_remote_panel));
    ac_remote_panel_reserve(ac_remote_panel, 2, 4);

    ac_remote_panel_add_item(
        ac_remote_panel,
        button_power,
        0,
        0,
        6,
        17,
        power[ac_remote->app_state.power][0],
        power[ac_remote->app_state.power][1],
        ac_remote_scene_universal_common_item_callback,
        NULL,
        context);
    ac_remote_panel_add_icon(ac_remote_panel, 5, 39, &I_power_text_21x5);
    ac_remote_panel_add_item(
        ac_remote_panel,
        button_mode,
        1,
        0,
        39,
        17,
        mode[ac_remote->app_state.mode][0],
        mode[ac_remote->app_state.mode][1],
        ac_remote_scene_universal_common_item_callback,
        NULL,
        context);
    ac_remote_panel_add_icon(ac_remote_panel, 40, 39, &I_mode_text_17x5);
    ac_remote_panel_add_icon(ac_remote_panel, 0, 59, &I_frame_30x39);
    ac_remote_panel_add_item(
        ac_remote_panel,
        button_temp_up,
        0,
        1,
        3,
        47,
        &I_tempup_24x21,
        &I_tempup_hover_24x21,
        ac_remote_scene_universal_common_item_callback,
        NULL,
        context);
    ac_remote_panel_add_item(
        ac_remote_panel,
        button_temp_down,
        0,
        2,
        3,
        89,
        &I_tempdown_24x21,
        &I_tempdown_hover_24x21,
        ac_remote_scene_universal_common_item_callback,
        NULL,
        context);
    ac_remote_panel_add_item(
        ac_remote_panel,
        button_fan,
        1,
        1,
        39,
        50,
        fan[ac_remote->app_state.fan][0],
        fan[ac_remote->app_state.fan][1],
        ac_remote_scene_universal_common_item_callback,
        NULL,
        context);
    ac_remote_panel_add_icon(ac_remote_panel, 43, 72, &I_fan_text_12x5);
    ac_remote_panel_add_item(
        ac_remote_panel,
        button_swing,
        1,
        2,
        39,
        83,
        &I_swing_19x20,
        &I_swing_hover_19x20,
        ac_remote_scene_universal_common_item_callback,
        NULL,
        context);
    ac_remote_panel_add_icon(ac_remote_panel, 38, 105, &I_swing_text_20x5);
    ac_remote_panel_add_item(
        ac_remote_panel,
        button_swing_h,
        0,
        3,
        3,
        114,
        swing_h[ac_remote->app_state.swing_h][0],
        swing_h[ac_remote->app_state.swing_h][1],
        ac_remote_scene_universal_common_item_callback,
        NULL,
        context);
    ac_remote_panel_add_item(
        ac_remote_panel,
        button_windfree,
        1,
        3,
        39,
        114,
        windfree[ac_remote->app_state.windfree][0],
        windfree[ac_remote->app_state.windfree][1],
        ac_remote_scene_universal_common_item_callback,
        NULL,
        context);

    // Heading in two weights. Widths measured from the u8g2 fonts: the bold part
    // is 21 px and the thin one 35 px, so with a 4 px gap the line ends at x=61,
    // same as the heading it replaces.
    ac_remote_panel_add_label(ac_remote_panel, label_title, 1, 11, FontPrimary, "5RC");
    ac_remote_panel_add_label(
        ac_remote_panel, label_title_suffix, 26, 11, FontSecondary, "Gitzmod");

    ac_remote_displayed_temperature(&ac_remote->app_state, buffer, sizeof(buffer));
    ac_remote_panel_add_label(ac_remote_panel, label_temperature, 4, 82, FontKeyboard, buffer);

    view_set_orientation(view_stack_get_view(ac_remote->view_stack), ViewOrientationVertical);
    view_dispatcher_switch_to_view(ac_remote->view_dispatcher, AC_RemoteAppViewStack);
}

void ac_remote_send_state(const ACRemoteAppSettings* settings) {
    furi_assert(settings);

    HvacSamsungPacket packet = hvac_samsung_create_packet();
    hvac_samsung_set_mode(packet, settings->mode);
    hvac_samsung_set_temperature(packet, settings->temperature);
    hvac_samsung_set_fan(packet, settings->fan);
    hvac_samsung_set_swing(packet, settings->swing, settings->swing_h);
    hvac_samsung_set_windfree(packet, settings->windfree);
    hvac_samsung_set_power(packet, settings->power);

    hvac_samsung_send(packet);
    hvac_samsung_free_packet(packet);
}

bool ac_remote_scene_samsung_on_event(void* context, SceneManagerEvent event) {
    AC_RemoteApp* ac_remote = context;
    ACRemotePanel* ac_remote_panel = ac_remote->ac_remote_panel;
    if(event.type != SceneManagerEventTypeCustom) {
        return false;
    }

    uint16_t event_type;
    int16_t event_value;
    ac_remote_custom_event_unpack(event.event, &event_type, &event_value);

    if(event_type == AC_RemoteCustomEventTypeSendSettings) {
        NotificationApp* notifications = furi_record_open(RECORD_NOTIFICATION);
        notification_message(notifications, &sequence_blink_white_100);
        ac_remote_send_state(&ac_remote->app_state);
        notification_message(notifications, &sequence_blink_stop);
        furi_record_close(RECORD_NOTIFICATION);
        return true;
    }

    if(event_type != AC_RemoteCustomEventTypeButtonPressed) {
        return true;
    }

    switch(event_value) {
    case button_power:
        ac_remote->app_state.power = ac_remote->app_state.power ? 0 : 1;
        ac_remote_panel_item_set_icons(
            ac_remote_panel,
            button_power,
            power[ac_remote->app_state.power][0],
            power[ac_remote->app_state.power][1]);
        break;
    case button_mode:
        ac_remote->app_state.mode++;
        if(ac_remote->app_state.mode > HvacSamsungModeAuto) {
            ac_remote->app_state.mode = HvacSamsungModeCool;
        }
        ac_remote_panel_item_set_icons(
            ac_remote_panel,
            button_mode,
            mode[ac_remote->app_state.mode][0],
            mode[ac_remote->app_state.mode][1]);

        ac_remote_displayed_temperature(&ac_remote->app_state, buffer, sizeof(buffer));
        ac_remote_panel_label_set_string(ac_remote_panel, label_temperature, buffer);

        if(!ac_remote->app_state.power) {
            return true;
        }
        break;
    case button_fan:
        ac_remote->app_state.fan++;
        if(ac_remote->app_state.fan > HvacSamsungFanHigh) {
            ac_remote->app_state.fan = HvacSamsungFanAuto;
        }
        ac_remote_panel_item_set_icons(
            ac_remote_panel,
            button_fan,
            fan[ac_remote->app_state.fan][0],
            fan[ac_remote->app_state.fan][1]);

        if(ac_remote->app_state.fan != HvacSamsungFanAuto) {
            ac_remote->app_state.windfree = 0;
            ac_remote_panel_item_set_icons(
                ac_remote_panel,
                button_windfree,
                windfree[ac_remote->app_state.windfree][0],
                windfree[ac_remote->app_state.windfree][1]);
        }

        if(!ac_remote->app_state.power) {
            return true;
        }
        break;
    case button_temp_up:
        if(ac_remote->app_state.mode == HvacSamsungModeFan) {
            return true;
        }
        if(ac_remote->app_state.temperature < HVAC_SAMSUNG_TEMPERATURE_MAX) {
            ac_remote->app_state.temperature++;
            snprintf(buffer, sizeof(buffer), "%ld", ac_remote->app_state.temperature);
            ac_remote_panel_label_set_string(ac_remote_panel, label_temperature, buffer);
        }
        if(!ac_remote->app_state.power) {
            return true;
        }
        break;
    case button_temp_down:
        if(ac_remote->app_state.mode == HvacSamsungModeFan) {
            return true;
        }
        if(ac_remote->app_state.temperature > HVAC_SAMSUNG_TEMPERATURE_MIN) {
            ac_remote->app_state.temperature--;
            snprintf(buffer, sizeof(buffer), "%ld", ac_remote->app_state.temperature);
            ac_remote_panel_label_set_string(ac_remote_panel, label_temperature, buffer);
        }
        if(!ac_remote->app_state.power) {
            return true;
        }
        break;
    case button_swing:
        ac_remote->app_state.swing = ac_remote->app_state.swing ? 0 : 1;
        if(ac_remote->app_state.swing) {
            ac_remote->app_state.windfree = 0;
            ac_remote_panel_item_set_icons(
                ac_remote_panel,
                button_windfree,
                windfree[ac_remote->app_state.windfree][0],
                windfree[ac_remote->app_state.windfree][1]);
        }
        if(!ac_remote->app_state.power) {
            return true;
        }
        break;
    case button_swing_h:
        ac_remote->app_state.swing_h = ac_remote->app_state.swing_h ? 0 : 1;
        ac_remote_panel_item_set_icons(
            ac_remote_panel,
            button_swing_h,
            swing_h[ac_remote->app_state.swing_h][0],
            swing_h[ac_remote->app_state.swing_h][1]);
        if(!ac_remote->app_state.power) {
            return true;
        }
        break;
    case button_windfree:
        ac_remote->app_state.windfree = ac_remote->app_state.windfree ? 0 : 1;
        if(ac_remote->app_state.windfree) {
            // The unit only accepts WindFree with the fan on auto and the vanes
            // parked, so move the rest of the state there too.
            ac_remote->app_state.fan = HvacSamsungFanAuto;
            ac_remote->app_state.swing = 0;
            ac_remote_panel_item_set_icons(
                ac_remote_panel,
                button_fan,
                fan[HvacSamsungFanAuto][0],
                fan[HvacSamsungFanAuto][1]);
        }
        ac_remote_panel_item_set_icons(
            ac_remote_panel,
            button_windfree,
            windfree[ac_remote->app_state.windfree][0],
            windfree[ac_remote->app_state.windfree][1]);
        if(!ac_remote->app_state.power) {
            return true;
        }
        break;
    default:
        break;
    }

    view_dispatcher_send_custom_event(
        ac_remote->view_dispatcher,
        ac_remote_custom_event_pack(AC_RemoteCustomEventTypeSendSettings, 0));
    return true;
}

void ac_remote_scene_samsung_on_exit(void* context) {
    AC_RemoteApp* ac_remote = context;
    ACRemotePanel* ac_remote_panel = ac_remote->ac_remote_panel;
    ac_remote_store_settings(&ac_remote->app_state);
    view_stack_remove_view(ac_remote->view_stack, ac_remote_panel_get_view(ac_remote_panel));
    ac_remote_panel_reset(ac_remote_panel);
}
```

---

## 8. The panel widget

Taken unchanged from the upstream project. A grid of buttons with icon, hover icon and
callback, plus free-floating labels and decorative icons.


### `views/ac_remote_panel.h`

<sub>76 lines</sub>

```c
/**
 * @file ac_remote_panel.h
 * GUI: ACRemotePanel view module API
 */

#pragma once

#include <gui/view.h>

#ifdef __cplusplus
extern "C" {
#endif

/** Button panel module descriptor */
typedef struct ACRemotePanel ACRemotePanel;

typedef struct ButtonItem ButtonItem;

/** Callback type to call for handling selecting ac_remote_panel items */
typedef void (*ButtonItemCallback)(void* context, uint32_t index);

ACRemotePanel* ac_remote_panel_alloc(void);

void ac_remote_panel_free(ACRemotePanel* ac_remote_panel);

void ac_remote_panel_reset(ACRemotePanel* ac_remote_panel);

void ac_remote_panel_reset_selection(ACRemotePanel* ac_remote_panel);

void ac_remote_panel_reserve(ACRemotePanel* ac_remote_panel, size_t reserve_x, size_t reserve_y);

void ac_remote_panel_add_item(
    ACRemotePanel* ac_remote_panel,
    uint16_t index,
    // uint8_t current_value_index,
    // uint8_t values_count,
    uint16_t matrix_place_x,
    uint16_t matrix_place_y,
    uint16_t x,
    uint16_t y,
    const Icon* icon_name,
    const Icon* icon_name_selected,
    ButtonItemCallback callback,
    ButtonItemCallback callback_long,
    void* callback_context);

void ac_remote_panel_item_set_icons(
    ACRemotePanel* ac_remote_panel,
    uint32_t index,
    const Icon* icon_name,
    const Icon* icon_name_selected);

View* ac_remote_panel_get_view(ACRemotePanel* ac_remote_panel);

void ac_remote_panel_add_label(
    ACRemotePanel* ac_remote_panel,
    int index,
    uint16_t x,
    uint16_t y,
    Font font,
    const char* label_str);

void ac_remote_panel_add_icon(
    ACRemotePanel* ac_remote_panel,
    uint16_t x,
    uint16_t y,
    const Icon* icon_name);

void ac_remote_panel_label_set_string(
    ACRemotePanel* ac_remote_panel,
    int index,
    const char* label_str);

#ifdef __cplusplus
}
#endif
```

### `views/ac_remote_panel.c`

<sub>528 lines</sub>

```c
#include "ac_remote_panel.h"

#include <gui/canvas.h>
#include <gui/elements.h>

#include <furi.h>
#include <furi_hal_resources.h>
#include <stdint.h>

#include <m-array.h>
#include <m-i-list.h>
#include <m-list.h>

typedef struct {
    // uint16_t to support multi-screen, wide button panel
    int index;
    uint16_t x;
    uint16_t y;
    Font font;
    const char* str;
} LabelElement;

LIST_DEF(LabelList, LabelElement, M_POD_OPLIST)
#define M_OPL_LabelList_t() LIST_OPLIST(LabelList)

typedef struct {
    uint16_t x;
    uint16_t y;
    const Icon* name;
    const Icon* name_selected;
} IconElement;

LIST_DEF(IconList, IconElement, M_POD_OPLIST)
#define M_OPL_IconList_t() LIST_OPLIST(IconList)

typedef struct ButtonItem {
    uint16_t index;
    ButtonItemCallback callback;
    ButtonItemCallback callback_long;
    IconElement icon;
    void* callback_context;
} ButtonItem;

ARRAY_DEF(ButtonArray, ButtonItem*, M_PTR_OPLIST);
#define M_OPL_ButtonArray_t() ARRAY_OPLIST(ButtonArray, M_PTR_OPLIST)
ARRAY_DEF(ButtonMatrix, ButtonArray_t);
#define M_OPL_ButtonMatrix_t() ARRAY_OPLIST(ButtonMatrix, M_OPL_ButtonArray_t())

struct ACRemotePanel {
    View* view;
};

typedef struct {
    ButtonMatrix_t button_matrix;
    IconList_t icons;
    LabelList_t labels;
    uint16_t reserve_x;
    uint16_t reserve_y;
    uint16_t selected_item_x;
    uint16_t selected_item_y;
} ACRemotePanelModel;

static ButtonItem** ac_remote_panel_get_item(ACRemotePanelModel* model, size_t x, size_t y);
static void ac_remote_panel_process_up(ACRemotePanel* ac_remote_panel);
static void ac_remote_panel_process_down(ACRemotePanel* ac_remote_panel);
static void ac_remote_panel_process_left(ACRemotePanel* ac_remote_panel);
static void ac_remote_panel_process_right(ACRemotePanel* ac_remote_panel);
static void ac_remote_panel_process_ok(ACRemotePanel* ac_remote_panel);
static void ac_remote_panel_process_ok_long(ACRemotePanel* ac_remote_panel);
static void ac_remote_panel_view_draw_callback(Canvas* canvas, void* _model);
static bool ac_remote_panel_view_input_callback(InputEvent* event, void* context);

ACRemotePanel* ac_remote_panel_alloc() {
    ACRemotePanel* ac_remote_panel = malloc(sizeof(ACRemotePanel));
    ac_remote_panel->view = view_alloc();
    view_set_orientation(ac_remote_panel->view, ViewOrientationVertical);
    view_set_context(ac_remote_panel->view, ac_remote_panel);
    view_allocate_model(ac_remote_panel->view, ViewModelTypeLocking, sizeof(ACRemotePanelModel));
    view_set_draw_callback(ac_remote_panel->view, ac_remote_panel_view_draw_callback);
    view_set_input_callback(ac_remote_panel->view, ac_remote_panel_view_input_callback);

    with_view_model(
        ac_remote_panel->view,
        ACRemotePanelModel * model,
        {
            model->reserve_x = 0;
            model->reserve_y = 0;
            model->selected_item_x = 0;
            model->selected_item_y = 0;
            ButtonMatrix_init(model->button_matrix);
            LabelList_init(model->labels);
        },
        true);

    return ac_remote_panel;
}

void ac_remote_panel_reset_selection(ACRemotePanel* ac_remote_panel) {
    with_view_model(
        ac_remote_panel->view,
        ACRemotePanelModel * model,
        {
            model->selected_item_x = 0;
            model->selected_item_y = 0;
        },
        true);
}

void ac_remote_panel_reserve(ACRemotePanel* ac_remote_panel, size_t reserve_x, size_t reserve_y) {
    furi_check(reserve_x > 0);
    furi_check(reserve_y > 0);

    with_view_model(
        ac_remote_panel->view,
        ACRemotePanelModel * model,
        {
            model->reserve_x = reserve_x;
            model->reserve_y = reserve_y;
            ButtonMatrix_reserve(model->button_matrix, reserve_x);
            for(size_t x = 0; x < reserve_x; ++x) {
                ButtonArray_t* array = ButtonMatrix_safe_get(model->button_matrix, x);
                ButtonArray_reserve(*array, reserve_y);
            }
        },
        true);
}

void ac_remote_panel_free(ACRemotePanel* ac_remote_panel) {
    furi_assert(ac_remote_panel);

    ac_remote_panel_reset(ac_remote_panel);

    with_view_model(
        ac_remote_panel->view,
        ACRemotePanelModel * model,
        {
            LabelList_clear(model->labels);
            ButtonMatrix_clear(model->button_matrix);
        },
        true);

    view_free(ac_remote_panel->view);
    free(ac_remote_panel);
}

void ac_remote_panel_reset(ACRemotePanel* ac_remote_panel) {
    furi_assert(ac_remote_panel);

    with_view_model(
        ac_remote_panel->view,
        ACRemotePanelModel * model,
        {
            for(size_t x = 0; x < model->reserve_x; ++x) {
                for(size_t y = 0; y < model->reserve_y; ++y) {
                    ButtonItem** button_item = ac_remote_panel_get_item(model, x, y);
                    free(*button_item);
                    *button_item = NULL;
                }
            }
            model->reserve_x = 0;
            model->reserve_y = 0;
            model->selected_item_x = 0;
            model->selected_item_y = 0;
            LabelList_reset(model->labels);
            IconList_reset(model->icons);
            ButtonMatrix_reset(model->button_matrix);
        },
        true);
}

static ButtonItem** ac_remote_panel_get_item(ACRemotePanelModel* model, size_t x, size_t y) {
    furi_assert(model);

    furi_check(x < model->reserve_x);
    furi_check(y < model->reserve_y);
    ButtonArray_t* button_array = ButtonMatrix_safe_get(model->button_matrix, x);
    ButtonItem** item = ButtonArray_safe_get(*button_array, y);
    return item;
}

void ac_remote_panel_add_item(
    ACRemotePanel* ac_remote_panel,
    uint16_t index,
    uint16_t matrix_place_x,
    uint16_t matrix_place_y,
    uint16_t x,
    uint16_t y,
    const Icon* icon_name,
    const Icon* icon_name_selected,
    ButtonItemCallback callback,
    ButtonItemCallback callback_long,
    void* callback_context) {
    furi_assert(ac_remote_panel);

    with_view_model( //-V773
        ac_remote_panel->view,
        ACRemotePanelModel * model,
        {
            ButtonItem** item_ptr =
                ac_remote_panel_get_item(model, matrix_place_x, matrix_place_y);
            furi_check(*item_ptr == NULL);
            *item_ptr = malloc(sizeof(ButtonItem));
            ButtonItem* item = *item_ptr;
            item->callback = callback;
            item->callback_long = callback_long;
            item->callback_context = callback_context;
            item->icon.x = x;
            item->icon.y = y;
            item->icon.name = icon_name;
            item->icon.name_selected = icon_name_selected;
            item->index = index;
        },
        true);
}

View* ac_remote_panel_get_view(ACRemotePanel* ac_remote_panel) {
    furi_assert(ac_remote_panel);
    return ac_remote_panel->view;
}

static void ac_remote_panel_view_draw_callback(Canvas* canvas, void* _model) {
    furi_assert(canvas);
    furi_assert(_model);

    ACRemotePanelModel* model = _model;

    canvas_clear(canvas);
    canvas_set_color(canvas, ColorBlack);

    for
        M_EACH(icon, model->icons, IconList_t) {
            canvas_draw_icon(canvas, icon->x, icon->y, icon->name);
        }

    for(size_t x = 0; x < model->reserve_x; ++x) {
        for(size_t y = 0; y < model->reserve_y; ++y) {
            ButtonItem* button_item = *ac_remote_panel_get_item(model, x, y);
            if(!button_item) {
                continue;
            }
            const Icon* icon_name = button_item->icon.name;
            if((model->selected_item_x == x) && (model->selected_item_y == y)) {
                icon_name = button_item->icon.name_selected;
            }
            canvas_draw_icon(canvas, button_item->icon.x, button_item->icon.y, icon_name);
        }
    }

    for
        M_EACH(label, model->labels, LabelList_t) {
            canvas_set_font(canvas, label->font);
            canvas_draw_str(canvas, label->x, label->y, label->str);
        }
}

static void ac_remote_panel_process_down(ACRemotePanel* ac_remote_panel) {
    with_view_model(
        ac_remote_panel->view,
        ACRemotePanelModel * model,
        {
            uint16_t new_selected_item_x = model->selected_item_x;
            uint16_t new_selected_item_y = model->selected_item_y;
            size_t i;

            if(new_selected_item_y < (model->reserve_y - 1)) {
                ++new_selected_item_y;

                for(i = 0; i < model->reserve_x; ++i) {
                    new_selected_item_x = (model->selected_item_x + i) % model->reserve_x;
                    if(*ac_remote_panel_get_item(model, new_selected_item_x, new_selected_item_y)) {
                        break;
                    }
                }
                if(i != model->reserve_x) {
                    model->selected_item_x = new_selected_item_x;
                    model->selected_item_y = new_selected_item_y;
                }
            }
        },
        true);
}

static void ac_remote_panel_process_up(ACRemotePanel* ac_remote_panel) {
    with_view_model(
        ac_remote_panel->view,
        ACRemotePanelModel * model,
        {
            size_t new_selected_item_x = model->selected_item_x;
            size_t new_selected_item_y = model->selected_item_y;
            size_t i;

            if(new_selected_item_y > 0) {
                --new_selected_item_y;

                for(i = 0; i < model->reserve_x; ++i) {
                    new_selected_item_x = (model->selected_item_x + i) % model->reserve_x;
                    if(*ac_remote_panel_get_item(model, new_selected_item_x, new_selected_item_y)) {
                        break;
                    }
                }
                if(i != model->reserve_x) {
                    model->selected_item_x = new_selected_item_x;
                    model->selected_item_y = new_selected_item_y;
                }
            }
        },
        true);
}

static void ac_remote_panel_process_left(ACRemotePanel* ac_remote_panel) {
    with_view_model(
        ac_remote_panel->view,
        ACRemotePanelModel * model,
        {
            size_t new_selected_item_x = model->selected_item_x;
            size_t new_selected_item_y = model->selected_item_y;
            size_t i;

            if(new_selected_item_x > 0) {
                --new_selected_item_x;

                for(i = 0; i < model->reserve_y; ++i) {
                    new_selected_item_y = (model->selected_item_y + i) % model->reserve_y;
                    if(*ac_remote_panel_get_item(model, new_selected_item_x, new_selected_item_y)) {
                        break;
                    }
                }
                if(i != model->reserve_y) {
                    model->selected_item_x = new_selected_item_x;
                    model->selected_item_y = new_selected_item_y;
                }
            }
        },
        true);
}

static void ac_remote_panel_process_right(ACRemotePanel* ac_remote_panel) {
    with_view_model(
        ac_remote_panel->view,
        ACRemotePanelModel * model,
        {
            uint16_t new_selected_item_x = model->selected_item_x;
            uint16_t new_selected_item_y = model->selected_item_y;
            size_t i;

            if(new_selected_item_x < (model->reserve_x - 1)) {
                ++new_selected_item_x;

                for(i = 0; i < model->reserve_y; ++i) {
                    new_selected_item_y = (model->selected_item_y + i) % model->reserve_y;
                    if(*ac_remote_panel_get_item(model, new_selected_item_x, new_selected_item_y)) {
                        break;
                    }
                }
                if(i != model->reserve_y) {
                    model->selected_item_x = new_selected_item_x;
                    model->selected_item_y = new_selected_item_y;
                }
            }
        },
        true);
}

void ac_remote_panel_process_ok(ACRemotePanel* ac_remote_panel) {
    ButtonItem* button_item = NULL;

    with_view_model(
        ac_remote_panel->view,
        ACRemotePanelModel * model,
        {
            button_item =
                *ac_remote_panel_get_item(model, model->selected_item_x, model->selected_item_y);
        },
        true);

    if(button_item && button_item->callback) {
        button_item->callback(button_item->callback_context, button_item->index);
    }
}

void ac_remote_panel_process_ok_long(ACRemotePanel* ac_remote_panel) {
    ButtonItem* button_item = NULL;

    with_view_model(
        ac_remote_panel->view,
        ACRemotePanelModel * model,
        {
            button_item =
                *ac_remote_panel_get_item(model, model->selected_item_x, model->selected_item_y);
        },
        true);

    if(button_item && button_item->callback_long) {
        button_item->callback_long(button_item->callback_context, button_item->index);
    }
}

static bool ac_remote_panel_view_input_callback(InputEvent* event, void* context) {
    ACRemotePanel* ac_remote_panel = context;
    furi_assert(ac_remote_panel);
    bool consumed = false;

    if(event->type == InputTypeShort) {
        switch(event->key) {
        case InputKeyUp:
            consumed = true;
            ac_remote_panel_process_up(ac_remote_panel);
            break;
        case InputKeyDown:
            consumed = true;
            ac_remote_panel_process_down(ac_remote_panel);
            break;
        case InputKeyLeft:
            consumed = true;
            ac_remote_panel_process_left(ac_remote_panel);
            break;
        case InputKeyRight:
            consumed = true;
            ac_remote_panel_process_right(ac_remote_panel);
            break;
        case InputKeyOk:
            consumed = true;
            ac_remote_panel_process_ok(ac_remote_panel);
            break;
        default:
            break;
        }
    }

    if(event->type == InputTypeLong) {
        switch(event->key) {
        case InputKeyOk:
            consumed = true;
            ac_remote_panel_process_ok_long(ac_remote_panel);
            break;
        default:
            break;
        }
    }

    return consumed;
}

void ac_remote_panel_add_label(
    ACRemotePanel* ac_remote_panel,
    int index,
    uint16_t x,
    uint16_t y,
    Font font,
    const char* label_str) {
    furi_assert(ac_remote_panel);

    with_view_model(
        ac_remote_panel->view,
        ACRemotePanelModel * model,
        {
            LabelElement* label = LabelList_push_raw(model->labels);
            label->index = index;
            label->x = x;
            label->y = y;
            label->font = font;
            label->str = label_str;
        },
        true);
}

void ac_remote_panel_add_icon(
    ACRemotePanel* ac_remote_panel,
    uint16_t x,
    uint16_t y,
    const Icon* icon_name) {
    furi_assert(ac_remote_panel);

    with_view_model( //-V773
        ac_remote_panel->view,
        ACRemotePanelModel * model,
        {
            IconElement* icon = IconList_push_raw(model->icons);
            icon->x = x;
            icon->y = y;
            icon->name = icon_name;
            icon->name_selected = icon_name;
        },
        true);
}

void ac_remote_panel_item_set_icons(
    ACRemotePanel* ac_remote_panel,
    uint32_t index,
    const Icon* icon_name,
    const Icon* icon_name_selected) {
    furi_assert(ac_remote_panel);

    with_view_model(
        ac_remote_panel->view,
        ACRemotePanelModel * model,
        {
            for(size_t x = 0; x < model->reserve_x; ++x) {
                for(size_t y = 0; y < model->reserve_y; ++y) {
                    ButtonItem** button_item = ac_remote_panel_get_item(model, x, y);
                    ButtonItem* item = *button_item;
                    if(item && item->index == index) {
                        item->icon.name = icon_name;
                        item->icon.name_selected = icon_name_selected;
                    }
                }
            }
        },
        true);
}

void ac_remote_panel_label_set_string(
    ACRemotePanel* ac_remote_panel,
    int index,
    const char* label_str) {
    with_view_model(
        ac_remote_panel->view,
        ACRemotePanelModel * model,
        {
            for
                M_EACH(label, model->labels, LabelList_t) {
                    if(label->index == index) {
                        label->str = label_str;
                    }
                }
        },
        true);
}
```

---

## 9. Icons and assets

### Format

Icons in the `assets/` folder are translated into C code at build time. The file name
determines the symbol name: `windfree_19x11.png` becomes `I_windfree_19x11`.

The converter reduces the PNG to 1 bit and **ignores the alpha channel** while doing so. That
leads to a convention you have to know about:

| Pixel in the PNG | Result on the display |
|---|---|
| opaque white `(255,255,255,255)` | **not** drawn (background) |
| transparent black `(0,0,0,0)` | **drawn** (black) |

So the transparent pixels are the ink. Miss this and your icons come out inverted.

Every button has two states: the normal icon and a `_hover_` icon for selection, usually the
inverted version. The two bottom toggles additionally have an "on" variant each, with a bar
under the glyph — four files per button.

### Generator

The four new icons were not drawn but generated, so that frame and alignment stay exactly
identical to the existing 19x11 buttons:


### `tools/mkicon.py`

Writes `windfree_*` and `swing_h_*` into `assets/`.

<sub>86 lines</sub>

```python
from PIL import Image

W, H = 19, 11
INK = (0, 0, 0, 0)         # transparent black -> drawn as black by the asset compiler
BG = (255, 255, 255, 255)  # opaque white -> not drawn

# Button frame, copied pixel for pixel from the existing 19x11 buttons
# (turbo/led/clean) so the new ones sit flush with them.
FRAME = [
    ".#################.",
    "#.................#",
    "#.................#",
    "#.................#",
    "#.................#",
    "#.................#",
    "#.................#",
    "#.................#",
    "##...............##",
    "###################",
    ".#################.",
]

GLYPHS = {
    "windfree": [  # "WF"
        "#...#.###",
        "#...#.#..",
        "#.#.#.##.",
        "##.##.#..",
        "#...#.#..",
    ],
    "swing_h": [  # left-right double arrow
        "..#.....#..",
        ".##.....##.",
        "###########",
        ".##.....##.",
        "..#.....#..",
    ],
}

# The glyph sits one row higher than centred so the "on" marker below it can be
# two rows tall. A single row reads as a hairline gap once the button is
# selected and the whole icon inverts, which is easy to miss entirely.
GLYPH_Y = 1
BAR_Y = range(6, 8)
BAR_X = range(2, 17)


def build(glyph, on, hover):
    img = Image.new("RGBA", (W, H), BG)
    px = img.load()

    if hover:
        # Selected: the whole button inks over, glyph and bar stay white.
        for y in range(H):
            for x in range(W):
                corner = (x in (0, W - 1)) and (y in (0, H - 1))
                px[x, y] = BG if corner else INK
    else:
        for y, row in enumerate(FRAME):
            for x, c in enumerate(row):
                if c == "#":
                    px[x, y] = INK

    fg = BG if hover else INK
    gx0 = 1 + (17 - len(glyph[0])) // 2
    for gy, row in enumerate(glyph):
        for gx, c in enumerate(row):
            if c == "#":
                px[gx0 + gx, GLYPH_Y + gy] = fg
    if on:
        for y in BAR_Y:
            for x in BAR_X:
                px[x, y] = fg
    return img


for name, glyph in GLYPHS.items():
    for on in (False, True):
        for hover in (False, True):
            fn = name + ("_on" if on else "") + ("_hover" if hover else "") + "_19x11.png"
            build(glyph, on, hover).save("assets/" + fn)
            im = Image.open("assets/" + fn)
            p = im.load()
            print("---", fn)
            for y in range(H):
                print("   " + "".join("#" if p[x, y][3] == 0 else "." for x in range(W)))
```

### Working out text widths in advance

In portrait orientation the display is only **64 pixels wide**. Whether a heading fits can be
determined up front instead of by trial and error: the u8g2 font data sits in the firmware
tree at `lib/u8g2/u8g2_fonts.c`, and the sum of the glyph advances is exactly what
`canvas_string_width()` reports on the device.

`FontPrimary` is `helvB08`, `FontSecondary` is `haxrcorp4089`.

For the heading the measurement gave:

| Text | Font | Width |
|---|---|---|
| `GNUSMAS` (original) | FontPrimary | 55 px, from x=6 → ends at 61 |
| `5RC - Gitzmod` | FontPrimary | 68 px — **does not fit** |
| `5RC` | FontPrimary | 21 px |
| `Gitzmod` | FontSecondary | 35 px |

Hence the final solution: `5RC` bold from x=1, `Gitzmod` thin from x=26, ending at x=61.


### `tools/fontwidth.py`

Reads the font data out of the firmware tree and measures text widths. Two details a naive parser trips over: the position pointers in the header are stored **big-endian**, and the glyph data contains literal `;` and `"`, so the C array cannot be delimited by scanning for a semicolon.

<sub>167 lines</sub>

```python
"""Measure Flipper text widths straight from the u8g2 font data in the firmware.

Mirrors u8g2_font_get_glyph_data() + u8g2_font_decode_glyph(): the advance
(delta x) of every glyph is summed, which is what canvas_string_width() reports.
"""
import io
import re
import sys

# u8g2 font data from a Flipper firmware source tree.
SRC = os.environ.get(
    "U8G2_FONTS",
    os.path.join(os.environ.get("FLIPPER_FW", r"C:\ffw"), "lib", "u8g2", "u8g2_fonts.c"))
HEADER_SIZE = 23

SIMPLE = {"n": 10, "t": 9, "r": 13, "a": 7, "b": 8, "f": 12, "v": 11,
          "\\": 92, '"': 34, "'": 39, "?": 63}


def unescape(literal):
    out = bytearray()
    i = 0
    while i < len(literal):
        c = literal[i]
        if c != "\\":
            out.append(ord(c))
            i += 1
            continue
        n = literal[i + 1]
        if n == "x":
            j = i + 2
            digits = ""
            while j < len(literal) and len(digits) < 2 and literal[j] in "0123456789abcdefABCDEF":
                digits += literal[j]
                j += 1
            out.append(int(digits, 16))
            i = j
        elif n in "01234567":
            j = i + 1
            digits = ""
            while j < len(literal) and len(digits) < 3 and literal[j] in "01234567":
                digits += literal[j]
                j += 1
            out.append(int(digits, 8))
            i = j
        else:
            out.append(SIMPLE[n])
            i += 2
    return bytes(out)


def extract(src, name):
    # The data contains literal ';' and '"' characters, so the array cannot be
    # delimited by scanning for a terminator - walk the string literals instead.
    m = re.search(re.escape(name) + r"\[([0-9]*)\][^=]*=", src)
    if not m:
        sys.exit("font not found: " + name)
    declared = int(m.group(1)) if m.group(1) else None

    literal = re.compile(r'\s*"((?:[^"\\]|\\.)*)"', re.DOTALL)
    data = bytearray()
    pos = m.end()
    while True:
        lit = literal.match(src, pos)
        if not lit:
            break
        data += unescape(lit.group(1))
        pos = lit.end()

    if declared is not None and len(data) + 1 != declared:
        # the declared size counts the trailing NUL of the C string
        sys.exit("%s: got %d bytes, C array declares %d" % (name, len(data), declared))
    return bytes(data)


class Font:
    def __init__(self, data):
        self.d = data
        self.bits_w = data[4]
        self.bits_h = data[5]
        self.bits_x = data[6]
        self.bits_y = data[7]
        self.bits_dx = data[8]
        # stored big-endian, high byte first
        self.start_upper_A = (data[17] << 8) | data[18]
        self.start_lower_a = (data[19] << 8) | data[20]

    def _bits(self, count):
        v = 0
        for i in range(count):
            v |= ((self.d[self.pos] >> self.bit) & 1) << i
            self.bit += 1
            if self.bit == 8:
                self.bit = 0
                self.pos += 1
        return v

    def _signed(self, count):
        return self._bits(count) - (1 << (count - 1))

    def advance(self, ch):
        code = ord(ch)
        p = HEADER_SIZE
        if code >= ord("a"):
            p += self.start_lower_a
        elif code >= ord("A"):
            p += self.start_upper_A
        while True:
            if self.d[p + 1] == 0:
                return None
            if self.d[p] == code:
                self.pos, self.bit = p + 2, 0
                self._bits(self.bits_w)
                self._bits(self.bits_h)
                self._signed(self.bits_x)
                self._signed(self.bits_y)
                return self._signed(self.bits_dx)
            p += self.d[p + 1]

    def width(self, s):
        total = 0
        for ch in s:
            a = self.advance(ch)
            if a is None:
                return None
            total += a
        return total


src = io.open(SRC, encoding="utf-8", errors="replace").read()

SCREEN_W = 64
PRIMARY = Font(extract(src, "u8g2_font_helvB08_tr"))
SECONDARY = Font(extract(src, "u8g2_font_haxrcorp4089_tr"))

# sanity check against the heading the app shipped with
print("check: 'GNUSMAS' FontPrimary = %d px at x=6 -> ends at %d (screen %d)\n"
      % (PRIMARY.width("GNUSMAS"), 6 + PRIMARY.width("GNUSMAS"), SCREEN_W))

# each candidate is a list of (font name, font, text) segments laid out in a row
LAYOUTS = [
    [("bold", PRIMARY, "5RC"), ("thin", SECONDARY, " - Gitzmod")],
    [("bold", PRIMARY, "5RC"), ("thin", SECONDARY, " Gitzmod")],
    [("bold", PRIMARY, "5RC -"), ("thin", SECONDARY, " Gitzmod")],
    [("bold", PRIMARY, "5RC"), ("thin", SECONDARY, "-Gitzmod")],
]

for segments in LAYOUTS:
    for start_x in (1, 2, 3):
        x = start_x
        parts, ok = [], True
        for kind, font, text in segments:
            w = font.width(text)
            if w is None:
                ok = False
                break
            parts.append("%s %r at x=%d (%d px)" % (kind, text, x, w))
            x += w
        if not ok:
            continue
        if start_x != 1 and x > SCREEN_W:
            continue
        verdict = "fits, %d px to spare" % (SCREEN_W - x) if x <= SCREEN_W \
            else "TOO WIDE by %d px" % (x - SCREEN_W)
        print("%-58s ends at x=%2d   %s" % (" + ".join(parts), x, verdict))
        break
    print()
```

---

## 10. Building and installing

### Build

```powershell
$env:FBT_NOENV   = "1"
$env:FBT_NO_SYNC = "1"
$env:PATH = "$env:USERPROFILE\.ufbt\toolchain\x86_64-windows\bin;$env:PATH"
cd C:\ffw
.\fbt.cmd fap_samsung_ac_remote
```

Result: `build\f7-firmware-D\.extapps\samsung_ac_remote.fap`

The run ends with `APPCHK` reporting `Target: 7, API: 87.1` — that is the assurance that
build and firmware match.

### Install

```powershell
python scripts\runfap.py -p COM4 `
    -s build\f7-firmware-D\.extapps\samsung_ac_remote.fap `
    -t /ext/apps/Infrared/samsung_ac_remote.fap
```

That copies the file and launches the app directly. Alternatively just drag the `.fap` to
`apps/Infrared/` on the SD card with qFlipper.

> **The COM port has to be free.** Anything holding a serial connection to the Flipper blocks
> this: qFlipper, a terminal — and, less obviously, a browser tab with a Flipper web tool.
> The Web Serial API holds the port and **grabs it again automatically after every replug**.

---

## 11. Taking screenshots

The firmware can stream its framebuffer over RPC (`Gui.StartScreenStream`). There is no
ready-made tool for it, but one is quickly built.

**Flow:** open the serial connection, send `start_rpc_session`, then exchange
length-prefixed protobuf messages. The protocol definitions sit in the firmware tree under
`assets/protobuf/`:

```bash
cd <fw>/assets/protobuf
python -m grpc_tools.protoc -I. --python_out=<target> *.proto
```

A frame is 1024 bytes in u8g2 page layout: byte `x + (y / 8) * 128`, bit `y % 8`. If the app
runs in portrait, the frame reports `orientation = 2` and has to be rotated by 90°.

**Two stumbling blocks:**

- The CLI command has to be terminated with **a single `\r`**. An extra `\n` lands in the
  RPC stream as `0x0A`, gets read there as a length prefix, and desynchronises everything
  after it.
- **Injecting key presses over RPC is unreliable.** On this firmware the events partly did
  not arrive at all. Deterministic screenshots are produced differently: write the settings
  file, restart the app, grab the frame. The app loads its settings on start, which makes
  every state exactly reproducible.


### `tools/flipshot.py`

RPC client and PNG output.

<sub>155 lines</sub>

```python
"""Grab Flipper Zero screenshots over the RPC screen stream.

The firmware mirrors its framebuffer through Gui.StartScreenStream once an RPC
session is open on the serial CLI. Frames are 1024 bytes in u8g2 page layout:
byte (x + (y // 8) * 128), bit (y % 8).
"""
import os
import sys
import time

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "pb"))

import serial
from PIL import Image

import flipper_pb2
import gui_pb2

WIDTH, HEIGHT = 128, 64
PORT = os.environ.get("FLIPPER_PORT", "COM4")

KEYS = {"up": 0, "down": 1, "right": 2, "left": 3, "ok": 4, "back": 5}
PRESS, RELEASE, SHORT, LONG = 0, 1, 2, 3


def varint_encode(value):
    out = bytearray()
    while True:
        b = value & 0x7F
        value >>= 7
        if value:
            out.append(b | 0x80)
        else:
            out.append(b)
            return bytes(out)


class Rpc:
    def __init__(self, port=PORT):
        self.s = serial.Serial(port, timeout=5)
        time.sleep(0.5)
        self.s.read(8192)  # CLI banner
        self.s.write(b"\r")
        time.sleep(0.4)
        self.s.read(8192)  # prompt
        # Single CR only: a trailing LF would land in the RPC stream as a
        # bogus length prefix and desynchronise everything after it.
        self.s.write(b"start_rpc_session\r")
        time.sleep(0.8)
        self.s.read(8192)  # command echo, last plain-text output
        self.cmd_id = 0

    def close(self):
        try:
            self.s.close()
        except Exception:
            pass

    def _read_exact(self, n):
        buf = bytearray()
        while len(buf) < n:
            chunk = self.s.read(n - len(buf))
            if not chunk:
                raise TimeoutError("short read: wanted %d, got %d" % (n, len(buf)))
            buf += chunk
        return bytes(buf)

    def _read_varint(self):
        value, shift = 0, 0
        while True:
            b = self._read_exact(1)[0]
            value |= (b & 0x7F) << shift
            if not b & 0x80:
                return value
            shift += 7

    def send(self, field, value=None):
        self.cmd_id += 1
        msg = flipper_pb2.Main()
        msg.command_id = self.cmd_id
        sub = getattr(msg, field)
        sub.SetInParent()  # marks the oneof even for empty request messages
        if value is not None:
            sub.CopyFrom(value)
        data = msg.SerializeToString()
        self.s.write(varint_encode(len(data)) + data)
        self.s.flush()

    def recv(self):
        size = self._read_varint()
        msg = flipper_pb2.Main()
        msg.ParseFromString(self._read_exact(size))
        return msg

    def start_screen_stream(self):
        self.send("gui_start_screen_stream_request")

    def stop_screen_stream(self):
        self.send("gui_stop_screen_stream_request")

    def next_frame(self, timeout=8.0):
        deadline = time.time() + timeout
        while time.time() < deadline:
            msg = self.recv()
            if msg.WhichOneof("content") == "gui_screen_frame":
                return msg.gui_screen_frame
        raise TimeoutError("no screen frame received")

    def key(self, name, kind=SHORT):
        # A real button gives press then release; the firmware synthesises the
        # short/long event from that pair, so send the pair, not the shortcut.
        for event in ((PRESS, RELEASE) if kind == SHORT else (PRESS, kind, RELEASE)):
            req = gui_pb2.SendInputEventRequest()
            req.key = KEYS[name]
            req.type = event
            self.send("gui_send_input_event_request", req)
            time.sleep(0.05)
        time.sleep(0.25)


def frame_to_image(frame, scale=4):
    data = frame.data
    img = Image.new("1", (WIDTH, HEIGHT), 1)  # 1 = white
    px = img.load()
    for y in range(HEIGHT):
        page = y // 8
        bit = y % 8
        for x in range(WIDTH):
            if (data[x + page * WIDTH] >> bit) & 1:
                px[x, y] = 0  # black
    # ScreenOrientation: 0/1 horizontal, 2/3 vertical
    if frame.orientation in (2, 3):
        img = img.transpose(Image.ROTATE_270 if frame.orientation == 2 else Image.ROTATE_90)
    img = img.convert("L")
    return img.resize((img.width * scale, img.height * scale), Image.NEAREST)


def grab(rpc, path, settle=0.6, scale=4):
    time.sleep(settle)
    rpc.start_screen_stream()
    frame = rpc.next_frame()
    rpc.stop_screen_stream()
    img = frame_to_image(frame, scale)
    img.save(path)
    print("saved %s  (%dx%d, orientation=%d)" % (path, img.width, img.height, frame.orientation))
    return frame


if __name__ == "__main__":
    out = sys.argv[1] if len(sys.argv) > 1 else "shot.png"
    rpc = Rpc()
    try:
        grab(rpc, out)
    finally:
        rpc.close()
```

### `tools/capture_states.py`

Drives every documented state through the settings file.

<sub>123 lines</sub>

```python
"""Deterministic app screenshots.

Instead of injecting key presses (which proved unreliable), each shot writes the
app's settings file, relaunches the app so it loads them, and grabs the frame.
Key order in the file must match the order ac_remote_load_settings() reads them.
"""
import os
import subprocess
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

from flipshot import Rpc, frame_to_image  # noqa: E402

# Point FLIPPER_FW at a firmware source tree and FLIPPER_PORT at the device.
FW = os.environ.get("FLIPPER_FW", r"C:\ffw")
STORAGE = os.path.join(FW, "scripts", "storage.py")
PORT = os.environ.get("FLIPPER_PORT", "COM4")
APP = "/ext/apps/Infrared/samsung_ac_remote.fap"
DATA_DIR = "/ext/apps_data/samsung_ac_remote"
SETTINGS = DATA_DIR + "/settings.txt"

OUT = os.path.join(HERE, "shots2")
os.makedirs(OUT, exist_ok=True)
TMP = os.path.join(HERE, "settings.txt")

COOL, HEAT, DRY, FAN, AUTO = 0, 1, 2, 3, 4
F_AUTO, F_LOW, F_MED, F_HIGH = 0, 1, 2, 3


def storage(*args):
    subprocess.run([sys.executable, STORAGE, "-p", PORT] + list(args),
                   check=False, capture_output=True)


def cli(command, settle=1.2):
    import serial
    s = serial.Serial(PORT, timeout=3)
    time.sleep(0.4)
    s.read(8192)
    s.write(b"\r")
    time.sleep(0.3)
    s.read(8192)
    s.write(command.encode() + b"\r")
    time.sleep(settle)
    out = s.read(8192)
    s.close()
    return out


def write_settings(mode, temp, fan, swing, swing_h, power, windfree):
    body = (
        "Filetype: Samsung AC Remote\n"
        "Version: 1\n"
        "# \n"
        "Mode: %d\n"
        "Temperature: %d\n"
        "Fan: %d\n"
        "Swing: %d\n"
        "SwingH: %d\n"
        "Power: %d\n"
        "WindFree: %d\n"
    ) % (mode, temp, fan, swing, swing_h, power, windfree)
    with open(TMP, "w", newline="\n") as f:
        f.write(body)
    storage("mkdir", DATA_DIR)
    storage("send", TMP, SETTINGS)


def shot(name, **state):
    cli("loader close")
    write_settings(**state)
    cli("loader open " + APP, settle=2.0)
    time.sleep(0.5)

    rpc = Rpc()
    try:
        rpc.start_screen_stream()
        frame = rpc.next_frame()
        rpc.stop_screen_stream()
    finally:
        rpc.close()
    path = os.path.join(OUT, name + ".png")
    frame_to_image(frame, scale=4).save(path)
    print("  " + name + ".png")


BASE = dict(mode=COOL, temp=24, fan=F_AUTO, swing=0, swing_h=0, power=0, windfree=0)


def variant(**kw):
    d = dict(BASE)
    d.update(kw)
    return d


SHOTS = [
    ("app-overview", variant()),
    ("power-on", variant(power=1)),
    ("mode-cool", variant(mode=COOL)),
    ("mode-heat", variant(mode=HEAT)),
    ("mode-dry", variant(mode=DRY)),
    ("mode-fan", variant(mode=FAN)),
    ("mode-auto", variant(mode=AUTO)),
    ("fan-auto", variant(fan=F_AUTO)),
    ("fan-low", variant(fan=F_LOW)),
    ("fan-med", variant(fan=F_MED)),
    ("fan-high", variant(fan=F_HIGH)),
    ("swing-h-on", variant(swing_h=1)),
    ("windfree-on", variant(windfree=1)),
    ("temp-30", variant(temp=30)),
]

if __name__ == "__main__":
    only = sys.argv[1:]
    for name, state in SHOTS:
        if only and name not in only:
            continue
        shot(name, **state)
    # leave the app in the documented default state
    shot("_final", **BASE)
```

---

## 12. Pitfalls

Collected while building this app — all of them cost time.

**Protocol**

- Power appears **twice** in the frame (bytes 6 and 13).
- Vertical and horizontal swing share **one** 3-bit field. Writing one axis alone clears the
  other.
- WindFree needs fan on auto and vertical swing off, otherwise the unit ignores it.
- The checksum skips the checksum nibbles themselves and is spread across two nibbles in
  **different** bytes.
- The 16–30 °C range is the **protocol's**. Real units are often narrower — this one only
  cools from 18 °C and silently discards frames below that. It then looks as if only the fan
  were running.

**Assets**

- The transparent pixels are the ink, not the white ones.
- A build against a fork SDK can drop icons as "duplicates" if the fork ships them in its
  firmware. On official firmware they are then missing → `MissingImports`. Always check
  against the API table of the firmware that actually runs on the device.

**User interface**

- Labels only store the **pointer** to their text. No stack buffers.
- With a portrait view the firmware **rotates the D-pad along with it**: what the widget sees
  as "down" is physically "right" (`view_port_input_mapping` in `view_port.c`).
- 64 px of width fill up fast. Work out text widths from the font data beforehand.

**Tooling**

- The COM port tends to be held by something else — qFlipper, a terminal, or a browser tab
  using Web Serial.
- Be careful with cleanup in scripts: a recorder that wipes its output directory on startup
  will delete the recordings of a still-running first instance when a second one fails to
  start.

---

## 13. Licensing and provenance

This app is **MIT** licensed. See `LICENSE` in the app directory — the copyright line comes
from the upstream project (Lia Yoffe).

| Project | License | What was taken from it |
|---|---|---|
| [dappermint/samsung-ac-remote-flipper-app](https://github.com/dappermint/samsung-ac-remote-flipper-app) | MIT | The app this is forked from: scene structure, protocol library, icon assets |
| [xakep666/flipperzero-midea-ac-remote](https://github.com/xakep666/flipperzero-midea-ac-remote) | MIT | The panel widget (`views/ac_remote_panel.*`), by way of the project above |
| [crankyoldgit/IRremoteESP8266](https://github.com/crankyoldgit/IRremoteESP8266) | LGPL-2.1 | Protocol knowledge from `ir_Samsung.cpp`: field layout, timings, the reset template bytes and the checksum algorithm, plus the captured frames used for verification |
| [flipperdevices/flipperzero-firmware](https://github.com/flipperdevices/flipperzero-firmware) | GPL-3.0 | SDK, headers and API table to build against |
| [olikraus/u8g2](https://github.com/olikraus/u8g2) | BSD 2-clause, fonts under separate terms | Font data, read at build time only to measure text widths |
| [xpack-dev-tools/arm-none-eabi-gcc-xpack](https://github.com/xpack-dev-tools/arm-none-eabi-gcc-xpack) | MIT packaging, GCC itself GPL-3.0 with runtime exception | Build toolchain |

**Two things worth being precise about:**

The upstream README states the panel code was adapted from a GPL project. That is **not
correct** — `flipperzero-midea-ac-remote` is MIT licensed, as is the `LICENSE` file shipped
with this project. The claim has been corrected here.

The relationship to IRremoteESP8266 deserves a careful reading rather than a confident
one-liner. No source was copied line by line; `lib/hvac_samsung/` was written in C from their
documented findings. But the constants themselves — timings, the 14-byte reset template, the
checksum construction — do originate there, and IRremoteESP8266 is LGPL-2.1. Whether that
makes this a derivative work is a judgement call, not something this document settles. If you
plan to redistribute, look at it deliberately.

Only the firmware SDK and the toolchain are involved at build time; nothing from them is
linked into the `.fap`, which resolves the firmware API at load time on the device.
