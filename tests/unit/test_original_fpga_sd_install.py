"""Offline original-board provisioning without touching a real SD card."""

import json
from pathlib import Path

import pytest

from fpga.e1_sync.original_125_14 import install_sd


def layout(tmp_path):
    boot = tmp_path / "sd"
    target = boot / "fpga/z10_125/v0.94"
    target.mkdir(parents=True)
    for name in (*install_sd.PAYLOAD, "fpga.dtbo"):
        (target / name).write_bytes(("original-" + name).encode())
    gen2 = boot / "fpga/z10_125_pro_v2/v0.94"
    gen2.mkdir(parents=True)
    (gen2 / "fpga.bin").write_bytes(b"tested-gen2-image")
    bundle = tmp_path / "bundle"
    bundle.mkdir()
    for name in install_sd.PAYLOAD:
        (bundle / name).write_bytes(("new-" + name).encode())
    manifest = {
        "targetModel": "z10_125",
        "part": "xc7z010clg400-1",
        "routedTimingMet": True,
        "simulationPassed": True,
        "payloadSha256": {n: install_sd.sha(bundle / n) for n in install_sd.PAYLOAD},
    }
    (bundle / "manifest.json").write_text(json.dumps(manifest))
    return boot, target, bundle, gen2


def test_preview_does_not_change_the_card(tmp_path):
    boot, _target, bundle, _ = layout(tmp_path)
    before = {p.relative_to(boot): p.read_bytes() for p in boot.rglob("*") if p.is_file()}
    result = install_sd.stage(boot, bundle)
    assert not result["installed"]
    assert set(result["filesToChange"]) == set(install_sd.PAYLOAD)
    assert {p.relative_to(boot): p.read_bytes() for p in boot.rglob("*") if p.is_file()} == before
    assert not (boot / "rtqlm-backups").exists()


def test_install_backs_up_originals_and_preserves_device_tree_and_gen2(tmp_path):
    boot, target, bundle, gen2 = layout(tmp_path)
    result = install_sd.stage(boot, bundle, install=True)
    backup = Path(result["backup"])
    for name in install_sd.PAYLOAD:
        assert (target / name).read_bytes() == (bundle / name).read_bytes()
        assert (backup / name).read_bytes() == ("original-" + name).encode()
    assert (target / "fpga.dtbo").read_bytes() == b"original-fpga.dtbo"
    assert (backup / "fpga.dtbo").read_bytes() == b"original-fpga.dtbo"
    assert (gen2 / "fpga.bin").read_bytes() == b"tested-gen2-image"
    assert not list(target.glob("*.rtqlm-*"))
    again = install_sd.stage(boot, bundle, install=True)
    assert again["installed"] and not again["filesToChange"]
    assert "backup" not in again


@pytest.mark.parametrize(
    "field,value",
    [
        ("targetModel", "z10_125_pro_v2"),
        ("routedTimingMet", False),
        ("simulationPassed", False),
        ("part", "xc7z020clg400-1"),
    ],
)
def test_unchecked_or_wrong_target_bundle_cannot_be_installed(tmp_path, field, value):
    boot, target, bundle, _ = layout(tmp_path)
    path = bundle / "manifest.json"
    manifest = json.loads(path.read_text())
    manifest[field] = value
    path.write_text(json.dumps(manifest))
    with pytest.raises(RuntimeError, match="bundle"):
        install_sd.stage(boot, bundle, install=True)
    assert (target / "fpga.bin").read_bytes() == b"original-fpga.bin"


def test_corrupted_image_is_rejected_before_card_changes(tmp_path):
    boot, _target, bundle, _ = layout(tmp_path)
    (bundle / "fpga.bin").write_bytes(b"corrupted")
    with pytest.raises(RuntimeError, match="checksum"):
        install_sd.stage(boot, bundle, install=True)
    assert not (boot / "rtqlm-backups").exists()


def test_incomplete_stock_layout_is_not_overwritten(tmp_path):
    boot, target, bundle, _ = layout(tmp_path)
    (target / "fpga.dtbo").unlink()
    with pytest.raises(RuntimeError, match="missing"):
        install_sd.stage(boot, bundle, install=True)
    assert (target / "fpga.bin").read_bytes() == b"original-fpga.bin"


def test_replacement_failure_restores_already_replaced_files(tmp_path, monkeypatch):
    boot, target, bundle, _ = layout(tmp_path)
    real_replace = Path.replace

    def interrupted(path, destination):
        if path.name.startswith("fpga.bit.rtqlm-new-"):
            raise OSError("simulated write interruption")
        return real_replace(path, destination)

    monkeypatch.setattr(Path, "replace", interrupted)
    with pytest.raises(OSError, match="interruption"):
        install_sd.stage(boot, bundle, install=True)
    for name in (*install_sd.PAYLOAD, "fpga.dtbo"):
        assert (target / name).read_bytes() == ("original-" + name).encode()
    assert not list(target.glob("*.rtqlm-*"))
