"""Physical drive detection by hardware serial (fishrand/drive_detect.py).

Parser/sysfs-walking logic only, against fixture text and fake sysfs
trees - real drive detection was verified by hand against this machine's
actual internal SSD and USB stick (see the design plan for that session).
"""

from __future__ import annotations

import pytest

from fishrand import drive_detect as dd

MOUNTS = """\
proc /proc proc rw,nosuid,nodev,noexec,relatime 0 0
tmpfs /tmp tmpfs rw,nosuid,nodev 0 0
/dev/sda2 / ext4 rw,relatime 0 0
/dev/sda1 /boot vfat rw,relatime 0 0
/dev/sdb1 /run/media/guts/MY\\040STICK vfat rw,nosuid,nodev 0 0
"""


def test_read_mounts_decodes_octal_space(monkeypatch, tmp_path):
    mounts_file = tmp_path / "mounts"
    mounts_file.write_text(MOUNTS, encoding="utf-8")
    monkeypatch.setattr(dd, "PROC_MOUNTS", mounts_file)
    mounts = dd._read_mounts()
    assert ("/dev/sdb1", "/run/media/guts/MY STICK", "vfat") in mounts
    assert ("tmpfs", "/tmp", "tmpfs") in mounts


@pytest.fixture()
def fake_sys_block(tmp_path, monkeypatch):
    sys_block = tmp_path / "sys_block"
    sys_block.mkdir()
    monkeypatch.setattr(dd, "SYS_BLOCK", sys_block)
    return sys_block


def _make_ata_disk(sys_block, name, serial, vendor="ATA", model="SAMSUNG"):
    device = sys_block / name / "device"
    device.mkdir(parents=True)
    (device / "serial").write_text(serial)
    (device / "vendor").write_text(vendor)
    (device / "model").write_text(model)
    (sys_block / name).mkdir(exist_ok=True)


def _make_usb_disk(sys_block, name, serial, vendor="SanDisk", model="Cruzer Force"):
    # device/ -> ../../../usbN/N-M/N-M:1.0/hostX/targetX/X:X:X:X (no direct serial)
    usb_root = sys_block.parent / "usb_devices" / f"usb-{name}"
    scsi_leaf = usb_root / f"{name}:1.0" / "host0" / "target0" / "0:0:0:0"
    scsi_leaf.mkdir(parents=True)
    (usb_root / "serial").write_text(serial)
    (usb_root / "idVendor").write_text("0781")
    (usb_root / "vendor").write_text(vendor)  # not read for USB path, harmless
    device_link = sys_block / name / "device"
    device_link.parent.mkdir(parents=True, exist_ok=True)
    device_link.symlink_to(scsi_leaf)
    # USB descriptor doesn't expose vendor/model at the block level the way
    # ATA does - list_drives() falls back to the disk name in that case.


class TestDiskNameForSource:
    def test_strips_partition_suffix(self, fake_sys_block):
        (fake_sys_block / "sda").mkdir()
        assert dd._disk_name_for_source("/dev/sda2") == "sda"

    def test_whole_disk_mount(self, fake_sys_block):
        (fake_sys_block / "sdb").mkdir()
        assert dd._disk_name_for_source("/dev/sdb") == "sdb"

    def test_non_block_source_ignored(self, fake_sys_block):
        assert dd._disk_name_for_source("tmpfs") is None

    def test_unknown_disk_ignored(self, fake_sys_block):
        assert dd._disk_name_for_source("/dev/sdz9") is None


class TestSerialForDisk:
    def test_direct_ata_serial(self, fake_sys_block):
        _make_ata_disk(fake_sys_block, "sda", "S20RNXAGB00279")
        assert dd.serial_for_disk("sda") == "S20RNXAGB00279"

    def test_usb_descriptor_fallback(self, fake_sys_block):
        _make_usb_disk(fake_sys_block, "sdb", "4C530000080602121202")
        assert dd.serial_for_disk("sdb") == "4C530000080602121202"

    def test_no_serial_available(self, fake_sys_block):
        (fake_sys_block / "sdc" / "device").mkdir(parents=True)
        assert dd.serial_for_disk("sdc") is None


class TestListDrives:
    def test_dedupes_partitions_and_skips_virtual_fs(self, fake_sys_block, monkeypatch, tmp_path):
        _make_ata_disk(fake_sys_block, "sda", "S20RNXAGB00279")
        mounts_file = tmp_path / "mounts"
        mounts_file.write_text(
            "/dev/sda2 / ext4 rw 0 0\n"
            "/dev/sda1 /boot vfat rw 0 0\n"
            "proc /proc proc rw 0 0\n"
            "tmpfs /tmp tmpfs rw 0 0\n",
            encoding="utf-8",
        )
        monkeypatch.setattr(dd, "PROC_MOUNTS", mounts_file)
        drives = dd.list_drives()
        assert drives == [{"serial": "S20RNXAGB00279", "label": "ATA SAMSUNG", "mountpoint": "/"}]

    def test_is_present(self, fake_sys_block, monkeypatch, tmp_path):
        _make_ata_disk(fake_sys_block, "sda", "S20RNXAGB00279")
        mounts_file = tmp_path / "mounts"
        mounts_file.write_text("/dev/sda / ext4 rw 0 0\n", encoding="utf-8")
        monkeypatch.setattr(dd, "PROC_MOUNTS", mounts_file)
        assert dd.is_present("S20RNXAGB00279") is True
        assert dd.is_present("nonexistent-serial") is False
