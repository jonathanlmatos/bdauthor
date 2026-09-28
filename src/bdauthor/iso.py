"""Pack a BDMV directory into an ISO image with pycdlib.

The image is a UDF 2.60 + ISO9660 bridge, not the pure UDF 2.50 (with metadata partition)
that ImgBurn or tsMuxeR write, so a strict hardware player or burner may reject it.
Burning the BDMV directory directly is the safest way to get a disc.
"""

import re
from dataclasses import dataclass
from pathlib import Path

import pycdlib
from pycdlib.pycdlibexception import PyCdlibException

from bdauthor.bdmv import DISC_DIRECTORIES, directory_size, missing_files
from bdauthor.model import Media
from bdauthor.mux.base import ProgressCallback

_DEFAULT_LABEL = "BDAUTHOR"
_MAX_ISO_NAME = 30  # ISO9660 interchange level 3: name.ext up to 30 characters
_PART_SUFFIX = ".part"


class IsoError(Exception):
    """The ISO image could not be created."""


@dataclass(frozen=True)
class IsoResult:
    path: Path
    size_bytes: int
    file_count: int
    label: str


def find_disc_root(source: Path) -> Path:
    """The directory that contains BDMV/ (accepts the BDMV directory itself too)."""
    if (source / "BDMV").is_dir():
        return source
    if source.name == "BDMV" and source.is_dir():
        return source.parent
    raise IsoError(f"{source} does not contain a BDMV directory")


def volume_label(root: Path, label: str | None) -> str:
    """An ISO9660-safe volume label: A-Z, 0-9 and underscore, at most 32 characters."""
    raw = label if label is not None else root.resolve().name
    cleaned = re.sub(r"[^A-Z0-9_]", "_", raw.upper())[:32].strip("_")
    return cleaned or _DEFAULT_LABEL


def iso_file_name(name: str) -> str:
    """ISO9660 level 3 file identifier, e.g. 'MovieObject.bdmv' -> 'MOVIEOBJECT.BDMV;1'."""
    stem, dot, ext = name.rpartition(".")
    if not dot:
        stem, ext = name, ""
    identifier = f"{_iso_chars(stem)}.{_iso_chars(ext)}"
    if len(identifier) > _MAX_ISO_NAME:
        raise IsoError(f"file name too long for the ISO9660 layer: {name}")
    return f"{identifier};1"


def iso_dir_name(name: str) -> str:
    identifier = _iso_chars(name)
    if len(identifier) > _MAX_ISO_NAME + 1:
        raise IsoError(f"directory name too long for the ISO9660 layer: {name}")
    return identifier


def _iso_chars(text: str) -> str:
    return re.sub(r"[^A-Z0-9_]", "_", text.upper())


def _collect(root: Path) -> tuple[list[Path], list[Path]]:
    """(directories, files) below root that belong on the disc, relative to root, sorted."""
    directories: list[Path] = []
    files: list[Path] = []
    for top in DISC_DIRECTORIES:
        base = root / top
        if not base.is_dir():
            continue
        directories.append(Path(top))
        for path in sorted(base.rglob("*")):
            relative = path.relative_to(root)
            (directories if path.is_dir() else files).append(relative)
    return directories, files


def _add_tree(iso: pycdlib.PyCdlib, root: Path, directories: list[Path], files: list[Path]) -> None:
    def iso_path(relative: Path, *, is_file: bool) -> str:
        names = [iso_dir_name(part) for part in relative.parts[:-1]]
        last = relative.parts[-1]
        names.append(iso_file_name(last) if is_file else iso_dir_name(last))
        return "/" + "/".join(names)

    seen: set[str] = set()
    for relative in directories:
        iso.add_directory(iso_path(relative, is_file=False), udf_path="/" + relative.as_posix())
    for relative in files:
        target = iso_path(relative, is_file=True)
        if target in seen:
            raise IsoError(f"two files map to the same ISO9660 name: {relative}")
        seen.add(target)
        iso.add_file(str(root / relative), target, udf_path="/" + relative.as_posix())


def _verify(path: Path, root: Path, directories: list[Path], files: list[Path]) -> None:
    """Read the image back and compare directories, file list and sizes with the source."""
    iso = pycdlib.PyCdlib()
    try:
        iso.open(str(path))
        found_dirs: set[str] = set()
        found: dict[str, int] = {}
        for dirpath, dirs, names in iso.walk(udf_path="/"):
            base = dirpath.rstrip("/")
            found_dirs.update(f"{base}/{name}" for name in dirs)
            for name in names:
                full = f"{base}/{name}"
                found[full] = iso.get_record(udf_path=full).get_data_length()
    finally:
        iso.close()

    expected = {"/" + f.as_posix(): (root / f).stat().st_size for f in files}
    expected_dirs = {"/" + d.as_posix() for d in directories}  # includes empty ones (AUXDATA, ...)
    if found != expected or found_dirs != expected_dirs:
        missing = sorted((set(expected) - set(found)) | (expected_dirs - found_dirs))
        wrong = sorted(p for p in expected.keys() & found.keys() if expected[p] != found[p])
        raise IsoError(f"the image does not match the source (missing: {missing}, wrong size: {wrong})")


def build_iso(
    source: Path,
    dest: Path,
    media: Media,
    *,
    label: str | None = None,
    force: bool = False,
    on_progress: ProgressCallback | None = None,
) -> IsoResult:
    """Write `dest` from the BDMV (and CERTIFICATE) directories found under `source`."""
    root = find_disc_root(source)
    missing = missing_files(root / "BDMV")
    if missing:
        raise IsoError(f"{root / 'BDMV'} is not a complete disc; missing: {', '.join(missing)}")

    if dest.exists() and not force:
        raise IsoError(f"{dest} already exists; use --force to replace it")
    if dest.is_dir():
        raise IsoError(f"{dest} is a directory")

    directories, files = _collect(root)
    size = sum(directory_size(root / top) for top in DISC_DIRECTORIES if (root / top).is_dir())
    if size > media.capacity_bytes:
        raise IsoError(
            f"the disc content is {size / 2**30:.2f} GiB, which exceeds {media.label} "
            f"capacity ({media.capacity_bytes / 2**30:.2f} GiB)"
        )

    label = volume_label(root, label)
    partial = dest.with_name(dest.name + _PART_SUFFIX)
    iso = pycdlib.PyCdlib()
    try:
        iso.new(interchange_level=3, udf="2.60", vol_ident=label)
        _add_tree(iso, root, directories, files)
        dest.parent.mkdir(parents=True, exist_ok=True)

        def report(done: int, total: int, _opaque) -> None:
            if on_progress and total:
                on_progress(done * 100 / total)

        iso.write(str(partial), progress_cb=report)
        iso.close()
        _verify(partial, root, directories, files)
        partial.replace(dest)
    except (PyCdlibException, OSError) as exc:
        raise IsoError(f"could not write {dest}: {exc}") from exc
    finally:
        partial.unlink(missing_ok=True)
        try:
            iso.close()
        except PyCdlibException:
            pass  # already closed or never opened

    if on_progress:
        on_progress(100.0)
    return IsoResult(path=dest, size_bytes=dest.stat().st_size, file_count=len(files), label=label)
