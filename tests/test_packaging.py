"""배포 계약 — 설치본만으로 동작한다 (docs/02 의 「배포」).

저장소 클론 안에서만 동작하는 하네스는 다른 프로젝트에 얹을 수 없다. 휠이 모든
서브패키지와 런타임 리소스를 담는지, 그리고 그 설치본만으로 저장소를 부트스트랩할
수 있는지 검증한다.
"""

from __future__ import annotations

import os
import subprocess
import sys
import zipfile
from pathlib import Path

import pytest

import harness
import harness.schemas
import harness.spec

PACKAGE_DIR = Path(harness.__file__).resolve().parent
REPO_ROOT = PACKAGE_DIR.parent

REQUIRED_ENTRIES = (
    "harness/cli.py",
    "harness/adapters/registry.py",
    "harness/exec/runner.py",
    "harness/context/builder.py",
    "harness/eval/arms.py",
    "harness/resources/schemas/task.schema.json",
    "harness/resources/templates/spec.yaml",
)


@pytest.fixture(scope="module")
def wheel(tmp_path_factory) -> Path:
    out = tmp_path_factory.mktemp("wheel")
    build = subprocess.run(
        [
            sys.executable,
            "-m",
            "pip",
            "wheel",
            "--no-deps",
            "--no-build-isolation",
            "-w",
            str(out),
            str(REPO_ROOT),
        ],
        capture_output=True,
        text=True,
    )
    assert build.returncode == 0, build.stderr[-2000:]
    wheels = sorted(out.glob("harness_framework-*.whl"))
    assert len(wheels) == 1, f"휠이 하나가 아니다: {wheels}"
    return wheels[0]


def test_runtime_resources_live_inside_the_package():
    """저장소 루트의 형제 디렉토리를 런타임에 읽지 않는다."""
    for directory in (harness.schemas.SCHEMA_DIR, harness.spec.TEMPLATE_DIR):
        assert directory.is_dir(), f"{directory} 가 없다"
        assert PACKAGE_DIR in directory.parents, f"{directory} 가 패키지 밖이다"


def test_the_package_and_the_project_declare_the_same_version():
    """설치본의 버전과 `harness.__version__` 이 갈라지면 무엇이 설치됐는지 알 수 없다."""
    pyproject = (REPO_ROOT / "pyproject.toml").read_text(encoding="utf-8")
    declared = next(
        line.split("=", 1)[1].strip().strip('"')
        for line in pyproject.splitlines()
        if line.startswith("version")
    )
    assert declared == harness.__version__
    assert declared != "0.0.0", "배포 버전을 정해야 한다"


def test_wheel_carries_every_module_and_resource(wheel: Path):
    names = set(zipfile.ZipFile(wheel).namelist())
    missing = [entry for entry in REQUIRED_ENTRIES if entry not in names]
    assert not missing, f"휠에 빠진 항목: {missing}"


def test_installed_wheel_bootstraps_a_repo(wheel: Path, plain_repo: Path, tmp_path: Path):
    site = tmp_path / "site"
    install = subprocess.run(
        [sys.executable, "-m", "pip", "install", "--no-deps", "--target", str(site), str(wheel)],
        capture_output=True,
        text=True,
    )
    assert install.returncode == 0, install.stderr[-2000:]

    # cwd 는 저장소 밖이다 — 클론이 sys.path 에 없어도 동작해야 한다.
    done = subprocess.run(
        [sys.executable, "-m", "harness", "init", "--repo", str(plain_repo)],
        cwd=str(tmp_path),
        env={**os.environ, "PYTHONPATH": str(site)},
        capture_output=True,
        text=True,
    )
    assert done.returncode == 0, done.stderr[-2000:]
    assert (plain_repo / ".harness" / "config.yaml").is_file()
