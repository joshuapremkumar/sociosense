# SocioSense

> **A culturally grounded, verifiably safe family outing planner.**
> Qloo's Taste AI™ API (v2) supplies cross-domain cultural relevance; a deterministic, fail-closed pipeline enforces health, accessibility and safety constraints.

![Python](https://img.shields.io/badge/python-3.10%2B-blue)
![Dependencies](https://img.shields.io/badge/dependencies-none-success)
![Tests](https://img.shields.io/badge/tests-47%20passing-brightgreen)

**Contents:** [Overview](#overview) · [Key differences](#key-differences-from-naive--draft-implementations) · [Quickstart](#quickstart) · [Execution flow](#request-execution-flow) · [Architecture](#core-system-architecture) · [Live API notes](#what-live-qloo-actually-returns) · [Configuration](#configuration) · [Testing](#testing) · [Limits](#known-limits)

---

## Overview

Generic LLMs frequently hallucinate venue features or fall back on stereotypes (e.g. "any Italian restaurant works for a family dinner"). When the group includes a senior with low-sodium, soft-texture and low-noise needs alongside a 24-year-old with a synthwave and indie-film taste profile, **correctness is not optional**.

SocioSense separates cultural inference from factual verification:

* **Qloo API v2** handles taste intelligence — affinity across music, film, books and dining.
* **A deterministic pipeline** checks venue hard constraints. The optional LLM is restricted to writing prose: it never selects venues, scores affinity, or infers menu and accessibility attributes.

---

## Key differences from naive / draft implementations

| Area | Naive / draft blueprint | SocioSense |
| --- | --- | --- |
| **Endpoint** | `POST https://api.qloo.com/v1/insights` with a JSON body | `GET /v2/insights` with explicit query parameters (`filter.type`, `signal.interests.entities`/`.tags`, `take`, …) |
| **Parsing** | Expected a flat `recommendations` array | Reads `results.entities[]`, maps `query.affinity`, flattens explainability, normalises upper-case IDs |
| **Search** | One `type` filter, trusted the fuzzy top hit | Explicit `types` + domain validation, plus a lexical name guard (`matching.py`) that rejects false matches |
| **Place filtering** | `urn:entity:place` (returns malls, streets, landmarks) | `filter.tags=urn:tag:category:place:restaurant` with `intersection`, plus a dining-shape check |
| **Attribution** | One flat seed list for the whole family | One insights call **per member**, so affinity stays attributable |
| **Failure mode** | Mock data with invented venues on any error | Fail-closed (`QlooError` / `QlooAuthError`); fixtures only via explicit `--demo` (`data_mode="fixture"`) |
| **Health data** | Validated menu attributes Qloo does not return | Dedicated `FactsProvider` with per-attribute provenance (`DECLARED` / `MEASURED`) |
| **Reliability** | No retries, timeouts or caching | Jittered exponential backoff, `Retry-After`, TTL cache, 47 tests |

---

## Quickstart

### 1. Zero-config demo (fictional fixtures)

```bash
python -m sociosense plan --profile examples/family.json \
  --facts examples/demo_facts.json --demo
```

### 2. Live API

```bash
cp .env.example .env          # then set QLOO_API_KEY
python -m sociosense doctor   # search + tags + insights health check
python -m sociosense plan --profile examples/family.json --limit 6
```

The hackathon key works **only** against `https://hackathon.api.qloo.com`; staging and production return `401`. Set that in `QLOO_BASE_URL`. `.env` is loaded automatically and is gitignored.

On Windows, use `py -3.14` if `python` is not on `PATH`, and set `PYTHONIOENCODING=utf-8` for live output.

---

## Request execution flow

```
profile.json
   │
   ├─ 1. resolve seeds      GET /v2/search, /v2/tags    weak matches rejected
   ├─ 2. resolve cuisine    GET /v2/tags                requirement -> filter tag
   ├─ 3. per-member taste   GET /v2/insights            affinity stays attributable
   ├─ 4. merge              union + missing-member floor
   ├─ 5. enrich             FactsProvider                DECLARED / MEASURED only
   ├─ 6. constrain          PASS / FAIL / UNKNOWN        never PASS without evidence
   └─ 7. rank               bridge + quality + coverage
```

Every report ends with the audit trace for that plan: resolved seed count, rejected weak matches, per-member calls, facts coverage and constraint totals.

---

## Core system architecture

### 1. Seed resolution: entities vs tags

* **Entities** — specific works, people or brands (*The Bear*, *Aesop*), resolved via `GET /v2/search` and sent as `signal.interests.entities`.
* **Tags** — genres, cuisines and moods, resolved via `GET /v2/tags` and sent as `signal.interests.tags`.

Searching `"Mediterranean cuisine"` as an *entity* returns a restaurant called *Mediterranean*; searching it as a *tag* returns `urn:tag:genre:place:restaurant:mediterranean`, which is an actual cultural signal. `requirements.cuisine` is resolved the same way and applied as a hard `filter.tags` constraint.

### 2. Scoring and group fairness

A venue liked by three people and disliked by one should not beat a consensus choice:

$$\text{bridge} = (1 - f)\cdot\text{mean}(\text{affinity}) + f\cdot\min(\text{affinity}), \quad f = 0.6$$

$$\text{final} = 0.70\cdot\text{bridge} + 0.20\cdot\text{quality} + 0.10\cdot\text{verification coverage}$$

Weights live in `RankingWeights` and must sum to 1.0. If a member's lookup fails, they count as **zero**, not as "no preference" — the plan degrades honestly.

### 3. Factual provenance

Qloo predicts **cultural taste**, not nutrition or room acoustics. Dietary and accessibility claims carry provenance:

```python
Claim(value="low", source="printed low-sodium menu",
      method=ClaimMethod.DECLARED, confidence=0.9)
```

* Ordering: `INFERRED < CROWDSOURCED < DECLARED < MEASURED`.
* Only `DECLARED` or `MEASURED` at confidence ≥ 0.6 can satisfy a hard constraint.
* Without `--facts`, `NullFactsProvider` reports every such constraint as `UNKNOWN` instead of guessing.
* Records match on Qloo `entity_id` first, then exact venue name, so fixture facts do not accidentally validate live venues.

---

## What live Qloo actually returns

Verified against the hackathon host. These behaviours are encoded in the client and covered by shape tests.

| Observed | Handling |
| --- | --- |
| Hackathon keys `401` on staging/production | `QLOO_BASE_URL` documented; `doctor` prints the endpoint used |
| `/v2/tags` without `filter.query`/`filter.tags`/`filter.popularity.*` → `400` | Always send `filter.query`; read `results.tags` |
| Place insights need a tag or query filter | Dining tag filter on by default; landmarks and streets are dropped |
| Explainability is `{"signal.interests.entities": [{entity_id, score}]}` with upper-case IDs | Flattened to `{lowercase_id: score}` before matching |
| `filter.external.exists=resy` returns 0 rows in Bengaluru | Not used for ranking; booking status stays `UNKNOWN` |
| No menu, nutrition or noise fields exist | Health constraints stay `UNKNOWN` unless `--facts` supplies verified claims |
| Search returns the wrong entity for genre strings | Name guard rejects it; the trace records `rejected_for_weak_name_match` |

Run `doctor` before debugging a plan — it separates credential, tag and insights failures instead of collapsing them into "no results".

---

## Configuration

| Variable | Default | Description |
| --- | --- | --- |
| `QLOO_API_KEY` | *none* | Required for live runs |
| `QLOO_BASE_URL` | `https://api.qloo.com` | Use `https://hackathon.api.qloo.com` for hackathon keys |
| `QLOO_TIMEOUT_S` | `10` | Per-request timeout |
| `QLOO_MAX_RETRIES` | `3` | Retries on 429 / 5xx |
| `QLOO_CACHE_TTL_S` | `900` | In-process cache TTL |
| `SOCIO_RADIUS_M` | `8000` | Search radius in metres (Qloo max 800 000) |
| `SOCIO_MIN_NAME_MATCH` | `0.5` | Minimum lexical similarity to accept a search/tag hit |
| `SOCIO_DINING_TAGS` | `urn:tag:category:place:restaurant` | Comma-separated hard tag filters for place queries |
| `SOCIO_REQUIRE_VERIFICATION` | `true` | When true, unverified venues can never be `READY` |
| `OPENAI_API_KEY` | *none* | Only for `--llm` prose polishing |

---

## Codebase structure

```text
sociosense/
├── agent.py          # Pipeline orchestration and trace generation
├── matching.py       # Lexical name guard (blocks false seed matches)
├── constraints.py    # PASS / FAIL / UNKNOWN engine
├── scoring.py        # Bridge, quality and coverage metrics
├── synth.py          # Markdown, JSON, guarded LLM phrasing
├── transport.py      # Retries, jitter, Retry-After
├── demo.py           # Synthetic offline taste graph (--demo only)
├── clients/
│   ├── qloo.py       # v2 search, tags, insights
│   └── facts.py      # Provenance-bearing venue attributes
examples/             # Family profile, demo graph, sample facts
tests/                # 47 unit tests
```

---

## Testing

```bash
python -m unittest discover -s tests -t . -v
```

47 unit tests cover: v2 request shape and auth header, 401/5xx behaviour (no fabrication), 429 retry with `Retry-After`, caching, nested tag results, tag-seed insight parameters, dining-shape filtering, explainability flattening, name-match rejection, `UNKNOWN` vs `PASS`, inferred claims rejected as evidence, fairness weighting, missing-member handling, the full fixture pipeline, and the LLM outline guard.

Live response shapes are represented by hand-written payloads, not committed API dumps.

---

## Privacy and safety design

* **No PII leaves the process.** Member labels and ages stay local. Qloo receives entity UUIDs and the free-text query needed for resolution.
* **Deterministic execution.** No free-roaming agent loop; every step is typed and audited.
* **Explicit trace.** Each plan reports tool calls, seed outcomes, facts coverage and constraint totals.
* **No fabricated safety claims.** Unknown is shown as unknown, and `--llm` output is validated against a closed index list.
* **Not medical advice.** Confirm dietary and accessibility needs directly with the venue.

---

## Known limits

* Affinity is a relative taste estimate, not quality or safety. It is labelled as such in every report.
* Ambiguous tastes still need a specific title or artist; unresolved seeds are listed rather than silently dropped.
* Qloo exposes cuisine only as free-text `cuisine_description`, shown as a note rather than a verified attribute.
* Cost is `seeds + members + 1` requests per plan; the cache helps repeated plans in one area.
* The health layer is only as good as the facts source. Default behaviour is honest emptiness, not competence.
* No license is granted yet; add one before redistributing.
