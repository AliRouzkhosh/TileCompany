"""Create, inspect, and safely extract local recovery volume archives.

This helper is deliberately dependency-free so it can run in the pinned
application image. It never follows symlinks and never writes outside the
explicit extraction target.
"""

from __future__ import annotations

import argparse
import json
import os
import tarfile
from pathlib import Path, PurePosixPath


def _safe_name(name: str) -> PurePosixPath:
    path = PurePosixPath(name)
    if path.is_absolute() or ".." in path.parts or not path.parts:
        raise ValueError(f"Unsafe archive member path: {name!r}")
    return path


def _members(archive: tarfile.TarFile):
    members = archive.getmembers()
    for member in members:
        _safe_name(member.name)
        if not (member.isdir() or member.isreg()):
            raise ValueError("Recovery archives may contain only directories and regular files.")
    return members


def create(source: Path, destination: Path) -> dict[str, int]:
    if not source.is_dir():
        raise ValueError("Archive source must be a directory.")
    destination.parent.mkdir(parents=True, exist_ok=True)
    members = 0
    with tarfile.open(destination, "w:gz", dereference=False) as archive:
        for root, directories, filenames in os.walk(source, followlinks=False):
            root_path = Path(root)
            if root_path.is_symlink():
                raise ValueError("Recovery archive source contains a symlinked directory.")
            relative_root = root_path.relative_to(source)
            if relative_root.parts:
                archive.add(root_path, arcname=relative_root.as_posix(), recursive=False)
                members += 1
            for filename in filenames:
                item = root_path / filename
                if item.is_symlink() or not item.is_file():
                    raise ValueError("Recovery archive source contains a non-regular file or symlink.")
                archive.add(item, arcname=item.relative_to(source).as_posix(), recursive=False)
                members += 1
    return {"member_count": members, "size_bytes": destination.stat().st_size}


def verify(archive_path: Path) -> dict[str, int]:
    if not archive_path.is_file() or archive_path.stat().st_size == 0:
        raise ValueError("Recovery archive is missing or empty.")
    with tarfile.open(archive_path, "r:gz") as archive:
        members = _members(archive)
    return {"member_count": len(members), "size_bytes": archive_path.stat().st_size}


def extract(archive_path: Path, destination: Path) -> dict[str, int]:
    if destination.exists() and any(destination.iterdir()):
        raise ValueError("Recovery extraction target must be empty.")
    destination.mkdir(parents=True, exist_ok=True)
    with tarfile.open(archive_path, "r:gz") as archive:
        members = _members(archive)
        for member in members:
            target = destination.joinpath(*_safe_name(member.name).parts)
            if member.isdir():
                target.mkdir(parents=True, exist_ok=True)
                os.chmod(target, member.mode)
                os.utime(target, (member.mtime, member.mtime))
                continue
            target.parent.mkdir(parents=True, exist_ok=True)
            extracted = archive.extractfile(member)
            if extracted is None:
                raise ValueError("Recovery archive member could not be read.")
            with extracted, target.open("xb") as output:
                while chunk := extracted.read(1024 * 1024):
                    output.write(chunk)
            os.chmod(target, member.mode)
            os.utime(target, (member.mtime, member.mtime))
    return {"member_count": len(members)}


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("operation", choices=("create", "verify", "extract"))
    parser.add_argument("first")
    parser.add_argument("second", nargs="?")
    arguments = parser.parse_args()
    if arguments.operation == "create":
        if not arguments.second:
            parser.error("create requires SOURCE and DESTINATION")
        result = create(Path(arguments.first), Path(arguments.second))
    elif arguments.operation == "verify":
        result = verify(Path(arguments.first))
    else:
        if not arguments.second:
            parser.error("extract requires ARCHIVE and DESTINATION")
        result = extract(Path(arguments.first), Path(arguments.second))
    print(json.dumps(result, sort_keys=True))


if __name__ == "__main__":
    main()
