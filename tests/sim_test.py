#!/usr/bin/env python3
"""Simulated PEX88096 SPI controller + Winbond flash, to test pex_flash_prog.py without hardware.

    python3 -I tests/sim_test.py
"""
import contextlib, io, os, struct, sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, ROOT)
import pex_flash_prog as P  # noqa: E402

DUMPS = os.path.join(ROOT, "flash-dumps")
X4_DUMP = open(os.path.join(DUMPS, P.X4_DUMP[0]), "rb").read()
X8_DUMP = open(os.path.join(DUMPS, P.X8_DUMP[0]), "rb").read()
IMG_A = os.path.join(ROOT, "images", "sbr-x16-A.bin")
IMG_B = os.path.join(ROOT, "images", "sbr-x16-B.bin")
IMG_X4 = os.path.join(DUMPS, "sbr-x4-0x400-0xF58.bin")
IMG_X8 = os.path.join(DUMPS, "sbr-x8x8-0x400-0xF20.bin")
STN_X4 = [0x02490000, 0x02490249, 0x02490249]
STN_X8 = [0x0FFF0000, 0x0FFF0FFF, 0x0FFF0FFF]
STN_X16 = [0, 0, 0]
STN_MIXED = [0, 0, 0x02490000]      # pairs A-D x16, E four x4


class FakeBar:
    def __init__(self, flash1m, jedec=(0xEF, 0x60, 0x18), sr=(0, 0, 0), stn=STN_X4,
                 ignore_wren=False, ignore_erase=False, size=16 << 20):
        self.flash = bytearray(b"\xff" * size)
        self.flash[:len(flash1m)] = flash1m
        self.jedec, self.sr, self.stn = bytes(jedec), list(sr), stn
        self.ignore_wren, self.ignore_erase = ignore_wren, ignore_erase
        self.wel = False
        self.tx = bytearray()      # bytes of the current chip-select cycle
        self.rx = bytearray()
        self.wr = 0
        self.rd = 0
        self.log = []

    # BAR0 accesses
    def read(self, off):
        if off == P.CTRL:
            return 0
        if off == P.IO_MODE:
            return 0
        if off == P.RD_DATA:
            return self.rd
        if P.STN_CFG <= off < P.STN_CFG + 12:
            return self.stn[(off - P.STN_CFG) // 4]
        raise AssertionError(f"unexpected read {off:#x}")

    def write(self, off, val):
        if off == P.WR_DATA:
            self.wr = val
            return
        assert off == P.CTRL, f"write to {off:#x} outside the SPI controller"
        n = (val >> P.LEN_SHIFT & 0x7F) // 8
        assert val & P.VALID and n in (1, 4) and (val >> P.CS_SHIFT & 0xF) == 0
        if val & P.WRITE_OP:
            self.tx += self.wr.to_bytes(4, "big") if n == 4 else bytes([self.wr & 0xFF])
            data = b""
        else:
            data = self.reply(n)
            self.rd = int.from_bytes(data, "big") if n == 4 else data[0]
        if val & P.LAST:
            self.execute(bool(val & P.ATOMIC))
            self.tx, self.rx = bytearray(), bytearray()

    def reply(self, n):
        cmd = self.tx[0]
        if cmd == 0x9F:
            src = self.jedec + b"\0"
        elif cmd in (0x05, 0x35, 0x15):
            idx = {0x05: 0, 0x35: 1, 0x15: 2}[cmd]
            v = self.sr[idx] | (2 if idx == 0 and self.wel else 0)
            src = bytes([v]) * 4
        else:
            raise AssertionError(f"read phase after command {cmd:#x}")
        out = src[len(self.rx):len(self.rx) + n]
        self.rx += out
        return out

    def execute(self, atomic):
        cmd = self.tx[0]
        self.log.append((cmd, len(self.tx), atomic))
        if cmd == 0x06:
            if not self.ignore_wren:
                self.wel = True
        elif cmd == 0x04:
            self.wel = False
        elif cmd == 0x20:
            assert len(self.tx) == 4
            a = int.from_bytes(self.tx[1:4], "big")
            if self.wel and not self.ignore_erase:
                self.flash[a & ~0xFFF:(a & ~0xFFF) + 4096] = b"\xff" * 4096
            self.wel = False
        elif cmd == 0x02:
            a = int.from_bytes(self.tx[1:4], "big")
            data = self.tx[4:]
            assert 1 <= len(data) <= 256 and (a % 256) + len(data) <= 256
            if self.wel:
                for i, b in enumerate(data):
                    self.flash[a + i] &= b
            self.wel = False
        elif cmd in (0x9F, 0x05, 0x35, 0x15):
            pass
        else:
            raise AssertionError(f"unexpected flash command {cmd:#x}")

    def window(self, off, size):
        return bytes(self.flash[off:off + size])


def run(args, bar, answer="WRITE", link=("16", "16.0 GT/s PCIe")):
    buf = io.StringIO()
    with contextlib.redirect_stdout(buf):
        rc = P.main(args, bar=bar, bdf=None, link=link, ask=lambda _: answer)
    return rc, buf.getvalue()


def image_at_400(path):
    f = bytearray(b"\xff" * (1 << 20))
    img = open(path, "rb").read()
    f[0x400:0x400 + len(img)] = img
    return bytes(f)


results = []


def case(name, cond, out=""):
    results.append(cond)
    print(("PASS " if cond else "FAIL ") + name)
    if not cond:
        print(out)


# 1. id in the x4 boot
b = FakeBar(X4_DUMP)
rc, out = run(["id"], b)
case("id prints chip, widths, SR2/SR3", rc == 0 and "Winbond" in out and "stn1 x4,x4,x4,x4" in out
     and "status register 2" in out and b.flash[:1 << 20] == X4_DUMP, out)

# 2. x16-A over the original x4 content
b = FakeBar(X4_DUMP)
rc, out = run(["write-sbr", "--image", IMG_A], b)
case("x16-A replaces the x4 image (erase + program, exact)", rc == 0 and bytes(b.flash[:1 << 20]) == image_at_400(IMG_A)
     and "x4 original" in out, out)
case("erase used WREN(atomic) then 0x20 in its own cycle",
     any(b.log[i][:3] == (0x06, 1, True) and b.log[i + 1][:2] == (0x20, 4) for i in range(len(b.log) - 1)), str(b.log))
case("rest of the 16 MiB chip untouched", b.flash[1 << 20:] == b"\xff" * ((16 << 20) - (1 << 20)))

# 3. B over A (a chip this tool wrote), then restore x4 over B
b = FakeBar(image_at_400(IMG_A), stn=STN_X16)
rc, out = run(["write-sbr", "--image", IMG_B], b)
case("x16-B over x16-A (x16 boot)", rc == 0 and bytes(b.flash[:1 << 20]) == image_at_400(IMG_B), out)
rc, out = run(["write-sbr", "--image", IMG_X4], b)
case("restore x4 over x16-B gives back the exact x4 dump", rc == 0 and bytes(b.flash[:1 << 20]) == X4_DUMP, out)

# 3b. the untested mixed example (A-D x16, E four x4) over x16-A, then x16-A back over it
import make_mixed_sbr as M  # noqa: E402
mixp = os.path.join(HERE, "mixed-example.bin")
open(mixp, "wb").write(M.build(M.parse_layout("x16,x16,x16,x16,x4"))[1])
b = FakeBar(image_at_400(IMG_A), stn=STN_X16)
rc, out = run(["write-sbr", "--image", mixp], b)
case("mixed example over x16-A (x16 boot)", rc == 0 and bytes(b.flash[:1 << 20]) == image_at_400(mixp), out)
b.stn = STN_MIXED
rc, out = run(["write-sbr", "--image", IMG_A], b)
case("x16-A back over the mixed example (mixed boot)", rc == 0 and bytes(b.flash[:1 << 20]) == image_at_400(IMG_A)
     and "stn5 x4,x4,x4,x4" in out, out)
os.remove(mixp)

# 4. fallback boot after a rejected image: blank chip, no erase needed
b = FakeBar(b"\xff" * (1 << 20), stn=STN_X4)
rc, out = run(["write-sbr", "--image", IMG_A], b, link=("4", "2.5 GT/s PCIe"))
case("blank chip: programs without erasing", rc == 0 and bytes(b.flash[:1 << 20]) == image_at_400(IMG_A)
     and not any(c == 0x20 for c, _, _ in b.log), out)


def refused(name, bar, args, want_rc=2, answer="WRITE"):
    before = bytes(bar.flash)
    rc, out = run(args, bar, answer)
    case(name, rc == want_rc and bytes(bar.flash) == before and not any(c in (0x20, 0x02) for c, _, _ in bar.log), out)
    return out


refused("refuse: x8x8 boot (live x8 codes), even with x4 content", FakeBar(X4_DUMP, stn=STN_X8),
        ["write-sbr", "--image", IMG_A])
refused("refuse: chip holds the x8x8 dump", FakeBar(X8_DUMP), ["write-sbr", "--image", IMG_A])
x8_in_other = bytearray(b"\xff" * (1 << 20))
x8_in_other[0x8000:0x8000 + 2848] = open(IMG_X8, "rb").read()
refused("refuse: x8x8 SBR found anywhere in the chip", FakeBar(bytes(x8_in_other)), ["write-sbr", "--image", IMG_A])
refused("refuse: image with x8 codes", FakeBar(X4_DUMP), ["write-sbr", "--image", IMG_X8])
refused("refuse: wrong chip ID", FakeBar(X4_DUMP, jedec=(0xC2, 0x25, 0x38)), ["write-sbr", "--image", IMG_A])
refused("refuse: block protect bits", FakeBar(X4_DUMP, sr=(0x1C, 0, 0)), ["write-sbr", "--image", IMG_A])
refused("refuse: CMP bit in SR2", FakeBar(X4_DUMP, sr=(0, 0x40, 0)), ["write-sbr", "--image", IMG_A])
refused("refuse: WPS bit in SR3", FakeBar(X4_DUMP, sr=(0, 0, 0x04)), ["write-sbr", "--image", IMG_A])
odd = bytearray(X4_DUMP)
odd[0x20000] = 0x00
refused("refuse: unknown content (one stray byte)", FakeBar(bytes(odd)), ["write-sbr", "--image", IMG_A])
bad = bytearray(open(IMG_A, "rb").read())
bad[0x100] ^= 1
badp = os.path.join(HERE, "bad-checksum.bin")
open(badp, "wb").write(bad)
refused("refuse: image with a bad checksum", FakeBar(X4_DUMP), ["write-sbr", "--image", badp])
os.remove(badp)
refused("stop: chip ignores write-enable (rc 4)", FakeBar(X4_DUMP, ignore_wren=True), ["write-sbr", "--image", IMG_A],
        want_rc=4)
refused("cancel at the prompt (rc 3)", FakeBar(X4_DUMP), ["write-sbr", "--image", IMG_A], want_rc=3, answer="no")

b = FakeBar(X4_DUMP, ignore_erase=True)
rc, out = run(["write-sbr", "--image", IMG_A], b)
case("erase does not take: stops before programming (rc 5)", rc == 5 and not any(c == 0x02 for c, _, _ in b.log)
     and bytes(b.flash[:1 << 20]) == X4_DUMP, out)

print(f"\n{sum(results)}/{len(results)} passed")
sys.exit(0 if all(results) else 1)
