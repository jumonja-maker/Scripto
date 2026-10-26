"""Release safety tests; all GitHub reads and mutations are replaced locally."""
import copy
import hashlib
import importlib.util
import io
import json
from pathlib import Path
from types import SimpleNamespace

import pytest

SPEC = importlib.util.spec_from_file_location("publish_release", Path(__file__).parents[1] / "scripts/publish_release.py")
publisher = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(publisher)
VERSION = "0.1.1"
COMMIT = "a" * 40


@pytest.fixture
def publication(tmp_path, monkeypatch):
    directory = tmp_path / "dist"
    directory.mkdir()
    sums = []
    for name in [f"ScriptoLib-{VERSION}-Setup.exe", f"ScriptoLib-{VERSION}-windows-x64.zip"]:
        payload = name.encode()
        (directory / name).write_bytes(payload)
        sums.append(f"{hashlib.sha256(payload).hexdigest()}  {name}\n")
    (directory / "SHA256SUMS.txt").write_text("".join(sums))
    notes = tmp_path / "docs/releases"
    notes.mkdir(parents=True)
    (notes / f"{VERSION}.md").write_text("Version-specific changes.\n")
    monkeypatch.setattr(publisher, "ROOT", tmp_path)
    monkeypatch.setenv("GITHUB_REPOSITORY", publisher.REPOSITORY)
    expected = publisher.local_assets(directory, VERSION)
    state = SimpleNamespace(
        directory=directory, expected=expected, commands=[], release=None,
        latest={"tag_name": "v0.1.0"}, private=False, wrong_tag=False,
        mutate_assets=lambda assets: None, updater_versions=[],
        list_calls=0, hide_created_from_list=False, mutate_create=lambda release: None,
        stale_asset_reads=0, uploaded=False, mutations=[], sleeps=[],
    )

    def api(path, *, missing_ok=False, method="GET", data=None):
        if method == "POST":
            assert path == "/releases"
            assert data["draft"] is True and data["prerelease"] is False
            assert data["tag_name"] == "v" + VERSION and data["target_commitish"] == COMMIT
            state.mutations.append("create")
            state.release = {"id": 123, "draft": True, "prerelease": False, "target_commitish": COMMIT,
                             "tag_name": "v" + VERSION, "assets": [], "html_url": "https://github.com/release"}
            value = copy.deepcopy(state.release)
            state.mutate_create(value)
        elif path == "":
            value = {"full_name": publisher.REPOSITORY, "private": state.private}
        elif path == "/releases/latest":
            value = state.latest
        elif path == "/releases?per_page=100&page=1":
            state.list_calls += 1
            value = [state.release] if state.release and not state.hide_created_from_list else []
        elif path == "/releases/123":
            value = copy.deepcopy(state.release)
            if state.uploaded and state.stale_asset_reads > 0:
                state.stale_asset_reads -= 1
                value["assets"] = []
        elif path == f"/git/ref/tags/v{VERSION}":
            value = {"object": {"type": "commit", "sha": "b" * 40}} if state.wrong_tag else (
                {"object": {"type": "commit", "sha": COMMIT}} if state.release and not state.release["draft"] else None
            )
        else:
            pytest.fail(f"Unexpected API request: {path}")
        if value is None and not missing_ok:
            pytest.fail(f"Unexpected missing API resource: {path}")
        return copy.deepcopy(value)

    def gh(*arguments):
        state.commands.append(arguments)
        state.mutations.append(arguments[0])
        if arguments[0] == "upload":
            assert state.release["draft"], "No public release may be overwritten"
            state.release["assets"] = [{"name": name, "state": "uploaded", **values} for name, values in expected.items()]
            state.mutate_assets(state.release["assets"])
            state.uploaded = True
        elif arguments[0] == "edit":
            # The fake server also enforces completeness at the publication point.
            publisher.verify_assets(state.release, expected)
            assert "--draft=false" in arguments and "--latest" in arguments and "--prerelease=false" in arguments
            state.release["draft"] = False
            state.latest = state.release
        else:
            pytest.fail(f"Unexpected mutation: {arguments}")

    def check_release(previous):
        state.updater_versions.append(previous)
        package = expected[f"ScriptoLib-{VERSION}-windows-x64.zip"]
        return SimpleNamespace(version=VERSION, size=package["size"], sha256=package["digest"].removeprefix("sha256:"))

    monkeypatch.setattr(publisher, "api", api)
    monkeypatch.setattr(publisher, "gh", gh)
    monkeypatch.setattr(publisher, "check_release", check_release)
    monkeypatch.setattr(publisher.time, "sleep", state.sleeps.append)
    return state


def test_complete_draft_verified_before_publication(publication):
    url = publisher.publish(VERSION, COMMIT, publication.directory)
    assert url == "https://github.com/release"
    assert publication.mutations == ["create", "upload", "edit"]
    assert len(publication.release["assets"]) == 3
    assert publication.updater_versions == ["0.1.0"]
    assert "Version-specific changes." in (publication.directory / "release-notes.md").read_text()


def test_published_release_never_replaced(publication):
    publication.release = {"id": 123, "tag_name": "v" + VERSION, "draft": False, "target_commitish": COMMIT}
    with pytest.raises(ValueError, match="already published"):
        publisher.publish(VERSION, COMMIT, publication.directory)
    assert publication.commands == []


@pytest.mark.parametrize("field,value", [("digest", "sha256:" + "0" * 64), ("size", 1), ("state", "starter")])
def test_bad_uploaded_asset_stays_draft(publication, field, value):
    publication.mutate_assets = lambda assets: assets[0].update({field: value})
    with pytest.raises(ValueError, match="size/digest mismatch"):
        publisher.publish(VERSION, COMMIT, publication.directory)
    assert publication.mutations == ["create", "upload"]
    assert publication.release["draft"] is True


def test_missing_uploaded_asset_stays_draft(publication):
    publication.mutate_assets = lambda assets: assets.pop()
    with pytest.raises(ValueError, match="exactly the three"):
        publisher.publish(VERSION, COMMIT, publication.directory)
    assert publication.release["draft"] is True
    assert all(command[0] != "edit" for command in publication.commands)


def test_interrupted_same_commit_draft_can_resume(publication):
    publication.release = {"id": 123, "draft": True, "prerelease": False, "target_commitish": COMMIT,
                           "tag_name": "v" + VERSION, "assets": [], "html_url": "https://github.com/release"}
    publisher.publish(VERSION, COMMIT, publication.directory)
    assert [command[0] for command in publication.commands] == ["upload", "edit"]


def test_draft_for_different_commit_is_preserved(publication):
    publication.release = {"id": 123, "tag_name": "v" + VERSION, "draft": True, "target_commitish": "b" * 40}
    with pytest.raises(ValueError, match="different commit"):
        publisher.publish(VERSION, COMMIT, publication.directory)
    assert publication.commands == []


def test_changed_local_artifact_blocks_upload(publication):
    (publication.directory / f"ScriptoLib-{VERSION}-Setup.exe").write_bytes(b"altered")
    with pytest.raises(ValueError, match="Invalid local artifact"):
        publisher.publish(VERSION, COMMIT, publication.directory)
    assert publication.commands == []


def test_private_repository_is_not_published(publication):
    publication.private = True
    with pytest.raises(ValueError, match="public official"):
        publisher.publish(VERSION, COMMIT, publication.directory)
    assert publication.commands == []


def test_existing_tag_cannot_point_elsewhere(publication):
    publication.wrong_tag = True
    with pytest.raises(ValueError, match="different commit"):
        publisher.publish(VERSION, COMMIT, publication.directory)
    assert publication.commands == []


def test_latest_version_cannot_be_downgraded(publication):
    publication.latest = {"tag_name": "v0.2.0"}
    with pytest.raises(ValueError, match="newer"):
        publisher.publish(VERSION, COMMIT, publication.directory)
    assert publication.commands == []


def test_draft_search_paginates_without_published_tag_endpoint(monkeypatch):
    calls = []

    def api(path):
        calls.append(path)
        if path == "/releases?per_page=100&page=1":
            return [{"tag_name": f"v0.0.{index}"} for index in range(100)]
        assert path == "/releases?per_page=100&page=2"
        return [{"id": 123, "tag_name": "v" + VERSION, "draft": True}]

    monkeypatch.setattr(publisher, "api", api)
    assert publisher.find_release("v" + VERSION)["id"] == 123
    assert len(calls) == 2


def test_create_response_works_when_release_listing_stays_stale(publication):
    publication.hide_created_from_list = True
    publisher.publish(VERSION, COMMIT, publication.directory)
    assert publication.list_calls == 1  # only the pre-existing-draft search
    assert publication.mutations == ["create", "upload", "edit"]


@pytest.mark.parametrize("field,value", [("id", 0), ("id", True), ("tag_name", "v9.9.9"), ("draft", False), ("target_commitish", "b" * 40)])
def test_invalid_create_response_never_uploads(publication, field, value):
    publication.mutate_create = lambda release: release.update({field: value})
    with pytest.raises(ValueError):
        publisher.publish(VERSION, COMMIT, publication.directory)
    assert publication.mutations == ["create"]
    assert publication.commands == []


def test_stale_asset_reads_retry_without_repeating_mutations(publication):
    publication.stale_asset_reads = 2
    publisher.publish(VERSION, COMMIT, publication.directory)
    assert publication.mutations == ["create", "upload", "edit"]
    assert publication.sleeps == [3, 3]


def test_stale_asset_reads_eventually_stop_and_preserve_draft(publication):
    publication.stale_asset_reads = 20
    with pytest.raises(ValueError, match="exactly the three"):
        publisher.publish(VERSION, COMMIT, publication.directory)
    assert publication.mutations == ["create", "upload"]
    assert publication.sleeps == [3, 3, 3, 3]
    assert publication.release["draft"] is True


def test_api_uses_json_post_response_and_disables_get_cache(monkeypatch):
    requests = []
    monkeypatch.setenv("GH_TOKEN", "test-token")

    def open_request(request, *, timeout):
        requests.append(request)
        assert timeout == 30
        return io.BytesIO(b'{"id":123}')

    monkeypatch.setattr(publisher, "urlopen", open_request)
    assert publisher.api("/releases", method="POST", data={"draft": True}) == {"id": 123}
    assert requests[0].get_method() == "POST"
    assert requests[0].get_header("Content-type") == "application/json"
    assert json.loads(requests[0].data) == {"draft": True}
    publisher.api("/releases/123")
    assert requests[1].get_method() == "GET"
    assert requests[1].get_header("Cache-control") == "no-cache"
