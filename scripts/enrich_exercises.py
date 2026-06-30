"""
One-time batch script: generate LLM insights for all exercises.

Usage:
    uv run python scripts/enrich_exercises.py

Checkpoint: scripts/enrich_checkpoint.json — resumes from where it stopped.
Cost estimate: < US$0.20 for all 1,324 exercises (gpt-4o-mini).
"""
from __future__ import annotations

import json
import os
import sqlite3
import sys
import time
from pathlib import Path

from dotenv import load_dotenv
from openai import OpenAI

load_dotenv()

# ── Config ────────────────────────────────────────────────────────────────────
DB_PATH     = os.getenv("DB_PATH", "data/exercises.db")
CHECKPOINT  = Path("scripts/enrich_checkpoint.json")
BATCH_SIZE  = 10
MAX_RETRIES = 3
MODEL       = "gpt-5.4-mini"

SYSTEM_PROMPT = """\
You are an exercise science expert. For each exercise given, return a JSON object.
Each item must correspond to one exercise (matched by "id") and contain exactly these keys:
  id                   - same id from input
  difficulty           - "beginner" | "intermediate" | "advanced"
  effort_type          - "strength" | "cardio" | "flexibility" | "balance"
  calories_per_min_met - float, MET value (e.g. 3.8 for moderate effort)
  common_mistakes      - array of 2-4 short strings in English
  benefits             - array of 2-4 short strings in English
  injury_risk          - "low" | "medium" | "high"
  injury_risk_area     - short string in English (e.g. "lower back", "knees") or null
  easier_variation     - short string in English describing a simpler version
  harder_variation     - short string in English describing a harder progression
  no_equipment_alt     - short string in English, a bodyweight substitute, or null if already bodyweight

Return a JSON object with a single key "results" containing the array.
Example: {"results": [{...}, {...}]}"""


def load_checkpoint() -> set[str]:
    if CHECKPOINT.exists():
        return set(json.loads(CHECKPOINT.read_text()))
    return set()


def save_checkpoint(done: set[str]) -> None:
    CHECKPOINT.write_text(json.dumps(sorted(done)))


def build_prompt(exercises: list[dict]) -> str:
    slim = []
    for ex in exercises:
        slim.append({
            "id": ex["id"],
            "name": ex["name"],
            "body_part": ex["body_part"],
            "equipment": ex["equipment"],
            "target": ex["target"],
            "muscle_group": ex["muscle_group"],
            "secondary_muscles": ex["secondary_muscles"],
            "instructions": (ex["instructions_en"] or "")[:300],
        })
    return f"Exercises:\n{json.dumps(slim, ensure_ascii=False)}"


def call_llm(client: OpenAI, exercises: list[dict]) -> list[dict]:
    prompt = build_prompt(exercises)
    for attempt in range(MAX_RETRIES):
        try:
            response = client.chat.completions.create(
                model=MODEL,
                messages=[
                    {"role": "system", "content": SYSTEM_PROMPT},
                    {"role": "user", "content": prompt},
                ],
                response_format={"type": "json_object"},
                temperature=0.2,
            )
            raw = response.choices[0].message.content.strip()
            parsed = json.loads(raw)
            if isinstance(parsed, dict):
                parsed = parsed.get("results") or next(iter(parsed.values()))
            return parsed
        except (json.JSONDecodeError, Exception) as e:
            wait = 2 ** attempt
            print(f"    Attempt {attempt + 1} failed: {e}. Retrying in {wait}s...")
            time.sleep(wait)
    raise RuntimeError(f"Batch failed after {MAX_RETRIES} attempts")


def upsert_insight(conn: sqlite3.Connection, insight: dict) -> None:
    conn.execute(
        """
        INSERT OR REPLACE INTO exercise_insights (
            exercise_id, difficulty, effort_type, calories_per_min_met,
            common_mistakes, benefits, injury_risk, injury_risk_area,
            easier_variation, harder_variation, no_equipment_alt, generated_at
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, datetime('now'))
        """,
        (
            insight["id"],
            insight.get("difficulty"),
            insight.get("effort_type"),
            insight.get("calories_per_min_met"),
            json.dumps(insight.get("common_mistakes") or [], ensure_ascii=False),
            json.dumps(insight.get("benefits") or [], ensure_ascii=False),
            insight.get("injury_risk"),
            insight.get("injury_risk_area"),
            insight.get("easier_variation"),
            insight.get("harder_variation"),
            insight.get("no_equipment_alt"),
        ),
    )


def main() -> None:
    api_key = os.getenv("OPENAI_API_KEY")
    if not api_key or api_key.startswith("sk-..."):
        print("ERROR: OPENAI_API_KEY não configurado no .env")
        sys.exit(1)

    client = OpenAI(api_key=api_key)
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row

    all_exercises = conn.execute(
        "SELECT id, name, body_part, equipment, target, muscle_group, "
        "secondary_muscles, instructions_en FROM exercises ORDER BY id"
    ).fetchall()

    total = len(all_exercises)
    done = load_checkpoint()
    pending = [dict(ex) for ex in all_exercises if ex["id"] not in done]

    print(f"Total: {total} | Já processados: {len(done)} | Pendentes: {len(pending)}")
    if not pending:
        print("Nada a fazer — todos os exercícios já foram enriquecidos.")
        conn.close()
        return

    batches = [pending[i : i + BATCH_SIZE] for i in range(0, len(pending), BATCH_SIZE)]
    processed = len(done)

    for i, batch in enumerate(batches):
        ids = [ex["id"] for ex in batch]
        print(f"Lote {i + 1}/{len(batches)} — exercícios {ids[0]}..{ids[-1]} ", end="", flush=True)
        try:
            results = call_llm(client, batch)
            for insight in results:
                upsert_insight(conn, insight)
                done.add(insight["id"])
            conn.commit()
            save_checkpoint(done)
            processed += len(results)
            print(f"OK  ({processed}/{total} total)")
        except Exception as e:
            print(f"FALHA: {e}")
            print("Checkpoint salvo. Execute novamente para retomar.")
            conn.close()
            sys.exit(1)

        # Brief pause to respect rate limits
        if i < len(batches) - 1:
            time.sleep(0.3)

    conn.close()
    print(f"\nConcluido! {processed} exercicios enriquecidos.")
    print("Execute o servidor para usar os novos endpoints.")


if __name__ == "__main__":
    main()
