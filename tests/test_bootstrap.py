"""scripts/bootstrap.py, exercised offline through file:// URLs."""

import hashlib
import importlib.util
import io
import json
import tarfile
import zipfile
from pathlib import Path

import pytest

SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "bootstrap.py"
spec = importlib.util.spec_from_file_location("bootstrap_script", SCRIPT)
bootstrap = importlib.util.module_from_spec(spec)
spec.loader.exec_module(bootstrap)


def sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def make_zip(path: Path) -> bytes:
    with zipfile.ZipFile(path, "w") as zf:
        zf.writestr("tsMuxeR", "#!/bin/sh\necho tool\n")
    return path.read_bytes()


def make_tar(path: Path) -> bytes:
    with tarfile.open(path, "w:gz") as tf:
        data = b"#!/bin/sh\n"
        info = tarfile.TarInfo("bin/mobj_dump")
        info.size, info.mode = len(data), 0o755
        tf.addfile(info, io.BytesIO(data))
    return path.read_bytes()


@pytest.fixture
def release(tmp_path):
    """A fake release directory plus a lock file pointing at it."""
    rel = tmp_path / "release"
    rel.mkdir()
    zip_bytes = make_zip(rel / "tool-1.zip")
    tar_bytes = make_tar(rel / "libs-1.tar.gz")
    (rel / "SHA256SUMS").write_text(
        f"{sha256(zip_bytes)}  tool-1.zip\n{sha256(tar_bytes)} *libs-1.tar.gz\n"
    )
    lock = {
        "release_url": rel.as_uri(),
        "assets": {
            "tool-1.zip": {"sha256": sha256(zip_bytes), "dest": "vendor/tool"},
            "libs-1.tar.gz": {"sha256": None, "dest": "vendor/libs"},
        },
    }
    lock_path = tmp_path / "lock.json"
    lock_path.write_text(json.dumps(lock))
    root = tmp_path / "root"
    root.mkdir()
    return rel, lock_path, root


def run(lock_path, root, *args):
    return bootstrap.main(["--lock", str(lock_path), "--root", str(root), *args])


def test_installs_a_pinned_zip_and_makes_it_executable(release):
    _, lock, root = release
    assert run(lock, root, "tool") == 0
    tool = root / "vendor" / "tool" / "tsMuxeR"
    assert tool.read_text().startswith("#!/bin/sh")
    assert tool.stat().st_mode & 0o111


def test_second_run_is_a_no_op_and_force_reinstalls(release, capsys):
    _, lock, root = release
    run(lock, root, "tool")
    run(lock, root, "tool")
    assert "tool-1.zip: up to date" in capsys.readouterr().out
    run(lock, root, "tool", "--force")
    assert "tool-1.zip: installed" in capsys.readouterr().out.splitlines()[-1]


def test_asset_without_a_pinned_hash_is_refused(release, capsys):
    _, lock, root = release
    assert run(lock, root, "libs") == 1
    assert "no pinned SHA-256" in capsys.readouterr().err
    assert not (root / "vendor" / "libs").exists()


def test_a_tampered_asset_is_refused_and_nothing_is_installed(release, capsys):
    rel, lock, root = release
    make_zip(rel / "tool-1.zip")  # same content, then corrupt it
    (rel / "tool-1.zip").write_bytes(b"not the pinned file")
    assert run(lock, root, "tool") == 1
    assert "SHA-256 mismatch" in capsys.readouterr().err
    assert not (root / "vendor" / "tool").exists()


def test_update_lock_pins_the_published_hashes_then_install_works(release):
    rel, lock, root = release
    assert run(lock, root, "--update-lock") == 0
    pinned = json.loads(lock.read_text())["assets"]["libs-1.tar.gz"]["sha256"]
    assert pinned == sha256((rel / "libs-1.tar.gz").read_bytes())
    assert run(lock, root) == 0
    assert (root / "vendor" / "libs" / "bin" / "mobj_dump").stat().st_mode & 0o111


def test_update_lock_fails_when_an_asset_is_not_in_sha256sums(release, capsys):
    rel, lock, root = release
    (rel / "SHA256SUMS").write_text("")
    assert run(lock, root, "--update-lock") == 1
    assert "not listed" in capsys.readouterr().err


def test_a_missing_download_is_a_clean_error(release, capsys):
    rel, lock, root = release
    (rel / "tool-1.zip").unlink()
    assert run(lock, root, "tool") == 1
    assert "could not download" in capsys.readouterr().err


def test_unknown_filter_is_an_error(release, capsys):
    _, lock, root = release
    assert run(lock, root, "nothing-matches") == 1
    assert "no asset matches" in capsys.readouterr().err


def test_zip_with_an_unsafe_path_is_refused(tmp_path):
    archive = tmp_path / "evil.zip"
    with zipfile.ZipFile(archive, "w") as zf:
        zf.writestr("../escape", "x")
    with pytest.raises(bootstrap.BootstrapError, match="unsafe path"):
        bootstrap.extract(archive, "evil.zip", tmp_path / "dest")
    assert not (tmp_path / "escape").exists()


def test_the_committed_lock_file_is_well_formed():
    lock = bootstrap.load_lock()
    assert lock["release_url"].startswith("https://")
    for name, entry in lock["assets"].items():
        assert entry["dest"].startswith("vendor/"), name
        assert entry["sha256"] is None or len(entry["sha256"]) == 64, name
