# Sources

## Primary: our own board

- Flash dumps from each DIP position (`flash-dumps/`): x8x8 and x4 images, and the x16 position all FF.
- JEDEC ID reads in the FS1-1 and FS1-3 boots: `ff ff ff` vs `ef 60 18`.
- Live register snapshots in the x4 and x16 boots: port pages, widths, link status.
- The seller's spec sheet (DIP table, connector pin table) and test sheet (x16 working on their board).

## Broadcom software

- **Broadcom PCI/PCIe SDK 9.81**, GitHub mirror https://github.com/xiallc/broadcom_pci_pcie_sdk (tag 9.81):
  - `PlxApi/PlxApiDirect.h`: BAR0 regions (`ATLAS_SPI_CS0_PCI_BASE_ADDR` 0x300000, PBAM 0x1C0000, AXI base
    0x60800000, CCR 0xFFF00000), port clock enable registers 0x30C–0x318, upstream register 0x360
  - `PlxApi/SpiFlash.c` / `SpiFlash.h`: SPI controller registers and bits, command framing for read, status,
    write enable, sector erase and page program
  - `Samples/PlxCm/PciDev.c` (`Plx_SpiFileLoad`): in-band programming with the switch running
- **ScrutinyLib** `atlasfwheader.h`, a public copy of Broadcom's Scrutiny library:
  https://github.com/bagalkote/ScrutinyLib/blob/main/scrutiny_lib/hal/switch/atlasfwheader.h. It gives the
  SBR header layout (PSB, PSW0–5, PSWX2, SerDes, 416-byte SoC settings block), checksum seed 0xA5, and "SBR
  starts 0x400 into the flash region". It does not document the width encoding.

## A shipping PEX88096 x16 image (used to confirm the width codes)

- **Lenovo ThinkSystem SR670 V2 PCIe switch card** (PEX88096 B0, 4 GPUs at x16), firmware yprc.b-08b-02.04.05.02:
  - page: https://datacentersupport.lenovo.com/us/en/downloads/ds559768
  - file: https://download.lenovo.com/servers/mig/2022/11/27/56488/lnvgy_fw_pcieswitch_yprc.b-08b-02.04.05.02-0_anyos_x86-64.uxz
    (522,603 bytes, SHA-256 `54759860179bd15434577a9f0f3ff21da9f1a43b33edb0b898ad1b2f8a34dc88`)
  - It is a zip containing `M2_YPRC-08B.upd`, a tar containing `17AA7819_02.04.05.02_pldm.fw`.
    `python3 -I sbr_info.py 17AA7819_02.04.05.02_pldm.fw` finds the SBR at offset `0x7B5A9` (0xB40 bytes).
  - Not redistributed here; download it from Lenovo.
- The dual-uplink sibling card (ds559769, yprc.a-08b-02.04.05.02) has the same SBR except one SoC word.

## Related chips and community reports

- PEX8749 (Gen3) keeps port configuration in register **300h**, set from EEPROM, with 3 bits per station:
  - forum post relaying the vendor's register table: https://forum.level1techs.com/t/pcie-switch-oculink-nvme-and-sata/216488/14
  - Broadcom errata: https://docs.broadcom.com/doc/PEX8749-48-47-33-32-25-24-23-17-16-13-12-Errata-and-Cautions
- PEX8696 / PEX8664: per-station port configuration straps (x16, x8x8, x8x4x4, x4x4x4x4):
  https://docs.broadcom.com/doc/PEX_8696_QSHDG_v1.0_29Mar10.pdf,
  https://docs.broadcom.com/doc/PEX_8664-16U8D_BB_RDK_HRM_v1.2_09Mar10.pdf
- PEX88000 product brief (port widths set independently): https://docs.broadcom.com/doc/BC-0484EN
- Level1Techs discussion of PEX880xx switch boards (one image per flash chip, one chip per DIP setting; the same
  Gen1-x4 fallback on other boards):
  https://forum.level1techs.com/t/a-neverending-story-pcie-3-0-4-0-5-0-bifurcation-adapters-switches-hbas-cables-nvme-backplanes-risers-extensions-the-good-the-bad-the-ugly/171428
