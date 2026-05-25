"""Detect curriculum DB writer conflicts (Docker worker vs local arq / in-process tests)."""
from __future__ import annotations

import os
import shutil
import subprocess
import sys
from dataclasses import dataclass


class DbContentionError(RuntimeError):
    pass


@dataclass
class WorkerCheckResult:
    docker_worker_running: bool
    local_arq_pids: list[int]
    local_live_small_pids: list[int]
    ok_for_in_process: bool
    ok_for_docker_enqueue: bool
    messages: list[str]


def _docker_worker_running(container: str = "wl-curriculum-worker") -> bool:
    if not shutil.which("docker"):
        return False
    try:
        out = subprocess.run(
            ["docker", "inspect", "-f", "{{.State.Running}}", container],
            capture_output=True,
            text=True,
            timeout=10,
            check=False,
        )
        return out.returncode == 0 and out.stdout.strip().lower() == "true"
    except (OSError, subprocess.TimeoutExpired):
        return False


def _windows_python_pids(pattern: str) -> list[int]:
    if sys.platform != "win32":
        return []
    try:
        ps = subprocess.run(
            [
                "powershell",
                "-NoProfile",
                "-Command",
                (
                    "Get-CimInstance Win32_Process -Filter \"Name='python.exe'\" | "
                    f"Where-Object {{ $_.CommandLine -match '{pattern}' }} | "
                    "Select-Object -ExpandProperty ProcessId"
                ),
            ],
            capture_output=True,
            text=True,
            timeout=15,
            check=False,
        )
        if ps.returncode != 0:
            return []
        return [int(x.strip()) for x in ps.stdout.splitlines() if x.strip().isdigit()]
    except (OSError, subprocess.TimeoutExpired, ValueError):
        return []


def check_curriculum_workers(*, exclude_pid: int | None = None) -> WorkerCheckResult:
    docker_up = _docker_worker_running()
    arq_pids = [
        p for p in _windows_python_pids(r"-m arq backend\.jobs\.arq_settings")
        if exclude_pid is None or p != exclude_pid
    ]
    live_pids = [
        p for p in _windows_python_pids(r"live_small_file_curriculum_test")
        if exclude_pid is None or p != exclude_pid
    ]
    msgs: list[str] = []
    if docker_up:
        msgs.append("Docker worker wl-curriculum-worker 運行中")
    if arq_pids:
        msgs.append(f"本機 Arq worker 進程：{arq_pids}")
    if live_pids:
        msgs.append(f"live_small_file 進程：{live_pids}")

    writers = docker_up or bool(arq_pids) or bool(live_pids)
    ok_in_process = not writers
    ok_enqueue = docker_up and not arq_pids and not live_pids

    if docker_up and arq_pids:
        msgs.append("衝突：Docker worker 與本機 Arq 同時存在")
    if docker_up and live_pids:
        msgs.append("衝突：Docker worker 與 live_small_file 同時寫 DB")

    return WorkerCheckResult(
        docker_worker_running=docker_up,
        local_arq_pids=arq_pids,
        local_live_small_pids=live_pids,
        ok_for_in_process=ok_in_process,
        ok_for_docker_enqueue=ok_enqueue,
        messages=msgs,
    )


def assert_no_db_contention(*, allow_in_process: bool = False) -> None:
    """In-process curriculum（live_small_file）不可與任何 worker 並存。"""
    r = check_curriculum_workers(exclude_pid=os.getpid())
    if allow_in_process:
        return
    if not r.ok_for_in_process:
        lines = [
            "拒絕 in-process curriculum：learning.db 可能正被其他 worker 寫入。",
            *r.messages,
            "",
            "建議：",
            "  • 長教材 / Arq 模式：docker compose up -d && live_arq_verify.py",
            "  • 本機 in-process 測試：先 docker compose stop curriculum-worker",
            "  • 強制略過（自負風險）：加 --force-in-process",
        ]
        raise DbContentionError("\n".join(lines))


def assert_docker_worker_ready() -> None:
    """Enqueue 測試（live_arq_verify）需 Docker worker，且無本機 arq。"""
    r = check_curriculum_workers(exclude_pid=os.getpid())
    if r.ok_for_docker_enqueue:
        return
    lines = [
        "Arq enqueue 測試需要唯一 writer：Docker worker。",
        *r.messages,
        "",
        "請執行：",
        "  cd wittgenstein-learning",
        "  docker compose up -d",
        "  docker compose logs -f curriculum-worker",
        "",
        "並停止本機：python -m arq backend.jobs.arq_settings.WorkerSettings",
    ]
    raise DbContentionError("\n".join(lines))
