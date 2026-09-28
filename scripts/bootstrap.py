"""Download the development tools (tsMuxeR, libbluray tools) into vendor/.

The tools are built by the `bootstrap` GitHub workflow and attached to the "bootstrap"
pre-release. This script only uses the standard library, so it runs before `uv sync`:

    python scripts/bootstrap.py                 # install everything listed in the lock file
    python scripts/bootstrap.py tsmuxer         # install only assets whose name contains "tsmuxer"
    python scripts/bootstrap.py --update-lock   # trust the release's SHA256SUMS and pin its hashes

Every asset must match the SHA-256 pinned in `bootstrap.lock.json` (committed), so a changed or
tampered release is refused. `--update-lock` is the one explicit step that trusts the release.
"""

import argparse
import hashlib
import json
import shutil
import sys
import tarfile
import tempfile
import urllib.error
import urllib.request
import zipfile
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
LOCK_PATH = Path(__file__).with_name("bootstrap.lock.json")
MARKER = ".bootstrap-installed"
CHUNK = 1 << 20


class BootstrapError(Exception):
    """A tool could not be installed."""


def load_lock(path: Path = LOCK_PATH) -> dict:
    return json.loads(path.read_text())


def save_lock(lock: dict, path: Path = LOCK_PATH) -> None:
    path.write_text(json.dumps(lock, indent=2) + "\n")


def sha256_of(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        while chunk := handle.read(CHUNK):
            digest.update(chunk)
    return digest.hexdigest()


def download(url: str, dest: Path) -> None:
    try:
        with urllib.request.urlopen(url) as response, dest.open("wb") as out:
            shutil.copyfileobj(response, out, CHUNK)
    except (urllib.error.URLError, OSError) as exc:
        raise BootstrapError(f"could not download {url}: {exc}") from exc


def parse_sha256sums(text: str) -> dict[str, str]:
    """`sha256sum` output: '<hash>  <name>' per line."""
    sums = {}
    for line in text.splitlines():
        digest, _, name = line.strip().partition(" ")
        if digest and name:
            sums[name.strip().lstrip("*")] = digest
    return sums


def extract(archive: Path, name: str, dest: Path) -> None:
    dest.mkdir(parents=True, exist_ok=True)
    if name.endswith(".zip"):
        with zipfile.ZipFile(archive) as zf:
            for member in zf.infolist():
                target = (dest / member.filename).resolve()
                if not target.is_relative_to(dest.resolve()):
                    raise BootstrapError(f"{name} contains an unsafe path: {member.filename}")
            zf.extractall(dest)
            for member in zf.infolist():
                if not member.is_dir():  # zipfile drops the executable bit
                    (dest / member.filename).chmod(0o755)
    elif name.endswith((".tar.gz", ".tgz")):
        with tarfile.open(archive) as tf:
            tf.extractall(dest, filter="data")  # rejects absolute paths and path traversal
    else:
        raise BootstrapError(f"do not know how to extract {name}")


def install_asset(name: str, entry: dict, base_url: str, root: Path, *, force: bool = False) -> str:
    """Download, verify and extract one asset. Returns 'installed' or 'up to date'."""
    expected = entry.get("sha256")
    if not expected:
        raise BootstrapError(
            f"{name} has no pinned SHA-256 in {LOCK_PATH.name}; run with --update-lock after the "
            "release is published, then commit the lock file"
        )
    dest = root / entry["dest"]
    marker = dest / MARKER
    if not force and marker.is_file() and marker.read_text().strip() == f"{name} {expected}":
        return "up to date"

    with tempfile.TemporaryDirectory() as tmp:
        archive = Path(tmp) / name
        download(f"{base_url}/{name}", archive)
        actual = sha256_of(archive)
        if actual != expected:
            raise BootstrapError(
                f"{name}: SHA-256 mismatch (expected {expected}, got {actual}); nothing was installed"
            )
        extract(archive, name, dest)
    marker.write_text(f"{name} {expected}\n")
    return "installed"


def update_lock(lock: dict, base_url: str) -> list[str]:
    """Pin the hashes published in the release's SHA256SUMS. Returns the names that changed."""
    with tempfile.TemporaryDirectory() as tmp:
        sums_path = Path(tmp) / "SHA256SUMS"
        download(f"{base_url}/SHA256SUMS", sums_path)
        sums = parse_sha256sums(sums_path.read_text())
    changed = []
    for name, entry in lock["assets"].items():
        published = sums.get(name)
        if published is None:
            raise BootstrapError(f"{name} is not listed in the release's SHA256SUMS")
        if entry.get("sha256") != published:
            entry["sha256"] = published
            changed.append(name)
    return changed


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("only", nargs="*", help="install only assets whose name contains one of these")
    parser.add_argument("--force", action="store_true", help="download and extract again")
    parser.add_argument("--update-lock", action="store_true", help="pin the hashes of the release")
    parser.add_argument("--lock", type=Path, default=LOCK_PATH, help=argparse.SUPPRESS)
    parser.add_argument("--root", type=Path, default=REPO_ROOT, help=argparse.SUPPRESS)
    args = parser.parse_args(argv)

    try:
        lock = load_lock(args.lock)
        base_url = lock["release_url"].rstrip("/")
        if args.update_lock:
            changed = update_lock(lock, base_url)
            save_lock(lock, args.lock)
            print(f"pinned {len(changed)} hash(es) in {args.lock.name}" + (f": {', '.join(changed)}" if changed else ""))
            return 0
        wanted = [n for n in lock["assets"] if not args.only or any(o.lower() in n.lower() for o in args.only)]
        if not wanted:
            raise BootstrapError(f"no asset matches {', '.join(args.only)}")
        for name in wanted:
            status = install_asset(name, lock["assets"][name], base_url, args.root, force=args.force)
            print(f"{name}: {status}")
    except BootstrapError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
