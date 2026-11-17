#!/usr/bin/env python3
"""Build candidate x16 configuration images (SBRs) for the PEX88096 board. Offline, no root.

What is known (README, 17 Nov):
  - The SBR header packs one 3-bit port-width code per x4 lane group, 24 groups (6 stations x 4),
    starting at header bit 16 of word 0x45C (flash offset) and running through word 0x464.
  - The boot firmware copies them to live registers port0 +0x300..+0x308 (16 bits per station).
  - Codes seen: 1 = x4 (x4 image), 7 = x8 (x8x8 image), 0 = x16 (station 0, the upstream port, in
    both images: it runs x16. With no image at all every port is x4).
  - Trailer: one checksum byte making the byte sum of the whole SBR = 0x5B (mod 256).

Both candidates start from the x8x8 image (the configuration this board normally runs) and set the
20 downstream group codes (stations 1-5) from 7 to 0, exactly as Lenovo's PEX88096 GPU switch card
(ThinkSystem SR670 V2, 4 GPUs at x16; see docs/sources.md) has them. Lenovo's 280 PHY/SerDes writes and its
port-0 register writes are identical to this board's, so nothing else changes. The candidates differ in
one header pair, 0x484/0x488 (x8x8: 0x411 / 0x000C0000), meaning unknown; Lenovo's x16 image and this
board's own x4 image both have it at 0:
  A: zero it, as Lenovo's x16 image and this board's own working x4 image   (try first)
  B: keep the x8x8 values                                                   (fallback)
The section-B config-date word (port0 +0x20C, BCD MMDDYYYY: 06042019 is Broadcom's reference value, also
in Lenovo's image; this board's x4 build uses 02242021) is set to a value that names the
variant, so the live register shows which image booted:
  A: 0x11172026      B: 0x11182026

    python3 -I make_x16_sbr.py          # writes images/sbr-x16-A.bin and images/sbr-x16-B.bin
"""
import hashlib, os, struct, sys

HERE = os.path.dirname(os.path.abspath(__file__))
SRC = os.path.join(HERE, "flash-dumps", "sbr-x8x8-0x400-0xF20.bin")
SRC_SHA = "d479e07e"            # prefix recorded when it was cut from the x8x8 dump
X4 = os.path.join(HERE, "flash-dumps", "sbr-x4-0x400-0xF58.bin")
OUT = os.path.join(HERE, "images")
MAGIC, SUM = 0xC0103DC4, 0x5B
FIELDS_AT, FIELD_BIT0, NGROUPS = 0x5C, 16, 24      # SBR-relative offset of word 0x45C
STAMP_IDX = 0x83                                    # section B dword index -> port0 +0x20C
VARIANTS = {"A": dict(keep_484=False, stamp=0x11172026), "B": dict(keep_484=True, stamp=0x11182026)}


def u32(b, o):
    return struct.unpack_from("<I", b, o)[0]


def groups(sbr):
    v = int.from_bytes(sbr[FIELDS_AT:FIELDS_AT + 12], "little")
    return [(v >> (FIELD_BIT0 + 3 * i)) & 7 for i in range(NGROUPS)]


def set_groups(sbr, codes):
    v = int.from_bytes(sbr[FIELDS_AT:FIELDS_AT + 12], "little")
    for i, c in enumerate(codes):
        sh = FIELD_BIT0 + 3 * i
        v = (v & ~(7 << sh)) | (c << sh)
    sbr[FIELDS_AT:FIELDS_AT + 12] = v.to_bytes(12, "little")


def section(sbr, hdr):
    off, ln = struct.unpack_from("<II", sbr, hdr)
    return off, ln


def fix_checksum(sbr):
    sbr[-4:] = b"\0\0\0\0"
    sbr[-4] = (SUM - sum(sbr)) & 0xFF


def check(sbr):
    assert u32(sbr, 0) == MAGIC, "magic"
    assert sum(sbr) & 0xFF == SUM, "checksum"
    bo, bl = section(sbr, 4)
    co, cl = section(sbr, 0x4C)
    assert bo + bl == co and co + cl + 4 == len(sbr), "section layout"


def main():
    src = bytearray(open(SRC, "rb").read())
    if not hashlib.sha256(src).hexdigest().startswith(SRC_SHA):
        sys.exit(f"{SRC} is not the backed-up x8x8 SBR (sha256 should start {SRC_SHA})")
    check(src)
    g = groups(src)
    if g != [0] * 4 + [7] * 20:
        sys.exit(f"unexpected group codes in the x8x8 SBR: {g}")
    if groups(open(X4, "rb").read()) != [0] * 4 + [1] * 20:
        sys.exit("unexpected group codes in the x4 SBR")
    bo, bl = section(src, 4)
    stamp_pos = [bo + i for i in range(0, bl, 8) if u32(src, bo + i + 4) & 0xFFFFFF == STAMP_IDX]
    if len(stamp_pos) != 1 or u32(src, stamp_pos[0]) != 0x06042019:
        sys.exit("could not find the single config-date entry in section B")
    os.makedirs(OUT, exist_ok=True)
    for name, v in VARIANTS.items():
        img = bytearray(src)
        set_groups(img, [0] * 24)
        if not v["keep_484"]:
            struct.pack_into("<II", img, 0x84, 0, 0)
        struct.pack_into("<I", img, stamp_pos[0], v["stamp"])
        fix_checksum(img)
        check(img)
        assert groups(img) == [0] * 24
        diffs = [o for o in range(0, len(img), 4) if img[o:o + 4] != src[o:o + 4]]
        path = os.path.join(OUT, f"sbr-x16-{name}.bin")
        with open(path, "wb") as f:
            f.write(img)
        print(f"{path}: {len(img)} bytes, sha256 {hashlib.sha256(img).hexdigest()[:16]}..., "
              f"checksum byte {img[-4]:#04x}")
        for o in diffs:
            print(f"   flash {0x400 + o:#06x}: {u32(src, o):08x} -> {u32(img, o):08x}")


if __name__ == "__main__":
    main()
