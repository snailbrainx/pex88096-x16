#!/usr/bin/env python3
"""Build an UNTESTED mixed-width image: some connector pairs x16, others x4 x4 x4 x4. Offline, no root.

No image built by this script has been booted. See docs/mixed-widths.md for what is known and what is not.

Each downstream station (connector pairs A-E = stations 1-5, 16 lanes each) gets its own width. The script
starts from images/sbr-x16-A.bin, the image that works on this board, and changes only:
  - the width codes of the stations you ask for, from 0 (one x16 port) to 1 (four x4 ports)
  - the config-date word (port 0 +0x20C) to 0x11202026, so the live register shows a mixed image booted
  - the checksum byte
Station 0 (the uplink to the host) stays x16.

    python3 -I make_mixed_sbr.py                              # example: A-D x16, E four x4
    python3 -I make_mixed_sbr.py --layout x16,x16,x16,x4,x4   # A-C x16, D and E four x4 each
"""
import argparse, hashlib, os, struct, sys
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from make_x16_sbr import OUT, STAMP_IDX, groups, set_groups, section, fix_checksum, check, u32  # noqa: E402

BASE = os.path.join(OUT, "sbr-x16-A.bin")
BASE_SHA = "946a720794497755"
BASE_STAMP, STAMP = 0x11172026, 0x11202026
CODES = {"x16": [0] * 4, "x4": [1] * 4}
PAIRS = "ABCDE"


def parse_layout(text):
    layout = ["x4" if w == "x4x4x4x4" else w for w in (w.strip().lower() for w in text.split(","))]
    if len(layout) != 5 or any(w not in CODES for w in layout):
        raise ValueError("the layout needs five comma-separated widths, pairs A-E in order, each x16 or x4")
    if "x4" not in layout:
        raise ValueError("all x16 is images/sbr-x16-A.bin itself; nothing to build")
    return layout


def build(layout):
    base = bytearray(open(BASE, "rb").read())
    if not hashlib.sha256(base).hexdigest().startswith(BASE_SHA):
        raise ValueError(f"{BASE} is not our x16-A image (sha256 should start {BASE_SHA})")
    check(base)
    if groups(base) != [0] * 24:
        raise ValueError("unexpected width codes in the x16-A image")
    bo, bl = section(base, 4)
    pos = [bo + i for i in range(0, bl, 8) if u32(base, bo + i + 4) & 0xFFFFFF == STAMP_IDX]
    if len(pos) != 1 or u32(base, pos[0]) != BASE_STAMP:
        raise ValueError("could not find the single config-date entry of the x16-A image")
    codes = CODES["x16"] + sum((CODES[w] for w in layout), [])
    img = bytearray(base)
    set_groups(img, codes)
    struct.pack_into("<I", img, pos[0], STAMP)
    fix_checksum(img)
    check(img)
    assert groups(img) == codes
    return base, img


def main():
    ap = argparse.ArgumentParser(description="Build an untested mixed x16/x4 image from images/sbr-x16-A.bin.")
    ap.add_argument("--layout", default="x16,x16,x16,x16,x4",
                    help="widths of pairs A-E, comma-separated, each x16 or x4 (default: x16,x16,x16,x16,x4)")
    a = ap.parse_args()
    try:
        layout = parse_layout(a.layout)
        base, img = build(layout)
    except ValueError as e:
        sys.exit(str(e))
    path = os.path.join(OUT, "sbr-mixed-" + "-".join(layout) + ".bin")
    with open(path, "wb") as f:
        f.write(img)
    print(f"{path}: {len(img)} bytes, sha256 {hashlib.sha256(img).hexdigest()[:16]}..., "
          f"checksum byte {img[-4]:#04x}")
    print("UNTESTED: no image like this has been booted. Read docs/mixed-widths.md before writing it.")
    for s, w in enumerate(layout, 1):
        ports = [16 * s] if w == "x16" else [16 * s + 4 * k for k in range(4)]
        print(f"   pair {PAIRS[s - 1]} (station {s}, lanes {16 * s}-{16 * s + 15}): "
              f"{'one x16 port, switch port' if w == 'x16' else 'four x4 ports, switch ports'} "
              f"{', '.join(map(str, ports))}")
    for o in range(0, len(img), 4):
        if img[o:o + 4] != base[o:o + 4]:
            print(f"   flash {0x400 + o:#06x}: {u32(base, o):08x} -> {u32(img, o):08x}")


if __name__ == "__main__":
    main()
