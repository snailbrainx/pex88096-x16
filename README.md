# PEX88096 switch board: making the x16 mode work

A PCIe 4.0 switch board built on the Broadcom PEX88096 (silkscreen **RTE88096-A54**, PCB rev 1.1, maker
unknown) has a DIP switch with three modes: **x16** (5 devices), **x8x8** (10) and **x4x4x4x4** (20). On our
board the x16 setting never worked. The switch came up in its bare fallback mode (x4 Gen1 to the host, no
devices), and the seller could not supply a fix.

This repo is how we fixed it **from Linux, with no hardware programmer**:

1. We read the switch's configuration flash through the switch's own PCIe register window.
2. We worked out the configuration image format.
3. We found that the x16 DIP position has **no flash chip at all**.
4. We rewrote the chip behind the (unneeded) x4 position with an x16 image built from the board's own x8x8
   image.

## Result (17 Nov 2026)

Five NVIDIA CMP 170HX (GA100) cards, each on two SlimSAS 8i cables:

| Card on connector | Switch port | Before (x8x8 mode) | After (our x16 image) |
|---|---|---|---|
| A | 16 | x8 Gen2 | **x16 Gen2** |
| B | 32 | x8 Gen2 | **x16 Gen2** |
| C | 48 | x8 Gen2 | **x16 Gen2** |
| D | 64 | x8 Gen2 | **x16 Gen2** |
| E | 80 | x8 Gen2 | **x16 Gen2** |

- The switch's link to the host stayed x16 Gen4.
- No AER errors were logged.
- Gen2 is these cards' maximum speed, so the gain is width: each card's link doubles, from ~3.1 GB/s measured
  at x8 to ~6 GB/s expected at x16. Benchmarks: to be added.

DIP settings after the fix:

| DIP | Chip behind it | Now boots |
|---|---|---|
| FS1-1 ("x16") | **none fitted** (JEDEC ID ff ff ff) | fallback, as it always did |
| FS1-2 ("x8x8") | original, never written | x8x8 (known-good fallback) |
| FS1-3 ("x4x4x4x4") | Winbond W25Q128JW, 1.8 V, 16 MiB | **x16 (our image)**; the original x4 image is in `flash-dumps/` |

## What we found, in short

- **Access.** The upstream port's 16 MiB BAR0 exposes:
  - a read window onto flash chip select 0 at `+0x300000`
  - the SPI controller at `+0x1C0000`

  This comes from Broadcom's own PCI/PCIe SDK. The DIP switch decides which physical chip sits on chip
  select 0, so each mode's chip can be read and written by booting in that mode.
- **The image (SBR)** is plain register data (not encrypted) at flash offset `0x400`, ending in one checksum
  byte: `(0xA5 + sum of all bytes) mod 256 == 0`.
- **Port widths** are one 3-bit code per x4 lane group (24 groups, 6 stations), packed in the SBR header from
  bit 16 of offset `+0x5C`. At boot the firmware copies them to port 0's registers `0x300-0x308`.
  - Codes: **0 = x16, 1 = x4, 7 = x8.**
  - Confirmed against Lenovo's PEX88096 GPU switch card firmware, which has every code at 0. Its 280
    PHY/SerDes writes are identical to this board's.
- **Our x16 image** is the board's x8x8 image with the 20 downstream codes changed from 7 to 0. It also zeroes
  one unexplained header pair (as Lenovo's x16 image and the board's own x4 image have it) and changes the
  config-date word so the running image can be identified. Everything else is the board's own configuration.

Details:

- [docs/how-we-did-it.md](docs/how-we-did-it.md): the whole investigation, including the wrong turns
- [docs/sbr-format.md](docs/sbr-format.md): flash layout, image format, width codes, register map
- [docs/hardware.md](docs/hardware.md): the board, its DIP switch, connectors and switch topology
- [docs/reproduce.md](docs/reproduce.md): step by step for another board, plus recovery
- [docs/sources.md](docs/sources.md): where every fact came from

## Files

| File | What it does | Writes to hardware? |
|---|---|---|
| `pex_flash_read.py` | Dumps the selected flash chip through the BAR0 window (BAR0 opened read-only) | no |
| `pex_regs_snapshot.py` | Saves the switch's per-port register pages and decodes link widths | no |
| `pex_flash_prog.py id` | Reads the chip's JEDEC ID, its status registers and the port widths running now | flash read commands only |
| `pex_flash_prog.py write-sbr` | Erases the first 4 KiB sector and writes an image at 0x400, with interlocks (below) | **yes** |
| `pex_flash_prog.py program` | Writes an image into a completely blank chip (fallback boot only), no erase | **yes** |
| `make_x16_sbr.py` | Builds `images/sbr-x16-A.bin` / `-B.bin` from the x8x8 image | no (offline) |
| `sbr_info.py` | Decodes any SBR, or finds SBRs inside vendor firmware files | no (offline) |
| `tests/sim_test.py` | Tests the write paths against a simulated SPI controller and flash chip (20 cases) | no |
| `flash-dumps/` | This board's original x8x8 and x4 flash contents (1 MiB each) and the SBRs cut from them | |
| `images/` | Our x16 images: A (used, working) and B (fallback variant) | |

`write-sbr` refuses unless all of these hold:

- the running port widths show no x8 code, so it can never write the x8x8 chip
- the chip is the Winbond seen at the x4 position, with no protection bits set
- the chip's first 1 MiB is exactly the original x4 dump, or blank, or a single x16/x4 image this tool wrote,
  and never the x8x8 content
- the image is a valid SBR with x16/x4 codes only

Before writing, it saves the chip's current contents and proves that write-enable latches. It also asks you to
type `WRITE`. After writing, it verifies by reading back.

## Warning

This was done on one board, with one set of images. It worked, but it is undocumented territory: no Broadcom
documentation for the PEX88000 SBR format is public. Back up every image before writing anything, and never
write the chip your working mode boots from. If an image breaks the switch's link to the host, the chip can
only be recovered with an external programmer. The flash is **1.8 V**, so a bare 3.3 V CH341A will damage it.
Use at your own risk.
