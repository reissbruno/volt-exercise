"""Fitness Exercises REST API — FastAPI + SQLite"""
from __future__ import annotations

import hashlib
import json
import os
import sqlite3
import threading
import time
from contextlib import asynccontextmanager
from typing import Annotated, Optional

from dotenv import load_dotenv
from fastapi import Depends, FastAPI, HTTPException, Query, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, RedirectResponse
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from fastapi.staticfiles import StaticFiles
from jose import JWTError, jwt
import bcrypt as _bcrypt
from pydantic import BaseModel

load_dotenv()

# ── Config ────────────────────────────────────────────────────────────────────
DB_PATH       = os.getenv("DB_PATH", "data/exercises.db")
JWT_SECRET    = os.getenv("JWT_SECRET", "change-this-secret-in-production")
JWT_ALGORITHM = "HS256"
JWT_EXPIRES   = 7  # days
ALLOWED_ORIGINS = os.getenv("ALLOWED_ORIGINS", "*")

FOCUS_BODY_PARTS: dict[str, list[str]] = {
    "upper":  ["chest", "back", "shoulders", "upper arms", "lower arms"],
    "lower":  ["upper legs", "lower legs"],
    "core":   ["waist"],
    "cardio": ["cardio"],
    "push":   ["chest", "shoulders"],
    "pull":   ["back", "upper arms"],
    "legs":   ["upper legs", "lower legs"],
    "full":   [],
    "custom": [],
}
VALID_FOCUS = set(FOCUS_BODY_PARTS)
DAYS = ["Sunday", "Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday"]

# ── Argos Translate ───────────────────────────────────────────────────────────
_argos_ready = False
_argos_error: str | None = None
_preload_progress: dict = {"total": 0, "done": 0, "running": False}


def _install_argos() -> None:
    global _argos_ready, _argos_error
    try:
        from argostranslate import package as _pkg, translate as _tr  # noqa: F401
        _pkg.update_package_index()
        installed = {(p.from_code, p.to_code) for p in _pkg.get_installed_packages()}
        needed = [("en", "pt")]
        for from_c, to_c in needed:
            if (from_c, to_c) not in installed:
                available = _pkg.get_available_packages()
                pkg = next((p for p in available if p.from_code == from_c and p.to_code == to_c), None)
                if pkg:
                    print(f"[argos] baixando pacote {from_c}→{to_c}…")
                    _pkg.install_from_path(pkg.download())
                    print(f"[argos] pacote {from_c}→{to_c} instalado")
        _argos_ready = True
        print("[argos] pronto")
        _preload_translations()
    except Exception as exc:
        _argos_error = str(exc)
        print(f"[argos] erro na inicialização: {exc}")


_PRELOAD_SENTINEL = "__preload_done__"


def _preload_translations() -> None:
    global _preload_progress
    try:
        conn = open_db()

        total = conn.execute(
            "SELECT COUNT(*) FROM exercises "
            "WHERE instructions_en IS NOT NULL AND instructions_en != ''"
        ).fetchone()[0]

        # Skip entirely if a previous run already completed
        already_done = conn.execute(
            "SELECT 1 FROM translations "
            "WHERE source_lang='__meta__' AND source_hash=?",
            (_PRELOAD_SENTINEL,),
        ).fetchone()

        if already_done:
            _preload_progress = {"total": total, "done": total, "running": False}
            print(f"[argos] pré-tradução já concluída ({total} em cache), pulando")
            conn.close()
            return

        rows = conn.execute(
            "SELECT instructions_en FROM exercises "
            "WHERE instructions_en IS NOT NULL AND instructions_en != ''"
        ).fetchall()

        _preload_progress = {"total": total, "done": 0, "running": True}
        print(f"[argos] pré-tradução iniciada: {total} exercícios")

        for row in rows:
            text = row["instructions_en"]
            h = _text_hash(f"en:pt:{text}")

            exists = conn.execute(
                "SELECT 1 FROM translations "
                "WHERE source_lang='en' AND target_lang='pt' AND source_hash=?",
                (h,),
            ).fetchone()

            if not exists:
                translated = _do_translate(text, "en", "pt")
                conn.execute(
                    "INSERT OR IGNORE INTO translations"
                    "(source_lang, target_lang, source_hash, translated_text) VALUES(?,?,?,?)",
                    ("en", "pt", h, translated),
                )
                conn.commit()

            _preload_progress["done"] += 1
            if _preload_progress["done"] % 100 == 0:
                pct = _preload_progress["done"] * 100 // total
                print(f"[argos] {_preload_progress['done']}/{total} ({pct}%) traduzidos")

        # Mark as done so subsequent startups skip this loop
        conn.execute(
            "INSERT OR IGNORE INTO translations"
            "(source_lang, target_lang, source_hash, translated_text) VALUES(?,?,?,?)",
            ("__meta__", "__meta__", _PRELOAD_SENTINEL, "1"),
        )
        conn.commit()

        _preload_progress["running"] = False
        print(f"[argos] pré-tradução concluída: {total} exercícios em cache")
        conn.close()
    except Exception as exc:
        _preload_progress["running"] = False
        print(f"[argos] erro na pré-tradução: {exc}")


def _do_translate(text: str, source: str, target: str) -> str:
    from argostranslate import translate as _tr
    return _tr.translate(text, source, target)


def _text_hash(text: str) -> str:
    return hashlib.sha256(text.encode()).hexdigest()


def _translate_cached(text: str, source: str, target: str, db: sqlite3.Connection) -> str:
    """Translate with cache. Returns original text if Argos not ready or text is empty."""
    if not text or not text.strip():
        return text
    h = _text_hash(f"{source}:{target}:{text}")
    row = db.execute(
        "SELECT translated_text FROM translations WHERE source_lang=? AND target_lang=? AND source_hash=?",
        (source, target, h),
    ).fetchone()
    if row:
        return row["translated_text"]
    if not _argos_ready:
        return text
    translated = _do_translate(text, source, target)
    db.execute(
        "INSERT OR IGNORE INTO translations(source_lang, target_lang, source_hash, translated_text) VALUES(?,?,?,?)",
        (source, target, h, translated),
    )
    db.commit()
    return translated


# ── Password & JWT ────────────────────────────────────────────────────────────
def hash_password(plain: str) -> str:
    return _bcrypt.hashpw(plain.encode(), _bcrypt.gensalt(12)).decode()


def verify_password(plain: str, hashed: str) -> bool:
    return _bcrypt.checkpw(plain.encode(), hashed.encode())


def create_token(payload: dict) -> str:
    from datetime import datetime, timedelta, timezone
    data = {**payload, "exp": datetime.now(timezone.utc) + timedelta(days=JWT_EXPIRES)}
    return jwt.encode(data, JWT_SECRET, algorithm=JWT_ALGORITHM)


# ── Database ──────────────────────────────────────────────────────────────────
def open_db() -> sqlite3.Connection:
    conn = sqlite3.connect(DB_PATH, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA foreign_keys=ON")
    return conn


def get_db():
    conn = open_db()
    try:
        yield conn
    finally:
        conn.close()


DB = Annotated[sqlite3.Connection, Depends(get_db)]


def init_db() -> None:
    conn = open_db()
    conn.executescript("""
        CREATE TABLE IF NOT EXISTS users (
            id            INTEGER PRIMARY KEY AUTOINCREMENT,
            name          TEXT    NOT NULL,
            email         TEXT    UNIQUE NOT NULL,
            password_hash TEXT    NOT NULL,
            weight_kg     REAL,
            height_cm     REAL,
            age           INTEGER,
            sex           TEXT,
            created_at    TEXT    DEFAULT (datetime('now'))
        );

        CREATE TABLE IF NOT EXISTS exercises (
            id                   TEXT PRIMARY KEY,
            name                 TEXT NOT NULL,
            name_pt              TEXT,
            category             TEXT,
            body_part            TEXT,
            equipment            TEXT,
            instructions_en      TEXT,
            instructions_es      TEXT,
            instructions_it      TEXT,
            instructions_tr      TEXT,
            instructions_pt      TEXT,
            muscle_group         TEXT,
            secondary_muscles    TEXT,
            target               TEXT,
            target_pt            TEXT,
            secondary_muscles_pt TEXT,
            image                TEXT,
            gif_url              TEXT,
            created_at           TEXT
        );

        CREATE TABLE IF NOT EXISTS workout_plans (
            id         INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id    INTEGER NOT NULL,
            name       TEXT    NOT NULL,
            focus      TEXT    NOT NULL DEFAULT 'custom',
            created_at TEXT    DEFAULT (datetime('now')),
            FOREIGN KEY (user_id) REFERENCES users(id) ON DELETE CASCADE
        );

        CREATE TABLE IF NOT EXISTS schedule_entries (
            id           INTEGER PRIMARY KEY AUTOINCREMENT,
            plan_id      INTEGER NOT NULL,
            day_of_week  INTEGER NOT NULL CHECK (day_of_week BETWEEN 0 AND 6),
            exercise_id  TEXT    NOT NULL,
            sets         INTEGER NOT NULL DEFAULT 3,
            reps         INTEGER NOT NULL DEFAULT 10,
            rest_seconds INTEGER NOT NULL DEFAULT 60,
            position     INTEGER NOT NULL DEFAULT 0,
            FOREIGN KEY (plan_id)     REFERENCES workout_plans(id) ON DELETE CASCADE,
            FOREIGN KEY (exercise_id) REFERENCES exercises(id)
        );

        CREATE TABLE IF NOT EXISTS translations (
            source_lang     TEXT NOT NULL,
            target_lang     TEXT NOT NULL,
            source_hash     TEXT NOT NULL,
            translated_text TEXT NOT NULL,
            PRIMARY KEY (source_lang, target_lang, source_hash)
        );

        CREATE TABLE IF NOT EXISTS exercise_insights (
            exercise_id            TEXT PRIMARY KEY,
            difficulty             TEXT,
            effort_type            TEXT,
            calories_per_min_met   REAL,
            common_mistakes        TEXT,
            common_mistakes_pt     TEXT,
            benefits               TEXT,
            benefits_pt            TEXT,
            injury_risk            TEXT,
            injury_risk_area       TEXT,
            injury_risk_area_pt    TEXT,
            easier_variation       TEXT,
            easier_variation_pt    TEXT,
            harder_variation       TEXT,
            harder_variation_pt    TEXT,
            no_equipment_alt       TEXT,
            no_equipment_alt_pt    TEXT,
            generated_at           TEXT,
            FOREIGN KEY (exercise_id) REFERENCES exercises(id)
        );
    """)
    conn.commit()

    # Migrate: add physical profile columns if missing
    for col, typedef in [("weight_kg","REAL"),("height_cm","REAL"),("age","INTEGER"),("sex","TEXT")]:
        try:
            conn.execute(f"ALTER TABLE users ADD COLUMN {col} {typedef}")
        except Exception:
            pass

    # Migrate: add PT translation columns if missing
    for col in ("name_pt", "instructions_pt", "target_pt", "secondary_muscles_pt"):
        try:
            conn.execute(f"ALTER TABLE exercises ADD COLUMN {col} TEXT")
        except Exception:
            pass

    # Migrate: add PT insight columns if missing
    for col in ("common_mistakes_pt", "benefits_pt", "injury_risk_area_pt",
                "easier_variation_pt", "harder_variation_pt", "no_equipment_alt_pt"):
        try:
            conn.execute(f"ALTER TABLE exercise_insights ADD COLUMN {col} TEXT")
        except Exception:
            pass

    conn.commit()
    conn.close()


def seed_if_empty() -> None:
    """Import exercises.json into SQLite if the exercises table is empty."""
    json_path = os.getenv("EXERCISES_JSON_PATH", "data/exercises.json")
    if not os.path.exists(json_path):
        print(f"[seed] exercises.json not found at {json_path} — skipping")
        return
    conn = open_db()
    count = conn.execute("SELECT COUNT(*) FROM exercises").fetchone()[0]
    if count > 0:
        conn.close()
        return
    print(f"[seed] exercises table empty — importing from {json_path} …")
    exercises: list[dict] = json.loads(open(json_path, encoding="utf-8").read())
    rows = []
    for ex in exercises:
        instr = ex.get("instructions") or {}
        rows.append((
            ex["id"], ex["name"],
            ex.get("category") or ex.get("body_part"),
            ex.get("body_part"), ex.get("equipment"),
            instr.get("en"), instr.get("es"), instr.get("it"), instr.get("tr"),
            ex.get("muscle_group"),
            json.dumps(ex.get("secondary_muscles") or []),
            ex.get("target"), ex.get("image"), ex.get("gif_url"), ex.get("created_at"),
        ))
    conn.executemany("""
        INSERT OR REPLACE INTO exercises
          (id, name, category, body_part, equipment,
           instructions_en, instructions_es, instructions_it, instructions_tr,
           muscle_group, secondary_muscles, target, image, gif_url, created_at)
        VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
    """, rows)
    conn.commit()
    conn.close()
    print(f"[seed] {len(rows)} exercises imported")


def _import_enrichments() -> None:
    """Load data/enrichments.json into exercise_insights (once)."""
    json_path = os.getenv("ENRICHMENTS_PATH", "data/enrichments.json")
    if not os.path.exists(json_path):
        return
    conn = open_db()
    if conn.execute("SELECT 1 FROM exercise_insights LIMIT 1").fetchone():
        conn.close()
        return
    print(f"[seed] importando enrichments de {json_path} …")
    try:
        enrichments: dict = json.loads(open(json_path, encoding="utf-8").read())
        for ex_id, ins in enrichments.items():
            conn.execute(
                """INSERT OR REPLACE INTO exercise_insights
                   (exercise_id, difficulty, effort_type, calories_per_min_met,
                    common_mistakes, common_mistakes_pt,
                    benefits, benefits_pt,
                    injury_risk,
                    injury_risk_area, injury_risk_area_pt,
                    easier_variation, easier_variation_pt,
                    harder_variation, harder_variation_pt,
                    no_equipment_alt, no_equipment_alt_pt,
                    generated_at)
                   VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,datetime('now'))""",
                (
                    ex_id,
                    ins.get("difficulty"),
                    ins.get("effort_type"),
                    ins.get("calories_per_min_met"),
                    json.dumps(ins.get("common_mistakes") or [], ensure_ascii=False),
                    json.dumps(ins.get("common_mistakes_pt") or [], ensure_ascii=False),
                    json.dumps(ins.get("benefits") or [], ensure_ascii=False),
                    json.dumps(ins.get("benefits_pt") or [], ensure_ascii=False),
                    ins.get("injury_risk"),
                    ins.get("injury_risk_area"),
                    ins.get("injury_risk_area_pt"),
                    ins.get("easier_variation"),
                    ins.get("easier_variation_pt"),
                    ins.get("harder_variation"),
                    ins.get("harder_variation_pt"),
                    ins.get("no_equipment_alt"),
                    ins.get("no_equipment_alt_pt"),
                ),
            )
        conn.commit()
        print(f"[seed] {len(enrichments)} enrichments importados")
    except Exception as exc:
        print(f"[seed] erro ao importar enrichments: {exc}")
    finally:
        conn.close()


def _import_translations_pt() -> None:
    """Load data/translations_pt.json into the exercises table (once)."""
    json_path = os.getenv("TRANSLATIONS_PT_PATH", "data/translations_pt.json")
    if not os.path.exists(json_path):
        return
    conn = open_db()
    # Skip if already imported
    if conn.execute("SELECT name_pt FROM exercises WHERE name_pt IS NOT NULL LIMIT 1").fetchone():
        conn.close()
        return
    print(f"[seed] importando traduções PT de {json_path} …")
    try:
        translations: dict = json.loads(open(json_path, encoding="utf-8").read())
        for ex_id, t in translations.items():
            sec_pt = t.get("secondary_muscles_pt") or []
            conn.execute(
                "UPDATE exercises SET name_pt=?, instructions_pt=?, target_pt=?, secondary_muscles_pt=? WHERE id=?",
                (
                    t.get("name_pt") or None,
                    t.get("instructions_pt") or None,
                    t.get("target_pt") or None,
                    json.dumps(sec_pt, ensure_ascii=False),
                    ex_id,
                ),
            )
        conn.commit()
        print(f"[seed] {len(translations)} traduções PT importadas")
    except Exception as exc:
        print(f"[seed] erro ao importar traduções PT: {exc}")
    finally:
        conn.close()


# ── Helpers ───────────────────────────────────────────────────────────────────
def row_to_dict(row: sqlite3.Row) -> dict:
    d = dict(row)
    for field in ("secondary_muscles", "secondary_muscles_pt"):
        raw = d.get(field)
        if isinstance(raw, str):
            try:
                d[field] = json.loads(raw)
            except Exception:
                d[field] = []
    return d


# ── Auth dependency ───────────────────────────────────────────────────────────
_bearer = HTTPBearer()
_bearer_optional = HTTPBearer(auto_error=False)


def get_current_user(
    creds: Annotated[HTTPAuthorizationCredentials, Depends(_bearer)],
    db: Annotated[sqlite3.Connection, Depends(get_db)],
) -> dict:
    try:
        payload = jwt.decode(creds.credentials, JWT_SECRET, algorithms=[JWT_ALGORITHM])
    except JWTError:
        raise HTTPException(status_code=401, detail="Invalid or expired token")
    row = db.execute(
        "SELECT id, name, email, weight_kg, height_cm, age, sex FROM users WHERE id = ?",
        (int(payload["id"]),),
    ).fetchone()
    if not row:
        raise HTTPException(status_code=401, detail="Session expired — please log in again")
    return dict(row)


CurrentUser = Annotated[dict, Depends(get_current_user)]


# ── Pydantic schemas ──────────────────────────────────────────────────────────
class RegisterIn(BaseModel):
    name: str
    email: str
    password: str
    weight_kg: Optional[float] = None
    height_cm: Optional[float] = None
    age: Optional[int] = None
    sex: Optional[str] = None


class LoginIn(BaseModel):
    email: str
    password: str


class PlanIn(BaseModel):
    name: str
    focus: str = "custom"


class PlanUpdate(BaseModel):
    name: Optional[str] = None
    focus: Optional[str] = None


class ScheduleIn(BaseModel):
    day_of_week: int
    exercise_id: str
    sets: int = 3
    reps: int = 10
    rest_seconds: int = 60


class ScheduleUpdate(BaseModel):
    sets: Optional[int] = None
    reps: Optional[int] = None
    rest_seconds: Optional[int] = None
    position: Optional[int] = None


class TranslateIn(BaseModel):
    text: str
    source: str = "en"
    target: str = "pt"


class ProfileUpdate(BaseModel):
    name: Optional[str] = None
    current_password: Optional[str] = None
    new_password: Optional[str] = None
    weight_kg: Optional[float] = None
    height_cm: Optional[float] = None
    age: Optional[int] = None
    sex: Optional[str] = None


# ── App ───────────────────────────────────────────────────────────────────────
@asynccontextmanager
async def lifespan(app: FastAPI):
    init_db()
    seed_if_empty()
    _import_enrichments()
    _import_translations_pt()
    threading.Thread(target=_install_argos, daemon=True).start()
    yield


app = FastAPI(title="Exercises API", version="1.0.0", lifespan=lifespan)

origins = ["*"] if ALLOWED_ORIGINS.strip() == "*" else [o.strip() for o in ALLOWED_ORIGINS.split(",")]
app.add_middleware(
    CORSMiddleware,
    allow_origins=origins,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.middleware("http")
async def log_requests(request: Request, call_next):
    start = time.perf_counter()
    response = await call_next(request)
    ms = (time.perf_counter() - start) * 1000
    print(f"{request.method} {request.url.path} {response.status_code} {ms:.1f}ms")
    return response


# ═════════════════════════════════════════════════════════════════════════════
# ROOT
# ═════════════════════════════════════════════════════════════════════════════

@app.get("/", include_in_schema=False)
def root():
    return RedirectResponse(url="/app")


@app.get("/app", include_in_schema=False)
def frontend():
    return FileResponse("frontend/app.html")


@app.get("/logo-volt.png", include_in_schema=False)
def logo():
    return FileResponse("frontend/logo-volt.png", media_type="image/png")


# ═════════════════════════════════════════════════════════════════════════════
# AUTH
# ═════════════════════════════════════════════════════════════════════════════

@app.post("/auth/register", status_code=201)
def register(body: RegisterIn, db: DB):
    if not body.name.strip():
        raise HTTPException(400, "name is required")
    if len(body.password) < 8:
        raise HTTPException(400, "password must be at least 8 characters")

    email = body.email.lower().strip()
    if db.execute("SELECT id FROM users WHERE email = ?", (email,)).fetchone():
        raise HTTPException(409, "Email already registered")

    cur = db.execute(
        "INSERT INTO users (name, email, password_hash, weight_kg, height_cm, age, sex) VALUES (?,?,?,?,?,?,?)",
        (body.name.strip(), email, hash_password(body.password),
         body.weight_kg, body.height_cm, body.age, body.sex),
    )
    db.commit()
    user = {
        "id": cur.lastrowid, "name": body.name.strip(), "email": email,
        "weight_kg": body.weight_kg, "height_cm": body.height_cm,
        "age": body.age, "sex": body.sex,
    }
    return {"token": create_token(user), "user": user}


@app.post("/auth/login")
def login(body: LoginIn, db: DB):
    row = db.execute(
        "SELECT id, name, email, password_hash, weight_kg, height_cm, age, sex FROM users WHERE email = ?",
        (body.email.lower().strip(),),
    ).fetchone()

    if not row or not verify_password(body.password, row["password_hash"]):
        raise HTTPException(401, "Email ou senha incorretos")

    user = {
        "id": row["id"], "name": row["name"], "email": row["email"],
        "weight_kg": row["weight_kg"], "height_cm": row["height_cm"],
        "age": row["age"], "sex": row["sex"],
    }
    return {"token": create_token(user), "user": user}


# ═════════════════════════════════════════════════════════════════════════════
# USER PROFILE
# ═════════════════════════════════════════════════════════════════════════════

@app.get("/me")
def get_me(user: CurrentUser, db: DB):
    row = db.execute(
        "SELECT id, name, email, weight_kg, height_cm, age, sex FROM users WHERE id=?",
        (user["id"],),
    ).fetchone()
    if not row:
        raise HTTPException(404, "Usuário não encontrado")
    return dict(row)


@app.put("/me")
def update_me(body: ProfileUpdate, user: CurrentUser, db: DB):
    row = db.execute("SELECT * FROM users WHERE id=?", (user["id"],)).fetchone()
    if not row:
        raise HTTPException(404, "Usuário não encontrado")

    updates: dict = {}
    if body.name and body.name.strip():
        updates["name"] = body.name.strip()
    if body.weight_kg is not None:
        updates["weight_kg"] = body.weight_kg
    if body.height_cm is not None:
        updates["height_cm"] = body.height_cm
    if body.age is not None:
        updates["age"] = body.age
    if body.sex is not None:
        updates["sex"] = body.sex
    if body.new_password:
        if not body.current_password:
            raise HTTPException(400, "Senha atual obrigatória para trocar a senha")
        pw_bytes = body.current_password.encode()
        hash_bytes = row["password_hash"].encode() if isinstance(row["password_hash"], str) else row["password_hash"]
        if not _bcrypt.checkpw(pw_bytes, hash_bytes):
            raise HTTPException(400, "Senha atual incorreta")
        if len(body.new_password) < 8:
            raise HTTPException(400, "Nova senha deve ter pelo menos 8 caracteres")
        updates["password_hash"] = _bcrypt.hashpw(body.new_password.encode(), _bcrypt.gensalt()).decode()

    if updates:
        set_clause = ", ".join(f"{k}=?" for k in updates)
        db.execute(f"UPDATE users SET {set_clause} WHERE id=?", (*updates.values(), user["id"]))
        db.commit()

    updated = db.execute(
        "SELECT id, name, email, weight_kg, height_cm, age, sex FROM users WHERE id=?",
        (user["id"],),
    ).fetchone()
    return dict(updated)


# ═════════════════════════════════════════════════════════════════════════════
# EXERCISES  (static routes before parameterised /{id})
# ═════════════════════════════════════════════════════════════════════════════

@app.get("/exercises/random")
def random_exercise(db: DB):
    row = db.execute("SELECT * FROM exercises ORDER BY RANDOM() LIMIT 1").fetchone()
    if not row:
        raise HTTPException(404, "No exercises found")
    return row_to_dict(row)


@app.get("/exercises/suggestions")
def suggest_exercises(
    focus: str,
    db: DB,
    limit: int = Query(default=20, ge=1, le=100),
):
    if focus not in FOCUS_BODY_PARTS:
        raise HTTPException(400, f"focus must be one of: {', '.join(sorted(VALID_FOCUS))}")

    parts = FOCUS_BODY_PARTS[focus]
    if not parts:
        rows = db.execute(
            "SELECT * FROM exercises ORDER BY RANDOM() LIMIT ?", (limit,)
        ).fetchall()
    else:
        placeholders = ",".join("?" * len(parts))
        rows = db.execute(
            f"SELECT * FROM exercises WHERE body_part IN ({placeholders}) ORDER BY RANDOM() LIMIT ?",
            (*parts, limit),
        ).fetchall()

    return [row_to_dict(r) for r in rows]


@app.get("/exercises")
def list_exercises(
    db: DB,
    page: int = Query(default=1, ge=1),
    limit: int = Query(default=20, ge=1, le=100),
    name: Optional[str] = None,
    category: Optional[str] = None,
    body_part: Optional[str] = None,
    equipment: Optional[str] = None,
    muscle_group: Optional[str] = None,
    target: Optional[str] = None,
):
    conditions, params = [], []
    for field, value in [
        ("name", name),
        ("category", category),
        ("body_part", body_part),
        ("equipment", equipment),
        ("muscle_group", muscle_group),
        ("target", target),
    ]:
        if value:
            conditions.append(f"{field} LIKE ? COLLATE NOCASE")
            params.append(f"%{value}%")

    where = f"WHERE {' AND '.join(conditions)}" if conditions else ""
    offset = (page - 1) * limit

    total = db.execute(f"SELECT COUNT(*) FROM exercises {where}", params).fetchone()[0]
    rows = db.execute(
        f"SELECT * FROM exercises {where} ORDER BY id LIMIT ? OFFSET ?",
        params + [limit, offset],
    ).fetchall()

    return {
        "data": [row_to_dict(r) for r in rows],
        "total": total,
        "page": page,
        "limit": limit,
        "totalPages": -(-total // limit),
    }


@app.get("/exercises/{exercise_id}/insights")
def get_exercise_insights(exercise_id: str, db: DB, lang: Optional[str] = Query(None)):
    row = db.execute(
        "SELECT * FROM exercise_insights WHERE exercise_id = ?", (exercise_id,)
    ).fetchone()
    if not row:
        raise HTTPException(404, "Insights não gerados para este exercício")
    d = dict(row)
    for field in ("common_mistakes", "benefits"):
        if isinstance(d.get(field), str):
            try:
                d[field] = json.loads(d[field])
            except Exception:
                d[field] = []

    if lang == "pt":
        for field in ("injury_risk_area", "easier_variation", "harder_variation", "no_equipment_alt"):
            pt_val = d.get(f"{field}_pt")
            if pt_val:
                d[field] = pt_val
            elif d.get(field):
                d[field] = _translate_cached(d[field], "en", "pt", db)
        for arr_field in ("common_mistakes", "benefits"):
            pt_key = f"{arr_field}_pt"
            if isinstance(d.get(pt_key), list) and d[pt_key]:
                d[arr_field] = d[pt_key]
            elif isinstance(d.get(arr_field), list):
                d[arr_field] = [_translate_cached(item, "en", "pt", db) for item in d[arr_field]]
        d["_translated"] = True

    return d


@app.get("/exercises/{exercise_id}/calories")
def get_exercise_calories(
    exercise_id: str,
    db: DB,
    weight: Optional[float] = Query(None),
    credentials: Annotated[
        Optional[HTTPAuthorizationCredentials], Depends(_bearer_optional)
    ] = None,
):
    insight = db.execute(
        "SELECT calories_per_min_met FROM exercise_insights WHERE exercise_id = ?",
        (exercise_id,),
    ).fetchone()
    if not insight:
        raise HTTPException(404, "Insights não gerados para este exercício")

    exercise = db.execute("SELECT name FROM exercises WHERE id = ?", (exercise_id,)).fetchone()
    if not exercise:
        raise HTTPException(404, "Exercise not found")

    met = insight["calories_per_min_met"]

    weight_kg = weight
    if weight_kg is None and credentials:
        try:
            payload = jwt.decode(credentials.credentials, JWT_SECRET, algorithms=[JWT_ALGORITHM])
            user = db.execute(
                "SELECT weight_kg FROM users WHERE id = ?", (int(payload["id"]),)
            ).fetchone()
            if user and user["weight_kg"]:
                weight_kg = user["weight_kg"]
        except Exception:
            pass
    if weight_kg is None:
        weight_kg = 70.0

    calories_per_min = met * weight_kg * 3.5 / 200
    return {
        "exercise_id": exercise_id,
        "exercise_name": exercise["name"],
        "weight_kg": weight_kg,
        "met_value": met,
        "calories_per_minute": round(calories_per_min, 2),
        "calories_30min": round(calories_per_min * 30, 1),
        "note": "Estimativa baseada em MET. Varia com intensidade e condicionamento físico.",
    }


@app.get("/exercises/{exercise_id}/alternatives")
def get_exercise_alternatives(
    exercise_id: str,
    db: DB,
    equipment: Optional[str] = Query(None),
    limit: int = Query(8, ge=1, le=20),
):
    ex = db.execute(
        "SELECT target, body_part, equipment FROM exercises WHERE id = ?", (exercise_id,)
    ).fetchone()
    if not ex:
        raise HTTPException(404, "Exercise not found")

    equip_clause = ""
    extra_params: list = []
    if equipment:
        equip_clause = "AND LOWER(e.equipment) = LOWER(?)"
        extra_params = [equipment]

    rows = db.execute(
        f"""SELECT e.id, e.name, e.name_pt, e.body_part, e.equipment, e.target, e.muscle_group,
                   e.image, e.gif_url,
                   ei.difficulty, ei.effort_type, ei.calories_per_min_met,
                   CASE WHEN e.target = ? THEN 0 ELSE 1 END AS relevance
            FROM exercises e
            LEFT JOIN exercise_insights ei ON e.id = ei.exercise_id
            WHERE e.id != ?
              AND (e.target = ? OR e.body_part = ?)
              {equip_clause}
            ORDER BY relevance, ei.difficulty NULLS LAST, e.name
            LIMIT ?""",
        [ex["target"], exercise_id, ex["target"], ex["body_part"]] + extra_params + [limit],
    ).fetchall()

    result = []
    for r in rows:
        d = dict(r)
        d.pop("relevance", None)
        result.append(d)
    return result


@app.get("/exercises/{exercise_id}")
def get_exercise(exercise_id: str, db: DB):
    row = db.execute("SELECT * FROM exercises WHERE id = ?", (exercise_id,)).fetchone()
    if not row:
        raise HTTPException(404, "Exercise not found")
    return row_to_dict(row)


@app.get("/categories")
def list_categories(db: DB):
    rows = db.execute(
        "SELECT DISTINCT category FROM exercises WHERE category IS NOT NULL ORDER BY category"
    ).fetchall()
    return [r[0] for r in rows]


@app.get("/body-parts")
def list_body_parts(db: DB):
    rows = db.execute(
        "SELECT DISTINCT body_part FROM exercises WHERE body_part IS NOT NULL ORDER BY body_part"
    ).fetchall()
    return [r[0] for r in rows]


@app.get("/equipment")
def list_equipment(db: DB):
    rows = db.execute(
        "SELECT DISTINCT equipment FROM exercises WHERE equipment IS NOT NULL ORDER BY equipment"
    ).fetchall()
    return [r[0] for r in rows]


# ═════════════════════════════════════════════════════════════════════════════
# TRANSLATE
# ═════════════════════════════════════════════════════════════════════════════

@app.post("/translate")
def translate_text(body: TranslateIn, db: DB):
    if not body.text.strip():
        return {"translated": ""}

    h = _text_hash(f"{body.source}:{body.target}:{body.text}")
    row = db.execute(
        "SELECT translated_text FROM translations WHERE source_lang=? AND target_lang=? AND source_hash=?",
        (body.source, body.target, h),
    ).fetchone()
    if row:
        return {"translated": row["translated_text"], "cached": True}

    if not _argos_ready:
        msg = _argos_error or "serviço de tradução ainda inicializando, aguarde alguns segundos"
        raise HTTPException(status_code=503, detail=msg)

    translated = _do_translate(body.text, body.source, body.target)

    db.execute(
        "INSERT OR IGNORE INTO translations(source_lang, target_lang, source_hash, translated_text) VALUES(?,?,?,?)",
        (body.source, body.target, h, translated),
    )
    db.commit()
    return {"translated": translated, "cached": False}


@app.get("/translate/status")
def translate_status():
    return {
        "ready": _argos_ready,
        "error": _argos_error,
        "preload": _preload_progress,
    }


# ═════════════════════════════════════════════════════════════════════════════
# PLANS
# ═════════════════════════════════════════════════════════════════════════════

@app.get("/plans")
def list_plans(user: CurrentUser, db: DB):
    rows = db.execute(
        "SELECT * FROM workout_plans WHERE user_id = ? ORDER BY created_at DESC",
        (user["id"],),
    ).fetchall()
    return [dict(r) for r in rows]


@app.post("/plans", status_code=201)
def create_plan(body: PlanIn, user: CurrentUser, db: DB):
    if not body.name.strip():
        raise HTTPException(400, "name is required")
    if body.focus not in VALID_FOCUS:
        raise HTTPException(400, f"focus must be one of: {', '.join(sorted(VALID_FOCUS))}")

    cur = db.execute(
        "INSERT INTO workout_plans (user_id, name, focus) VALUES (?, ?, ?)",
        (user["id"], body.name.strip(), body.focus),
    )
    db.commit()
    return dict(db.execute("SELECT * FROM workout_plans WHERE id = ?", (cur.lastrowid,)).fetchone())


@app.get("/plans/{plan_id}/progression")
def get_plan_progression(plan_id: int, user: CurrentUser, db: DB):
    plan = db.execute(
        "SELECT * FROM workout_plans WHERE id = ? AND user_id = ?",
        (plan_id, user["id"]),
    ).fetchone()
    if not plan:
        raise HTTPException(404, "Plan not found")

    entries = db.execute(
        """SELECT se.day_of_week, se.sets, se.reps, se.position,
                  e.id AS exercise_id, e.name,
                  ei.difficulty, ei.harder_variation, ei.effort_type
           FROM schedule_entries se
           JOIN exercises e ON se.exercise_id = e.id
           LEFT JOIN exercise_insights ei ON e.id = ei.exercise_id
           WHERE se.plan_id = ?
           ORDER BY se.day_of_week, se.position""",
        (plan["id"],),
    ).fetchall()

    days: dict = {}
    for e in entries:
        dow = e["day_of_week"]
        if dow not in days:
            days[dow] = []
        sets, reps = e["sets"], e["reps"]
        harder = e["harder_variation"]
        if reps < 12:
            suggestion = {"type": "reps", "sets": sets, "reps": reps + 2,
                          "note": f"Aumente para {reps + 2} repetições por série"}
        elif sets < 5:
            suggestion = {"type": "sets", "sets": sets + 1, "reps": 8,
                          "note": f"Adicione uma série ({sets + 1}×8) e construa de volta"}
        elif harder:
            suggestion = {"type": "variation", "sets": sets, "reps": reps,
                          "note": f"Avance para: {harder}"}
        else:
            suggestion = {"type": "weight", "sets": sets, "reps": reps,
                          "note": "Aumente a carga em 2,5–5 kg"}
        days[dow].append({
            "exercise_id": e["exercise_id"],
            "exercise_name": e["name"],
            "current": {"sets": sets, "reps": reps},
            "suggestion": suggestion,
            "harder_variation": harder,
            "difficulty": e["difficulty"],
            "effort_type": e["effort_type"],
        })

    return {
        "plan_id": plan_id,
        "plan_name": dict(plan)["name"],
        "progression": [
            {"day_of_week": dow, "day": DAYS[dow], "exercises": exs}
            for dow, exs in sorted(days.items())
        ],
    }


@app.get("/plans/{plan_id}")
def get_plan(plan_id: int, user: CurrentUser, db: DB):
    plan = db.execute(
        "SELECT * FROM workout_plans WHERE id = ? AND user_id = ?",
        (plan_id, user["id"]),
    ).fetchone()
    if not plan:
        raise HTTPException(404, "Plan not found")

    entries = db.execute(
        """SELECT
            se.id          AS entry_id,
            se.day_of_week, se.sets, se.reps, se.rest_seconds, se.position,
            e.id           AS exercise_id,
            e.name, e.category, e.body_part, e.equipment,
            e.target, e.muscle_group, e.secondary_muscles,
            e.image, e.gif_url
        FROM schedule_entries se
        JOIN exercises e ON se.exercise_id = e.id
        WHERE se.plan_id = ?
        ORDER BY se.day_of_week, se.position""",
        (plan["id"],),
    ).fetchall()

    schedule = [{"day_of_week": i, "day": DAYS[i], "exercises": []} for i in range(7)]
    for e in entries:
        try:
            secondary = json.loads(e["secondary_muscles"]) if e["secondary_muscles"] else []
        except Exception:
            secondary = []

        schedule[e["day_of_week"]]["exercises"].append({
            "entry_id":    e["entry_id"],
            "position":    e["position"],
            "sets":        e["sets"],
            "reps":        e["reps"],
            "rest_seconds": e["rest_seconds"],
            "exercise": {
                "id":               e["exercise_id"],
                "name":             e["name"],
                "category":         e["category"],
                "body_part":        e["body_part"],
                "equipment":        e["equipment"],
                "target":           e["target"],
                "muscle_group":     e["muscle_group"],
                "secondary_muscles": secondary,
                "image":            e["image"],
                "gif_url":          e["gif_url"],
            },
        })

    return {**dict(plan), "schedule": schedule}


@app.put("/plans/{plan_id}")
def update_plan(plan_id: int, body: PlanUpdate, user: CurrentUser, db: DB):
    if body.focus is not None and body.focus not in VALID_FOCUS:
        raise HTTPException(400, f"focus must be one of: {', '.join(sorted(VALID_FOCUS))}")

    plan = db.execute(
        "SELECT * FROM workout_plans WHERE id = ? AND user_id = ?",
        (plan_id, user["id"]),
    ).fetchone()
    if not plan:
        raise HTTPException(404, "Plan not found")

    new_name  = body.name.strip() if body.name else plan["name"]
    new_focus = body.focus or plan["focus"]
    db.execute(
        "UPDATE workout_plans SET name = ?, focus = ? WHERE id = ?",
        (new_name, new_focus, plan["id"]),
    )
    db.commit()
    return {**dict(plan), "name": new_name, "focus": new_focus}


@app.delete("/plans/{plan_id}", status_code=204)
def delete_plan(plan_id: int, user: CurrentUser, db: DB):
    plan = db.execute(
        "SELECT id FROM workout_plans WHERE id = ? AND user_id = ?",
        (plan_id, user["id"]),
    ).fetchone()
    if not plan:
        raise HTTPException(404, "Plan not found")
    db.execute("DELETE FROM workout_plans WHERE id = ?", (plan["id"],))
    db.commit()


@app.post("/plans/{plan_id}/schedule", status_code=201)
def add_to_schedule(plan_id: int, body: ScheduleIn, user: CurrentUser, db: DB):
    if not (0 <= body.day_of_week <= 6):
        raise HTTPException(400, "day_of_week must be 0 (Sun) – 6 (Sat)")
    if body.sets < 1:
        raise HTTPException(400, "sets must be >= 1")
    if body.reps < 1:
        raise HTTPException(400, "reps must be >= 1")
    if body.rest_seconds < 0:
        raise HTTPException(400, "rest_seconds must be >= 0")

    plan = db.execute(
        "SELECT id FROM workout_plans WHERE id = ? AND user_id = ?",
        (plan_id, user["id"]),
    ).fetchone()
    if not plan:
        raise HTTPException(404, "Plan not found")

    if not db.execute("SELECT id FROM exercises WHERE id = ?", (body.exercise_id,)).fetchone():
        raise HTTPException(404, "Exercise not found")

    max_pos = db.execute(
        "SELECT COALESCE(MAX(position), -1) FROM schedule_entries WHERE plan_id = ? AND day_of_week = ?",
        (plan["id"], body.day_of_week),
    ).fetchone()[0]

    cur = db.execute(
        "INSERT INTO schedule_entries (plan_id, day_of_week, exercise_id, sets, reps, rest_seconds, position) VALUES (?, ?, ?, ?, ?, ?, ?)",
        (plan["id"], body.day_of_week, body.exercise_id, body.sets, body.reps, body.rest_seconds, max_pos + 1),
    )
    db.commit()
    return {
        "id":          cur.lastrowid,
        "plan_id":     plan["id"],
        "day_of_week": body.day_of_week,
        "day":         DAYS[body.day_of_week],
        "exercise_id": body.exercise_id,
        "sets":        body.sets,
        "reps":        body.reps,
        "rest_seconds": body.rest_seconds,
        "position":    max_pos + 1,
    }


@app.put("/plans/{plan_id}/schedule/{entry_id}")
def update_schedule_entry(
    plan_id: int, entry_id: int, body: ScheduleUpdate, user: CurrentUser, db: DB
):
    plan = db.execute(
        "SELECT id FROM workout_plans WHERE id = ? AND user_id = ?",
        (plan_id, user["id"]),
    ).fetchone()
    if not plan:
        raise HTTPException(404, "Plan not found")

    entry = db.execute(
        "SELECT * FROM schedule_entries WHERE id = ? AND plan_id = ?",
        (entry_id, plan["id"]),
    ).fetchone()
    if not entry:
        raise HTTPException(404, "Schedule entry not found")

    new_sets  = body.sets         if body.sets         is not None else entry["sets"]
    new_reps  = body.reps         if body.reps         is not None else entry["reps"]
    new_rest  = body.rest_seconds if body.rest_seconds is not None else entry["rest_seconds"]
    new_pos   = body.position     if body.position     is not None else entry["position"]

    db.execute(
        "UPDATE schedule_entries SET sets=?, reps=?, rest_seconds=?, position=? WHERE id=?",
        (new_sets, new_reps, new_rest, new_pos, entry["id"]),
    )
    db.commit()
    return {**dict(entry), "sets": new_sets, "reps": new_reps, "rest_seconds": new_rest, "position": new_pos}


@app.delete("/plans/{plan_id}/schedule/{entry_id}", status_code=204)
def delete_schedule_entry(plan_id: int, entry_id: int, user: CurrentUser, db: DB):
    plan = db.execute(
        "SELECT id FROM workout_plans WHERE id = ? AND user_id = ?",
        (plan_id, user["id"]),
    ).fetchone()
    if not plan:
        raise HTTPException(404, "Plan not found")

    entry = db.execute(
        "SELECT id FROM schedule_entries WHERE id = ? AND plan_id = ?",
        (entry_id, plan["id"]),
    ).fetchone()
    if not entry:
        raise HTTPException(404, "Schedule entry not found")

    db.execute("DELETE FROM schedule_entries WHERE id = ?", (entry["id"],))
    db.commit()


# ── Static assets (images & GIFs) — must come after all API routes ────────────
import os as _os
if _os.path.isdir("public/images"):
    app.mount("/images", StaticFiles(directory="public/images"), name="images")
if _os.path.isdir("public/videos"):
    app.mount("/videos", StaticFiles(directory="public/videos"), name="videos")
