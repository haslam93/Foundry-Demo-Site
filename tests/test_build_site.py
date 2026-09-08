import stat
import sys
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
import build_site


class SiteBuildTests(unittest.TestCase):
    def test_readonly_windows_directory_is_made_writable_and_removed(self):
        operation = Mock()
        error = PermissionError("Read-only output")
        mode = stat.S_IFDIR | stat.S_IREAD
        status = SimpleNamespace(st_mode=mode, st_file_attributes=stat.FILE_ATTRIBUTE_READONLY)
        with patch.object(build_site.os, "lstat", return_value=status), \
                patch.object(build_site.os, "chmod") as chmod:
            build_site.remove_readonly(operation, "generated", (PermissionError, error, None))
        chmod.assert_called_once_with("generated", mode | stat.S_IWRITE)
        operation.assert_called_once_with("generated")

    def test_non_readonly_permissions_and_symlinks_are_not_changed(self):
        for mode, attributes in ((stat.S_IFDIR, 0), (stat.S_IFLNK, stat.FILE_ATTRIBUTE_READONLY)):
            with self.subTest(mode=mode):
                operation = Mock()
                error = PermissionError("Not a generated read-only directory")
                status = SimpleNamespace(st_mode=mode, st_file_attributes=attributes)
                with patch.object(build_site.os, "lstat", return_value=status), \
                        patch.object(build_site.os, "chmod") as chmod, \
                        self.assertRaises(PermissionError) as raised:
                    build_site.remove_readonly(operation, "generated", (PermissionError, error, None))
                self.assertIs(raised.exception, error)
                chmod.assert_not_called()
                operation.assert_not_called()

    def test_other_removal_errors_propagate(self):
        operation = Mock()
        error = FileNotFoundError("Missing output")
        with self.assertRaises(FileNotFoundError) as raised:
            build_site.remove_readonly(operation, "generated", (FileNotFoundError, error, None))
        self.assertIs(raised.exception, error)
        operation.assert_not_called()

    def test_rebuild_contains_only_allowlisted_public_files(self):
        public = (
            "index.html", "news.json", "resource-health.json", "feed.xml",
            "content/catalog.json", "demo/config.json", "assets/app.js", "assets/hub.css",
        )
        private = ("demo/agent.json", "README.md", ".env")
        with tempfile.TemporaryDirectory(prefix="foundry-site-test-") as directory:
            root = Path(directory)
            for name in (*public, *private, "_site/obsolete.html"):
                path = root / name
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_text("fixture", encoding="utf-8")
            with patch.object(build_site, "ROOT", root), patch.object(build_site, "validate") as validate:
                build_site.main()
            validate.assert_called_once()
            output = {path.relative_to(root / "_site").as_posix()
                      for path in (root / "_site").rglob("*") if path.is_file()}
            self.assertEqual(output, {*public, ".nojekyll"})


if __name__ == "__main__":
    unittest.main()
