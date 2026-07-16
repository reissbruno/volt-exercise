"""
Batch script: generate LLM insights for all exercises.

Reads:  data/exercises.json
Writes: data/enrichments.json  (keyed by exercise id, safe to interrupt)

Usage:
    uv run python scripts/enrich_exercises.py
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

DATA_PATH        = ROOT / "data" / "exercises.json"
ENRICHMENTS_PATH = ROOT / "data" / "enrichments.json"

BATCH_SIZE  = 10
MAX_RETRIES = 3
MODELS      = ["gpt-5.4-mini", "gpt-5.4-nano", "gpt-4.1-mini", "gpt-4o-mini"]
_model_idx  = 0


def current_model() -> str:
    return MODELS[_model_idx]


def rotate_model() -> bool:
    global _model_idx
    _model_idx += 1
    if _model_idx >= len(MODELS):
        return False
    print(f"[enrich] limite atingido — trocando para {MODELS[_model_idx]}")
    return True


SYSTEM_PROMPT = """\
You are an exercise science expert and Brazilian Portuguese (PT-BR) translator.
For each exercise given, return a JSON object with a single key "results" containing the array.
Each item must have exactly these keys:
  id                   - same id from input
  difficulty           - "beginner" | "intermediate" | "advanced"
  effort_type          - "strength" | "cardio" | "flexibility" | "balance"
  calories_per_min_met - float MET value (e.g. 3.8 for moderate effort)
  common_mistakes      - array of 2-4 short strings in English
  common_mistakes_pt   - same array translated to PT-BR
  benefits             - array of 2-4 short strings in English
  benefits_pt          - same array translated to PT-BR
  injury_risk          - "low" | "medium" | "high"
  injury_risk_area     - short string in English (e.g. "lower back") or null
  injury_risk_area_pt  - PT-BR translation or null
  easier_variation     - short string in English describing a simpler version
  easier_variation_pt  - PT-BR translation
  harder_variation     - short string in English describing a harder progression
  harder_variation_pt  - PT-BR translation
  no_equipment_alt     - bodyweight substitute in English, or null if already bodyweight
  no_equipment_alt_pt  - PT-BR translation or null

Return {"results": [{...}, {...}]}"""


def load_enrichments() -> dict:
    if ENRICHMENTS_PATH.exists():
        return json.loads(ENRICHMENTS_PATH.read_text(encoding="utf-8"))
    return {}


def save_enrichments(data: dict) -> None:
    ENRICHMENTS_PATH.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")


def build_prompt(exercises: list[dict]) -> str:
    slim = []
    for ex in exercises:
        instr = ex.get("instructions") or {}
        sec = ex.get("secondary_muscles") or []
        slim.append({
            "id":                ex["id"],
            "name":              ex["name"],
            "body_part":         ex.get("body_part") or ex.get("category"),
            "equipment":         ex.get("equipment"),
            "target":            ex.get("target"),
            "muscle_group":      ex.get("muscle_group"),
            "secondary_muscles": sec if isinstance(sec, list) else [],
            "instructions":      (instr.get("en") or "")[:300],
        })
    return f"Exercises:\n{json.dumps(slim, ensure_ascii=False)}"


def _is_daily_limit(msg: str) -> bool:
    return "requests per day" in msg or ("429" in msg and "RPD" in msg)


def call_llm(client: OpenAI, exercises: list[dict]) -> list[dict]:
    prompt = build_prompt(exercises)
    for attempt in range(MAX_RETRIES):
        try:
            response = client.chat.completions.create(
                model=current_model(),
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
        except Exception as e:
            msg = str(e)
            if _is_daily_limit(msg):
                raise  # propagate immediately — main() will rotate model
            wait = 2 ** attempt
            print(f"    tentativa {attempt + 1} falhou: {e}. aguardando {wait}s...")
            time.sleep(wait)
    raise RuntimeError(f"lote falhou após {MAX_RETRIES} tentativas")


def insight_to_dict(insight: dict) -> dict:
    return {
        "difficulty":           insight.get("difficulty"),
        "effort_type":          insight.get("effort_type"),
        "calories_per_min_met": insight.get("calories_per_min_met"),
        "common_mistakes":      insight.get("common_mistakes") or [],
        "common_mistakes_pt":   insight.get("common_mistakes_pt") or [],
        "benefits":             insight.get("benefits") or [],
        "benefits_pt":          insight.get("benefits_pt") or [],
        "injury_risk":          insight.get("injury_risk"),
        "injury_risk_area":     insight.get("injury_risk_area"),
        "injury_risk_area_pt":  insight.get("injury_risk_area_pt"),
        "easier_variation":     insight.get("easier_variation"),
        "easier_variation_pt":  insight.get("easier_variation_pt"),
        "harder_variation":     insight.get("harder_variation"),
        "harder_variation_pt":  insight.get("harder_variation_pt"),
        "no_equipment_alt":     insight.get("no_equipment_alt"),
        "no_equipment_alt_pt":  insight.get("no_equipment_alt_pt"),
    }


def main() -> None:
    api_key = os.getenv("OPENAI_API_KEY")
    if not api_key or api_key.startswith("sk-..."):
        print("ERROR: OPENAI_API_KEY não configurado no .env")
        sys.exit(1)

    if not DATA_PATH.exists():
        print(f"ERROR: {DATA_PATH} não encontrado")
        sys.exit(1)

    client = OpenAI(api_key=api_key)

    exercises: list[dict] = json.loads(DATA_PATH.read_text(encoding="utf-8"))
    enrichments = load_enrichments()
    done_ids = set(enrichments.keys())

    to_process = [ex for ex in exercises if ex["id"] not in done_ids]
    total = len(exercises)

    print(f"[enrich] {len(done_ids)}/{total} já processados, {len(to_process)} restantes")
    if to_process:
        print(f"[enrich] custo estimado: ~US$ {len(to_process) * 0.00015:.3f}")

    if not to_process:
        print("[enrich] nada a fazer — tudo já enriquecido!")
        return

    processed = 0
    errors = 0
    batches = [to_process[i : i + BATCH_SIZE] for i in range(0, len(to_process), BATCH_SIZE)]

    for i, batch in enumerate(batches):
        ids = [ex["id"] for ex in batch]
        print(f"[enrich] lote {i + 1}/{len(batches)} ({ids[0]}..{ids[-1]}) ", end="", flush=True)
        while True:
            try:
                results = call_llm(client, batch)
                for insight in results:
                    enrichments[insight["id"]] = insight_to_dict(insight)
                save_enrichments(enrichments)
                processed += len(results)
                pct = (len(done_ids) + processed) * 100 // total
                print(f"OK  ({len(done_ids) + processed}/{total}, {pct}%)")
                errors = 0
                time.sleep(0.3)
                break
            except Exception as exc:
                msg = str(exc)
                save_enrichments(enrichments)
                if _is_daily_limit(msg):
                    if not rotate_model():
                        print(f"\n[enrich] todos os modelos esgotaram o limite diário — progresso salvo ({len(done_ids) + processed}/{total})")
                        print(f"[enrich] rode novamente amanhã: uv run python scripts/enrich_exercises.py")
                        return
                    print(f"", flush=True)
                    print(f"[enrich] lote {i + 1}/{len(batches)} ({ids[0]}..{ids[-1]}) ", end="", flush=True)
                    continue  # retry same batch with new model
                if "429" in msg or "rate_limit" in msg.lower():
                    print(f"\n[enrich] rate limit transitório — aguardando 60s …")
                    time.sleep(60)
                    continue  # retry same batch
                errors += 1
                print(f"FALHA: {exc}")
                if errors >= 3:
                    print("[enrich] muitos erros consecutivos — abortando")
                    return
                time.sleep(2 ** errors)
                break

    total_done = len(done_ids) + processed
    print(f"\n[enrich] concluído — {total_done}/{total} exercícios em {ENRICHMENTS_PATH.name}")
    if total_done >= total:
        print("[enrich] commite e faça push:")
        print("  git add data/enrichments.json && git commit -m 'feat: enrichments' && git push")


if __name__ == "__main__":
    main()
