#!/usr/bin/env python3
"""Talk to the PEX88096 switch's SPI flash controller from Linux (chip select 0 only).

Mirrors Broadcom's own switch SDK (PLX SDK 9.81, PlxApi/SpiFlash.c: Spi_CmdSendAndReply,
Spi_WriteBuffer, Spi_GetStatus; Samples/PlxCm/PciDev.c: Plx_SpiFileLoad, which in PCIe mode
programs with the switch CPU running and does not erase). Controller registers at upstream
port BAR0 + 0x1C0000 (+0x78 read data, +0x7C I/O mode, +0x80 write data, +0x84 control/status);
flash read window at BAR0 + 0x300000.

    sudo python3 -I pex_flash_prog.py id
        JEDEC ID + status register. Sends only flash READ commands (0x9F, 0x05).

    sudo python3 -I pex_flash_prog.py program --image sbr.bin --offset 0x400
        Page-programs the image into BLANK flash, no erase. Refuses unless: the switch is in
        the DIP fallback boot (upstream x4 2.5 GT/s), the selected flash reads all FF over the
        whole 1 MiB window, the chip answers with a real JEDEC ID and no block protection, and
        the image passes the SBR checks (magic c0103dc4, byte sum 0x5B). Asks you to type WRITE.
        Verifies by reading back.

    sudo python3 -I pex_flash_prog.py write-sbr --image images/sbr-x16-A.bin
        Replaces the image in the chip the DIP selects, at 0x400: erases the first 4 KiB sector
        (0x20) only, then page-programs. Built to rewrite the x4 chip (FS1-3) and nothing else:
          - the live port-width registers must show no x8 code (so not the x8x8 boot),
          - the chip must be the Winbond EF 60 18 seen in the x4 boot, fully unprotected,
          - the chip's first 1 MiB must be exactly the backed-up x4 dump, or blank, or one
            valid image at 0x400 with x16/x4 codes only (one this tool wrote) and FF elsewhere;
            never the x8x8 content,
          - the image must be a valid SBR with x16/x4 codes only.
        Saves the chip's current content first, proves write-enable works (WREN/WRDI, no
        change), asks you to type WRITE, then erases, checks blank, programs, verifies.
"""
import argparse, datetime, glob, hashlib, mmap, os, struct, sys, time

BAR0_SIZE = 16 << 20
PBAM = 0x1C0000
RD_DATA, IO_MODE, WR_DATA, CTRL = PBAM + 0x78, PBAM + 0x7C, PBAM + 0x80, PBAM + 0x84
WINDOW = 0x300000
WINDOW_CHECK = 1 << 20

LEN_SHIFT, CS_SHIFT = 0, 7
LAST, WRITE_OP, ATOMIC, VALID = 1 << 11, 1 << 12, 1 << 14, 1 << 16
CMD_RDID, CMD_RDSR, CMD_WREN, CMD_PP = 0x9F, 0x05, 0x06, 0x02
CMD_WRDI, CMD_RDSR2, CMD_RDSR3, CMD_SE4K = 0x04, 0x35, 0x15, 0x20
PAGE = 256
SECTOR = 4096
SBR_MAGIC, SBR_SUM = 0xC0103DC4, 0x5B
SBR_OFF = 0x400
STN_CFG = 0x800000 + 0x300          # port 0 register page: 6 stations x 16 bits, 4 x 3-bit width codes each
WIDTH = {0: "x16", 1: "x4", 7: "x8"}
X4_CHIP_ID = bytes([0xEF, 0x60, 0x18])
OUTDIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "flash-dumps")
X4_DUMP = ("pex-flash-x4-20261117-011830.bin", "58cf47ce0a73f220")
X8_DUMP = ("pex-flash-x8x8-20261117-011057.bin", "374f2f682dad0e8c")
X8_SBR = ("sbr-x8x8-0x400-0xF20.bin", "d479e07ed8de3da2")
MFR = {0xEF: "Winbond", 0xC2: "Macronix", 0xC8: "GigaDevice", 0x20: "Micron/XMC", 0x1F: "Adesto/Atmel",
       0x9D: "ISSI", 0x85: "Puya", 0x0B: "XTX", 0x5E: "Zbit", 0x68: "Boya", 0xBF: "SST/Microchip", 0x01: "Spansion/Infineon"}


class Bar0:
    """Upstream port BAR0. read/write = one aligned 32-bit access each."""
    def __init__(self, bdf, writable):
        fd = os.open(f"/sys/bus/pci/devices/{bdf}/resource0", os.O_RDWR if writable else os.O_RDONLY)
        try:
            prot = mmap.PROT_READ | (mmap.PROT_WRITE if writable else 0)
            self.mm = mmap.mmap(fd, BAR0_SIZE, flags=mmap.MAP_SHARED, prot=prot, offset=0)
        finally:
            os.close(fd)
        self.mv = memoryview(self.mm).cast("I")

    def read(self, off):
        return self.mv[off // 4]

    def write(self, off, val):
        self.mv[off // 4] = val & 0xFFFFFFFF

    def window(self, off, size):
        base = (WINDOW + off) // 4
        return struct.pack(f"<{size // 4}I", *self.mv[base:base + size // 4].tolist())


class Spi:
    def __init__(self, bar):
        self.bar = bar

    def _wait(self):
        t0 = time.monotonic()
        while self.bar.read(CTRL) & VALID:
            if time.monotonic() - t0 > 1.0:
                raise RuntimeError("SPI controller did not become ready within 1 s")

    def xfer(self, tx, rx_len=0, more_cmds=False, more_data=False):
        """Spi_CmdSendAndReply: tx in 1- or 4-byte pieces, then rx_len bytes back."""
        i = 0
        while i < len(tx):
            n = min(4, len(tx) - i)
            if n not in (1, 4):
                raise ValueError("only 1- or 4-byte pieces (as the SDK uses)")
            chunk = tx[i:i + n]
            val = int.from_bytes(chunk, "big") if n == 4 else chunk[0]
            self.bar.write(WR_DATA, val)
            ctrl = (n * 8) << LEN_SHIFT | 0 << CS_SHIFT | VALID | WRITE_OP
            if more_cmds:
                ctrl |= ATOMIC
            if not more_data and i + n == len(tx) and rx_len == 0:
                ctrl |= LAST
            self.bar.write(CTRL, ctrl)
            self._wait()
            i += n
        out = bytearray()
        while len(out) < rx_len:
            n = min(4, rx_len - len(out))
            if n not in (1, 4):
                raise ValueError("only 1- or 4-byte reads")
            ctrl = (n * 8) << LEN_SHIFT | VALID
            if len(out) + n == rx_len:
                ctrl |= LAST
            self.bar.write(CTRL, ctrl)
            self._wait()
            v = self.bar.read(RD_DATA)
            out += v.to_bytes(4, "big") if n == 4 else bytes([v & 0xFF])
        return bytes(out)

    def jedec(self):
        return self.xfer(bytes([CMD_RDID]), 4)

    def status(self):
        return self.xfer(bytes([CMD_RDSR]), 1)[0]

    def status2(self):
        return self.xfer(bytes([CMD_RDSR2]), 1)[0]

    def status3(self):
        return self.xfer(bytes([CMD_RDSR3]), 1)[0]

    def write_enable_test(self):
        """WREN then WRDI: proves write commands reach the chip, changes nothing stored."""
        self.xfer(bytes([CMD_WREN]))
        on = self.status()
        self.xfer(bytes([CMD_WRDI]))
        off = self.status()
        return on, off

    def sector_erase(self, addr):
        """Spi_Erase framing (WREN as more-commands, then erase + 3-byte address), 4 KiB sector."""
        assert addr % SECTOR == 0 and addr < 1 << 24
        self.xfer(bytes([CMD_WREN]), more_cmds=True)
        self.xfer(bytes([CMD_SE4K, addr >> 16 & 0xFF, addr >> 8 & 0xFF, addr & 0xFF]))
        self.wait_idle(timeout=3.0)

    def wait_idle(self, timeout=2.0):
        t0 = time.monotonic()
        while self.status() & 1:
            if time.monotonic() - t0 > timeout:
                raise RuntimeError("flash stayed busy (WIP) too long")

    def page_program(self, addr, data):
        """Spi_WriteBuffer for one page-bounded chunk."""
        assert addr % 4 == 0 and len(data) % 4 == 0 and (addr % PAGE) + len(data) <= PAGE
        self.xfer(bytes([CMD_WREN]), more_cmds=True)
        self.xfer(bytes([CMD_PP, addr >> 16 & 0xFF, addr >> 8 & 0xFF, addr & 0xFF]), more_data=True)
        self.xfer(data)
        self.wait_idle()


def sysfs(bdf, name):
    with open(f"/sys/bus/pci/devices/{bdf}/{name}") as f:
        return f.read().strip()


def find_upstream():
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


def check_sbr(img):
    problems = []
    if len(img) % 4 or not 0x100 <= len(img) <= 0xC00:
        problems.append(f"size {len(img):#x} (want a multiple of 4, 0x100..0xC00)")
    if struct.unpack_from("<I", img, 0)[0] != SBR_MAGIC:
        problems.append("first word is not c0103dc4")
    if sum(img) & 0xFF != SBR_SUM:
        problems.append(f"byte sum {sum(img) & 0xFF:#04x}, want {SBR_SUM:#04x}")
    return problems


def sbr_groups(sbr):
    """The 24 port-width codes in an SBR header (one per x4 lane group)."""
    v = int.from_bytes(sbr[0x5C:0x68], "little")
    return [(v >> (16 + 3 * i)) & 7 for i in range(24)]


def live_groups(bar):
    """The same 24 codes as the switch is running them now (port0 +0x300..+0x308)."""
    w = [bar.read(STN_CFG + 4 * i) for i in range(3)]
    out = []
    for st in range(6):
        half = (w[st // 2] >> (16 * (st % 2))) & 0xFFFF
        out += [(half >> (3 * g)) & 7 for g in range(4)]
    return out


def fmt_groups(codes):
    return " | ".join(f"stn{s} " + ",".join(WIDTH.get(c, f"?{c}") for c in codes[4 * s:4 * s + 4])
                      for s in range(6))


def sbr_len_at(data, off):
    if len(data) < off + 0x68 or struct.unpack_from("<I", data, off)[0] != SBR_MAGIC:
        return None
    co, cl = struct.unpack_from("<II", data, off + 0x4C)
    n = co + cl + 4
    return n if 0x100 <= n <= 0xC00 and off + n <= len(data) else None


def load_backup(name, sha_prefix):
    path = os.path.join(OUTDIR, name)
    try:
        data = open(path, "rb").read()
    except OSError:
        return None
    return data if hashlib.sha256(data).hexdigest().startswith(sha_prefix) else None


def classify(cur, x4_dump, x8_dump, x8_sbr):
    """What the selected chip holds now (first 1 MiB). None = unknown: refuse."""
    blank = b"\xff"
    if cur == x8_dump[:len(cur)] or x8_sbr in cur:
        return "x8x8"
    if cur == x4_dump[:len(cur)]:
        return "x4 original (matches the backup exactly)"
    if cur.count(0xFF) == len(cur):
        return "blank"
    n = sbr_len_at(cur, SBR_OFF)
    if n and not check_sbr(cur[SBR_OFF:SBR_OFF + n]) and set(sbr_groups(cur[SBR_OFF:])) <= {0, 1} \
            and cur[:SBR_OFF] == blank * SBR_OFF and cur[SBR_OFF + n:] == blank * (len(cur) - SBR_OFF - n):
        return "one x16/x4 image at 0x400 (written by this tool)"
    return None


def describe_id(jid, sr):
    mfr, typ, cap = jid[0], jid[1], jid[2]
    size = (1 << cap) if 10 <= cap <= 28 else None
    print(f"JEDEC ID: {jid[:3].hex(' ')}  ->  {MFR.get(mfr, 'unknown maker')}, type {typ:#04x}, "
          + (f"{size // 1024} KiB" if size else "size unknown"))
    print(f"status register 1: {sr:#04x}  (busy={sr & 1}, write-enable={sr >> 1 & 1}, "
          f"block-protect bits={sr >> 2 & 0x7:#x}, SRWD={sr >> 7})")
    present = jid[:3] not in (b"\xff\xff\xff", b"\x00\x00\x00")
    print("chip present: " + ("yes" if present else "NO (no chip answers on this chip select)"))
    return present, size


def setup_log(tag):
    os.makedirs(OUTDIR, exist_ok=True)
    stamp = datetime.datetime.now().strftime("%Y%m%d-%H%M%S")
    path = os.path.join(OUTDIR, f"pex-prog-{tag}-{stamp}.log")
    log = open(path, "w")
    if os.environ.get("SUDO_UID"):
        os.chown(path, int(os.environ["SUDO_UID"]), int(os.environ["SUDO_GID"]))

    class Tee:
        def __init__(self, *s): self.s = s
        def write(self, x):
            for f in self.s: f.write(x); f.flush()
        def flush(self):
            for f in self.s: f.flush()
    sys.stdout = Tee(sys.__stdout__, log)
    sys.stderr = Tee(sys.__stderr__, log)
    print(f"log: {path}")


def save_owned(name, data):
    path = os.path.join(OUTDIR, name)
    with open(path, "wb") as f:
        f.write(data)
    if os.environ.get("SUDO_UID"):
        os.chown(path, int(os.environ["SUDO_UID"]), int(os.environ["SUDO_GID"]))
    return path


def write_sbr(a, bar, bdf, spi, jid, lg, ask):
    img = open(a.image, "rb").read()
    stamp = datetime.datetime.now().strftime("%Y%m%d-%H%M%S")
    print(f"image {a.image}: {len(img)} bytes, sha256 {hashlib.sha256(img).hexdigest()[:16]}..., "
          f"target {SBR_OFF:#x}-{SBR_OFF + len(img) - 1:#x}")
    problems = check_sbr(img)
    codes = sbr_groups(img) if len(img) >= 0x68 else []
    print(f"image port widths:       {fmt_groups(codes) if codes else '?'}")
    if not codes or not set(codes) <= {0, 1}:
        problems.append("the image has x8 or unknown width codes: this command only writes x16/x4 images")
    x4_dump, x8_dump, x8_sbr = load_backup(*X4_DUMP), load_backup(*X8_DUMP), load_backup(*X8_SBR)
    if x4_dump is None or x8_dump is None or x8_sbr is None:
        problems.append(f"a backup in {OUTDIR} is missing or changed (x4 dump, x8x8 dump, x8x8 SBR)")
    if 7 in lg:
        problems.append("the switch is running x8 ports, so the DIP selects the x8x8 chip: never written")
    if jid[:3] != X4_CHIP_ID:
        problems.append(f"chip ID {jid[:3].hex(' ')} is not the x4 chip's ef 60 18")
    sr1, sr2, sr3 = spi.status(), spi.status2(), spi.status3()
    if sr1 & 0xFD or sr2 & 0x41 or sr3 & 0x04:
        problems.append(f"chip is busy or write-protected (SR1 {sr1:#04x}, SR2 {sr2:#04x}, SR3 {sr3:#04x}); "
                        "not changing protection")
    cur = bar.window(0, WINDOW_CHECK)
    kind = classify(cur, x4_dump, x8_dump, x8_sbr) if x4_dump and x8_dump and x8_sbr else None
    print(f"chip holds now: {kind or 'UNKNOWN content'}")
    if kind is None or kind == "x8x8":
        problems.append("the chip's content is not the x4 backup, blank, or an image this tool wrote")
    if bdf:
        print(f"current content saved: {save_owned(f'before-write-{stamp}.bin', cur)}")
    if problems:
        print("NOT WRITING:")
        for x in problems:
            print("  - " + x)
        return 2

    on, off = spi.write_enable_test()
    print(f"write-enable test: after WREN status {on:#04x}, after WRDI {off:#04x}")
    if not on & 0x02 or off & 0x02:
        print("NOT WRITING: the chip did not latch write-enable, so write commands do not reach it. Nothing changed.")
        return 4
    print("All checks passed. This erases the first 4 KiB of the selected chip and writes the image at 0x400.")
    if ask("Type WRITE to erase and program: ").strip() != "WRITE":
        print("cancelled, nothing written")
        return 3

    blank = b"\xff" * WINDOW_CHECK
    if kind != "blank":
        t0 = time.monotonic()
        spi.sector_erase(0)
        after_erase = bar.window(0, WINDOW_CHECK)
        print(f"erased sector 0 in {time.monotonic() - t0:.2f} s; 1 MiB window now "
              + ("blank" if after_erase == blank else "NOT blank"))
        if after_erase != blank:
            if bdf:
                print(f"saved: {save_owned(f'after-erase-{stamp}.bin', after_erase)}")
            print("STOPPED before programming. The chip may be partly erased; the switch will fall back at "
                  "the next boot (still reachable). Do not reboot yet; report this output.")
            return 5
    t0 = time.monotonic()
    pos = 0
    while pos < len(img):
        addr = SBR_OFF + pos
        n = min(PAGE - addr % PAGE, len(img) - pos)
        spi.page_program(addr, img[pos:pos + n])
        pos += n
    print(f"programmed {len(img)} bytes in {time.monotonic() - t0:.2f} s")
    after = bar.window(0, WINDOW_CHECK)
    expect = bytearray(blank)
    expect[SBR_OFF:SBR_OFF + len(img)] = img
    ok = after == bytes(expect)
    print("verify: " + ("the chip holds exactly the image at 0x400 and FF elsewhere" if ok else
                        f"MISMATCH in {sum(x != y for x, y in zip(after, expect))} bytes"))
    if bdf:
        print(f"read-back saved: {save_owned(f'after-write-{stamp}.bin', after)}")
    if ok:
        print("Done. Shut down, PSU off, keep the same DIP setting, cold boot.")
    return 0 if ok else 1


def main(argv=None, bar=None, bdf=None, link=None, ask=input):
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)
    sub.add_parser("id")
    p = sub.add_parser("program")
    p.add_argument("--image", required=True)
    p.add_argument("--offset", default="0x400")
    w = sub.add_parser("write-sbr")
    w.add_argument("--image", required=True)
    a = ap.parse_args(argv)

    if bar is None:
        if os.geteuid() != 0:
            sys.exit("needs root: run with sudo")
        setup_log(a.cmd)
        bdf = find_upstream()
        link = (sysfs(bdf, "current_link_width"), sysfs(bdf, "current_link_speed"))
        bar = Bar0(bdf, writable=True)
    print(f"switch upstream {bdf}, link x{link[0]} {link[1]}")
    io = bar.read(IO_MODE)
    if io != 0:
        sys.exit(f"SPI controller I/O mode is {io:#x}, not serial (0); not touching it")
    spi = Spi(bar)
    jid = spi.jedec()
    present, size = describe_id(jid, spi.status())
    lg = live_groups(bar)
    print(f"port widths running now: {fmt_groups(lg)}")
    if present:
        print(f"status register 2: {spi.status2():#04x}, status register 3: {spi.status3():#04x}")

    if a.cmd == "id":
        return 0
    if a.cmd == "write-sbr":
        return write_sbr(a, bar, bdf, spi, jid, lg, ask)

    # ---- program: every check first, then one confirmation ----
    img = open(a.image, "rb").read()
    off = int(a.offset, 0)
    problems = check_sbr(img)
    if off % PAGE or off < 0x100 or off + len(img) > WINDOW_CHECK:
        problems.append(f"offset {off:#x} (want page-aligned, >= 0x100)")
    if not (link[0] == "4" and link[1].startswith("2.5")):
        problems.append(f"switch link is x{link[0]} {link[1]}: this only runs in the FS1-1 fallback boot "
                        "(x4 2.5 GT/s), so the x8x8 and x4 chips are deselected")
    if not present:
        problems.append("no flash chip answers")
    sr = spi.status()
    if sr & 0x9C:
        problems.append(f"flash has block protection or SRWD set (status {sr:#04x}); not clearing it")
    before = bar.window(0, WINDOW_CHECK)
    if before.count(0xFF) != len(before):
        problems.append("the selected flash is NOT blank: refusing (this tool never erases or overwrites)")
    print(f"image {a.image}: {len(img)} bytes, sha256 {hashlib.sha256(img).hexdigest()[:16]}..., "
          f"target {off:#x}-{off + len(img) - 1:#x}")
    if problems:
        print("NOT WRITING:"); [print("  - " + x) for x in problems]
        return 2
    print("All checks passed: blank chip, fallback boot, valid image, no protection.")
    if ask("Type WRITE to program the flash: ").strip() != "WRITE":
        print("cancelled, nothing written"); return 3

    t0 = time.monotonic()
    pos = 0
    while pos < len(img):
        addr = off + pos
        n = min(PAGE - addr % PAGE, len(img) - pos)
        spi.page_program(addr, img[pos:pos + n])
        pos += n
    print(f"programmed {len(img)} bytes in {time.monotonic() - t0:.2f} s")

    after = bar.window(0, WINDOW_CHECK)
    written = after[off:off + len(img)]
    ok = written == img
    print("verify written range: " + ("MATCHES the image" if ok else
          f"MISMATCH in {sum(x != y for x, y in zip(written, img))} bytes"))
    # the rest must still be blank; a flash smaller than the window may show the image again at each multiple of its size
    expect = [bytearray(b"\xff" * WINDOW_CHECK) for _ in range(2)]
    expect[0][off:off + len(img)] = img
    for k in range(0, WINDOW_CHECK, size if size and size < WINDOW_CHECK else WINDOW_CHECK):
        expect[1][k + off:k + off + len(img)] = img
    print("rest of the 1 MiB window: " + ("still blank" if after in (bytes(expect[0]), bytes(expect[1]))
                                          else "CHANGED outside the image (unexpected)"))
    if bdf and os.environ.get("SUDO_UID"):
        path = os.path.join(OUTDIR, f"after-prog-{datetime.datetime.now():%Y%m%d-%H%M%S}.bin")
        open(path, "wb").write(after)
        os.chown(path, int(os.environ["SUDO_UID"]), int(os.environ["SUDO_GID"]))
        print(f"read-back saved: {path}")
    return 0 if ok else 1


if __name__ == "__main__":
    try:
        sys.exit(main())
    except SystemExit:
        raise
    except BaseException:
        import traceback
        traceback.print_exc()
        sys.exit(1)
