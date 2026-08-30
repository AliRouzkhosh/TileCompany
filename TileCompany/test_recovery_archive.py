import importlib.util
import io
import tarfile
from pathlib import Path
from tempfile import TemporaryDirectory

from django.test import SimpleTestCase


def _archive_module():
    path = Path(__file__).resolve().parents[1] / "ops" / "recovery_archive.py"
    spec = importlib.util.spec_from_file_location("recovery_archive", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


archive = _archive_module()


class RecoveryArchiveTests(SimpleTestCase):
    def test_archive_round_trip_is_readable_and_preserves_relative_tree(self):
        with TemporaryDirectory() as temporary:
            root = Path(temporary)
            source, target, archive_path = root / "source", root / "target", root / "media.tar.gz"
            (source / "nested").mkdir(parents=True)
            (source / "nested" / "asset.txt").write_bytes(b"recovery-safe")

            created = archive.create(source, archive_path)
            self.assertEqual(created["member_count"], 2)
            self.assertEqual(archive.verify(archive_path)["member_count"], 2)
            archive.extract(archive_path, target)
            self.assertEqual((target / "nested" / "asset.txt").read_bytes(), b"recovery-safe")

    def test_path_traversal_archive_is_rejected_before_extraction(self):
        with TemporaryDirectory() as temporary:
            root = Path(temporary)
            archive_path, target = root / "unsafe.tar.gz", root / "target"
            with tarfile.open(archive_path, "w:gz") as handle:
                member = tarfile.TarInfo("../escape.txt")
                member.size = 1
                handle.addfile(member, io.BytesIO(b"x"))

            with self.assertRaises(ValueError):
                archive.extract(archive_path, target)

    def test_absolute_and_nested_traversal_members_are_rejected(self):
        with TemporaryDirectory() as temporary:
            root = Path(temporary)
            for index, name in enumerate(("/escape.txt", "nested/../../escape.txt")):
                archive_path = root / f"unsafe-{index}.tar.gz"
                with tarfile.open(archive_path, "w:gz") as handle:
                    member = tarfile.TarInfo(name)
                    member.size = 1
                    handle.addfile(member, io.BytesIO(b"x"))
                with self.assertRaises(ValueError):
                    archive.verify(archive_path)

    def test_link_and_special_members_are_rejected_before_any_write(self):
        with TemporaryDirectory() as temporary:
            root = Path(temporary)
            for index, member in enumerate((
                tarfile.TarInfo("link"),
                tarfile.TarInfo("hard-link"),
                tarfile.TarInfo("fifo"),
            )):
                if index == 0:
                    member.type = tarfile.SYMTYPE
                    member.linkname = "../escape"
                elif index == 1:
                    member.type = tarfile.LNKTYPE
                    member.linkname = "../escape"
                else:
                    member.type = tarfile.FIFOTYPE
                archive_path = root / f"unsafe-member-{index}.tar.gz"
                target = root / f"target-{index}"
                with tarfile.open(archive_path, "w:gz") as handle:
                    handle.addfile(member)
                with self.assertRaises(ValueError):
                    archive.extract(archive_path, target)
                self.assertTrue(target.exists())
                self.assertFalse(any(target.iterdir()))
