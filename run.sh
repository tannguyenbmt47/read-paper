#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "$0")"

if [ ! -d .venv ]; then
  echo "→ Tạo môi trường ảo…"
  python3 -m venv .venv
  .venv/bin/pip install -q --upgrade pip
  .venv/bin/pip install -q -r requirements.txt
fi

if [ ! -f .env ]; then
  cp .env.example .env
  echo "→ Đã tạo .env — mở ra điền OPENROUTER_API_KEY rồi chạy lại."
  exit 1
fi

exec .venv/bin/uvicorn server.main:app --host 127.0.0.1 --port "${PORT:-8010}" "$@"
