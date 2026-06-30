# ⚡ VOLT

**Fitness training platform** — REST API built with FastAPI, vanilla JS SPA, and a dataset of 1,324 exercises with animated GIFs.

![Python](https://img.shields.io/badge/Python-3.11+-3776AB?style=flat&logo=python&logoColor=white)
![FastAPI](https://img.shields.io/badge/FastAPI-0.111+-009688?style=flat&logo=fastapi&logoColor=white)
![SQLite](https://img.shields.io/badge/SQLite-embedded-003B57?style=flat&logo=sqlite&logoColor=white)
![License](https://img.shields.io/badge/License-Educational-orange?style=flat)

---

## Table of Contents

- [Overview](#overview)
- [Features](#features)
- [Prerequisites](#prerequisites)
- [Installation](#installation)
- [Environment Variables](#environment-variables)
- [Docker](#docker)
- [LLM Enrichment](#llm-enrichment-optional)
- [API Endpoints](#api-endpoints)
- [Project Structure](#project-structure)
- [Tech Stack](#tech-stack)
- [License](#license)

---

## Overview

VOLT is a full-stack workout management app. The backend exposes a REST API with JWT authentication, weekly training plans, and double-progression suggestions. The frontend is a vanilla JS SPA with a workout timer, weekly dashboard, and load progression charts — no JS framework required.

| | |
|---|---|
| **Backend** | FastAPI + SQLite |
| **Frontend** | Vanilla JS (SPA) |
| **Exercises** | 1,324 with animated GIFs |
| **Languages** | PT · EN · ES · IT · TR |
| **Auth** | JWT (HS256, 7 days) |

---

## Features

### 🏋️ Exercises
- Search by name, body part, equipment, and target muscle
- Animated GIF + instructions translated to Portuguese
- LLM-generated insights: difficulty, MET, common mistakes, injury risk, and variations
- Alternative exercises for the same muscle group

### 📅 Training Plan
- Personalized weekly plan (Mon → Sun)
- Configurable sets, reps, and rest time per exercise
- Automatic progression suggestions via **double progression**:
  `reps ↑` → `sets +` → `harder variation →` → `weight ↑`

### ⏱️ Workout Timer
- Active stopwatch with rest timer between sets
- Automatic load log saved to localStorage
- Completion screen with session metrics

### 📊 Weekly Dashboard
- Counters for workouts, sets, time, and calories
- Weekly frequency heatmap + consecutive-day streak 🔥
- Chips for the most-trained muscle groups of the week
- Sparkline chart showing load progression per exercise
- Recent session history with estimated kcal

### 🔥 Calorie Calculation

```
aerobic  = MET × weight_kg × 3.5 / 200 × effective_minutes
strength = volume_kg / 60
total    = aerobic + strength
```

> `effective_minutes = max(real_duration, sets × 1 min)` — ensures realistic estimates even for short sessions.

### 📱 Mobile
- Fixed bottom navigation bar (Home · Plan · Workout · Profile)
- Responsive layout for small screens

---

## Prerequisites

- [Python 3.11+](https://www.python.org/downloads/)
- [uv](https://docs.astral.sh/uv/getting-started/installation/) — Python package manager

---

## Installation

```bash
# 1. Clone the repository
git clone https://github.com/reissbruno/volt-exercise.git
cd volt-exercise

# 2. Install dependencies
uv sync

# 3. Configure environment variables
cp .env.example .env
# Edit .env and set JWT_SECRET to a secure random value (min. 32 chars)

# 4. Start the server
uv run uvicorn server:app --reload
```

| URL | Description |
|---|---|
| `http://localhost:8000/app` | Application |
| `http://localhost:8000/docs` | Swagger UI |
| `http://localhost:8000/redoc` | ReDoc |

> The SQLite database is created automatically at `data/exercises.db` on first run.

---

## Environment Variables

Create a `.env` file in the project root (use `.env.example` as a template):

| Variable | Default | Description |
|---|---|---|
| `PORT` | `8000` | Server port |
| `DB_PATH` | `data/exercises.db` | SQLite database path |
| `JWT_SECRET` | _(required)_ | JWT secret — use a long random value |
| `ALLOWED_ORIGINS` | `*` | Allowed CORS origins |
| `EXERCISES_JSON_PATH` | `data/exercises.json` | Dataset path |
| `OPENAI_API_KEY` | — | Only for LLM enrichment (optional) |

> **Never commit the `.env` file** — it is already in `.gitignore`.

---

## Docker

```bash
# Build
docker build -t volt-exercise .

# Run — mount assets and database as external volumes
docker run -p 8000:8000 \
  -e JWT_SECRET=your-secret-here \
  -v $(pwd)/public:/app/public \
  -v $(pwd)/data:/app/data \
  volt-exercise
```

The image includes the exercise dataset but **not the media assets** — they live in the repo but are excluded from the Docker build via `.dockerignore` to keep the image lean. Mount them as a volume at runtime as shown above.

---

## LLM Enrichment (Optional)

The `scripts/enrich_exercises.py` script populates the `exercise_insights` table via OpenAI, enabling the `/insights`, `/alternatives`, and `/progression` endpoints with full data.

```bash
# Add OPENAI_API_KEY to .env, then:
uv run python scripts/enrich_exercises.py
```

| | |
|---|---|
| **Model** | GPT-4o mini |
| **Estimated cost** | < US$ 0.20 for all 1,324 exercises |
| **Checkpoint** | `scripts/enrich_checkpoint.json` — resumes where it left off |
| **Without enrichment** | Default MET 3.5; `/insights` returns 404 |

---

## API Endpoints

### Authentication

| Method | Route | Description |
|---|---|---|
| `POST` | `/auth/register` | Sign up |
| `POST` | `/auth/login` | Log in — returns JWT |
| `GET` | `/me` | Authenticated profile |
| `PUT` | `/me` | Update profile |

### Exercises

| Method | Route | Description |
|---|---|---|
| `GET` | `/exercises` | List with filters and pagination |
| `GET` | `/exercises/{id}` | Exercise by ID |
| `GET` | `/exercises/{id}/insights` | LLM insights |
| `GET` | `/exercises/{id}/alternatives` | Alternative exercises |
| `GET` | `/exercises/random` | Random exercise |
| `GET` | `/exercises/suggestions` | Suggestions by muscle focus |

### Training Plans

| Method | Route | Description |
|---|---|---|
| `GET` | `/plans` | List user's plans |
| `POST` | `/plans` | Create a new plan |
| `GET` | `/plans/{id}` | Plan details |
| `PUT` | `/plans/{id}` | Update plan |
| `DELETE` | `/plans/{id}` | Delete plan |
| `GET` | `/plans/{id}/progression` | Progression suggestions |
| `POST` | `/plans/{id}/schedule` | Add exercise to a day |
| `PUT` | `/plans/{id}/schedule/{entry_id}` | Update entry |
| `DELETE` | `/plans/{id}/schedule/{entry_id}` | Remove entry |

### Utilities

| Method | Route | Description |
|---|---|---|
| `GET` | `/body-parts` | Available body parts |
| `GET` | `/equipment` | Available equipment |
| `GET` | `/categories` | Available categories |
| `POST` | `/translate` | Translate text en → pt |
| `GET` | `/translate/status` | Translation engine status |

---

## Project Structure

```
volt-exercise/
├── data/
│   └── exercises.json          # Dataset — 1,324 exercises (6.3 MB)
├── public/                     # Media assets — 1,324 GIFs + thumbnails (~140 MB)
│   ├── images/                 # JPG thumbnails
│   └── videos/                 # Animated GIFs
├── frontend/
│   └── app.html                # Full SPA (~2,259 lines)
├── scripts/
│   └── enrich_exercises.py     # Batch LLM enrichment
├── server.py                   # FastAPI API (~1,094 lines)
├── pyproject.toml              # Python dependencies (uv)
├── Dockerfile
├── .dockerignore
└── .env.example
```

---

## Tech Stack

**Backend**
| Library | Purpose |
|---|---|
| [FastAPI](https://fastapi.tiangolo.com/) | Async REST framework |
| [Uvicorn](https://www.uvicorn.org/) | ASGI server |
| [SQLite](https://www.sqlite.org/) | Embedded database |
| [python-jose](https://github.com/mpdavis/python-jose) | JWT authentication |
| [bcrypt](https://github.com/pyca/bcrypt/) | Password hashing |
| [Argos Translate](https://github.com/argosopentech/argos-translate) | Offline translation en → pt |
| [OpenAI SDK](https://github.com/openai/openai-python) | LLM enrichment (optional) |

**Frontend**
- HTML5 + CSS3 + plain JavaScript — no frameworks or bundlers
- [Google Fonts](https://fonts.google.com/) — Anton + Manrope
- LocalStorage for sessions, load logs, and user preferences

---

## License

For **educational and non-commercial use only**. Media assets belong to their original creators.

| | |
|---|---|
| Repository | [github.com/reissbruno/volt-exercise](https://github.com/reissbruno/volt-exercise) |
| Base dataset | [github.com/hasaneyldrm/exercises-dataset](https://github.com/hasaneyldrm/exercises-dataset) |
| Translations | ES · IT · TR (community) · PT (Argos Translate) |
| Original media | Kaggle — "Fitness Exercises Dataset" by omarxadel |

> For commercial use, refer to the original source on [Kaggle](https://www.kaggle.com/).
