#!/usr/bin/env python3
"""Decode a Broadcom PEX880xx ("Atlas") configuration image (SBR), or find SBRs inside a bigger file.

Works on a bare SBR, on a flash dump (SBR at 0x400), or on a vendor firmware file (scans for every
valid SBR: magic c0103dc4, consistent section offsets, checksum byte correct). Offline, no root.

    python3 -I sbr_info.py images/sbr-x16-A.bin
    python3 -I sbr_info.py flash-dumps/pex-flash-x4-20261117-011830.bin
    python3 -I sbr_info.py some_vendor_firmware.fw --extract found.bin
"""
import argparse, struct, sys

MAGIC = struct.pack("<I", 0xC0103DC4)
WIDTH = {0: "x16", 1: "x4", 7: "x8"}
PHY_PREFIX = (0x7F, 0x7B, 0x73)


def u32(b, o):
    return struct.unpack_from("<I", b, o)[0]


def sbr_len(data, off):
    """Length of a valid SBR starting at off, or None."""
    if data[off:off + 4] != MAGIC or off + 0x1FC > len(data):
        return None
    bo, bl = struct.unpack_from("<II", data, off + 4)
    co, cl = struct.unpack_from("<II", data, off + 0x4C)
    n = co + cl + 4
    if bo != 0x1FC or bo + bl != co or bl % 8 or cl % 8 or n > 0x20000 or off + n > len(data):
        return None
    return n if (0xA5 + sum(data[off:off + n])) & 0xFF == 0 else None


def find_all(data):
    hits, i = [], data.find(MAGIC)
    while i != -1:
        n = sbr_len(data, i)
        if n:
            hits.append((i, n))
        i = data.find(MAGIC, i + 1)
    return hits


def groups(sbr):
    v = int.from_bytes(sbr[0x5C:0x68], "little")
    return [(v >> (16 + 3 * i)) & 7 for i in range(24)]


def show(sbr):
    n = len(sbr)
    bo, bl = struct.unpack_from("<II", sbr, 4)
    co, cl = struct.unpack_from("<II", sbr, 0x4C)
    print(f"  length {n:#x} bytes, checksum byte {sbr[-4]:#04x} (0xA5 + byte sum = 0 mod 256: OK)")
    g = groups(sbr)
    print("  port-width codes per station (one 3-bit code per x4 lane group; 0=x16 1=x4 7=x8):")
    for st in range(6):
        c = g[4 * st:4 * st + 4]
        if c == [0] * 4:
            desc = "one x16 port"
        elif c == [7] * 4:
            desc = "two x8 ports"
        else:
            desc = " ".join(WIDTH.get(x, f"code{x}") for x in c)
        print(f"    station {st} (lanes {16 * st:2d}-{16 * st + 15:2d}): {c}  -> {desc}")
    soc = [(o, u32(sbr, o)) for o in range(0x5C, 0x1FC, 4) if u32(sbr, o)]
    print("  SoC settings block, non-zero words (offset from the magic):")
    for i in range(0, len(soc), 4):
        print("    " + "   ".join(f"+{o:03x} {v:08x}" for o, v in soc[i:i + 4]))
    print(f"  section B (per-port register writes): {bl // 8} entries")
    for i in range(0, bl, 8):
        v, c = struct.unpack_from("<II", sbr, bo + i)
        b = (c & 0xFFFFFF) * 4
        print(f"    port {b >> 12:3d} +{b & 0xFFF:03x} = {v:08x}   (code {c >> 24:02x})")
    C = [struct.unpack_from("<II", sbr, co + i) for i in range(0, cl, 8)]
    phy = sum(1 for a, _ in C if a >> 24 in PHY_PREFIX)
    print(f"  section C (address, value writes): {len(C)} entries, {phy} PHY/SerDes (0x7F/0x7B/0x73...), others:")
    for a, v in C:
        if a >> 24 not in PHY_PREFIX:
            print(f"    {a:08x} = {v:08x}")


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("file")
    ap.add_argument("--extract", metavar="OUT", help="write the (first) SBR found to OUT")
    a = ap.parse_args()
    data = open(a.file, "rb").read()
    hits = find_all(data)
    if not hits:
        sys.exit("no valid SBR found (magic c0103dc4 + consistent header + checksum)")
    for off, n in hits:
        print(f"SBR at file offset {off:#x}:")
        show(data[off:off + n])
    if a.extract:
        off, n = hits[0]
        open(a.extract, "wb").write(data[off:off + n])
        print(f"wrote {a.extract} ({n} bytes from offset {off:#x})")


if __name__ == "__main__":
    main()
