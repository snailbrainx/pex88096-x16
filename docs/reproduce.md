# Doing it on another board

This was done on one board. If yours is the same RTE88096-A54 with the same images, the steps below should
carry over; the tools check the images byte for byte and refuse anything they don't recognise. If your images
differ, use the tools to read and understand them first, and adapt.

You need Linux, root, Python 3 and nothing else: no Broadcom driver or SDK. Read the whole page first.

## 0. Ground rules

- Keep one DIP position (x8x8 here) as your **never-written** fallback.
- Dump and back up every readable image **before** writing anything, and keep copies off the machine.
- Cold boot (PSU off) between DIP changes. The switch reads its image only at power-up.
- Have nothing running on the GPUs while writing. The switch keeps running during the write (it reads flash
  only at boot), but there's no reason to take chances.

## 1. Find the switch and dump each mode

```
lspci -d 1000:c010            # the PEX88096 bridges; the tools find the upstream one themselves
sudo python3 -I pex_flash_read.py --label x8x8      # in the FS1-2 boot
sudo python3 -I pex_flash_read.py --label x4        # in the FS1-3 boot
sudo python3 -I pex_flash_read.py --label x16       # in the FS1-1 boot (ours: all FF)
```

Each run reads the window twice and compares the two reads. It saves `flash-dumps/pex-flash-<label>-<time>.bin`
and prints a summary. `python3 -I sbr_info.py <dump>` decodes what was found.

## 2. Is there a chip behind the x16 position?

```
sudo python3 -I pex_flash_prog.py id                # FS1-1 boot, then again in the FS1-3 boot as a control
```

- **A real ID in both boots, x16 chip blank:** the chip exists but is empty. You can write the x16 image straight
  into it with `pex_flash_prog.py program --image images/sbr-x16-A.bin`. That command only writes a fully blank
  chip, only in the fallback boot, and never erases. Your x4 chip stays intact.
- **`ff ff ff` at FS1-1 but a real ID at FS1-3** (our case): no chip. Rewrite the x4 chip instead (steps 3–5).
- **`ff ff ff` everywhere:** the tool isn't reaching the chip on your board. Stop.

`pex_regs_snapshot.py --label <name>` saves and decodes the live port registers at any point. It is read-only.

## 3. Build the image

```
python3 -I make_x16_sbr.py
```

This builds `images/sbr-x16-A.bin` (the one that worked here) and `-B.bin`. It insists the input is exactly
this board's x8x8 SBR (`flash-dumps/sbr-x8x8-0x400-0xF20.bin`, checked by SHA-256). If your x8x8 image differs,
compare the two with `sbr_info.py`. The only change needed is the width codes (7 → 0 for the downstream
stations) plus the checksum byte. Keep everything else from your own board.

## 4. Write it (into the x4 chip, in the FS1-3 boot)

```
sudo python3 -I pex_flash_prog.py write-sbr --image images/sbr-x16-A.bin
```

The interlocks compare the chip against the dumps in `flash-dumps/`: `X4_DUMP`, `X8_DUMP` and `X8_SBR` near the
top of `pex_flash_prog.py`, each with a SHA-256 prefix. If your images differ from ours, it will refuse
("UNKNOWN content"). Put your own dumps there and update those three entries; never point them at anything you
haven't verified.

Expected output (ours):

```
port widths running now: stn0 x16,... | stn1 x4,x4,x4,x4 | ...
chip holds now: x4 original (matches the backup exactly)
write-enable test: after WREN status 0x02, after WRDI 0x00
Type WRITE to erase and program: WRITE
erased sector 0 in 1.74 s; 1 MiB window now blank
programmed 2848 bytes in 0.01 s
verify: the chip holds exactly the image at 0x400 and FF elsewhere
```

## 5. Cold boot and check

Shut down, switch the PSU off, keep FS1-3, power up:

```
sudo python3 -I pex_regs_snapshot.py --label x16
nvidia-smi --query-gpu=pci.bus_id,pcie.link.gen.current,pcie.link.width.current --format=csv
```

Port 0 registers `0x300–0x308` should read all zero, and `0x20C` should be `11172026`. The downstream ports 16,
32, 48, 64 and 80 should report max x16, with the cards linked x16. GPU PCI bus numbers change from x8x8 mode, so
update anything that refers to cards by bus address.

## Recovery

| Situation | What to do |
|---|---|
| Anything goes wrong | FS1-2 (the untouched x8x8 chip) and a cold boot gets you back |
| Switch comes up in fallback on FS1-3 (upstream x4 2.5 GT/s): image rejected | still reachable: `write-sbr` again (it accepts a blank chip or one of its own images) |
| Image boots, but cards don't train x16 | try `images/sbr-x16-B.bin`, or restore x4 |
| Want the x4 mode back | in the FS1-3 boot: `sudo python3 -I pex_flash_prog.py write-sbr --image flash-dumps/sbr-x4-0x400-0xF58.bin` (gives back the exact original chip contents) |
| Switch no longer appears on FS1-3 at all | FS1-2 for normal use. Recovering that chip needs an external SPI programmer with a **1.8 V** adapter and a SOIC-8 clip (if the chip has legs); write the saved `before-write-*.bin` back with flashrom |

`tests/sim_test.py` runs every write path and every refusal against a simulated controller and chip. Run it
after changing anything in `pex_flash_prog.py`.
