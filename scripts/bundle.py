"""Build a Windows Python 3.12 x64 SDK bundle, with no EXE entry points."""

from __future__ import annotations

import json
import shutil
import subprocess
import sys
import tempfile
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


def main() -> None:
    output = Path(sys.argv[1])
    with tempfile.TemporaryDirectory(prefix="toolkit-bundle-") as scratch:
        root = Path(scratch)
        wheels, bundle = root / "wheels", root / "toolkit"
        subprocess.run(
            [sys.executable, "-m", "pip", "download", "--only-binary=:all:", "--platform", "win_amd64", "--python-version", "312",
             "--implementation", "cp", "--abi", "cp312", "--dest", str(wheels), "--require-hashes",
             "-r", str(ROOT / "requirements" / "windows-py312.lock")], check=True,
        )
        patterns = (".git", ".venv", ".runtime", "__pycache__", ".pytest_cache", ".mypy_cache", ".ruff_cache", ".pyright", "dist")
        def ignored(folder: str, names: list[str]) -> set[str]:
            return {name for name in names if name in patterns or name.endswith(".egg-info")}

        shutil.copytree(ROOT, bundle, ignore=ignored)
        runtime = bundle / ".runtime"
        runtime.mkdir()
        packages: list[str] = []
        for wheel in sorted(wheels.glob("*.whl")):
            packages.append(wheel.name)
            with zipfile.ZipFile(wheel) as archive:
                for info in archive.infolist():
                    path = Path(info.filename)
                    if info.is_dir() or path.suffix.lower() in (".exe", ".pyc") or any(part.endswith(".data") for part in path.parts):
                        continue
                    if path.is_absolute() or ".." in path.parts:
                        raise ValueError(f"Invalid wheel member: {info.filename}")
                    target = runtime / path
                    target.parent.mkdir(parents=True, exist_ok=True)
                    target.write_bytes(archive.read(info))
        manifest: dict[str, str | list[str]] = {"python": "3.12", "platform": "win_amd64", "wheels": packages}
        (runtime / "manifest.json").write_text(json.dumps(manifest, indent=2))
        output.parent.mkdir(parents=True, exist_ok=True)
        with zipfile.ZipFile(output, "w", zipfile.ZIP_DEFLATED) as archive:
            for path in sorted(bundle.rglob("*")):
                if path.is_file():
                    if path.suffix.lower() == ".exe":
                        raise ValueError(f"EXE is not allowed in the bundle: {path}")
                    archive.write(path, path.relative_to(root))
        print(f"Built {output}: {len(packages)} wheels, no executable entry points")


if __name__ == "__main__":
    main()
