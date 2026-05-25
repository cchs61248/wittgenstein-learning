"""Abandon a stuck generating session (marks abandoned, clears source_file_ids_json)."""
import argparse
import sqlite3
from pathlib import Path

DEFAULT_DB = Path(__file__).resolve().parents[2] / "data" / "learning.db"


def main(session_id: str, db_path: Path, *, delete_checkpoint: bool) -> None:
    conn = sqlite3.connect(db_path)
    before = conn.execute(
        "SELECT status FROM sessions WHERE session_id=?", (session_id,)
    ).fetchone()
    if not before:
        print(f"{session_id}: NOT FOUND")
        conn.close()
        return
    print(f"{session_id}: before status={before[0]}")
    if delete_checkpoint:
        conn.execute(
            "DELETE FROM curriculum_checkpoints WHERE session_id=?",
            (session_id,),
        )
        print(f"{session_id}: checkpoint deleted")
    conn.execute(
        "UPDATE sessions SET status='abandoned', source_file_ids_json='[]' "
        "WHERE session_id=? AND status='generating'",
        (session_id,),
    )
    conn.commit()
    after = conn.execute(
        "SELECT status FROM sessions WHERE session_id=?", (session_id,)
    ).fetchone()
    print(f"{session_id}: after status={after[0] if after else 'NOT FOUND'}")
    conn.close()


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Abandon a stuck generating session")
    parser.add_argument("session_id")
    parser.add_argument("--db", type=Path, default=DEFAULT_DB)
    parser.add_argument(
        "--delete-checkpoint",
        action="store_true",
        help="Also delete curriculum_checkpoints row",
    )
    args = parser.parse_args()
    main(args.session_id, args.db, delete_checkpoint=args.delete_checkpoint)