"""Print curriculum worker / DB contention status.

Usage (from wittgenstein-learning/):
  .\\backend\\.venv\\Scripts\\python.exe backend/tools/check_curriculum_workers.py
"""
from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from backend.tools.curriculum_worker_guard import check_curriculum_workers


def main() -> None:
    r = check_curriculum_workers()
    print("=== Curriculum worker 狀態 ===")
    for msg in r.messages or ["（無 Docker / 本機 worker）"]:
        print(f"  • {msg}")
    print()
    print(f"  in-process 測試（live_small_file）安全： {'是' if r.ok_for_in_process else '否'}")
    print(f"  Docker enqueue 測試（live_arq_verify）安全： {'是' if r.ok_for_docker_enqueue else '否'}")
    if not r.ok_for_in_process and not r.ok_for_docker_enqueue:
        sys.exit(1)


if __name__ == "__main__":
    main()
