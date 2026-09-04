"""Measure Flipper text widths straight from the u8g2 font data in the firmware.

Mirrors u8g2_font_get_glyph_data() + u8g2_font_decode_glyph(): the advance
(delta x) of every glyph is summed, which is what canvas_string_width() reports.
"""
import io
import re
import sys

SRC = r"C:\Users\CHRIST~1.SCH\AppData\Local\Temp\ffw\lib\u8g2\u8g2_fonts.c"
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
