#!/usr/bin/env python3
"""Build a self-contained Windows x64 payload and folder-local NSIS installer.

This script runs on Linux or Windows with MinGW and NSIS. It never executes
downloaded Windows binaries. Application runtime downloads are pinned below.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import sys
import tomllib
import urllib.request
import zipfile

ROOT = Path(__file__).resolve().parents[1]
PYTHON_URL = "https://www.python.org/ftp/python/3.12.10/python-3.12.10-embed-amd64.zip"
PYSIDE_VERSION = "6.10.3"
INPUT_HASHES = {
    "python-3.12.10-embed-amd64.zip": "4acbed6dd1c744b0376e3b1cf57ce906f9dc9e95e68824584c8099a63025a3c3",
    "pyside6_essentials-6.10.3-cp39-abi3-win_amd64.whl": "051a70e2df79cf1c012a208ad82a98e8de23ca7fe3b0f3344d07152d15705b59",
    "shiboken6-6.10.3-cp39-abi3-win_amd64.whl": "38261c88e87a1c32d1302b642b755470d2369c78af3c7d7d9e95d631f79b556c",
}


def digest(path: Path) -> str:
    with path.open("rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest()


def verify_input(path: Path) -> None:
    if digest(path) != INPUT_HASHES.get(path.name):
        raise ValueError(f"Build dependency SHA256 mismatch: {path.name}")


def run(*args: str, env: dict[str, str] | None = None) -> None:
    subprocess.run(args, check=True, env=env)


def extract_zip(path: Path, destination: Path) -> None:
    with zipfile.ZipFile(path) as archive:
        for item in archive.infolist():
            name = Path(item.filename)
            if name.is_absolute() or ".." in name.parts or "\\" in item.filename:
                raise ValueError(f"Unsafe build dependency path: {item.filename}")
        archive.extractall(destination)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--version", default="0.1.0")
    parser.add_argument("--cache", type=Path, default=ROOT / "build" / "cache")
    parser.add_argument("--output", type=Path, default=ROOT / "dist")
    parser.add_argument("--cc", default="x86_64-w64-mingw32-gcc")
    parser.add_argument("--makensis", default="makensis")
    parser.add_argument("--windres", default="x86_64-w64-mingw32-windres")
    parser.add_argument("--nsisdir", type=Path)
    parser.add_argument("--skip-installer", action="store_true")
    options = parser.parse_args()
    if not re.fullmatch(r"(?:0|[1-9]\d*)\.(?:0|[1-9]\d*)\.(?:0|[1-9]\d*)", options.version):
        parser.error("version must be MAJOR.MINOR.PATCH")
    source_version = re.search(r'^__version__\s*=\s*[\"\']([^\"\']+)[\"\']', (ROOT / "src/scriptolib/__init__.py").read_text(encoding="utf-8"), re.MULTILINE)
    if not source_version or source_version.group(1) != options.version:
        parser.error("--version must equal src/scriptolib/__init__.py __version__; update source before packaging")
    project_version = tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))["project"]["version"]
    if project_version != options.version:
        parser.error("--version must equal pyproject.toml project.version")
    if not (ROOT / "bootstrap.py").is_file():
        raise FileNotFoundError("bootstrap.py must exist before packaging")
    cache, output = options.cache.resolve(), options.output.resolve()
    cache.mkdir(parents=True, exist_ok=True)
    output.mkdir(parents=True, exist_ok=True)
    python_zip = cache / "python-3.12.10-embed-amd64.zip"
    if not python_zip.exists():
        with urllib.request.urlopen(PYTHON_URL, timeout=60) as response, python_zip.open("wb") as target:
            shutil.copyfileobj(response, target)
    verify_input(python_zip)
    wheels = cache / "wheels"
    wheels.mkdir(exist_ok=True)
    for package in ["PySide6_Essentials", "shiboken6"]:
        if not list(wheels.glob(f"{package.lower()}-{PYSIDE_VERSION}-*win_amd64.whl")):
            run(sys.executable, "-m", "pip", "download", "--only-binary=:all:", "--platform", "win_amd64", "--python-version", "312", "--abi", "cp312", "--implementation", "cp", "--no-deps", f"{package}=={PYSIDE_VERSION}", "-d", str(wheels))
    payload = output / f"ScriptoLib-{options.version}-portable"
    if payload.exists():
        shutil.rmtree(payload)
    version_dir = payload / "versions" / options.version
    runtime, app = version_dir / "runtime", version_dir / "app"
    runtime.mkdir(parents=True)
    app.mkdir()
    extract_zip(python_zip, runtime)
    site_packages = runtime / "Lib" / "site-packages"
    site_packages.mkdir(parents=True)
    selected_wheels = []
    for pattern in [f"pyside6_essentials-{PYSIDE_VERSION}-*win_amd64.whl", f"shiboken6-{PYSIDE_VERSION}-*win_amd64.whl"]:
        matches = list(wheels.glob(pattern))
        if len(matches) != 1:
            raise ValueError(f"Expected exactly one wheel for {pattern}")
        selected_wheels.append(matches[0])
        verify_input(matches[0])
        extract_zip(matches[0], site_packages)
    (runtime / "python312._pth").write_text("python312.zip\n.\nLib/site-packages\n../app/src\nimport site\n", encoding="utf-8")
    shutil.copy2(ROOT / "bootstrap.py", app / "bootstrap.py")
    shutil.copytree(ROOT / "src", app / "src", ignore=shutil.ignore_patterns("__pycache__", "*.pyc"))
    docs = app / "docs"
    docs.mkdir()
    for name in ["README.md", "LICENSE", "THIRD_PARTY_NOTICES.md"]:
        if (ROOT / name).is_file():
            shutil.copy2(ROOT / name, docs / name)
    for source in (ROOT / "docs").glob("*.md"):
        shutil.copy2(source, docs / source.name)
    if (ROOT / "packaging" / "licenses").is_dir():
        shutil.copytree(ROOT / "packaging" / "licenses", docs / "licenses")
    inputs = {"python": {"url": PYTHON_URL, "sha256": digest(python_zip)}, "wheels": {p.name: digest(p) for p in selected_wheels}}
    (app / "build-inputs.json").write_text(json.dumps(inputs, indent=2) + "\n", encoding="utf-8")
    native_resource = output / "native-resource.rc"
    native_resource_object = output / "native-resource.o"
    native_resource.write_text('1 24 "' + (ROOT / "packaging" / "app.manifest").as_posix() + '"\n', encoding="utf-8")
    run(options.windres, f"--preprocessor={options.cc}", "--preprocessor-arg=-E", "--preprocessor-arg=-xc", "--preprocessor-arg=-DRC_INVOKED", str(native_resource), "-O", "coff", "-o", str(native_resource_object))
    for binary, source in [("ScriptoLib.exe", "launcher.c"), ("Uninstall.exe", "uninstall.c")]:
        run(options.cc, "-municode", "-mwindows", "-Os", "-s", "-static", "-Wall", "-Wextra", "-Werror", str(ROOT / "packaging" / source), str(native_resource_object), "-o", str(payload / binary))
    files = {p.relative_to(version_dir).as_posix(): digest(p) for p in sorted(version_dir.rglob("*")) if p.is_file()}
    manifest = {"format": 1, "application": "ScriptoLib", "version": options.version, "files": files}
    (version_dir / "scriptolib-package.json").write_text(json.dumps(manifest, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    archive_path = output / f"ScriptoLib-{options.version}-windows-x64.zip"
    with zipfile.ZipFile(archive_path, "w", zipfile.ZIP_DEFLATED, compresslevel=6) as archive:
        for path in sorted(version_dir.rglob("*")):
            if path.is_file():
                archive.write(path, path.relative_to(version_dir).as_posix())
    owned = [*files, "scriptolib-package.json", ".scriptolib-version.json", ".scriptolib-owned.txt"]
    ownership = {"format": 1, "application": "ScriptoLib", "version": options.version, "files": [*files, "scriptolib-package.json"]}
    (version_dir / ".scriptolib-version.json").write_text(json.dumps(ownership, indent=2) + "\n", encoding="utf-8")
    (version_dir / ".scriptolib-owned.txt").write_text("\n".join(owned) + "\n", encoding="utf-8")
    (payload / ".scriptolib-install.json").write_text('{"format":1,"application":"ScriptoLib"}\n', encoding="utf-8")
    (payload / "current.txt").write_text(options.version + "\n", encoding="ascii")
    installer = output / f"ScriptoLib-{options.version}-Setup.exe"
    if not options.skip_installer:
        env = os.environ.copy()
        if options.nsisdir:
            env["NSISDIR"] = str(options.nsisdir.resolve())
        engine = output / f"ScriptoLib-{options.version}-engine.exe"
        run(options.makensis, f"-DVERSION={options.version}", f"-DPAYLOAD_DIR={payload}", f"-DOUTPUT_FILE={engine}", str(ROOT / "packaging" / "installer.nsi"), env=env)
        resource = output / "setup-resource.rc"
        resource_object = output / "setup-resource.o"
        resource.write_text('101 RCDATA "' + engine.as_posix() + '"\n1 24 "' + (ROOT / "packaging" / "app.manifest").as_posix() + '"\n', encoding="utf-8")
        run(options.windres, f"--preprocessor={options.cc}", "--preprocessor-arg=-E", "--preprocessor-arg=-xc", "--preprocessor-arg=-DRC_INVOKED", str(resource), "-O", "coff", "-o", str(resource_object))
        max_payload_path = max(len(p.relative_to(payload).as_posix()) for p in payload.rglob("*") if p.is_file())
        max_install_root = min(150, 258 - max_payload_path)
        if max_install_root < 20:
            raise ValueError("Payload paths are too long for reliable Windows installation")
        run(options.cc, "-municode", "-mwindows", "-Os", "-s", "-static", "-Wall", "-Wextra", "-Werror", f"-DMAX_INSTALL_ROOT={max_install_root}", str(ROOT / "packaging" / "setup.c"), str(resource_object), "-lshell32", "-lole32", "-o", str(installer))
        # Prove that the executable embeds the exact freshly built engine.
        if engine.read_bytes() not in installer.read_bytes():
            raise ValueError("Installer resource does not match built NSIS engine")
    outputs = [archive_path] + ([installer] if not options.skip_installer else [])
    (output / "SHA256SUMS.txt").write_text("".join(f"{digest(p)}  {p.name}\n" for p in outputs), encoding="ascii")
    print(json.dumps({"installer": str(installer) if not options.skip_installer else None, "update_archive": str(archive_path), "portable_directory": str(payload)}, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
