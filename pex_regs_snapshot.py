#!/usr/bin/env python3
"""Read-only snapshot of the PEX88096's register pages, to compare DIP modes.

Opens the upstream port's BAR0 O_RDONLY (cannot write). Reads:
  - the chip register page, BAR0 + 0x0000..0x0FFF (port clock enables at 0x30C.. etc.)
  - each port's 4 KiB register page at BAR0 + 0x800000 + port*0x1000 (PCIe config + PLX regs)
    for ports 0..95 in steps of 4, 116, 117, 224..235
Saves flash-dumps/regs-<label>-<time>.bin (raw) and .txt (one line per non-zero word).

    sudo python3 -I pex_regs_snapshot.py --label x4
"""
import argparse, datetime, os, struct, sys
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from pex_flash_read import find_upstream, sysfs, OUTDIR  # noqa: E402
import mmap

BAR0_SIZE = 16 << 20
PORTS = list(range(0, 96, 4)) + [116, 117] + list(range(224, 236))


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--label", required=True)
    a = ap.parse_args()
    if os.geteuid() != 0:
        sys.exit("needs root: run with sudo")
    bdf = find_upstream()
    fd = os.open(f"/sys/bus/pci/devices/{bdf}/resource0", os.O_RDONLY)
    mm = mmap.mmap(fd, BAR0_SIZE, flags=mmap.MAP_SHARED, prot=mmap.PROT_READ)
    os.close(fd)
    mv = memoryview(mm).cast("I")
    pages = [("chip", 0x0)] + [(f"port{p}", 0x800000 + p * 0x1000) for p in PORTS]
    raw = bytearray()
    lines = [f"# {bdf} link x{sysfs(bdf, 'current_link_width')} {sysfs(bdf, 'current_link_speed')} label {a.label}"]
    for name, base in pages:
        words = mv[base // 4:(base + 0x1000) // 4].tolist()
        raw += struct.pack("<1024I", *words)
        if name != "chip" and words[0] in (0xFFFFFFFF, 0):
            lines.append(f"{name} absent ({words[0]:08x})")
            continue
        for i, w in enumerate(words):
            if w not in (0, 0xFFFFFFFF):
                lines.append(f"{name} +{i * 4:03x} = {w:08x}")
    mv.release(); mm.close()
    os.makedirs(OUTDIR, exist_ok=True)
    stamp = datetime.datetime.now().strftime("%Y%m%d-%H%M%S")
    base = os.path.join(OUTDIR, f"regs-{a.label}-{stamp}")
    open(base + ".bin", "wb").write(raw)
    open(base + ".txt", "w").write("\n".join(lines) + "\n")
    if os.environ.get("SUDO_UID"):
        for ext in (".bin", ".txt"):
            os.chown(base + ext, int(os.environ["SUDO_UID"]), int(os.environ["SUDO_GID"]))
    print(f"saved {base}.txt ({len(lines)} lines) and .bin")


if __name__ == "__main__":
    main()
