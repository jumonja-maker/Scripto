import hashlib
import json
import os
from pathlib import Path
import zipfile

import pytest

from scriptolib.updates import ReleaseInfo, UpdateError, activate_version, current_version, stage_update
from scriptolib.update_worker import (
    PENDING_FILE, finish_or_recover, launch_allowed, mark_update_ready,
    prepare_restart, process_alive, register_update_child, run_update,
)


class Child:
    def __init__(self, pid):
        self.pid = pid
        self.stopped = False
    def poll(self):
        return 0 if self.stopped else None
    def terminate(self):
        self.stopped = True
    def kill(self):
        self.stopped = True
    def wait(self, timeout):
        self.stopped = True
        return 0


@pytest.fixture
def installation(tmp_path):
    root = tmp_path / "ScriptoLib"
    root.mkdir()
    for version in ("0.1.0", "0.2.0"):
        source = tmp_path / f"{version}.zip"
        with zipfile.ZipFile(source, "w") as package:
            package.writestr("scriptolib-package.json", json.dumps({"format": 1, "application": "ScriptoLib", "version": version}))
            package.writestr("runtime/pythonw.exe", b"fake runtime")
            package.writestr("app/bootstrap.py", b"# fake bootstrap")
        release = ReleaseInfo(version, f"https://github.com/jumonja-maker/Scripto/releases/download/v{version}/ScriptoLib-{version}-windows-x64.zip", hashlib.sha256(source.read_bytes()).hexdigest(), source.stat().st_size, "")
        stage_update(root, source, release)
    activate_version(root, "0.1.0")
    (root / "media").mkdir()
    (root / "media" / "Фильм.mkv").write_bytes(b"media")
    return root


def pending(root):
    return json.loads((root / "cache" / PENDING_FILE).read_text())


def rewrite_pending(root, **changes):
    record = pending(root)
    record.update(changes)
    (root / "cache" / PENDING_FILE).write_text(json.dumps(record))


def test_prepare_blocks_unrelated_launch_and_concurrent_update(installation):
    root = installation
    token = prepare_restart(root, "0.2.0", parent_pid=123)
    assert not launch_allowed(root)
    assert launch_allowed(root, token)
    with pytest.raises(UpdateError, match="уже выполняется"):
        prepare_restart(root, "0.2.0")
    assert current_version(root) == "0.1.0"


def test_parent_timeout_leaves_old_running_without_switch(installation):
    root = installation
    token = prepare_restart(root, "0.2.0", parent_pid=123)
    def unexpected(*args):
        pytest.fail("must not spawn while old GUI is alive")
    result = run_update(root, "0.2.0", 123, token, wait_for_parent=lambda *_: False, spawn=unexpected)
    assert not result.success and not result.rolled_back
    assert current_version(root) == "0.1.0"
    assert launch_allowed(root)
    assert not (root / "cache" / "update-helper.lock").exists()


def test_success_requires_ready_then_prunes_after_helper_exit(installation):
    root = installation
    token = prepare_restart(root, "0.2.0", parent_pid=123)
    calls = []
    def spawn(base, version, received_token):
        assert current_version(base) == version
        assert received_token == token
        calls.append(version)
        return Child(456)
    result = run_update(root, "0.2.0", 123, token, wait_for_parent=lambda *_: True,
                        spawn=spawn, ready=lambda *_: True)
    assert result.success and not result.rolled_back
    assert calls == ["0.2.0"]
    assert (root / "versions" / "0.1.0").exists()  # helper has not pruned itself
    assert pending(root)["status"] == "ready"
    assert finish_or_recover(root, alive=lambda *_: True) == []
    assert (root / "versions" / "0.1.0").exists()
    assert finish_or_recover(root, alive=lambda *_: False)
    assert not (root / "versions" / "0.1.0").exists()
    assert (root / "media" / "Фильм.mkv").read_bytes() == b"media"
    assert launch_allowed(root)


def test_failed_new_version_stopped_before_rollback(installation):
    root = installation
    token = prepare_restart(root, "0.2.0", parent_pid=123)
    children = []
    def spawn(base, version, received_token):
        if version == "0.1.0":
            assert children[0].stopped
        child = Child(456 + len(children))
        children.append(child)
        return child
    def ready(base, received_token, version, child, timeout):
        return version == "0.1.0"
    result = run_update(root, "0.2.0", 123, token, wait_for_parent=lambda *_: True, spawn=spawn, ready=ready)
    assert not result.success and result.rolled_back
    assert children[0].stopped and not children[1].stopped
    assert current_version(root) == "0.1.0"
    finish_or_recover(root, alive=lambda *_: False)
    assert not (root / "versions" / "0.2.0").exists()
    assert (root / "media" / "Фильм.mkv").exists()


def test_spawn_failure_restarts_previous_version(installation):
    root = installation
    token = prepare_restart(root, "0.2.0", parent_pid=123)
    calls = []
    def spawn(base, version, received_token):
        calls.append(version)
        if version == "0.2.0":
            raise OSError("bad executable")
        return Child(457)
    result = run_update(root, "0.2.0", 123, token, wait_for_parent=lambda *_: True, spawn=spawn, ready=lambda *_: True)
    assert result.rolled_back
    assert calls == ["0.2.0", "0.1.0"]
    assert current_version(root) == "0.1.0"


def test_startup_recovers_helper_crash_before_ready(installation):
    root = installation
    prepare_restart(root, "0.2.0", parent_pid=123)
    activate_version(root, "0.2.0")
    rewrite_pending(root, status="starting", helper_pid=444, child_pid=555)
    messages = finish_or_recover(root, alive=lambda *_: False)
    assert messages and "восстановлена" in messages[0]
    assert current_version(root) == "0.1.0"
    assert launch_allowed(root)
    assert not (root / "versions" / "0.2.0").exists()


def test_recovery_does_not_kill_or_remove_live_unacknowledged_child(installation):
    root = installation
    prepare_restart(root, "0.2.0", parent_pid=123)
    activate_version(root, "0.2.0")
    rewrite_pending(root, status="recovery_required", helper_pid=444, child_pid=555)
    messages = finish_or_recover(root, alive=lambda pid: pid == 555)
    assert messages and "отложена" in messages[0]
    assert current_version(root) == "0.2.0"
    assert (root / "versions" / "0.1.0").exists()
    assert not launch_allowed(root)


def test_ready_marker_recovers_helper_crash_after_healthy_launch(installation):
    root = installation
    token = prepare_restart(root, "0.2.0", parent_pid=123)
    activate_version(root, "0.2.0")
    rewrite_pending(root, status="starting", helper_pid=444)
    mark_update_ready(root, token, "0.2.0")
    finish_or_recover(root, alive=lambda *_: False)
    assert current_version(root) == "0.2.0"
    assert not (root / "versions" / "0.1.0").exists()
    assert launch_allowed(root)


def test_wrong_token_and_version_cannot_acknowledge(installation):
    root = installation
    token = prepare_restart(root, "0.2.0", parent_pid=123)
    with pytest.raises(UpdateError):
        mark_update_ready(root, "f" * 32, "0.1.0")
    with pytest.raises(UpdateError):
        mark_update_ready(root, token, "0.2.0")
    with pytest.raises(UpdateError):
        run_update(root, "0.2.0", 999, token)
    assert current_version(root) == "0.1.0"


def test_real_process_probe_detects_current_process():
    assert process_alive(os.getpid())
    assert not process_alive(None)


def test_ambiguous_spawn_crash_preserves_every_version_until_confirmed(installation):
    root = installation
    prepare_restart(root, "0.2.0", parent_pid=123)
    activate_version(root, "0.2.0")
    rewrite_pending(root, status="starting", helper_pid=444, child_pid=None)
    messages = finish_or_recover(root, alive=lambda *_: False)
    assert messages and "приостановлено" in messages[0]
    assert current_version(root) == "0.2.0"
    assert (root / "versions" / "0.1.0").exists()
    assert (root / "versions" / "0.2.0").exists()
    assert not launch_allowed(root)
    # Only an explicitly confirmed repair may override missing PID evidence;
    # even that never overrides a process that is positively known to be alive.
    finish_or_recover(root, alive=lambda *_: False, confirm_no_app_processes=True)
    assert current_version(root) == "0.1.0"
    assert launch_allowed(root)


def test_early_child_registration_closes_unrecorded_pid_case(installation):
    root = installation
    token = prepare_restart(root, "0.2.0", parent_pid=123)
    activate_version(root, "0.2.0")
    rewrite_pending(root, status="starting", helper_pid=444, child_pid=None)
    register_update_child(root, token, pid=555)
    assert pending(root)["child_pid"] == 555
    assert finish_or_recover(root, alive=lambda pid: pid == 555)
    assert current_version(root) == "0.2.0"
    mark_update_ready(root, token, "0.2.0")
    finish_or_recover(root, alive=lambda *_: False)
    assert current_version(root) == "0.2.0"
    assert launch_allowed(root)
