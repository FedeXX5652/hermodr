<p align="center"><img src="assets/logo.svg" alt="Hermóðr" width="360"></p>

# Hermóðr

My own news digest. I got tired of reading the same story ten times and still not knowing what actually happened, so this groups the notes from many outlets into stories, keeps the ones that matter, and writes a short neutral summary of each one with a local model. Nothing leaves my machine.

Hermóðr was the messenger that rode to Hel and came back with the news. Seemed fitting.

## What it does

- Reads RSS feeds (`config/sources.yaml`) and pulls the full text of each note.
- Groups notes that tell the same thing into one story. Wire copies don't count as extra coverage.
- Scores each story: how many outlets, how many *families* of outlets, how long it's been going, front pages, and how much it matches my profile. Every story says why it's there.
- Summary in one or two sentences, only with facts that show up in at least two families. Every number and every name is checked against the notes; what can't be checked is dropped.
- "Cómo la titularon": the headline from each family, side by side. "Punto ciego": stories that only one family is covering.
- If I don't run it for a few days, the next edition covers the whole gap with the same space, so it gets pickier instead of longer.
- One html page per edition, desktop and phone. `hermodr serve` serves them locally and keeps my clicks and votes, so I can tune the weights later.

The output is in Spanish, because the news is.

## The model

Any GGUF that `llama-server` (llama.cpp) can run. Hermodr starts its own server from `config/model.toml`, checks everything before using it (sha256 of the model, every option against `--help`, port, gpu memory) and stops it when the edition is done. If something doesn't match, it doesn't start: better to fail loud than to summarize with the wrong thing. Each summary keeps which model and which parameters made it.

`hermodr run --no-model` works without any model: extracts instead of summaries. Good for testing sources and weights.

## Setup

Needs Python 3.11+ and [uv](https://docs.astral.sh/uv/).

```
uv sync
uv run hermodr init           # creates config/*.toml and *.yaml from the .example files
                              # edit them: sources, profile, model
uv run hermodr check          # validates all of it, every error at once
uv run hermodr sources        # tests every feed
uv run hermodr model          # starts the model, asks for a json, stops it
```

## Use

```
uv run hermodr run            # next edition
uv run hermodr serve          # read it at http://localhost:8077
uv run hermodr redo           # rebuild the last one (after changing sources or weights)
uv run hermodr families       # suggests media families from what each outlet covers
uv run hermodr stats          # clicks, votes, broken feeds
```

The real config files and everything in `data/` stay out of git. Only the `.example` files are here.

## Tests

```
uv run pytest
```

They run against a fake world (feeds, articles and a fake llama-server), no network and no model needed.

## License

MIT
