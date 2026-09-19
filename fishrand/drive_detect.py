"""PHYSICAL DRIVE DETECTION BY HARDWARE SERIAL (Linux).

Used to bind a diary entry to whichever physical drive its private key was
saved to (server/main.py's /api/diary/entries + /api/decrypt) - not just a
USB stick, any locally mounted drive, including the internal one.

Checked on demand, once per request; never polled in the background.

Reading a drive's hardware serial needs two different sysfs paths
depending on how it's attached - confirmed on this machine:
    - An internal SATA/NVMe drive answers a SCSI/ATA "unit serial number"
      query directly: /sys/block/<disk>/device/serial.
    - A USB flash drive's SCSI-over-USB translation usually does NOT
      support that query (ENXIO) - its serial only shows up one level up,
      in the USB device descriptor itself (a "serial" file living next to
      "idVendor" on the actual USB device node, a few directories above
      the interface/host/target/lun nodes /sys/block/<disk>/device
      resolves to).

Limitation: only plain block devices with a normal filesystem are
resolved (vfat/exFAT/ext4/...). LUKS/LVM/other stacked devices are not.
"""

from __future__ import annotations

import os
import pathlib
import re

PROC_MOUNTS = pathlib.Path("/proc/mounts")
SYS_BLOCK = pathlib.Path("/sys/block")

_OCTAL_ESCAPE = re.compile(r"\\([0-7]{3})")
_TRAILING_PARTITION = re.compile(r"^(.*?)(?:p?\d+)$")
_IGNORED_FS = {"proc", "sysfs", "tmpfs", "devtmpfs", "devpts", "cgroup", "cgroup2",
               "overlay", "squashfs", "efivarfs", "pstore", "bpf", "tracefs", "mqueue",
               "securityfs", "debugfs", "configfs", "autofs", "fusectl", "hugetlbfs"}


def _unescape(field: str) -> str:
    return _OCTAL_ESCAPE.sub(lambda m: chr(int(m.group(1), 8)), field)


def _read_mounts() -> list[tuple[str, str, str]]:
    """(source, mountpoint, fstype) for every current mount."""
    mounts = []
    for line in PROC_MOUNTS.read_text(encoding="utf-8").splitlines():
        parts = line.split()
        if len(parts) >= 3:
            mounts.append((_unescape(parts[0]), _unescape(parts[1]), parts[2]))
    return mounts


def _disk_name_for_source(source: str) -> str | None:
    """'/dev/sda2' -> 'sda', '/dev/sda' -> 'sda'. Only real block devices."""
    if not source.startswith("/dev/"):
        return None
    name = source.removeprefix("/dev/")
    if (SYS_BLOCK / name).exists():
        return name  # whole-disk mount, no partition suffix to strip
    match = _TRAILING_PARTITION.match(name)
    stripped = match.group(1) if match else name
    return stripped if (SYS_BLOCK / stripped).exists() else None


def _disk_name_for_path(path: pathlib.Path) -> str | None:
    target = pathlib.Path(path).resolve()
    best: tuple[int, str] | None = None
    for source, mountpoint, _fstype in _read_mounts():
        mp = pathlib.Path(mountpoint)
        if (target == mp or mp in target.parents) and (best is None or len(mountpoint) >= best[0]):
            best = (len(mountpoint), source)
    return _disk_name_for_source(best[1]) if best else None


def _read_text(path: pathlib.Path) -> str | None:
    try:
        text = path.read_text(encoding="utf-8", errors="replace").strip()
    except OSError:
        return None
    return text or None


def _direct_serial(disk_name: str) -> str | None:
    return _read_text(SYS_BLOCK / disk_name / "device" / "serial")


def _usb_descriptor_serial(disk_name: str) -> str | None:
    device_dir = (SYS_BLOCK / disk_name / "device").resolve()
    if not device_dir.exists():
        return None
    for candidate in [device_dir, *device_dir.parents]:
        if (candidate / "serial").is_file() and (candidate / "idVendor").is_file():
            return _read_text(candidate / "serial")
    return None


def serial_for_disk(disk_name: str) -> str | None:
    return _direct_serial(disk_name) or _usb_descriptor_serial(disk_name)


def serial_for_path(path: pathlib.Path) -> str | None:
    disk_name = _disk_name_for_path(path)
    return serial_for_disk(disk_name) if disk_name else None


def _label_for_disk(disk_name: str) -> str:
    vendor = _read_text(SYS_BLOCK / disk_name / "device" / "vendor")
    model = _read_text(SYS_BLOCK / disk_name / "device" / "model")
    parts = [p for p in (vendor, model) if p]
    return " ".join(parts) if parts else disk_name


def list_drives() -> list[dict]:
    """Every currently mounted physical drive with a readable serial, one
    entry per disk (a drive's several partitions collapse into one, kept
    at its shortest/root-most mountpoint)."""
    by_disk: dict[str, tuple[str, int]] = {}  # disk -> (mountpoint, depth)
    for source, mountpoint, fstype in _read_mounts():
        if fstype in _IGNORED_FS:
            continue
        disk_name = _disk_name_for_source(source)
        if not disk_name:
            continue
        depth = mountpoint.count("/")
        if disk_name not in by_disk or depth < by_disk[disk_name][1]:
            by_disk[disk_name] = (mountpoint, depth)

    drives = []
    for disk_name, (mountpoint, _depth) in sorted(by_disk.items()):
        serial = serial_for_disk(disk_name)
        if not serial:
            continue
        drives.append({"serial": serial, "label": _label_for_disk(disk_name), "mountpoint": mountpoint})
    return drives


def is_present(serial: str) -> bool:
    return any(d["serial"] == serial for d in list_drives())


__all__ = ["serial_for_path", "serial_for_disk", "list_drives", "is_present"]
