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

FW = r"C:\Users\CHRIST~1.SCH\AppData\Local\Temp\ffw"
STORAGE = os.path.join(FW, "scripts", "storage.py")
PORT = "COM4"
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
