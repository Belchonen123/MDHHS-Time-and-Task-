# MDHHS-POC-Builder

## What it does

This app lets you upload **MDHHS-6064** (Michigan Home Help authorization) PDFs, run validation against MDHHS rules, and get **plan-of-care** **Excel** and **PDF** outputs. **All processing happens on your machine** so patient information never has to leave your device unless you choose optional LLM help.

## HIPAA posture

- PDF parsing, calculation, validation, and file generation **all run locally**.
- **Optional** LLM refinement sends **only redacted task data** (no name, ID, case number, address) to **Anthropic’s** API.
- **Localhost-only:** the server binds to **127.0.0.1**, not `0.0.0.0`.
- For a full **HIPAA BAA** with Anthropic, contact **sales@anthropic.com**.

## Setup (one time)

1. Clone the repo
2. Install **Python 3.11+** and **Node 20+**
3. In the project root, run `npm install` (installs **concurrently** for one-command dev)
4. Run `npm run install:all` (Python deps in `backend/`, then `npm install` in `frontend/`)
5. Copy `mdhhs-poc-builder/.env.example` to **`.env`** in the monorepo root (or `backend/`) and set `ANTHROPIC_API_KEY` (only if you use `--llm` or the **“Use Claude”** toggle in the UI)

## Daily use

From the **repo root**, `npm run dev` starts the API and the frontend together (one backend + Vite). If you work inside **`frontend/`**, `npm run dev` there also starts **both** the API and the app — use this if your IDE’s “Run dev server” only opened Vite before (which uploads could not work).

Open the app at **http://localhost:3456** (if that port is in use, use the Local URL printed in the terminal).

Drop an MDHHS-6064 PDF in the upload flow. Generated files are written under `backend/storage/{client_id}/`.

## Project structure

```text
mdhhs-poc-builder/
├── package.json
├── .env.example
├── README.md
├── backend/
│   ├── main.py
│   ├── requirements.txt
│   ├── app/              # pipeline, API, DB
│   ├── data/             # SQLite (gitignored)
│   ├── storage/          # uploads + outputs (gitignored)
│   ├── templates/        # plan_of_care_template.xlsx (required at startup)
│   └── tests/
└── frontend/             # Vite + React + TypeScript
    ├── src/
    └── package.json
```

## The math

Michigan’s rules **equate 4.3 weeks per month** for converting monthly to weekly minutes and dollars. The **weekly budget in minutes** is the sum, over the days you selected in the week, of **(minimum hours per day for that day × 60)**, and **weekly cost** is derived from the authorized monthly amount using that same 4.3-weeks convention so totals stay consistent with the authorization.

## Testing

`cd backend && pytest` — runs all cross-check tests (including **Ottilie Smith** reference data). **All** validation checks must pass.

## License

Use per your organization’s policy.
# MDHHS-Time-and-Task-
