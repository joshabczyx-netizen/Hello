#!/usr/bin/env bash
#
# archram-install.sh
#
# Install a RAM-booted, LUKS-encrypted Arch Linux system onto a selected drive.
#
# Run this from a booted Arch Linux live ISO (UEFI). It will:
#   1. Let you pick a target drive (or set TARGET_DISK=).
#   2. Wipe it and create a GPT layout: EFI System Partition + LUKS2 partition.
#   3. Build an Arch root filesystem with pacstrap and configure it with the
#      'ramboot' mkinitcpio hook shipped alongside this script.
#   4. Pack that root into a squashfs image and store it inside the LUKS
#      partition; place the kernel + initramfs on the ESP and install
#      systemd-boot.
#
# The installed system unlocks LUKS at boot, copies the squashfs into RAM and
# runs entirely from memory via an overlayfs. All changes are discarded on
# reboot. To persist changes, rebuild the image with this installer.
#
# Configuration (override via environment):
#   TARGET_DISK      whole-disk device to install onto (e.g. /dev/sda)
#   HOSTNAME_        system hostname                              [archram]
#   USERNAME_        non-root user to create (empty to skip)      [arch]
#   TIMEZONE         /usr/share/zoneinfo zone                     [UTC]
#   LOCALE           locale to generate/use                       [en_US.UTF-8]
#   KEYMAP           console keymap                               [us]
#   MAPPER           dm-crypt mapper name                         [cryptram]
#   ESP_SIZE         EFI partition size                           [1GiB]
#   SQUASH_COMP      mksquashfs compressor                        [zstd]
#   COW_SIZE         writable overlay tmpfs size at runtime       [50%]
#   EXTRA_PACKAGES   extra pacman packages (space separated)
#   LUKS_PASSPHRASE  disk passphrase (else prompted)
#   ROOT_PASSWORD    root password (else prompted)
#   USER_PASSWORD    user password (else prompted; defaults to root's)
#   ASSUME_YES=1     skip the destructive-wipe confirmation
#
set -euo pipefail

# --------------------------------------------------------------------------- #
# Configuration
# --------------------------------------------------------------------------- #
HOSTNAME_="${HOSTNAME_:-archram}"
USERNAME_="${USERNAME_:-arch}"
TIMEZONE="${TIMEZONE:-UTC}"
LOCALE="${LOCALE:-en_US.UTF-8}"
KEYMAP="${KEYMAP:-us}"
MAPPER="${MAPPER:-cryptram}"
ESP_SIZE="${ESP_SIZE:-1GiB}"
SQUASH_COMP="${SQUASH_COMP:-zstd}"
COW_SIZE="${COW_SIZE:-50%}"
DATA_FS="${DATA_FS:-f2fs}"            # filesystem holding the squashfs image
PERSIST="${PERSIST:-auto}"           # auto|yes|no - restore saved overlay at boot
EXTRA_PACKAGES="${EXTRA_PACKAGES:-}"
ASSUME_YES="${ASSUME_YES:-0}"

# Secure Boot (sbctl)
SECUREBOOT="${SECUREBOOT:-1}"        # 1 = create keys, (maybe) enroll, sign
SB_ENROLL="${SB_ENROLL:-auto}"       # auto|yes|no - enroll keys into firmware
SB_MICROSOFT="${SB_MICROSOFT:-1}"    # 1 = also enroll Microsoft vendor certs
# SB_KEYDIR=/path/to/keystore        # optional persistent sbctl keystore

ROOT_IMG_NAME="airootfs.sfs"          # squashfs filename inside the LUKS fs
# tar + zstd power 'archram-persist'; f2fs-tools lets the running system fsck
# the data partition.
BASE_PACKAGES="base linux linux-firmware mkinitcpio cryptsetup \
sudo networkmanager nano vim openssh terminus-font tar zstd f2fs-tools"

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
SKEL_DIR="${SCRIPT_DIR}/airootfs"

# Working locations
BUILD_DIR=""
ESP_MNT=""
DATA_MNT=""

# --------------------------------------------------------------------------- #
# Helpers
# --------------------------------------------------------------------------- #
c_red=$'\e[31m'; c_grn=$'\e[32m'; c_ylw=$'\e[33m'; c_blu=$'\e[34m'; c_rst=$'\e[0m'
msg()  { printf '%s::%s %s\n' "${c_blu}" "${c_rst}" "$*"; }
ok()   { printf '%s::%s %s\n' "${c_grn}" "${c_rst}" "$*"; }
warn() { printf '%s::%s %s\n' "${c_ylw}" "${c_rst}" "$*" >&2; }
die()  { printf '%serror:%s %s\n' "${c_red}" "${c_rst}" "$*" >&2; exit 1; }

cleanup() {
    set +e
    [[ -n "${ESP_MNT}"  && -d "${ESP_MNT}"  ]] && mountpoint -q "${ESP_MNT}"  && umount "${ESP_MNT}"
    [[ -n "${DATA_MNT}" && -d "${DATA_MNT}" ]] && mountpoint -q "${DATA_MNT}" && umount "${DATA_MNT}"
    cryptsetup status "${MAPPER}" >/dev/null 2>&1 && cryptsetup close "${MAPPER}"
    [[ -n "${ESP_MNT}"  ]] && rmdir "${ESP_MNT}"  2>/dev/null
    [[ -n "${DATA_MNT}" ]] && rmdir "${DATA_MNT}" 2>/dev/null
}
trap cleanup EXIT

confirm() {
    [[ "${ASSUME_YES}" == "1" ]] && return 0
    local reply
    read -r -p "$1 [y/N] " reply
    [[ "${reply}" =~ ^[Yy]$ ]]
}

# Append the right partition suffix (nvme0n1 -> nvme0n1p1, sda -> sda1).
partdev() {
    local disk="$1" num="$2"
    [[ "${disk}" =~ [0-9]$ ]] && echo "${disk}p${num}" || echo "${disk}${num}"
}

read_secret() {  # read_secret VARNAME "Prompt"
    local __var="$1" __prompt="$2" __a __b
    while :; do
        read -r -s -p "${__prompt}: " __a; echo
        read -r -s -p "${__prompt} (again): " __b; echo
        [[ "${__a}" == "${__b}" ]] || { warn "Entries did not match."; continue; }
        [[ -n "${__a}" ]] || { warn "Must not be empty."; continue; }
        printf -v "${__var}" '%s' "${__a}"
        break
    done
}

# --------------------------------------------------------------------------- #
# Preflight
# --------------------------------------------------------------------------- #
preflight() {
    [[ "${EUID}" -eq 0 ]] || die "Must run as root."
    [[ -d /sys/firmware/efi ]] || die "Not booted in UEFI mode (need /sys/firmware/efi)."
    [[ -d "${SKEL_DIR}/etc/initcpio/hooks" ]] || \
        die "Cannot find the 'airootfs' skeleton next to this script (${SKEL_DIR})."

    msg "Ensuring required tools are present on the live system..."
    local need=(pacstrap arch-chroot mksquashfs cryptsetup sgdisk mkfs.fat "mkfs.${DATA_FS}" bootctl blkid)
    [[ "${SECUREBOOT}" == "1" ]] && need+=(sbctl)
    local missing=()
    local t
    for t in "${need[@]}"; do command -v "${t}" >/dev/null 2>&1 || missing+=("${t}"); done
    if ((${#missing[@]})); then
        warn "Missing: ${missing[*]} - installing toolchain via pacman..."
        pacman -Sy --needed --noconfirm \
            arch-install-scripts squashfs-tools gptfdisk dosfstools cryptsetup \
            f2fs-tools sbctl systemd
    fi
    for t in "${need[@]}"; do
        command -v "${t}" >/dev/null 2>&1 || die "Still missing required tool: ${t}"
    done
}

# --------------------------------------------------------------------------- #
# Disk selection
# --------------------------------------------------------------------------- #
select_disk() {
    if [[ -z "${TARGET_DISK:-}" ]]; then
        msg "Available disks:"
        lsblk -dpno NAME,SIZE,MODEL,TRAN | grep -vE 'loop|sr0' || true
        echo
        read -r -p "Target disk (whole device, e.g. /dev/sda): " TARGET_DISK
    fi
    [[ -b "${TARGET_DISK}" ]] || die "Not a block device: ${TARGET_DISK}"
    # Reject partitions; we need a whole disk.
    [[ "$(lsblk -dno TYPE "${TARGET_DISK}")" == "disk" ]] || \
        die "${TARGET_DISK} is not a whole disk."

    echo
    warn "EVERYTHING on ${TARGET_DISK} will be ERASED:"
    lsblk -po NAME,SIZE,FSTYPE,MOUNTPOINTS "${TARGET_DISK}" || true
    echo
    confirm "Proceed and DESTROY all data on ${TARGET_DISK}?" || die "Aborted by user."
}

# --------------------------------------------------------------------------- #
# Partition, encrypt, format
# --------------------------------------------------------------------------- #
partition_and_encrypt() {
    ESP_PART="$(partdev "${TARGET_DISK}" 1)"
    LUKS_PART="$(partdev "${TARGET_DISK}" 2)"

    msg "Partitioning ${TARGET_DISK} (GPT: ESP ${ESP_SIZE} + LUKS rest)..."
    wipefs -a "${TARGET_DISK}" >/dev/null 2>&1 || true
    sgdisk --zap-all "${TARGET_DISK}" >/dev/null
    sgdisk \
        --new=1:0:+"${ESP_SIZE}" --typecode=1:ef00 --change-name=1:EFI \
        --new=2:0:0              --typecode=2:8309 --change-name=2:cryptroot \
        "${TARGET_DISK}" >/dev/null
    partprobe "${TARGET_DISK}" 2>/dev/null || true
    udevadm settle 2>/dev/null || true

    msg "Formatting EFI System Partition (FAT32)..."
    mkfs.fat -F32 -n EFI "${ESP_PART}" >/dev/null

    if [[ -z "${LUKS_PASSPHRASE:-}" ]]; then
        read_secret LUKS_PASSPHRASE "Disk encryption passphrase"
    fi

    msg "Creating LUKS2 container on ${LUKS_PART}..."
    printf '%s' "${LUKS_PASSPHRASE}" | \
        cryptsetup luksFormat --type luks2 --batch-mode "${LUKS_PART}" -
    printf '%s' "${LUKS_PASSPHRASE}" | \
        cryptsetup open "${LUKS_PART}" "${MAPPER}" -

    msg "Creating ${DATA_FS} filesystem inside the encrypted container..."
    case "${DATA_FS}" in
        f2fs) mkfs.f2fs -f -l cryptroot "/dev/mapper/${MAPPER}" >/dev/null ;;
        ext4) mkfs.ext4 -q -L cryptroot "/dev/mapper/${MAPPER}" ;;
        *)    "mkfs.${DATA_FS}" "/dev/mapper/${MAPPER}" ;;
    esac

    LUKS_UUID="$(blkid -s UUID -o value "${LUKS_PART}")"
    [[ -n "${LUKS_UUID}" ]] || die "Could not read LUKS UUID."
    ok "LUKS UUID: ${LUKS_UUID}"
}

# --------------------------------------------------------------------------- #
# Build & configure the root filesystem
# --------------------------------------------------------------------------- #
build_rootfs() {
    BUILD_DIR="$(mktemp -d /var/tmp/archram.XXXXXX)"
    msg "Bootstrapping base system into ${BUILD_DIR}..."
    # shellcheck disable=SC2086
    pacstrap -K "${BUILD_DIR}" ${BASE_PACKAGES} ${EXTRA_PACKAGES}

    # CPU microcode (best effort).
    local ucode=""
    if grep -q GenuineIntel /proc/cpuinfo; then ucode="intel-ucode"
    elif grep -q AuthenticAMD /proc/cpuinfo; then ucode="amd-ucode"; fi
    if [[ -n "${ucode}" ]]; then
        msg "Installing ${ucode}..."
        pacstrap "${BUILD_DIR}" "${ucode}" || warn "microcode install failed - continuing."
    fi

    msg "Installing the ramboot hook, mkinitcpio drop-in and persist tool..."
    cp -a "${SKEL_DIR}/." "${BUILD_DIR}/"
    chmod 0755 "${BUILD_DIR}/etc/initcpio/hooks/ramboot" \
               "${BUILD_DIR}/etc/initcpio/install/ramboot" \
               "${BUILD_DIR}/usr/local/bin/archram-persist"

    # Minimal fstab: the root is an overlay assembled by the initramfs, so
    # there is nothing for systemd to mount from disk.
    cat > "${BUILD_DIR}/etc/fstab" <<'EOF'
# Root is an in-RAM overlay assembled by the 'ramboot' initramfs hook.
# Intentionally empty - nothing is mounted from disk at runtime.
EOF

    msg "Configuring locale, time, hostname, console..."
    echo "${HOSTNAME_}" > "${BUILD_DIR}/etc/hostname"
    ln -sf "/usr/share/zoneinfo/${TIMEZONE}" "${BUILD_DIR}/etc/localtime"
    sed -i "s/^#\(${LOCALE//./\\.} \)/\1/" "${BUILD_DIR}/etc/locale.gen"
    echo "LANG=${LOCALE}" > "${BUILD_DIR}/etc/locale.conf"
    echo "KEYMAP=${KEYMAP}" > "${BUILD_DIR}/etc/vconsole.conf"
    cat > "${BUILD_DIR}/etc/hosts" <<EOF
127.0.0.1   localhost
::1         localhost
127.0.1.1   ${HOSTNAME_}.localdomain ${HOSTNAME_}
EOF

    # Passwords / users.
    if [[ -z "${ROOT_PASSWORD:-}" ]]; then
        read_secret ROOT_PASSWORD "Root password"
    fi

    msg "Applying configuration inside the chroot..."
    arch-chroot "${BUILD_DIR}" /bin/bash -euo pipefail <<CHROOT
locale-gen
printf '%s\n%s\n' "${ROOT_PASSWORD}" "${ROOT_PASSWORD}" | passwd root >/dev/null
systemctl enable NetworkManager.service
CHROOT

    if [[ -n "${USERNAME_}" ]]; then
        if [[ -z "${USER_PASSWORD:-}" ]]; then USER_PASSWORD="${ROOT_PASSWORD}"; fi
        msg "Creating user '${USERNAME_}' with sudo access..."
        arch-chroot "${BUILD_DIR}" /bin/bash -euo pipefail <<CHROOT
useradd -m -G wheel,audio,video,storage,network -s /bin/bash "${USERNAME_}"
printf '%s\n%s\n' "${USER_PASSWORD}" "${USER_PASSWORD}" | passwd "${USERNAME_}" >/dev/null
sed -i 's/^# %wheel ALL=(ALL:ALL) ALL/%wheel ALL=(ALL:ALL) ALL/' /etc/sudoers
CHROOT
    fi

    msg "Generating the initramfs with the ramboot hook..."
    arch-chroot "${BUILD_DIR}" mkinitcpio -P
}

# --------------------------------------------------------------------------- #
# Secure Boot (sbctl): create keys, optionally enroll, sign the boot chain.
#
# Firmware must be in "Setup Mode" (clear the existing Secure Boot keys in the
# firmware setup screen) for enrollment to succeed. The bootloader and kernel
# are signed; the separate initramfs is NOT covered by Secure Boot - see the
# README's UKI note to close that gap.
# --------------------------------------------------------------------------- #
setup_secureboot() {
    if [[ "${SECUREBOOT}" != "1" ]]; then
        warn "Secure Boot signing disabled (SECUREBOOT=0)."
        return 0
    fi
    command -v sbctl >/dev/null 2>&1 || die "SECUREBOOT=1 but sbctl is not available."

    msg "Configuring Secure Boot (sbctl)..."

    # Restore a persistent keystore, if one was provided.
    if [[ -n "${SB_KEYDIR:-}" ]] && compgen -G "${SB_KEYDIR}/keys/*" >/dev/null 2>&1; then
        msg "Restoring Secure Boot keys from ${SB_KEYDIR}..."
        mkdir -p /var/lib/sbctl
        cp -a "${SB_KEYDIR}/." /var/lib/sbctl/
    fi

    # Create platform keys only if none exist yet.
    if compgen -G "/var/lib/sbctl/keys/*" >/dev/null 2>&1 || \
       compgen -G "/usr/share/secureboot/keys/*" >/dev/null 2>&1; then
        msg "Reusing existing Secure Boot keys."
    else
        msg "Creating Secure Boot keys..."
        sbctl create-keys
    fi

    # Enroll into firmware only when it is safe/possible to do so.
    local in_setup="no"
    if sbctl status --json >/tmp/sbstatus.json 2>/dev/null; then
        grep -q '"setup_mode":true' /tmp/sbstatus.json && in_setup="yes"
    fi
    local do_enroll=0
    case "${SB_ENROLL}" in
        yes)    do_enroll=1 ;;
        no)     do_enroll=0 ;;
        auto|*) [[ "${in_setup}" == "yes" ]] && do_enroll=1 ;;
    esac

    if (( do_enroll )); then
        msg "Enrolling Secure Boot keys into firmware..."
        local mflag=()
        [[ "${SB_MICROSOFT}" == "1" ]] && mflag=(--microsoft)
        sbctl enroll-keys "${mflag[@]}" || \
            warn "Key enrollment failed (firmware not in Setup Mode?). Continuing."
    else
        warn "Not enrolling keys (firmware not in Setup Mode or SB_ENROLL=no)."
        warn "Enroll later with: sbctl enroll-keys --microsoft"
    fi

    # Sign the binaries Secure Boot actually validates: the bootloader + kernel.
    msg "Signing bootloader and kernel..."
    local f
    for f in \
        "${ESP_MNT}/EFI/systemd/systemd-bootx64.efi" \
        "${ESP_MNT}/EFI/BOOT/BOOTX64.EFI" \
        "${ESP_MNT}/vmlinuz-linux"; do
        [[ -f "${f}" ]] && { sbctl sign -s "${f}" || warn "Failed to sign ${f}"; }
    done

    # Persist keys back to the keystore for reproducible re-installs.
    if [[ -n "${SB_KEYDIR:-}" ]]; then
        msg "Saving Secure Boot keys to ${SB_KEYDIR}..."
        mkdir -p "${SB_KEYDIR}"
        if   [[ -d /var/lib/sbctl ]];        then cp -a /var/lib/sbctl/. "${SB_KEYDIR}/"
        elif [[ -d /usr/share/secureboot ]]; then cp -a /usr/share/secureboot/. "${SB_KEYDIR}/"
        fi
    fi
}

# --------------------------------------------------------------------------- #
# Bootloader + squashfs deployment
# --------------------------------------------------------------------------- #
deploy() {
    ESP_MNT="$(mktemp -d)"
    DATA_MNT="$(mktemp -d)"
    mount "${ESP_PART}" "${ESP_MNT}"
    mount "/dev/mapper/${MAPPER}" "${DATA_MNT}"

    msg "Installing systemd-boot to the ESP..."
    bootctl --esp-path="${ESP_MNT}" install

    msg "Copying kernel and initramfs to the ESP..."
    cp "${BUILD_DIR}/boot/vmlinuz-linux" "${ESP_MNT}/vmlinuz-linux"
    cp "${BUILD_DIR}/boot/initramfs-linux.img" "${ESP_MNT}/initramfs-linux.img"

    # Microcode initrd, if present.
    local ucode_line=""
    local f
    for f in intel-ucode.img amd-ucode.img; do
        if [[ -f "${BUILD_DIR}/boot/${f}" ]]; then
            cp "${BUILD_DIR}/boot/${f}" "${ESP_MNT}/${f}"
            ucode_line+="initrd  /${f}"$'\n'
        fi
    done

    msg "Writing systemd-boot configuration..."
    cat > "${ESP_MNT}/loader/loader.conf" <<EOF
default archram.conf
timeout 3
console-mode max
editor   no
EOF

    cat > "${ESP_MNT}/loader/entries/archram.conf" <<EOF
title   Arch Linux (RAM / LUKS)
linux   /vmlinuz-linux
${ucode_line}initrd  /initramfs-linux.img
options ramboot_dev=UUID=${LUKS_UUID} ramboot_name=${MAPPER} ramboot_img=/${ROOT_IMG_NAME} copytoram=yes ramboot_cowsize=${COW_SIZE} ramboot_persist=${PERSIST} rw
EOF

    setup_secureboot

    # Persistence snapshot directory (archram-persist writes here at runtime).
    mkdir -p "${DATA_MNT}/persist"

    msg "Packing root filesystem into squashfs (${SQUASH_COMP})..."
    # /boot lives on the ESP already; exclude it from the image.
    mksquashfs "${BUILD_DIR}" "${DATA_MNT}/${ROOT_IMG_NAME}" \
        -comp "${SQUASH_COMP}" -noappend -e boot

    sync
    local img_size
    img_size="$(du -h "${DATA_MNT}/${ROOT_IMG_NAME}" | cut -f1)"
    ok "Root image size: ${img_size}"

    umount "${ESP_MNT}"; umount "${DATA_MNT}"
    cryptsetup close "${MAPPER}"
}

# --------------------------------------------------------------------------- #
# Main
# --------------------------------------------------------------------------- #
main() {
    preflight
    select_disk
    partition_and_encrypt
    build_rootfs
    deploy

    msg "Cleaning up build directory..."
    rm -rf "${BUILD_DIR}"

    echo
    ok "Done. ${TARGET_DISK} now holds a RAM-booted, LUKS-encrypted Arch Linux."
    cat <<EOF

  Boot the machine and you will be prompted for the disk passphrase. The
  squashfs root is copied into RAM and the disk is then closed - the system
  runs entirely from memory and discards all changes on reboot.

  Remember the squashfs RAM copy needs RAM >= the image size plus working set.
  To change the installed system, re-run this installer.
EOF
}

main "$@"
