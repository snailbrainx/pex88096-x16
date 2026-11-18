# Mixed widths: x16 and x4 on the same board (untested)

> **Untested.** Nobody has booted an image like this. What follows is what the image format, as far as we
> understand it ([sbr-format.md](sbr-format.md)), says should be possible. No working board has shown it yet.

## The idea

The switch sets port widths **per station**: six stations of 16 lanes each, not one mode for the whole chip.
Station 0 is the uplink to the host and stays x16. Stations 1–5 are the connector pairs A–E, and each could
be configured on its own:

- **x16:** one device on the pair, such as a GPU
- **x4 x4 x4 x4:** four devices on the pair, such as NVMe drives

So the board could, for example, run four GPUs at x16 and four NVMe drives at x4:

```
16 (uplink) + 4 x 16 (GPUs on A-D) + 4 x 4 (drives on E) = 96 lanes
```

## Why we think it should work

- **The format stores widths per lane group.** The image holds one 3-bit code per x4 lane group (24 of them),
  and the firmware copies them per station into port 0 registers `0x300–0x308`.
- **Both codes are proven on this board.** Code 0 (x16) runs in our x16 image, and code 1 (x4) in the maker's
  x4 image.
- **Stations already differ within one image.** Every image we have runs station 0 at x16 next to downstream
  stations at x8 or x4.
- **Broadcom documents independent widths.** The PEX88000 product brief says port widths are set
  independently. The older PEX8696 had a separate width strap for each station.

## Example: four GPUs at x16, four NVMe drives at x4

The example image starts from `images/sbr-x16-A.bin`, the image that works here. It changes only station 5's
width codes (0 → 1), the config-date word and the checksum byte:

```
$ python3 -I make_mixed_sbr.py --layout x16,x16,x16,x16,x4
images/sbr-mixed-x16-x16-x16-x16-x4.bin: 2848 bytes, sha256 9598c763bef53397..., checksum byte 0x30
UNTESTED: no image like this has been booted. Read docs/mixed-widths.md before writing it.
   pair A (station 1, lanes 16-31): one x16 port, switch port 16
   pair B (station 2, lanes 32-47): one x16 port, switch port 32
   pair C (station 3, lanes 48-63): one x16 port, switch port 48
   pair D (station 4, lanes 64-79): one x16 port, switch port 64
   pair E (station 5, lanes 80-95): four x4 ports, switch ports 80, 84, 88, 92
   flash 0x0464: 00000000 -> 00249000
   flash 0x05fc: 11172026 -> 11202026
   flash 0x0f1c: 000000ed -> 00000030
```

| Pair | Connectors | Width | Switch ports | Use |
|---|---|---|---|---|
| A | A1 + A2 | x16 | 16 | GPU |
| B | B1 + B2 | x16 | 32 | GPU |
| C | C1 + C2 | x16 | 48 | GPU |
| D | D1 + D2 | x16 | 64 | GPU |
| E | E1 | x4, x4 | 80, 84 | two NVMe drives |
| E | E2 | x4, x4 | 88, 92 | two NVMe drives |

`--layout` takes five widths, for pairs A–E in order, each `x16` or `x4`. For example, `x16,x16,x16,x4,x4`
gives three GPUs and eight drives. The script builds the image only; it writes nothing to hardware. Built
images are not committed.

### Writing and checking it

Writing and checking work exactly as for the x16 image ([reproduce.md](reproduce.md), steps 4 and 5). In the
FS1-3 boot:

```
sudo python3 -I pex_flash_prog.py write-sbr --image images/sbr-mixed-x16-x16-x16-x16-x4.bin
```

- **The writer accepts it.** The image contains only x16 and x4 codes, and the chip currently holds an image
  the writer itself wrote.
- **Going back works the same way.** Once the mixed image is on the chip, `write-sbr` will still write
  `images/sbr-x16-A.bin` back over it.
- **Both directions are covered in `tests/sim_test.py`.**

After a cold boot, `pex_regs_snapshot.py` should show:

- port 0 `0x20C` = `11202026`
- `0x300` = `0x304` = 0, and `0x308` = `02490000`, which means station 5 is four x4 ports
- ports 16, 32, 48 and 64 at x16, and ports 80, 84, 88 and 92 at x4

## What is not known

- **Whether x4 ports need the x4 image's extra writes.** Besides its width codes, the maker's x4 image has
  extra entries that the x8x8 and x16 images lack:
  - PSB: port 112 `+f30=2`, port 251 `+f60=90000002`, port 112 `+7b4=0`, port 0 `+318=0`
  - SerDes section: `fff000b8=c0001800`, `60410060=ff`, `60001148=8000`
  - SoC settings block: `+1F8=00001002`

  The example leaves all of them out. If the x4 ports don't come up, adding them is the next thing to try. That
  means lengthening the PSB and SerDes sections and updating the header words at `+0x08`, `+0x4C` and
  `+0x50`; the x4 image shows how they change.
- **How the drives get clock and reset.** Each SlimSAS 8i connector carries 8 lanes, so pair E would feed two
  drives per connector, through 8i-to-2× U.2 cables or an 8i-to-2× M.2 adapter. The seller's pin table lists
  one reference clock, PERST# and PRSNT# per connector, yet the maker sells the x4 mode as "20 devices". Check
  how two drives on one connector get their clock and reset before buying cables.
- **Uneven splits inside one station, such as x8 + x4 + x4.** The codes are per x4 group, so the format can
  express this, but no image we've seen does it. We don't know which code combination the chip expects, so
  the script doesn't offer it.
- **x8x8 stations.** These are possible in principle, using code 7. However, `pex_flash_prog.py` treats any
  x8 code as the sign of the never-write x8x8 chip and refuses to write, by design, so the script offers only
  x16 and x4.

## Bandwidth

- **Drives:** the switch is PCIe 4.0, so each x4 drive gets Gen4 x4, about 7 GB/s.
- **Shared uplink:** every device shares the single Gen4 x16 link to the host, about 32 GB/s. Four fast drives
  (about 28 GB/s) nearly fill it on their own, so drive↔CPU and GPU↔CPU traffic compete for it.
- **Drive↔GPU transfers** (peer-to-peer, GPUDirect Storage) can stay inside the switch without touching the
  uplink.

## Risk

The risk is the same as for the x16 image:

- **Fallback:** FS1-2 stays the never-written x8x8 fallback.
- **Rejected image:** if the switch rejects the image, it should come up in its fallback mode (upstream x4, 2.5 GT/s),
  and the chip can be rewritten from Linux.
- **Switch disappears:** if the switch doesn't appear at all on FS1-3, recovering that chip needs an external
  1.8 V programmer (see Recovery in [reproduce.md](reproduce.md)).
