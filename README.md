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
| Root image (`.sfs`) | LUKS2 → f2fs        | **yes**   | yes (read-only)        |
| Running root     | RAM (tmpfs overlay)   | n/a       | **no — wiped on reboot**² |

² …unless you explicitly save it with `archram-persist snapshot` (full system)
or `archram-persist save` (incremental); see [Persistence](#persistence).

¹ UEFI cannot read LUKS, so the bootloader/kernel/initramfs must sit on an
unencrypted ESP. Only the OS image itself is encrypted. The bootloader and
kernel are **Secure Boot signed** so the firmware refuses tampered binaries —
see [Secure Boot](#secure-boot) and [Security notes](#security-notes).

## Disk layout

```
/dev/<disk>
├─ part1  ESP    FAT32   (default 1 GiB)  →  /vmlinuz-linux, /initramfs-linux.img, systemd-boot (signed)
└─ part2  LUKS2  f2fs    (rest of disk)   →  /airootfs.sfs   (squashfs root image)
```

The filesystem inside the LUKS container is **f2fs** by default (flash-friendly,
well suited to the SSD/USB media this kind of image usually lives on). Override
with `DATA_FS=ext4` if you prefer.

## Boot flow

1. The firmware verifies and runs the Secure Boot–signed `systemd-boot`, which
   loads the signed kernel + initramfs from the ESP.
2. The `ramboot` initramfs hook prompts for the LUKS passphrase, unlocks the
   partition and mounts the f2fs filesystem.
3. The squashfs image is **copied into a tmpfs** (`copytoram=yes`).
4. The disk is **unmounted and the LUKS mapping closed** — from here nothing
   touches storage; the drive can even be physically removed.
5. If a persistence snapshot exists (and `ramboot_persist!=no`), it is
   **restored into the fresh tmpfs upper** before the overlay is assembled.
6. An **overlayfs** is assembled: read-only squashfs (lower) + tmpfs (upper).
7. The cow tmpfs is bind-mounted at `/var/lib/ramboot/cow` so it can be
   snapshotted later, then `switch_root` hands off to the in-RAM system.

## Requirements

- A machine booted from the **Arch Linux live ISO in UEFI mode**.
- Working internet (the installer runs `pacstrap`).
- Enough RAM: at runtime you need roughly **`squashfs size` + working set**. A
  minimal base image is ~400–600 MB, so 2 GB RAM is a comfortable floor; add
  more for desktops or large `EXTRA_PACKAGES`.
- For Secure Boot **key enrollment**, the firmware must be in **Setup Mode**
  (clear/erase the existing Secure Boot keys in your firmware setup screen
  first). Without Setup Mode the installer still creates keys and signs the
  boot chain, but you must enroll the keys yourself later. See
  [Secure Boot](#secure-boot).

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
| `DATA_FS`         | `f2fs`         | Filesystem in the LUKS container (`f2fs` or `ext4`)|
| `ESP_SIZE`        | `1GiB`         | EFI partition size                                 |
| `SQUASH_COMP`     | `zstd`         | `mksquashfs` compressor (`zstd`, `xz`, `gzip`, …)  |
| `COW_SIZE`        | `50%`          | Writable overlay tmpfs size cap at runtime         |
| `PERSIST`         | `auto`         | Restore a saved overlay at boot (`auto`/`yes`/`no`)|
| `EXTRA_PACKAGES`  | *(empty)*      | Extra pacman packages, space separated             |
| `LUKS_PASSPHRASE` | *(prompted)*   | Disk encryption passphrase                         |
| `ROOT_PASSWORD`   | *(prompted)*   | Root account password                              |
| `USER_PASSWORD`   | *(root's)*     | Password for `USERNAME_`                            |
| `ASSUME_YES`      | `0`            | `1` skips the destructive-wipe confirmation        |
| `SECUREBOOT`      | `1`            | `1` create keys, (maybe) enroll, sign boot chain   |
| `SB_ENROLL`       | `auto`         | `auto`/`yes`/`no` — enroll keys into firmware      |
| `SB_MICROSOFT`    | `1`            | `1` also enroll Microsoft vendor certs (`-m`)      |
| `SB_KEYDIR`       | *(unset)*      | Persistent sbctl keystore to reuse across installs |

## Secure Boot

The installer uses [`sbctl`](https://github.com/Foxboron/sbctl) to:

1. **Create** your own Secure Boot keys (Platform Key, KEK, db).
2. **Enroll** them into the firmware — only when the firmware is in **Setup
   Mode** (`SB_ENROLL=auto`), or unconditionally with `SB_ENROLL=yes`. The
   Microsoft vendor certificates are enrolled too (`SB_MICROSOFT=1`) so
   firmware/option-ROMs signed by Microsoft keep working.
3. **Sign** the bootloader (`systemd-bootx64.efi`, `BOOTX64.EFI`) and the
   kernel (`vmlinuz-linux`). With Secure Boot enabled the firmware then refuses
   to run any unsigned/tampered bootloader or kernel.

Put the firmware in **Setup Mode** before installing (clear the existing keys in
your firmware's Secure Boot menu), then enable Secure Boot afterwards. If you
skip Setup Mode, keys are still created and the chain is signed; enroll later
from a running system or the firmware:

```bash
sbctl enroll-keys --microsoft   # requires Setup Mode
```

Reuse the same keys across re-installs by pointing `SB_KEYDIR` at persistent
media (e.g. a USB stick): `SB_KEYDIR=/run/media/usb/sbkeys ./archram-install.sh`.

> **Initramfs caveat.** Secure Boot validates the bootloader and kernel, but the
> **separate initramfs is not signature-checked** — an attacker who can write to
> the ESP could swap it and capture your LUKS passphrase. To close this gap,
> build a **Unified Kernel Image** (kernel + initramfs + cmdline in one signed
> EFI binary) via mkinitcpio's UKI preset and sign that instead. This installer
> ships the standard signed-bootloader-and-kernel setup; UKI is a documented
> hardening step on top.

## Repository layout

```
archram-install.sh                         # the installer (run from the live ISO)
airootfs/                                  # skeleton copied into the new root
├─ etc/
│  ├─ initcpio/
│  │  ├─ hooks/ramboot                     # runtime hook: unlock, restore, copy-to-RAM, overlay
│  │  └─ install/ramboot                   # build hook: pulls modules/binaries in
│  └─ mkinitcpio.conf.d/ramboot.conf       # HOOKS/MODULES/compression for the image
├─ usr/local/bin/archram-persist           # snapshot the RAM overlay back to the drive
└─ var/lib/ramboot/cow/                     # bind-mount point for the live cow tmpfs
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
| `ramboot_persist=`| `auto`           | restore saved upper (`auto`/`yes`/`no`)  |

By default the initramfs is built **without `autodetect`** so the image is
portable across machines. If you only ever boot on the build host and want a
smaller initramfs, add `autodetect` after `base udev` in
`airootfs/etc/mkinitcpio.conf.d/ramboot.conf`.

## Persistence

The system is amnesiac by default — every change lives in the RAM overlay and
is gone on reboot. When you *do* want to keep what you changed, write it back
onto the encrypted drive with the bundled **`archram-persist`** tool. It has two
modes:

| Mode                       | What it saves                                   | Result on next boot |
| -------------------------- | ----------------------------------------------- | ------------------- |
| `archram-persist snapshot` | **The entire system** — re-images all of `/` into a new squashfs and replaces the base image (`/airootfs.sfs`) | The whole current system is the new base |
| `archram-persist save`     | Only the overlay **diff** (changes since the base), as `/persist/upper.tar.zst` | Diff is restored on top of the base |

Both write into the LUKS-encrypted partition, so snapshots are **encrypted at
rest**, and each keeps one `.bak`.

Because the default `copytoram=yes` releases the disk at boot, the drive may
have been removed. **Re-insert it before saving** — the tool waits for the
drive, re-unlocks LUKS (prompting for the passphrase), mounts it read-write just
long enough to write, then closes it again.

```bash
# After changing the running system, re-insert the drive and pick a mode:
sudo archram-persist snapshot    # full system image -> drive (encrypted)
sudo archram-persist save        # or: just the incremental overlay diff
archram-persist status           # show drive info and sizes
```

How it works:

- The initramfs bind-mounts the writable overlay layer (the tmpfs `upper`) into
  the running system at `/var/lib/ramboot/cow`, so it stays reachable after
  `switch_root`.
- **`snapshot`** runs `mksquashfs /` (excluding virtual filesystems, the package
  cache and the cow/drive mounts) to build a fresh full-system image, then
  atomically swaps it in as the boot image and clears the now-redundant diff.
- **`save`** archives the `upper` with `tar --zstd`, preserving overlay
  whiteouts, ACLs and `trusted.*` xattrs so deletions are reproduced. The
  `ramboot` hook restores it into the fresh RAM upper at boot (`ramboot_persist=`,
  default `auto`).

To boot amnesiac for one session, edit the boot entry and set
`ramboot_persist=no`; to disable diff restore permanently, install with
`PERSIST=no`.

> **Notes.** Saving requires `copytoram=yes` (the default); with `copytoram=no`
> the drive stays mounted for the running system and can't be re-mounted
> read-write. A `snapshot` re-images only the root filesystem — the
> kernel/initramfs on the ESP are untouched, so if you **updated the kernel**,
> re-run the installer to refresh and re-sign the ESP boot files. A full-system
> image is larger, so remember the RAM-at-boot requirement grows with it.

## Updating the installed system

Because the running root is in RAM, changes do not persist. To change packages
or configuration, **re-run the installer** (it rebuilds and rewrites the
squashfs). The disk passphrase and layout can be reused; the erase step still
applies, so back up anything you stored elsewhere on that disk.

For lightweight tweaks without a full reinstall you can instead mount the LUKS
partition, replace `/airootfs.sfs` with a freshly built squashfs, and update
the ESP kernel/initramfs if the kernel changed.

## Security notes

- **The ESP is not encrypted**, but the bootloader and kernel are **Secure Boot
  signed**, so the firmware rejects tampered binaries (mitigating "evil maid"
  attacks on those files). The **initramfs is still not covered** by Secure
  Boot — see the [Secure Boot](#secure-boot) UKI caveat for full coverage and
  optional TPM measured-boot sealing.
- Secure Boot only protects you once it is **enabled in firmware with your keys
  enrolled**. Putting the firmware in Setup Mode and re-enabling Secure Boot is a
  manual step (see [Secure Boot](#secure-boot)).
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
