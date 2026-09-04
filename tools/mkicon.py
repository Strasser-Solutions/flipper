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

GLYPH_Y = 2
# "on" marker: a bar on the last free interior row, under the glyph
BAR_Y = 7
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
        for x in BAR_X:
            px[x, BAR_Y] = fg
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
