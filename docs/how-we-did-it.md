# How we did it

This follows the investigation in order, 5–17 Nov 2026, including the wrong turns.

## 1. The symptom

The board shipped with DIP FS1-1 ON, which the silkscreen and the seller's spec sheet both call **x16**: five
devices, each on a pair of SlimSAS 8i connectors. In that setting the GPUs never appeared. `lspci` showed:

- the switch's upstream link at **x4, 2.5 GT/s** (Gen1), with every port's capability Gen1
- **23 x4 downstream ports** plus two x1 ports and a management endpoint, none with a device present

That is the chip's own default layout: every station split into x4 ports. It is what a Broadcom switch does
when its configuration image (SBR) does not load. FS1-2 (**x8x8**) worked: upstream x16 Gen4, five cards at
x8. FS1-3 (**x4x4x4x4**) also worked. Of all eight DIP combinations, only x8x8 (and, tested later, x4) booted properly.

Cables could not explain it. The switch failed before any card was involved, since even its link to the host
was wrong. So the x16 position's image was missing, broken, or not selectable.

## 2. The seller

The seller sent a test sheet showing x16 working on their board (four GPUs at x16), so the mode exists. They
never sent an x16 image or said which flash chip FS1-1 selects. That left us on our own.

## 3. Reading the flash without a programmer

The original plan was to photograph the board, find the flash chips and read each with a 1.8 V-capable SPI
programmer. Then we found that Broadcom's own switch SDK (PLX SDK 9.81, mirrored on GitHub) reads the flash
in-band:

- `PlxApiDirect.h` defines `ATLAS_SPI_CS0_PCI_BASE_ADDR = 0x300000`, a memory-mapped read window onto flash
  chip select 0, inside the upstream port's 16 MiB BAR0.
- `SpiFlash.c` drives the SPI controller ("PBAM") at BAR0 + `0x1C0000` for commands: JEDEC ID, status, write
  enable, page program, erase.
- The SDK supports chip select 0 only. The DIP switch evidently moves a different physical chip onto that chip
  select, so each mode's chip can be reached by booting in that mode.

`pex_flash_read.py` opens BAR0 read-only and copies the window twice, comparing the two reads.

## 4. The x8x8 image

Booted on FS1-2 and dumped 1 MiB:

- 0x000–0x3FF blank (FF); a ~2.8 KB structure at **0x400**; everything else blank
- low entropy, readable structure: **not encrypted** (one forum post had claimed these images are)
- a magic word `c0103dc4`, section offsets and lengths, and a list of 283 (address, value) register writes,
  most of them to PHY/SerDes blocks (`0x7F00xxxx`, `0x7B04xxxx`, `0x7305xxxx`)

## 5. The x4 image, and the diff

Booted on FS1-3 and dumped again: a different image, so the DIP really does select a different chip. Comparing
the two:

- All 280 PHY/SerDes writes are identical.
- A run of header bits at 0x45C–0x467 changes from all `7`s to all `1`s, read as 3-bit fields: 20 fields, one
  per downstream x4 lane group (5 stations × 4).
- A pair of header words at 0x484/0x488 is set in x8x8 and zero in x4.
- A "date" word differs: `06042019` vs `02242021`, BCD month-day-year.
- The x4 build has seven extra register writes.

Both images satisfy one checksum rule: the byte sum from the magic through the last word ≡ 0x5B (mod 256).
That is the same as `(0xA5 + sum) mod 256 == 0`, the form Broadcom's Scrutiny library uses.

## 6. The x16 position is empty

Booted on FS1-1 and dumped: **all FF**. Blank, missing, or not answering? We wrote `pex_flash_prog.py id`,
which sends a JEDEC ID read (0x9F) and a status read through the SPI controller, both read commands only.

- FS1-1 boot: `ff ff ff`, status `ff`: nothing drives the data line.
- Control test, FS1-3 boot: `ef 60 18` (Winbond W25Q128JW, 1.8 V, 16 MiB), status `00`.

The tool worked on a real chip, so **the x16 position has no working chip**. That fully explains the board's
behaviour: with no image, the switch boots its default layout. Most likely the chip was never fitted.

Since x8x8 is better than x4 for these cards anyway, the plan became: **rewrite the x4 chip with an x16 image.**
FS1-2 stays the untouched fallback.

## 7. Finding where widths live

A read-only register snapshot (`pex_regs_snapshot.py`) in the x4 boot decoded the switch's port pages at
BAR0 + `0x800000` + port × `0x1000`:

- Port 0 is the upstream port (station 0, x16 Gen4).
- 20 x4 downstream ports: 16, 20, …, 92. Port numbers are lane numbers.
- Internal virtual ports 224–226 / 232–234: the chip is internally a root switch feeding sub-switches.
- **Port 0 registers 0x300/0x304/0x308 read `0000 0249 0249 0249 0249 0249`.** These are six 16-bit station
  fields, each holding four 3-bit codes: `0x249` = 1,1,1,1, exactly the image's x4 codes. Station 0 reads
  `0x000`.

So station 0's codes are 0 in both images (they sit at header bit 16, just before the 20 downstream fields),
and station 0 runs at **x16**. With no image at all, every port, port 0 included, is x4. So **0 = x16**, at
least for the upstream station.

Broadcom's older Gen3 PEX8749 also keeps its port configuration in register **300h**, set from EEPROM, but with
3 bits per station and different codes. That supports the location, not our values.

The section-B entries turned out to be per-port register writes: dword index × 4 = port × 0x1000 + offset.
The "date" entry writes port 0's register 0x20C, and the live register read the x4 image's value. It is a
config stamp, not a mode setting.

## 8. Confirmation from a shipping product

Lenovo publishes firmware for the PCIe switch card in its ThinkSystem SR670 V2 GPU server (a PEX88096 feeding
four GPUs at x16), as a plain download. Its PLDM firmware file contains a complete SBR in the same format.
`sbr_info.py` finds it at offset 0x7B5A9:

- **all 24 width codes are 0**: six x16 ports
- **the 280 PHY/SerDes writes and the port-0 register writes are byte-for-byte identical to our board's**,
  config date `06042019` included. Both are evidently built from Broadcom's reference configuration.
- the 0x484/0x488 pair is 0

So the x16 image needed exactly what Lenovo did. Change the width codes, and keep everything else as this
board's own settings (connectors, clocking and so on live in the SoC settings block and differ between boards).

## 9. Building the image

`make_x16_sbr.py`, starting from the x8x8 image:

- sets the 20 downstream codes from 7 to 0
- **A:** zeroes 0x484/0x488, as Lenovo's x16 image and the board's own working x4 image have it.
  **B:** keeps the x8x8 values.
- sets the config-date word to `11172026` (A) or `11182026` (B), so the running image can be identified from
  port 0 register 0x20C
- recomputes the checksum byte

As a check, rebuilding the x4 image from the x8x8 one (or the reverse) with the same rules reproduces the
width codes, date and checksum exactly. What remains different is only the x4 build's extra register writes
and the 0x484/0x488 pair: vendor additions that can be copied, not derived.

## 10. Writing it safely

`pex_flash_prog.py write-sbr` follows the SDK's command framing:

- write enable, sent as "more commands follow"
- then a 4 KiB sector erase (0x20) with a 3-byte address
- then page program (0x02) in 256-byte pages
- polling the status register between steps

It refuses unless the interlocks in the README all pass. It saves the chip's contents first, and checks that
write-enable latches (WREN then WRDI, nothing stored changes) before asking for `WRITE`. It was tested against a
simulated controller and flash chip (`tests/sim_test.py`, 20 cases including every refusal) before touching
hardware.

The real run, in the FS1-3 boot:

- The chip matched the x4 backup exactly, and write-enable latched.
- The erase left the 1 MiB window blank, and programming 2848 bytes took 0.01 s.
- The read-back matched the image exactly.

## 11. Result

Cold boot, still on FS1-3:

- port 0 registers 0x300–0x308 all zero (every station x16)
- 0x20C = `11172026` (image A)
- downstream ports 16, 32, 48, 64 and 80 at max x16, **linked x16 Gen2**
- all five cards at x16 in `nvidia-smi`, and no AER errors

## Things we got wrong along the way

- **"Cards A and D are miswired."** In the x4 boot, card A trained on lanes 8–11 of its slot and card D on
  lanes 4–7. We reasoned that a correctly wired x16 card can only train x4 on the first or last group, so
  their two cables had to be swapped. The owner had already checked the cables, and with the x16 image both
  cards trained at full x16. A GA100 card at x4 can evidently land on a middle lane group. Nothing was wrong.
- **"0x06042019 is not a date."** It is: a BCD month-day-year stamp, and Broadcom's reference value.

## A question that came up

- **"Lenovo's card runs four GPUs at x16. Is the chip limited to four?"** No. 96 lanes = one x16 upstream + five
  x16 downstream. Lenovo's image already sets up five downstream x16 ports; they wire four to GPU slots and one
  to I/O.
