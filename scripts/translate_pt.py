#!/usr/bin/env python3
"""Translate exercise fields to Brazilian Portuguese using OpenAI GPT-4o mini.

Reads:  data/exercises.json
Writes: data/translations_pt.json  (keyed by exercise id, safe to interrupt)

Usage:
    OPENAI_API_KEY=sk-... uv run python scripts/translate_pt.py
    # or with .env:
    uv run python scripts/translate_pt.py
"""
from __future__ import annotations
import json
import os
import sys
import time
from pathlib import Path

ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(ROOT))

from dotenv import load_dotenv
load_dotenv(ROOT / ".env")

try:
    from openai import OpenAI
except ImportError:
    print("ERROR: openai not installed. Run: uv add openai")
    sys.exit(1)

DATA_PATH   = ROOT / "data" / "exercises.json"
OUTPUT_PATH = ROOT / "data" / "translations_pt.json"

BATCH_SIZE = 15
MODELS     = ["gpt-5.4-mini", "gpt-5.4-nano", "gpt-4.1-mini", "gpt-4o-mini"]
_model_idx = 0


def current_model() -> str:
    return MODELS[_model_idx]


def rotate_model() -> bool:
    global _model_idx
    _model_idx += 1
    if _model_idx >= len(MODELS):
        return False
    print(f"[translate] limite atingido — trocando para {MODELS[_model_idx]}")
    return True

api_key = os.getenv("OPENAI_API_KEY")
if not api_key:
    print("ERROR: OPENAI_API_KEY not set in environment or .env")
    sys.exit(1)

client = OpenAI(api_key=api_key)


def load_output() -> dict:
    if OUTPUT_PATH.exists():
        return json.loads(OUTPUT_PATH.read_text(encoding="utf-8"))
    return {}


def save_output(data: dict) -> None:
    OUTPUT_PATH.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")


def translate_batch(exercises: list[dict]) -> dict[str, dict]:
    items = []
    for ex in exercises:
        sec = ex.get("secondary_muscles") or []
        if isinstance(sec, str):
            try:
                sec = json.loads(sec)
            except Exception:
                sec = []
        items.append({
            "id":                ex["id"],
            "name":              ex["name"],
            "instructions":      (ex.get("instructions") or {}).get("en") or "",
            "target":            ex.get("target") or "",
            "secondary_muscles": sec if isinstance(sec, list) else [],
        })

    prompt = (
        'You are a fitness translator for Brazilian Portuguese (PT-BR).\n'
        'Translate the exercise data below. Return JSON: {"exercises": [...]}\n\n'
        'Each output item must have:\n'
        '- "id": unchanged\n'
        '- "name_pt": Brazilian gym name (e.g. "Barbell Bench Press"→"Supino Reto com Barra", '
        '"Push Up"→"Flexão de Braço", "Squat"→"Agachamento", "Deadlift"→"Levantamento Terra")\n'
        '- "instructions_pt": full PT-BR translation (empty string if original empty)\n'
        '- "target_pt": muscle in PT-BR (e.g. "pectorals"→"peitoral", "quadriceps"→"quadríceps", '
        '"lats"→"latíssimo do dorso", "abs"→"abdominais", "glutes"→"glúteos", '
        '"hamstrings"→"isquiotibiais", "deltoids"→"deltoides")\n'
        '- "secondary_muscles_pt": array of translated secondary muscle names\n\n'
        'Use standard Brazilian fitness/anatomy terminology. Return only valid JSON.\n\n'
        f'Input:\n{json.dumps(items, ensure_ascii=False)}'
    )

    resp = client.chat.completions.create(
        model=current_model(),
        messages=[{"role": "user", "content": prompt}],
        temperature=0.1,
        response_format={"type": "json_object"},
    )

    raw = json.loads(resp.choices[0].message.content)
    items_out = raw.get("exercises", [])

    return {
        item["id"]: {
            "name_pt":              item.get("name_pt") or "",
            "instructions_pt":      item.get("instructions_pt") or "",
            "target_pt":            item.get("target_pt") or "",
            "secondary_muscles_pt": item.get("secondary_muscles_pt") or [],
        }
        for item in items_out
        if "id" in item
    }


def main() -> None:
    if not DATA_PATH.exists():
        print(f"ERROR: {DATA_PATH} not found")
        sys.exit(1)

    exercises: list[dict] = json.loads(DATA_PATH.read_text(encoding="utf-8"))
    output = load_output()
    done_ids = set(output.keys())

    to_process = [ex for ex in exercises if ex["id"] not in done_ids]
    total = len(exercises)

    print(f"[translate] {len(done_ids)}/{total} já traduzidos, {len(to_process)} restantes")
    if to_process:
        cost_est = len(to_process) * 0.000045
        print(f"[translate] custo estimado: ~US$ {cost_est:.3f}")

    if not to_process:
        print("[translate] nada a fazer — tudo já traduzido!")
        return

    processed = 0
    errors = 0
    for i in range(0, len(to_process), BATCH_SIZE):
        batch = to_process[i : i + BATCH_SIZE]
        while True:
            try:
                results = translate_batch(batch)
                output.update(results)
                save_output(output)
                processed += len(batch)
                pct = (len(done_ids) + processed) * 100 // total
                print(f"[translate] {len(done_ids) + processed}/{total} ({pct}%)")
                errors = 0
                time.sleep(0.3)
                break
            except Exception as exc:
                msg = str(exc)
                save_output(output)
                if "requests per day" in msg or ("429" in msg and "RPD" in msg):
                    if not rotate_model():
                        print(f"\n[translate] todos os modelos esgotaram o limite diário — progresso salvo ({len(done_ids) + processed}/{total})")
                        print(f"[translate] rode novamente amanhã: uv run python scripts/translate_pt.py")
                        return
                    continue  # retry same batch with new model
                if "429" in msg or "rate_limit" in msg.lower():
                    print(f"[translate] rate limit transitório — aguardando 60s …")
                    time.sleep(60)
                    continue  # retry same batch
                errors += 1
                print(f"[translate] erro no lote {i // BATCH_SIZE + 1}: {exc}")
                if errors >= 3:
                    print("[translate] muitos erros consecutivos — abortando")
                    return
                time.sleep(2 ** errors)
                break

    total_done = len(done_ids) + processed
    print(f"\n[translate] {total_done}/{total} exercícios em {OUTPUT_PATH.name}")
    if total_done < total:
        print(f"[translate] {total - total_done} restantes — rode novamente para continuar")


if __name__ == "__main__":
    main()
