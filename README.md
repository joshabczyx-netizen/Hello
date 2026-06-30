# archram — RAM-booted, LUKS-encrypted Arch Linux installer

`archram-install.sh` provisions a **fully amnesiac** Arch Linux system onto a
drive you select. The root filesystem is stored as a **squashfs image inside a
LUKS2-encrypted partition**. At boot the system unlocks the disk, **copies the
image into RAM**, closes the disk, and runs **entirely from memory** on an
overlayfs. Every change made while running is discarded on the next reboot.

This is the archiso `copytoram` idea applied to a normal, installed,
encrypted disk — think of it as a personal, encrypted, live-USB-style OS that
lives on an internal drive.

## What you get

| Layer            | Where it lives        | Encrypted | Persists across reboot |
| ---------------- | --------------------- | --------- | ---------------------- |
| Kernel + initramfs | EFI System Partition | no¹      | yes                    |
| Root image (`.sfs`) | LUKS2 partition      | **yes**   | yes (read-only)        |
| Running root     | RAM (tmpfs overlay)   | n/a       | **no — wiped on reboot** |

¹ UEFI cannot read LUKS, so the bootloader/kernel/initramfs must sit on an
unencrypted ESP. Only the OS image itself is encrypted. See
[Security notes](#security-notes).

## Disk layout

```
/dev/<disk>
├─ part1  ESP    FAT32   (default 1 GiB)  →  /vmlinuz-linux, /initramfs-linux.img, systemd-boot
└─ part2  LUKS2  ext4    (rest of disk)   →  /airootfs.sfs   (squashfs root image)
```

## Boot flow

1. `systemd-boot` (on the ESP) loads the kernel + initramfs.
2. The `ramboot` initramfs hook prompts for the LUKS passphrase and unlocks the
   partition.
3. The squashfs image is **copied into a tmpfs** (`copytoram=yes`).
4. The disk is **unmounted and the LUKS mapping closed** — from here nothing
   touches storage; the drive can even be physically removed.
5. An **overlayfs** is assembled: read-only squashfs (lower) + tmpfs (upper).
6. `switch_root` hands off to the in-RAM system.

## Requirements

- A machine booted from the **Arch Linux live ISO in UEFI mode**.
- Working internet (the installer runs `pacstrap`).
- Enough RAM: at runtime you need roughly **`squashfs size` + working set**. A
  minimal base image is ~400–600 MB, so 2 GB RAM is a comfortable floor; add
  more for desktops or large `EXTRA_PACKAGES`.

## Usage

From the live ISO, clone this repo (or copy the files over) and run:

```bash
# Interactive — prompts for disk, passphrase, and passwords:
sudo ./archram-install.sh
```

Fully non-interactive example:

```bash
sudo TARGET_DISK=/dev/nvme0n1 \
     LUKS_PASSPHRASE='correct horse battery staple' \
     ROOT_PASSWORD='change-me' \
     HOSTNAME_=rambox \
     USERNAME_=josh \
     EXTRA_PACKAGES='git tmux htop' \
     ASSUME_YES=1 \
     ./archram-install.sh
```

> ⚠️ The target disk is **completely erased**. Double-check `TARGET_DISK`.

### Configuration variables

| Variable          | Default        | Meaning                                            |
| ----------------- | -------------- | -------------------------------------------------- |
| `TARGET_DISK`     | *(prompted)*   | Whole-disk device to install onto                  |
| `HOSTNAME_`       | `archram`      | System hostname                                    |
| `USERNAME_`       | `arch`         | Non-root user (empty string = don't create one)    |
| `TIMEZONE`        | `UTC`          | `zoneinfo` zone, e.g. `Europe/London`              |
| `LOCALE`          | `en_US.UTF-8`  | Locale to generate and use                         |
| `KEYMAP`          | `us`           | Console keymap (also used at the passphrase prompt)|
| `MAPPER`          | `cryptram`     | dm-crypt mapper name                               |
| `ESP_SIZE`        | `1GiB`         | EFI partition size                                 |
| `SQUASH_COMP`     | `zstd`         | `mksquashfs` compressor (`zstd`, `xz`, `gzip`, …)  |
| `COW_SIZE`        | `50%`          | Writable overlay tmpfs size cap at runtime         |
| `EXTRA_PACKAGES`  | *(empty)*      | Extra pacman packages, space separated             |
| `LUKS_PASSPHRASE` | *(prompted)*   | Disk encryption passphrase                         |
| `ROOT_PASSWORD`   | *(prompted)*   | Root account password                              |
| `USER_PASSWORD`   | *(root's)*     | Password for `USERNAME_`                            |
| `ASSUME_YES`      | `0`            | `1` skips the destructive-wipe confirmation        |

## Repository layout

```
archram-install.sh                         # the installer (run from the live ISO)
airootfs/                                  # skeleton copied into the new root
└─ etc/
   ├─ initcpio/
   │  ├─ hooks/ramboot                     # runtime hook: unlock, copy-to-RAM, overlay
   │  └─ install/ramboot                   # build hook: pulls modules/binaries in
   └─ mkinitcpio.conf.d/ramboot.conf       # HOOKS/MODULES/compression for the image
```

## The `ramboot` hook

The hook reads these kernel command-line parameters (set automatically by the
installer in `loader/entries/archram.conf`):

| Parameter         | Default          | Meaning                                  |
| ----------------- | ---------------- | ---------------------------------------- |
| `ramboot_dev=`    | *(required)*     | LUKS partition, e.g. `UUID=…`            |
| `ramboot_name=`   | `cryptram`       | dm-crypt mapper name                     |
| `ramboot_img=`    | `/airootfs.sfs`  | squashfs path inside the partition       |
| `copytoram=`      | `yes`            | `no` keeps the squashfs on disk (loop)   |
| `ramboot_cowsize=`| `50%`            | writable overlay tmpfs size cap          |

By default the initramfs is built **without `autodetect`** so the image is
portable across machines. If you only ever boot on the build host and want a
smaller initramfs, add `autodetect` after `base udev` in
`airootfs/etc/mkinitcpio.conf.d/ramboot.conf`.

## Updating the installed system

Because the running root is in RAM, changes do not persist. To change packages
or configuration, **re-run the installer** (it rebuilds and rewrites the
squashfs). The disk passphrase and layout can be reused; the erase step still
applies, so back up anything you stored elsewhere on that disk.

For lightweight tweaks without a full reinstall you can instead mount the LUKS
partition, replace `/airootfs.sfs` with a freshly built squashfs, and update
the ESP kernel/initramfs if the kernel changed.

## Security notes

- **The ESP is not encrypted.** The kernel and initramfs are exposed; an
  attacker with physical access could tamper with them (an "evil maid"
  attack). For stronger guarantees, enable **UEFI Secure Boot** and sign the
  boot files, or use a **Unified Kernel Image** with measured boot / TPM
  sealing. This installer does not set that up.
- The LUKS passphrase is read into a shell variable during install. Prefer the
  interactive prompt over `LUKS_PASSPHRASE=` in your shell history/environment.
- The amnesiac design means **no logs, keys, or files written at runtime
  survive a reboot** — a feature for kiosk/forensics-resistant use, but it also
  means you must store anything you want to keep on separate, explicitly-mounted
  media.

## Caveats

- UEFI only (no BIOS/MBR path).
- Single disk, single LUKS partition.
- Swap is intentionally absent (it would defeat the RAM-only model); the system
  is bounded by physical RAM.

## License

MIT — see [LICENSE](LICENSE).
