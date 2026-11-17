#!/usr/bin/env python3
"""Read-only dump of the PEX88096 switch's SPI flash through its memory window.

Broadcom's switch SDK (PLX SDK 9.81, PlxApi/SpiFlash.c + PlxApiDirect.h) maps flash
chip select 0 into the upstream port's 16 MiB BAR0 at offset 0x300000
(ATLAS_SPI_CS0_PCI_BASE_ADDR); its own "spisave ... /mmr" reads it there 32 bits at a time.
This script does only that: it opens BAR0 read-only (O_RDONLY + PROT_READ), so it cannot
write a single register. It never touches the SPI controller, the switch's CPU or the flash
contents.

    sudo python3 -I pex_flash_read.py --label x8x8            # dump 1 MiB, read twice, compare
    sudo python3 -I pex_flash_read.py --label x8x8 --size 0x400000
    python3 -I pex_flash_read.py --analyse dump.bin           # summary of an existing dump (no root)
"""
import argparse, datetime, glob, hashlib, math, mmap, os, struct, sys

WINDOW_OFF = 0x300000          # ATLAS_SPI_CS0_PCI_BASE_ADDR
BAR0_SIZE = 16 << 20
MAX_SIZE = BAR0_SIZE - WINDOW_OFF
OUTDIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "flash-dumps")


def sysfs(bdf, name):
    with open(f"/sys/bus/pci/devices/{bdf}/{name}") as f:
        return f.read().strip()


def find_upstream():
    """The top Broadcom PEX880xx bridge (its parent is not Broadcom) with a 16 MiB BAR0."""
    hits = []
    for d in glob.glob("/sys/bus/pci/devices/*"):
        bdf = os.path.basename(d)
        try:
            if sysfs(bdf, "vendor") != "0x1000" or sysfs(bdf, "device") not in ("0xc010", "0xc012"):
                continue
            if not sysfs(bdf, "class").startswith("0x0604"):
                continue
            parent = os.path.basename(os.path.dirname(os.path.realpath(d)))
            if os.path.exists(f"/sys/bus/pci/devices/{parent}/vendor") and sysfs(parent, "vendor") == "0x1000":
                continue
            start, end, _ = sysfs(bdf, "resource").splitlines()[0].split()
            if int(end, 16) - int(start, 16) + 1 == BAR0_SIZE:
                hits.append(bdf)
        except OSError:
            continue
    if len(hits) != 1:
        sys.exit(f"expected one PEX880xx upstream port with a 16 MiB BAR0, found {hits}")
    return hits[0]


def open_bar0(bdf):
    """Whole 16 MiB BAR0, read-only; element access = one aligned 32-bit load."""
    fd = os.open(f"/sys/bus/pci/devices/{bdf}/resource0", os.O_RDONLY)
    try:
        mm = mmap.mmap(fd, BAR0_SIZE, flags=mmap.MAP_SHARED, prot=mmap.PROT_READ, offset=0)
    finally:
        os.close(fd)
    return mm, memoryview(mm).cast("I")


# Read-only sanity reads. The SPI manual read-data register (PBAM+0x78) is skipped on purpose:
# reading it could pop a data FIFO.
SANITY = [
    (0x000000, "port 0 regs: device/vendor ID (expect c0101000 or c0121000)"),
    (0x000008, "port 0 regs: class/revision (expect 060400xx)"),
    (0x1C007C, "SPI controller: manual I/O mode (PBAM+0x7C)"),
    (0x1C0084, "SPI controller: manual control/status (PBAM+0x84)"),
]


def read_window(bdf, size):
    mm, mv = open_bar0(bdf)
    try:
        sanity = [(off, mv[off // 4], what) for off, what in SANITY]
        base = WINDOW_OFF // 4
        # coarse scan: first word of every 4 KiB block across the whole window
        coarse = [(i * 4096, mv[base + i * 1024]) for i in range(MAX_SIZE // 4096)]
        out = bytearray()
        step = 4096 // 4
        for i in range(0, size // 4, step):
            n = min(step, size // 4 - i)
            out += struct.pack(f"<{n}I", *mv[base + i:base + i + n].tolist())
        return bytes(out), sanity, coarse
    finally:
        mv.release()
        mm.close()


def analyse(data):
    print(f"size {len(data):#x} bytes, sha256 {hashlib.sha256(data).hexdigest()}")
    blk = 4096
    ranges, cur = [], None
    for off in range(0, len(data), blk):
        b = data[off:off + blk]
        used = b.count(0xFF) != len(b)
        if used and cur is None:
            cur = off
        if not used and cur is not None:
            ranges.append((cur, off)); cur = None
    if cur is not None:
        ranges.append((cur, len(data)))
    print("non-blank 4 KiB regions (everything else is FF):")
    for a, b in ranges or [(0, 0)]:
        if a == b:
            print("  none: the whole range is blank (all FF)"); break
        seg = data[a:b]
        cnt = [0] * 256
        for c in seg:
            cnt[c] += 1
        ent = -sum(n / len(seg) * math.log2(n / len(seg)) for n in cnt if n)
        print(f"  {a:#08x} - {b:#08x}  ({(b - a) // 1024} KiB)  entropy {ent:.2f} bits/byte"
              + ("  <- looks encrypted or compressed" if ent > 7.8 else ""))
    # look for repeats: if the content wraps, the flash is smaller than the window read
    for sz in (1 << n for n in range(16, 25)) if data.count(0xFF) != len(data) else ():
        if sz < len(data) and data[:sz] == data[sz:2 * sz]:
            print(f"content repeats every {sz:#x} bytes: the flash is probably {sz // 1024} KiB")
            break
    print("first 256 bytes:")
    for off in range(0, min(256, len(data)), 16):
        row = data[off:off + 16]
        asc = "".join(chr(c) if 32 <= c < 127 else "." for c in row)
        print(f"  {off:06x}  {row.hex(' ')}  {asc}")
    strings, s = [], bytearray()
    for i, c in enumerate(data):
        if 32 <= c < 127:
            s.append(c)
        else:
            if len(s) >= 8:
                strings.append((i - len(s), s.decode()))
            s = bytearray()
    if strings:
        print(f"text strings (first 30 of {len(strings)}):")
        for off, t in strings[:30]:
            print(f"  {off:#08x}  {t[:80]}")


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--label", default=None, help="DIP setting this boot, e.g. x8x8, x16, x4")
    ap.add_argument("--size", default="0x100000", help="bytes to read from the window (default 1 MiB)")
    ap.add_argument("--bdf", default=None, help="switch upstream port (default: found automatically)")
    ap.add_argument("--analyse", metavar="FILE", help="only summarise an existing dump")
    a = ap.parse_args()

    if a.analyse:
        with open(a.analyse, "rb") as f:
            analyse(f.read())
        return
    size = int(a.size, 0)
    if size <= 0 or size % 4096 or size > MAX_SIZE:
        sys.exit(f"--size must be a multiple of 4096 and at most {MAX_SIZE:#x}")
    if os.geteuid() != 0:
        sys.exit("needs root to open the switch's BAR0: run with sudo")
    # every run leaves a log in flash-dumps/, also when it stops early or fails
    os.makedirs(OUTDIR, exist_ok=True)
    stamp = datetime.datetime.now().strftime("%Y%m%d-%H%M%S")
    logpath = os.path.join(OUTDIR, f"pex-flash-{a.label or 'run'}-{stamp}.log")
    log = open(logpath, "w")
    owner = (int(os.environ["SUDO_UID"]), int(os.environ["SUDO_GID"])) if os.environ.get("SUDO_UID") else None
    if owner:
        os.chown(OUTDIR, *owner); os.chown(logpath, *owner)

    class Tee:
        def __init__(self, *s): self.s = s
        def write(self, x):
            for f in self.s: f.write(x); f.flush()
        def flush(self):
            for f in self.s: f.flush()
    sys.stdout = Tee(sys.__stdout__, log)
    sys.stderr = Tee(sys.__stderr__, log)
    print(f"log: {logpath}")
    try:
        run(a, size, stamp, owner)
    except SystemExit:
        raise
    except BaseException:
        import traceback
        traceback.print_exc()
        sys.exit(1)


def run(a, size, stamp, owner):
    bdf = a.bdf or find_upstream()
    if sysfs(bdf, "vendor") != "0x1000" or sysfs(bdf, "device") not in ("0xc010", "0xc012"):
        sys.exit(f"{bdf} is not a Broadcom PEX880xx")
    width, speed = sysfs(bdf, "current_link_width"), sysfs(bdf, "current_link_speed")
    label = a.label or ("fallback" if width == "4" else "unlabelled")
    print(f"switch upstream {bdf}, link x{width} {speed}, reading {size:#x} bytes at BAR0+{WINDOW_OFF:#x}")

    d1, sanity, coarse = read_window(bdf, size)
    print("register sanity reads (read-only):")
    for off, val, what in sanity:
        print(f"  BAR0+{off:#08x} = {val:08x}  {what}")
    live = [(o, v) for o, v in coarse if v not in (0xFFFFFFFF, 0)]
    print(f"coarse scan of the whole window ({len(coarse)} words, one per 4 KiB): "
          f"{len(live)} not FF/00")
    for o, v in live[:20]:
        print(f"  window+{o:#08x} = {v:08x}")
    if d1.count(0xFF) == len(d1) and not live:
        print("The whole flash window reads as FF: memory-mapped reads are not active on this switch "
              "(or that flash is blank). Nothing was changed. Stopping.")
        sys.exit(2)
    d2, _, _ = read_window(bdf, size)
    path = os.path.join(OUTDIR, f"pex-flash-{label}-{stamp}.bin")
    with open(path, "wb") as f:
        f.write(d1)
    if owner:
        os.chown(path, *owner)
    same = d1 == d2
    print(f"saved {path}")
    print("second read " + ("matches the first" if same else
          f"DIFFERS in {sum(x != y for x, y in zip(d1, d2))} bytes: reads are not stable, do not trust this dump"))
    analyse(d1)


if __name__ == "__main__":
    main()
