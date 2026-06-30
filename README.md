# ⚡ VOLT

**Plataforma de treinos físicos** — API REST com FastAPI, SPA em vanilla JS e dataset de 1.324 exercícios com GIFs animados.

![Python](https://img.shields.io/badge/Python-3.11+-3776AB?style=flat&logo=python&logoColor=white)
![FastAPI](https://img.shields.io/badge/FastAPI-0.111+-009688?style=flat&logo=fastapi&logoColor=white)
![SQLite](https://img.shields.io/badge/SQLite-embedded-003B57?style=flat&logo=sqlite&logoColor=white)
![License](https://img.shields.io/badge/Licença-Educacional-orange?style=flat)

---

## Índice

- [Visão Geral](#visão-geral)
- [Funcionalidades](#funcionalidades)
- [Pré-requisitos](#pré-requisitos)
- [Instalação e Execução](#instalação-e-execução)
- [Assets de Mídia](#assets-de-mídia)
- [Variáveis de Ambiente](#variáveis-de-ambiente)
- [Docker](#docker)
- [Enriquecimento LLM](#enriquecimento-llm-opcional)
- [Endpoints da API](#endpoints-da-api)
- [Estrutura do Projeto](#estrutura-do-projeto)
- [Tecnologias](#tecnologias)
- [Licença](#licença)

---

## Visão Geral

VOLT é uma aplicação full-stack para gerenciamento de treinos. O backend expõe uma API REST com autenticação JWT, planos de treino semanais e sugestões de progressão por double progression. O frontend é uma SPA em vanilla JS com cronômetro de treino, dashboard semanal e gráficos de evolução de carga — sem nenhum framework JS.

| | |
|---|---|
| **Backend** | FastAPI + SQLite |
| **Frontend** | Vanilla JS (SPA) |
| **Exercícios** | 1.324 com GIFs animados |
| **Idiomas** | PT · EN · ES · IT · TR |
| **Autenticação** | JWT (HS256, 7 dias) |

---

## Funcionalidades

### 🏋️ Exercícios
- Busca por nome, parte do corpo, equipamento e músculo alvo
- GIF animado + instruções traduzidas para português
- Insights gerados por LLM: dificuldade, MET, erros comuns, risco de lesão e variações
- Exercícios alternativos pelo mesmo grupo muscular

### 📅 Plano de Treino
- Plano semanal personalizado (Seg → Dom)
- Sets, reps e tempo de descanso configuráveis por exercício
- Sugestões de progressão automática por **double progression**:
  `reps ↑` → `série +` → `variação →` → `carga ↑`

### ⏱️ Treino (Cronômetro)
- Cronômetro ativo com timer de descanso entre séries
- Log automático de carga salvo no localStorage
- Tela de conclusão com métricas de sessão

### 📊 Dashboard Semanal
- Contadores de treinos, séries, tempo e calorias
- Heatmap de frequência semanal + streak de dias consecutivos 🔥
- Chips dos grupos musculares mais trabalhados na semana
- Gráfico sparkline de evolução de carga por exercício
- Histórico das últimas sessões com kcal estimadas

### 🔥 Cálculo de Calorias

```
aeróbico = MET × peso_kg × 3,5 / 200 × minutos_efetivos
força    = volume_kg / 60
total    = aeróbico + força
```

> `minutos_efetivos = max(duração_real, séries × 1 min)` — garante estimativas realistas mesmo em sessões rápidas.

### 📱 Mobile
- Bottom navigation bar fixa (Início · Plano · Treino · Perfil)
- Layout responsivo para telas pequenas

---

## Pré-requisitos

- [Python 3.11+](https://www.python.org/downloads/)
- [uv](https://docs.astral.sh/uv/getting-started/installation/) — gerenciador de pacotes Python

---

## Instalação e Execução

```bash
# 1. Clonar o repositório
git clone https://github.com/reissbruno/volt-exercise.git
cd volt-exercise

# 2. Instalar dependências
uv sync

# 3. Configurar variáveis de ambiente
cp .env.example .env
# Edite .env e defina JWT_SECRET com um valor seguro (mín. 32 chars)

# 4. Iniciar o servidor
uv run uvicorn server:app --reload
```

| URL | Descrição |
|---|---|
| `http://localhost:8000/app` | Aplicação |
| `http://localhost:8000/docs` | Swagger UI |
| `http://localhost:8000/redoc` | ReDoc |

> O banco SQLite é criado automaticamente em `data/exercises.db` na primeira execução.

---

## Assets de Mídia

Os ~140 MB de assets (`public/images/` e `public/videos/`) **não estão incluídos no repositório** para manter o clone leve.

**Para rodar com mídia completa**, baixe os assets separadamente e coloque-os em:

```
public/
├── images/   # 1.324 thumbnails JPG (~12 MB)
└── videos/   # 1.324 GIFs animados (~127 MB)
```

> Sem os assets, a aplicação funciona normalmente — os exercícios são listados sem imagem/GIF.

---

## Variáveis de Ambiente

Crie um arquivo `.env` na raiz (use `.env.example` como base):

| Variável | Padrão | Descrição |
|---|---|---|
| `PORT` | `8000` | Porta do servidor |
| `DB_PATH` | `data/exercises.db` | Caminho do banco SQLite |
| `JWT_SECRET` | _(obrigatório)_ | Segredo JWT — use um valor aleatório longo |
| `ALLOWED_ORIGINS` | `*` | Origens CORS permitidas |
| `EXERCISES_JSON_PATH` | `data/exercises.json` | Caminho do dataset |
| `OPENAI_API_KEY` | — | Apenas para enriquecimento LLM (opcional) |

> **Nunca commite o arquivo `.env`** — ele já está no `.gitignore`.

---

## Docker

```bash
# Build
docker build -t volt-exercise .

# Run — monta assets e banco como volumes externos
docker run -p 8000:8000 \
  -e JWT_SECRET=seu-segredo-aqui \
  -v $(pwd)/public:/app/public \
  -v $(pwd)/data:/app/data \
  volt-exercise
```

A imagem não inclui os assets de mídia por padrão (~140 MB). Para embutir tudo na imagem, descomente `COPY public/` no `Dockerfile`.

---

## Enriquecimento LLM (Opcional)

O script `scripts/enrich_exercises.py` popula a tabela `exercise_insights` via OpenAI, habilitando os endpoints `/insights`, `/alternatives` e `/progression` com dados completos.

```bash
# Adicione OPENAI_API_KEY no .env, depois:
uv run python scripts/enrich_exercises.py
```

| | |
|---|---|
| **Modelo** | GPT-4o mini |
| **Custo estimado** | < US$ 0,20 para todos os 1.324 exercícios |
| **Checkpoint** | `scripts/enrich_checkpoint.json` — retoma de onde parou |
| **Sem enrichment** | MET padrão 3,5; `/insights` retorna 404 |

---

## Endpoints da API

### Autenticação

| Método | Rota | Descrição |
|---|---|---|
| `POST` | `/auth/register` | Cadastro |
| `POST` | `/auth/login` | Login — retorna JWT |
| `GET` | `/me` | Perfil autenticado |
| `PUT` | `/me` | Atualiza perfil |

### Exercícios

| Método | Rota | Descrição |
|---|---|---|
| `GET` | `/exercises` | Lista com filtros e paginação |
| `GET` | `/exercises/{id}` | Exercício por ID |
| `GET` | `/exercises/{id}/insights` | Insights LLM |
| `GET` | `/exercises/{id}/alternatives` | Exercícios alternativos |
| `GET` | `/exercises/random` | Exercício aleatório |
| `GET` | `/exercises/suggestions` | Sugestões por foco muscular |

### Planos de Treino

| Método | Rota | Descrição |
|---|---|---|
| `GET` | `/plans` | Lista planos do usuário |
| `POST` | `/plans` | Cria novo plano |
| `GET` | `/plans/{id}` | Detalhes do plano |
| `PUT` | `/plans/{id}` | Atualiza plano |
| `DELETE` | `/plans/{id}` | Remove plano |
| `GET` | `/plans/{id}/progression` | Sugestões de progressão |
| `POST` | `/plans/{id}/schedule` | Adiciona exercício a um dia |
| `PUT` | `/plans/{id}/schedule/{entry_id}` | Atualiza entrada |
| `DELETE` | `/plans/{id}/schedule/{entry_id}` | Remove entrada |

### Utilitários

| Método | Rota | Descrição |
|---|---|---|
| `GET` | `/body-parts` | Partes do corpo disponíveis |
| `GET` | `/equipment` | Equipamentos disponíveis |
| `GET` | `/categories` | Categorias disponíveis |
| `POST` | `/translate` | Traduz texto en → pt |
| `GET` | `/translate/status` | Status do motor de tradução |

---

## Estrutura do Projeto

```
volt-exercise/
├── data/
│   └── exercises.json          # Dataset — 1.324 exercícios (6,3 MB)
├── public/                     # Assets de mídia — não versionados (~140 MB)
│   ├── images/                 # Thumbnails JPG
│   └── videos/                 # GIFs animados
├── frontend/
│   └── app.html                # SPA completa (~2.259 linhas)
├── scripts/
│   └── enrich_exercises.py     # Enriquecimento LLM em lote
├── server.py                   # API FastAPI (~1.094 linhas)
├── pyproject.toml              # Dependências Python (uv)
├── Dockerfile
├── .dockerignore
└── .env.example
```

---

## Tecnologias

**Backend**
| Lib | Uso |
|---|---|
| [FastAPI](https://fastapi.tiangolo.com/) | Framework REST assíncrono |
| [Uvicorn](https://www.uvicorn.org/) | Servidor ASGI |
| [SQLite](https://www.sqlite.org/) | Banco de dados embutido |
| [python-jose](https://github.com/mpdavis/python-jose) | Autenticação JWT |
| [bcrypt](https://github.com/pyca/bcrypt/) | Hash de senhas |
| [Argos Translate](https://github.com/argosopentech/argos-translate) | Tradução offline en → pt |
| [OpenAI SDK](https://github.com/openai/openai-python) | Enriquecimento LLM (opcional) |

**Frontend**
- HTML5 + CSS3 + JavaScript puro — sem frameworks ou bundlers
- [Google Fonts](https://fonts.google.com/) — Anton + Manrope
- LocalStorage para sessões, carga e preferências do usuário

---

## Licença

Uso **educacional e não-comercial**. Os assets de mídia pertencem aos seus criadores originais.

| | |
|---|---|
| Repositório | [github.com/reissbruno/volt-exercise](https://github.com/reissbruno/volt-exercise) |
| Dataset base | [github.com/hasaneyldrm/exercises-dataset](https://github.com/hasaneyldrm/exercises-dataset) |
| Traduções | ES · IT · TR (comunidade) · PT (Argos Translate) |
| Mídia original | Kaggle — "Fitness Exercises Dataset" por omarxadel |

> Para uso comercial, consulte a fonte original no [Kaggle](https://www.kaggle.com/).
