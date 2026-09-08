"""Package only public runtime files; never upload the repository as a Pages artifact."""

import os
import shutil
import stat

from update_news import ROOT
from validate_site import validate


def remove_readonly(operation, path, exception_info):
    error = exception_info[1]
    if not isinstance(error, PermissionError):
        raise error
    status = os.lstat(path)
    if stat.S_ISLNK(status.st_mode) or not (
        getattr(status, "st_file_attributes", 0) & stat.FILE_ATTRIBUTE_READONLY
    ):
        raise error
    # Windows/OneDrive can mark our generated directories read-only.
    os.chmod(path, status.st_mode | stat.S_IWRITE)
    operation(path)


def main():
    validate()
    destination = ROOT / "_site"
    if destination.is_symlink():
        raise ValueError("Refusing to replace a symlinked site output directory")
    if destination.exists():
        shutil.rmtree(destination, onerror=remove_readonly)
    destination.mkdir()
    for name in ("index.html", "news.json", "resource-health.json", "feed.xml",
                 "content/catalog.json", "demo/config.json"):
        source = ROOT / name
        if source.is_symlink():
            raise ValueError(f"Public artifact input must not be a symlink: {name}")
        target = destination / name
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(source, target)
    for source in (ROOT / "assets").rglob("*"):
        if source.is_file():
            if source.is_symlink() or source.suffix not in {".css", ".js", ".svg", ".png"}:
                raise ValueError(f"Unexpected public asset: {source.name}")
            target = destination / source.relative_to(ROOT)
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(source, target)
    (destination / ".nojekyll").touch()
    print("Public Pages artifact written to _site (no deployment manifests, tests, tokens or tooling).")


if __name__ == "__main__":
    main()
