#!/usr/bin/env python3
"""Publish verified local build outputs; never modify a published release."""
from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path
import re
import subprocess
import sys
import time
from urllib.error import HTTPError
from urllib.request import Request, urlopen

ROOT = Path(__file__).resolve().parents[1]
REPOSITORY = "jumonja-maker/Scripto"
API = f"https://api.github.com/repos/{REPOSITORY}"
sys.path.insert(0, str(ROOT / "src"))
from scriptolib.updates import check_release, version_tuple  # noqa: E402


def api(path: str, *, missing_ok: bool = False):
    request = Request(API + path, headers={
        "Accept": "application/vnd.github+json", "User-Agent": "ScriptoLib-Release",
        "X-GitHub-Api-Version": "2022-11-28",
        "Authorization": "Bearer " + os.environ["GH_TOKEN"],
    })
    try:
        with urlopen(request, timeout=30) as response:
            return json.load(response)
    except HTTPError as error:
        if missing_ok and error.code == 404:
            return None
        raise


def gh(*arguments: str) -> None:
    subprocess.run(["gh", "release", *arguments, "--repo", REPOSITORY], check=True)


def find_release(tag: str) -> dict | None:
    # /releases/tags/{tag} is documented for published releases. Authenticated
    # listing includes drafts; subsequent reads use their stable numeric ID.
    page = 1
    while True:
        releases = api(f"/releases?per_page=100&page={page}")
        for release in releases:
            if release.get("tag_name") == tag:
                return release
        if len(releases) < 100:
            return None
        page += 1


def sha256(path: Path) -> str:
    with path.open("rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest()


def local_assets(directory: Path, version: str) -> dict[str, dict]:
    names = [f"ScriptoLib-{version}-Setup.exe", f"ScriptoLib-{version}-windows-x64.zip"]
    checksums = directory / "SHA256SUMS.txt"
    expected = {}
    for line in checksums.read_text(encoding="ascii").splitlines():
        digest, name = line.split("  ", 1)
        if name in expected or name not in names or not re.fullmatch(r"[0-9a-f]{64}", digest):
            raise ValueError("Unexpected or repeated SHA256SUMS entry")
        expected[name] = digest
    if set(expected) != set(names):
        raise ValueError("SHA256SUMS must describe the installer and update ZIP")
    assets = {}
    for name in [*names, checksums.name]:
        path = directory / name
        digest = sha256(path)
        if path.stat().st_size <= 0 or (name in expected and digest != expected[name]):
            raise ValueError(f"Invalid local artifact: {name}")
        assets[name] = {"size": path.stat().st_size, "digest": "sha256:" + digest}
    return assets


def verify_assets(release: dict, expected: dict[str, dict]) -> None:
    actual = release.get("assets", [])
    if len(actual) != len(expected) or {asset["name"] for asset in actual} != set(expected):
        raise ValueError("Release must contain exactly the three expected assets")
    for asset in actual:
        if asset.get("state") != "uploaded" or any(asset.get(key) != value for key, value in expected[asset["name"]].items()):
            raise ValueError(f"GitHub size/digest mismatch: {asset['name']}")


def verify_tag(tag: str, commit: str, *, missing_ok: bool) -> None:
    reference = api(f"/git/ref/tags/{tag}", missing_ok=missing_ok)
    if reference is None:
        return
    obj = reference["object"]
    for _ in range(4):
        if obj["type"] != "tag":
            break
        obj = api(f"/git/tags/{obj['sha']}")["object"]
    if obj.get("type") != "commit" or obj.get("sha") != commit:
        raise ValueError("Release tag already points to a different commit")


def require_draft(release: dict, commit: str) -> None:
    if release.get("draft") is not True:
        raise ValueError("Release is already published; bump the version instead of replacing assets")
    if release.get("target_commitish") != commit:
        raise ValueError("Existing draft belongs to a different commit; review it manually")


def publish(version: str, commit: str, directory: Path) -> str:
    version_tuple(version)
    if not re.fullmatch(r"[0-9a-f]{40}", commit):
        raise ValueError("A full commit SHA is required")
    if os.environ.get("GITHUB_REPOSITORY", REPOSITORY) != REPOSITORY:
        raise ValueError("This publisher is only for the official repository")
    repository = api("")
    if repository.get("full_name") != REPOSITORY or repository.get("private") is not False:
        raise ValueError("Publishing is enabled only for the public official repository")
    expected = local_assets(directory, version)
    tag = "v" + version
    release = find_release(tag)
    if release:
        require_draft(release, commit)
    latest = api("/releases/latest", missing_ok=True)
    previous = latest["tag_name"].removeprefix("v") if latest else "0.0.0"
    if version_tuple(previous) >= version_tuple(version):
        raise ValueError("New stable version must be newer than the published latest release")
    verify_tag(tag, commit, missing_ok=True)
    if not release:
        notes = directory / "release-notes.md"
        version_notes = (ROOT / "docs" / "releases" / f"{version}.md").read_text(encoding="utf-8")
        notes.write_text(
            version_notes.rstrip() + "\n\n"
            f"Исходники: https://github.com/{REPOSITORY}/commit/{commit}\n\n"
            "SHA256 всех трёх приложенных файлов сверяется с GitHub перед публикацией.\n",
            encoding="utf-8",
        )
        gh("create", tag, "--draft", "--target", commit, "--title", f"ScriptoLib {version}", "--notes-file", str(notes))
        release = find_release(tag)
    if release is None:
        raise ValueError("Created draft was not found; inspect the release before retrying")
    endpoint = f"/releases/{release['id']}"
    # Resume only unpublished drafts for the identical commit. A failed upload
    # stays a draft; --clobber never intentionally replaces a public artifact.
    require_draft(api(endpoint), commit)
    gh("upload", tag, *(str(directory / name) for name in expected), "--clobber")
    release = api(endpoint)
    require_draft(release, commit)
    verify_assets(release, expected)
    verify_tag(tag, commit, missing_ok=True)
    gh("edit", tag, "--draft=false", "--prerelease=false", "--latest")
    # Also verify the unauthenticated updater path; do not install anything.
    for attempt in range(5):
        try:
            latest = api("/releases/latest")
            if latest.get("tag_name") != tag or latest.get("draft") or latest.get("prerelease"):
                raise ValueError("Published release is not stable/latest")
            verify_assets(latest, expected)
            verify_tag(tag, commit, missing_ok=False)
            found = check_release(previous)
            package = expected[f"ScriptoLib-{version}-windows-x64.zip"]
            if found is None or found.version != version or found.size != package["size"] or "sha256:" + found.sha256 != package["digest"]:
                raise ValueError("Updater cannot yet see the verified published package")
            return latest["html_url"]
        except (HTTPError, ValueError, RuntimeError):
            if attempt == 4:
                raise
            time.sleep(3)
    raise AssertionError("unreachable")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--version", required=True)
    parser.add_argument("--commit", required=True)
    parser.add_argument("--directory", type=Path, default=ROOT / "dist")
    options = parser.parse_args()
    print(publish(options.version, options.commit, options.directory.resolve()))


if __name__ == "__main__":
    main()
