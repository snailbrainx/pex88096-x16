# The board

- **Silkscreen:** RTE88096-A54, PCB rev 1.1 (seller's spec sheet v1.0). The maker is unknown.
- **Switch:** Broadcom PEX88096, PCIe 4.0, 96 lanes. It reports Broadcom's default IDs (`1000:c010`,
  subsystem `1000:a096`), with no board-maker ID.
- **Host side:** an x16 card edge, PCIe 4.0.
- **Device side:** ten SlimSAS 8i connectors in pairs A1/A2, B1/B2, C1/C2, D1/D2, E1/E2. Per the seller's pin
  table, the "1" connector carries lanes 0–7 of a slot and the "2" connector lanes 8–15. Each connector has
  its own reference clock, PERST# and PRSNT#.
- **Risers:** each GPU sits on an x16 riser fed by two cables, one from each connector of its pair. The seller's
  test sheet uses the RBS-16G4-2P54 riser, with port 1 marked "8X" and port 2 marked "16X".
- **Near the DIP switch:** a 32-pin QFN that looks like a microcontroller (possibly NXP), a small 8-pin chip
  (U2), and a 4-pin header on the top edge (probably a serial or I2C management port).

## DIP switch FS1 (one switch ON at a time)

| DIP | Silkscreen / spec sheet | What it did on our board |
|---|---|---|
| FS1-1 | x16: five devices, each on a connector pair | fallback: no chip answers behind it (probably never fitted) |
| FS1-2 | x8x8: ten devices | x8x8 |
| FS1-3 | x4x4x4x4: twenty devices | x4; **now x16, after rewriting its chip** |

The DIP selects which flash chip the switch boots from: a different chip answers on SPI chip select 0 in each
position. The FS1-3 chip is a **Winbond W25Q128JW (JEDEC `ef 60 18`, 1.8 V, 16 MiB)**. We never identified
the FS1-2 chip, since it was only ever read, never written. Behind FS1-1 nothing answers (`ff ff ff`).

## Switch topology (as seen in our x16 boot)

```
host ── port 0 (x16 Gen4, upstream, station 0)
         ├─ internal 224 → 232 ── port 16  (connectors A1+A2)  x16
         ├─ internal 225 → 233 ── port 32 (B), port 48 (C)     x16 each
         ├─ internal 226 → 234 ── port 64 (D), port 80 (E)     x16 each
         └─ internal 227 → 235 ── ports 116 / 117 (x1, management endpoints)
```

- Port numbers are lane numbers, so the five x16 groups start at lanes 16, 32, 48, 64 and 80.
- The internal ports are x16 Gen4 links between the root switch and its sub-switches. B+C share one, and so
  do D+E.
- In x4 mode the same three sub-switches carry ports 16–28, 32–60 and 64–92.
- All five cards share the one Gen4 x16 uplink to the host (about 32 GB/s). That only matters when several cards
  talk to the CPU at the same moment. Card-to-card traffic stays inside the switch.

## Behaviour without an image

Booting from an empty position (FS1-1) gives the chip's built-in defaults:

- upstream x4 at 2.5 GT/s
- every port's capability at Gen1
- 23 x4 downstream ports plus x1 ports 116/117 and a management endpoint
- none of the device connectors brought up

The register window (BAR0) and the SPI controller still work in this state, so a chip behind that position
could still be written from Linux.
