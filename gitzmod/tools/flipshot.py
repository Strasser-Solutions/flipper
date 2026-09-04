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
