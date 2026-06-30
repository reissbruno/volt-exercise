"""Seed data/exercises.json into the SQLite database."""
import json
import os
import sys
from pathlib import Path

# Allow importing server.py from the project root
ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(ROOT))

from dotenv import load_dotenv
load_dotenv(ROOT / ".env")

from server import init_db, open_db  # noqa: E402

DATA_PATH = Path(os.getenv("EXERCISES_JSON_PATH", str(ROOT / "data" / "exercises.json")))

if not DATA_PATH.exists():
    print(f"ERROR: exercises.json not found at {DATA_PATH}")
    print("Set EXERCISES_JSON_PATH in .env to override.")
    sys.exit(1)

print(f"Loading {DATA_PATH} …")
exercises: list[dict] = json.loads(DATA_PATH.read_text(encoding="utf-8"))

init_db()
conn = open_db()

sql = """
    INSERT OR REPLACE INTO exercises
      (id, name, category, body_part, equipment,
       instructions_en, instructions_es, instructions_it, instructions_tr,
       muscle_group, secondary_muscles, target, image, gif_url, created_at)
    VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
"""

rows = []
for ex in exercises:
    instr = ex.get("instructions") or {}
    rows.append((
        ex["id"],
        ex["name"],
        ex.get("category") or ex.get("body_part"),
        ex.get("body_part"),
        ex.get("equipment"),
        instr.get("en"),
        instr.get("es"),
        instr.get("it"),
        instr.get("tr"),
        ex.get("muscle_group"),
        json.dumps(ex.get("secondary_muscles") or []),
        ex.get("target"),
        ex.get("image"),
        ex.get("gif_url"),
        ex.get("created_at"),
    ))

conn.executemany(sql, rows)
conn.commit()
conn.close()
print(f"Done — {len(rows)} exercises seeded into {os.getenv('DB_PATH', 'exercises.db')}")
