# Running with Docker

## Three steps

```bash
cp .env.example .env
# open .env and add OPENROUTER_API_KEY from https://openrouter.ai/keys
echo "DOCKER_UID=$(id -u)" >> .env
echo "DOCKER_GID=$(id -g)" >> .env

docker compose up -d --build
```

Open <http://127.0.0.1:8000>. To change the port: `PORT=8010 docker compose up -d`

## Two traps worth knowing about

**1. `unable to open database file`.** The container runs under its own uid and
cannot write to the host's `./data`. That is what `DOCKER_UID` and `DOCKER_GID`
in `.env` are for: they make the container run as you. Without those two lines,
SQLite fails the moment you open the first paper.

**2. The mount must be somewhere the Docker daemon can see.** Isolated temporary
directories (`/tmp/...` in some environments) cannot be mounted — the volume
silently comes up empty and the app reports no papers. Keeping `./data` next to
`docker-compose.yml` avoids this.

## Layout model (docling)

The default image does **not** include docling: it pulls in torch and a model
bundle, taking the image from **302MB to several GB**. Without it,
`parser.parse_pdf()` still runs on PyMuPDF heuristics — figure crops are less
accurate on pages with several tables, but nothing breaks.

If you need that accuracy:

```bash
WITH_LAYOUT=1 docker compose build
# then remove LAYOUT_BACKEND=off from docker-compose.yml
```

## Data

Everything lives in `./data`: the SQLite database `papers.db`, source PDFs, and
cropped images. Upgrading the image does not lose read papers. To back up, copy
that directory.

## Why the image needs fonts

`fonts-liberation` is **not** for display — the server renders nothing.
`server/slide_fit.py` uses it to measure text width from real font metrics
(Liberation Sans is metric-compatible with Arial, the font `_SLIDES_CSS`
specifies), then determines whether a slide overflows the 1280×720 frame and
shrinks the type to fit. Remove the font and the measurement falls back to a
crude estimate, which lets slides get clipped at the bottom.

## Checking the build

```bash
docker compose ps                          # should report "healthy"
curl -s -o /dev/null -w "%{http_code}\n" http://127.0.0.1:8000/api/config   # 200
docker compose logs -f app                 # errors, if any
```
