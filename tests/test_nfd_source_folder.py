"""SPEC-ADDON-062 (I15, cloud-sync half): a source folder whose on-disk name is NFD,
while the configured path is the NFC spelling, is found and used.

The names are created on the filesystem of `tmp_path`. A filesystem that folds NFC
and NFD (macOS/APFS) cannot hold the case, so those runs skip rather than pass.
"""
from __future__ import annotations

import os
import unicodedata
from pathlib import Path

import pytest

from addons.cloud_sync.service import SyncManager
from settings_world import BASE, body, row, sworld, without_message  # noqa: F401

NFC_DIR = unicodedata.normalize("NFC", "がぎぐ")
NFD_DIR = unicodedata.normalize("NFD", NFC_DIR)
NFC_LEAF = unicodedata.normalize("NFC", "ばびぶ")
NFD_LEAF = unicodedata.normalize("NFD", NFC_LEAF)
assert NFC_DIR != NFD_DIR and NFC_LEAF != NFD_LEAF


@pytest.fixture()
def nfd_capable(tmp_path):
    probe = tmp_path / "probe-fs"
    probe.mkdir()
    (probe / NFD_DIR).mkdir()
    if (probe / NFC_DIR).exists():
        pytest.skip("this filesystem folds NFC and NFD; run on Linux (Docker)")


def _make_nfd_folder(drive_root: Path, *components: str) -> Path:
    path = drive_root.joinpath(*components)
    path.mkdir(parents=True)
    (path / "a.txt").write_text("x")
    return path


# Each case: the on-disk components, and the NFC spelling a mapping is configured with.
CASES = {
    "top-level": ((NFD_DIR,), NFC_DIR),
    "nested-ancestor": ((NFD_DIR, "plain"), f"{NFC_DIR}/plain"),
    "nested-leaf": (("plain", NFD_LEAF), f"plain/{NFC_LEAF}"),
    "both-levels": ((NFD_DIR, NFD_LEAF), f"{NFC_DIR}/{NFC_LEAF}"),
}


@pytest.mark.parametrize("case", CASES)
class TestNfdSourceFolder:
    # SPEC-ADDON-062 I15: _folder_inside_drive finds the folder.
    def test_folder_inside_drive_finds_it(self, nfd_capable, tmp_path, case):
        components, configured = CASES[case]
        root = tmp_path / "drive"
        root.mkdir()
        _make_nfd_folder(root, *components)

        assert SyncManager._folder_inside_drive(root, configured) is True

    # SPEC-ADDON-062 I15: the save-time folder check accepts it.
    async def test_saving_the_mapping_is_accepted(self, nfd_capable, sworld, case):
        components, configured = CASES[case]
        sworld.add_drive("Photos")
        _make_nfd_folder(sworld.drive_root("Photos"), *components)

        response = await sworld.put_config(
            body(mappings=[row("Photos", configured, "gdrive:a")])
        )

        assert response.status_code == 200, response.text

    # SPEC-ADDON-062 I15: _source_usable passes and rclone gets the folder as it is on disk.
    async def test_a_sync_hands_the_on_disk_folder_to_rclone(
        self, nfd_capable, sworld, case
    ):
        components, configured = CASES[case]
        root = sworld.add_drive("Photos")
        on_disk = _make_nfd_folder(root, *components)
        sworld.write_file(
            body(schema_version=1, mappings=[row("Photos", configured, "gdrive:a")])
        )

        response = await sworld.client.post(
            f"{BASE}/Photos/start", params={"path": configured}
        )
        await sworld.wait_not_syncing("Photos", configured)

        assert response.status_code == 200
        (source,) = sworld.launched_sources()
        assert os.fsencode(source) == os.fsencode(str(on_disk))
        entry = next(
            d for d in (await sworld.status())["drives"] if d["path"] == configured
        )
        assert entry["error_kind"] is None
