"""docs/05 의 `container` 프로파일 — agent 프로세스만 컨테이너 안에서 실행한다.

판정 증거(AC·diff·경로)는 호스트에서 만든다. 여기서 검증하는 것은 **감싸는 규칙**과
**준비물 부족의 분류**이며, 컨테이너 런타임이 없는 환경에서도 전부 돈다.
"""

import sys
from pathlib import Path

import pytest
import yaml
from test_runner import ScriptedAdapter, commit, configure, go, task_of, write_task

from harness.adapters.base import AgentRequest, RuntimeFailure
from harness.adapters.generic_cli import GenericCliAdapter, container_argv
from harness.config import load
from harness.errors import ConfigError
from harness.models import ContainerSpec, ExecutionProfile, State, Verdict

SPEC = ContainerSpec(image="example/agent:1")


def request_for(tmp_path: Path, spec: ContainerSpec | None = SPEC) -> AgentRequest:
    (tmp_path / "worktree").mkdir(exist_ok=True)
    return AgentRequest(
        task_id="T-001",
        prompt="구현하라",
        workspace=tmp_path / "worktree",
        outbox=tmp_path / "outbox",
        profile=ExecutionProfile.CONTAINER,
        allowed_tools=None,
        timeout_s=30,
        env={"PATH": "/usr/bin", "HARNESS_OUTBOX": str(tmp_path / "outbox")},
        attempt=1,
        container=spec,
    )


# --------------------------------------------------------------------- 감싸는 규칙


def test_workspace_and_outbox_are_mounted_at_the_same_path(tmp_path):
    argv = container_argv(
        SPEC,
        ["agent", "--go"],
        workspace=tmp_path / "worktree",
        outbox=tmp_path / "outbox",
        env_names=(),
        interactive=False,
    )
    workspace = str(tmp_path / "worktree")
    outbox = str(tmp_path / "outbox")
    assert argv[:3] == ["docker", "run", "--rm"]
    assert f"{workspace}:{workspace}" in argv
    assert f"{outbox}:{outbox}" in argv
    assert argv[argv.index("-w") + 1] == workspace
    # 이미지 뒤는 어댑터가 만든 argv 그대로다.
    assert argv[argv.index("example/agent:1") + 1 :] == ["agent", "--go"]


def test_env_is_passed_by_name_so_values_never_reach_the_process_list(tmp_path):
    argv = container_argv(
        SPEC,
        ["agent"],
        workspace=tmp_path / "w",
        outbox=tmp_path / "o",
        env_names=("HARNESS_OUTBOX", "ANTHROPIC_API_KEY"),
        interactive=False,
    )
    assert argv.count("-e") == 2
    assert "ANTHROPIC_API_KEY" in argv
    assert not any("ANTHROPIC_API_KEY=" in part for part in argv)


def test_the_control_plane_is_never_mounted(tmp_path):
    argv = container_argv(
        SPEC,
        ["agent"],
        workspace=tmp_path / "w",
        outbox=tmp_path / "o",
        env_names=(),
        interactive=False,
    )
    assert not any(".harness" in part for part in argv)


def test_network_is_the_runtime_default_unless_configured(tmp_path):
    argv = container_argv(
        SPEC, ["agent"], workspace=tmp_path / "w", outbox=tmp_path / "o", env_names=(), interactive=False
    )
    assert "--network" not in argv

    cut = ContainerSpec(image="example/agent:1", network="none")
    argv = container_argv(
        cut, ["agent"], workspace=tmp_path / "w", outbox=tmp_path / "o", env_names=(), interactive=False
    )
    assert argv[argv.index("--network") + 1] == "none"


def test_extra_mounts_and_runtime_are_configurable(tmp_path):
    spec = ContainerSpec(
        image="example/agent:1", runtime="podman", mounts=("/etc/ca.pem:/etc/ca.pem:ro",)
    )
    argv = container_argv(
        spec, ["agent"], workspace=tmp_path / "w", outbox=tmp_path / "o", env_names=(), interactive=False
    )
    assert argv[0] == "podman"
    assert "/etc/ca.pem:/etc/ca.pem:ro" in argv


def test_stdin_delivery_keeps_the_container_attached(tmp_path):
    argv = container_argv(
        SPEC, ["agent"], workspace=tmp_path / "w", outbox=tmp_path / "o", env_names=(), interactive=True
    )
    assert "-i" in argv


class RecordingAdapter(GenericCliAdapter):
    """실행 직전의 argv 를 잡아 둔다 — 감싸기가 실행 경로에 실제로 닿는지 본다."""

    spawned: list[str] = []

    def _spawn(self, argv, request):
        self.spawned = list(argv)
        return super()._spawn(argv, request)


def test_adapter_execution_goes_through_the_runtime(tmp_path):
    adapter = RecordingAdapter("cli", {"command": ["agent", "--go"], "prompt_delivery": "file"})
    spec = ContainerSpec(image="example/agent:1", runtime="harness-no-such-runtime")
    result = adapter.execute(request_for(tmp_path, spec))

    assert adapter.spawned[0] == "harness-no-such-runtime"
    assert adapter.spawned[-2:] == ["agent", "--go"]
    assert "example/agent:1" in adapter.spawned
    # 런타임이 없으면 실행 자체가 성립하지 않는다 (docs/04).
    assert result.runtime_failure is RuntimeFailure.PROTOCOL_VIOLATION


def test_without_a_spec_the_adapter_runs_on_the_host(tmp_path):
    adapter = GenericCliAdapter("cli", {"command": [sys.executable, "-c", "print('host')"]})
    result = adapter.execute(request_for(tmp_path, None))

    assert result.runtime_failure is None
    assert result.stdout.strip() == "host"


# --------------------------------------------------------------------- config


def test_config_loads_the_container_spec(repo):
    config = configure(
        repo,
        profile="worktree",
        container={"image": "example/agent:1", "network": "none", "mounts": ["/a:/a:ro"]},
    )
    assert config.container == ContainerSpec(
        image="example/agent:1", runtime="docker", network="none", mounts=("/a:/a:ro",)
    )


def test_config_rejects_a_container_block_without_an_image(repo):
    data = yaml.safe_load((repo / ".harness" / "config.yaml").read_text(encoding="utf-8"))
    data["container"] = {"runtime": "docker"}
    (repo / ".harness" / "config.yaml").write_text(yaml.safe_dump(data), encoding="utf-8")

    with pytest.raises(ConfigError):
        load(repo)


# --------------------------------------------------------------------- runner


def test_container_profile_dispatches_with_the_spec_and_a_worktree(repo):
    # runtime 은 준비물 검사만 통과하면 된다. 이 테스트가 보는 것은 감싸기가 아니라 dispatch 이고,
    # ScriptedAdapter 는 프로세스를 띄우지 않으므로 실제 컨테이너 런타임이 필요 없다.
    # git 은 모든 테스트가 이미 요구하는 실행 파일이다.
    configure(repo, profile="container", container={"image": "example/agent:1", "runtime": "git"})
    write_task(repo, "T-001", acceptance=[{"cmd": [sys.executable, "-c", "pass"]}])
    adapter = ScriptedAdapter([{"files": {"src/a.py": "x = 1\n"}}])

    store = go(repo, adapter=adapter)

    request = adapter.requests[0]
    assert request.container == ContainerSpec(image="example/agent:1", runtime="git")
    # 워크스페이스 생명주기는 worktree 와 같다 — 저장소 밖의 워크트리에서 돈다.
    assert request.workspace != repo
    assert task_of(store, "T-001").verdict is Verdict.VERIFIED


def test_a_missing_runtime_is_a_prerequisite_not_a_defect(repo):
    configure(
        repo,
        profile="container",
        container={"image": "example/agent:1", "runtime": "harness-no-such-runtime"},
        max_attempts=1,
    )
    write_task(repo, "T-001", acceptance=[{"cmd": [sys.executable, "-c", "pass"]}])
    adapter = ScriptedAdapter([{"files": {"src/a.py": "x = 1\n"}}])

    store = go(repo, adapter=adapter)

    task = task_of(store, "T-001")
    assert task.verdict is Verdict.BLOCKED
    assert "harness-no-such-runtime" in (task.reason or "")
    assert task.state is State.HUMAN_REQUIRED
    assert adapter.requests == []  # agent 는 실행되지 않는다


def test_container_profile_without_a_container_block_is_a_config_defect(repo):
    configure(repo, profile="container")
    write_task(repo, "T-001", acceptance=[{"cmd": [sys.executable, "-c", "pass"]}])
    commit(repo)

    with pytest.raises(Exception) as caught:
        go(repo, adapter=ScriptedAdapter([{}]))
    assert "container" in str(caught.value)
