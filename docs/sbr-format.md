# PEX88096 flash and configuration image (SBR) format

What we could establish from this board's two working images, its live registers, Broadcom's public SDK and
Lenovo's PEX88096 firmware. No Broadcom document for this format is public. Things marked *unknown* are
unknown.

## Reaching the switch from Linux

The upstream port (Broadcom `1000:c010`; ours is the topmost Broadcom bridge) has a **16 MiB BAR0**. It maps
the chip's internal AXI space from `0x60000000`, and Broadcom's SDK (`PlxApi/PlxApiDirect.h`) names these
regions:

| BAR0 offset | What | Used by |
|---|---|---|
| `0x800000 + port*0x1000` | per-port register page: PCIe config space plus vendor registers (AXI `0x60800000`) | `pex_regs_snapshot.py` |
| `0x1C0000` | SPI controller "PBAM" (manual command interface) | `pex_flash_prog.py` |
| `0x300000` | memory-mapped read window onto SPI **chip select 0** (13 MiB visible) | `pex_flash_read.py`, verify |

SPI controller registers (from `SpiFlash.c` / `SpiFlash.h`):

| Offset | Register |
|---|---|
| `+0x78` | manual read data (4 bytes come back big-endian) |
| `+0x7C` | I/O mode (0 = serial; the tools refuse to run otherwise) |
| `+0x80` | manual write data (4-byte pieces big-endian, 1-byte pieces in the low byte) |
| `+0x84` | control/status: bits 6:0 = length in bits (8 or 32), chip select from bit 7, `LAST` = bit 11 (end of chip-select cycle), `WRITE` = bit 12, `ATOMIC` = bit 14 (more commands follow), `VALID` = bit 16 (set to start, poll until clear) |

Write sequences follow the SDK's own:

- sector erase: WREN with `ATOMIC`, then `20 aa aa aa` (the SDK uses `D8`, the 64 KiB erase)
- page program: WREN with `ATOMIC`, then `02 aa aa aa` with "more data", then the data in 4-byte pieces
- in both cases, poll status register 1 until the busy bit clears

The SDK supports chip select 0 only. On this board, the DIP switch decides which physical chip answers on chip
select 0.

## Flash layout

```
0x000000 - 0x0003FF   FF
0x000400 - ...        SBR (2.8-2.9 KB here)
...      - end        FF
```

Broadcom's Scrutiny library header says the SBR starts 0x400 into its flash region. The board's x8x8 chip also
has zero bytes from the end of its SBR up to 0x1000, which looks like padding from whatever tool programmed it.
The switch ignores them, since the SBR length comes from its header.

## SBR layout (offsets from the magic word)

| Offset | Content |
|---|---|
| `+0x000` | magic `C0103DC4` (little-endian bytes `c4 3d 10 c0`) |
| `+0x004 / +0x008` | **PSB** offset (always `0x1FC`) / length: per-port register writes, which we called "section B" |
| `+0x00C … +0x048` | other section pointers (PSW0–5, PSWX2 in Scrutiny's naming); unused in all images seen |
| `+0x04C / +0x050` | **SerDes** section offset / length: (address, value) writes, "section C" |
| `+0x054 / +0x058` | reserved |
| `+0x05C … +0x1FB` | **SoC settings block**, 416 bytes; the port-width codes are inside it |
| `+0x1FC …` | PSB entries: 8 bytes each, `(value, code)`; `code = (0x0F or 0x1F) << 24 \| dword index`, and `dword index * 4 = port * 0x1000 + register offset` |
| after PSB | SerDes entries: 8 bytes each, `(AXI address, value)`; mostly PHY (`0x7F00xxxx`, `0x7B04xxxx`, `0x7305xxxx`), a few chip registers (`0x6000xxxx`, `0x6041xxxx`, CCR `0xFFF00xxx`) |
| last 4 bytes | checksum byte, then three zero bytes |

**Checksum:** `(0xA5 + sum of every byte of the SBR, checksum byte included) mod 256 == 0`. It holds for this
board's two images and for Lenovo's.

## Port-width codes

There is one 3-bit code per x4 lane group: 24 groups, 4 per station, 6 stations of 16 lanes. They are packed
little-endian from **bit 16 of the word at `+0x5C`**:

```
group i (0..23) = (int.from_bytes(sbr[0x5C:0x68], "little") >> (16 + 3*i)) & 7
station s = groups 4s .. 4s+3 = lanes 16s .. 16s+15
```

At boot the firmware copies them into **port 0 registers `0x300`, `0x304`, `0x308`**: 16 bits per station
(station 0 in the low half of `0x300`), four 3-bit codes each.

| Code | Meaning | Evidence |
|---|---|---|
| 0 | the whole station is one x16 port | upstream station in both board images (runs x16); every station in Lenovo's 4-GPU x16 card; our x16 image (five downstream x16 links) |
| 1 | x4 port | board's x4 image; live register `0x249` per station; x4 ports seen |
| 7 | x8 port (two per station) | board's x8x8 image; x8 ports seen |
| others | unknown on this chip | — |

With no image at all, the chip defaults to x4 everywhere: upstream x4 Gen1 and 23 x4 downstream ports.

The bits below 16 in `+0x5C` are something else: `0x0300` on this board, `0x03FD` on Lenovo's.

## Other registers seen (port 0 page)

| Register | Content |
|---|---|
| `0x20C` | config "date", written by a PSB entry: BCD MMDDYYYY. `06042019` is Broadcom's reference value; this board's x4 build used `02242021`; our images use `11172026` (A) / `11182026` (B). Not a mode setting. |
| `0x300–0x308` | port-width codes, as above |
| `0x30C / 0x310 / 0x314` | port clock enables, one bit per port for ports 0–31 / 32–63 / 64–95 (SDK). x4 boot: `11110001 11111111 11111111`; x16 boot: `00010001` ×3 |
| `0x318` | port clock enable for ports 96–127 (the SDK maps bits to the x1 management ports 116/117); the x4 build writes 0 here |
| `0x360` | upstream port number (SDK, `ATLAS_REG_VS0_UPSTREAM`) |

## This board's images vs Lenovo's

These are non-zero words of the SoC settings block. Values differing between boards are board-specific
(clocking, I/O, management). They were kept from this board's image.

| Offset | Board x8x8 | Board x4 | Our x16 (A) | Lenovo x16 |
|---|---|---|---|---|
| `+05C` | `f0000300` | `90000300` | `00000300` | `000003fd` |
| `+060` | `ffffffff` | `24924924` | `00000000` | `00000000` |
| `+064` | `00ffffff` | `00249249` | `00000000` | `00000000` |
| `+068` | `004c0000` | `004c0000` | `004c0000` | `404d0000` |
| `+06C` | `5500a880` | `5500a880` | `5500a880` | `5000a480` |
| `+078` | 0 | 0 | 0 | `00000421` |
| `+07C` | 0 | 0 | 0 | `0013003f` |
| `+084` | `00000411` | 0 | 0 | 0 |
| `+088` | `000c0000` | 0 | 0 | 0 |
| `+0B4` | `10000000` | `10000000` | `10000000` | `00000f40` |
| `+0D4` | 0 | 0 | 0 | `00000003` |
| `+1F8` | 0 | `00001002` | 0 | 0 |

Words common to all of them (`+0B8/+0BC` an ARM jump stub, `+140`, `+1E0`, `+1E8`, `+1EC`, `+1F0`) are not
shown.

PSB and SerDes contents:

- **Board x8x8 / our x16:**
  - PSB: 9 entries, the date plus 8 port-0 writes
  - SerDes: 280 PHY writes plus `60410064=1f`, `600010f8=7f`, `fff004f8=318`
- **Board x4:** the same, plus:
  - PSB: port 112 `+f30=2`, port 251 `+f60=90000002`, port 112 `+7b4=0`, port 0 `+318=0`
  - SerDes: `fff000b8=c0001800`, `60410060=ff`, `60001148=8000`
- **Lenovo:**
  - PSB: the same 8 port-0 writes and the same date, plus port 0 `+42c=2`
  - SerDes: the **identical 280 PHY writes**, plus `2a0a0010=c8`, `2a0a000c=1`, `2a0a0014=8` and the same three chip writes

## What our x16 image changes vs the board's x8x8 image

```
flash 0x045c: f0000300 -> 00000300     width codes, stations 1-5: 7 -> 0
flash 0x0460: ffffffff -> 00000000
flash 0x0464: 00ffffff -> 00000000
flash 0x0484: 00000411 -> 00000000     unknown pair, zeroed as in Lenovo x16 / board x4  (image A only)
flash 0x0488: 000c0000 -> 00000000
flash 0x05fc: 06042019 -> 11172026     config date (identifies image A)
flash 0x0f1c: 0000000e -> 000000ed     checksum byte
```

## Unknown

- the meaning of `+084/+088`, the x4 build's extra writes, and most of the SoC block
- codes other than 0/1/7 on this chip
- whether other boards' SoC settings would work on this one (we never tried; we kept the board's own)
