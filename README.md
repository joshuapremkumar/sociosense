# SocioSense

A family outing planner where **Qloo decides what is culturally relevant** and a
deterministic tool pipeline decides **what is actually safe and verifiable**.
The LLM, if enabled, is allowed to write prose — never to pick a venue or invent
an attribute.

Runs on Python 3.10+ with no third-party dependencies.

```bash
# 1. zero-config sanity check of the demo pipeline (venues are fictional)
python -m sociosense plan --profile examples/family.json \
  --facts examples/demo_facts.json --demo

# 2. live credentials: copy .env.example to .env and fill in the key
python -m sociosense doctor                     # search + tags + insights
python -m sociosense plan --profile examples/family.json --limit 6
```

`.env` is loaded automatically (real environment variables win). The file is
gitignored; only `.env.example` is committed.

---

## What changed from the draft blueprint

The blueprint was a good product idea with an API layer that would not have
worked against Qloo, plus a safety filter that validated fields Qloo does not
return. Concretely:

| Draft | Problem | Here |
| --- | --- | --- |
| `POST https://api.qloo.com/v1/insights` with a JSON body | Qloo's Insights API is `GET /v2/insights` with query parameters. There is no `/v1`. | `GET /v2/insights` with `filter.type`, `signal.interests.entities`/`.tags`, `filter.location.query`, `filter.location.radius`, `take`, `feature.explainability` |
| `response.json()["recommendations"]` | The real shape is `results.entities[]`, with affinity under `query.affinity` and explainability under `query.explainability["signal.interests.entities"]`. | parsed and normalised in `clients/qloo.py` (nested arrays, upper-case IDs lower-cased) |
| `search_entity(..., type=...)` | The search parameter is `types` and takes `urn:entity:*` values. | `types` + domain enum, validated |
| Trust the first search hit | Search is fuzzy: `"French New Wave"` returns *The French Connection*, `"Synthwave"` returns *Synthwave Goose*. Silently accepting these poisons every downstream score. | lexical name-match guard (`matching.py`); weak matches are reported as unresolved, not used |
| `filter.type=urn:entity:place` for restaurants | Returns streets, malls and landmarks (`Bangalore`, `Church street`, `PRESTIGE SHANTINIKETAN`). | `filter.tags=urn:tag:category:place:restaurant` + `operator.filter.tags=intersection`, plus a dining-shape check on parsed rows |
| On any error, return `DEMO_KEY` mocks with invented restaurants, addresses and 0.94 "affinity" | Fabricated data presented as Qloo-validated. This is the single most dangerous line in the draft. | fail-closed: `QlooError` / `QlooAuthError`. The synthetic graph is only reachable via an explicit `--demo` flag and is labelled `data_mode="fixture"` in every output |
| `menu_attributes: ["low-sodium option", "soft texture"]` fed into a "health filter" | Qloo does not carry menu nutrition, texture, or room acoustics. Against live data the filter would reject everything; against the mocks it "passed" because the mocks were written to pass. | a separate `FactsProvider` with per-attribute provenance; nothing unverified is ever reported as satisfied |
| One flat list of seeds for the whole family | Destroys attribution: you cannot say which member a venue suits, and a venue loved by three people and hated by one can outrank a consensus choice. | one insights call **per member**, then a worst-member-weighted bridge score |
| `requirements["low_sodium"]` | Never checked by the original filter (dead config). | mapped to a real constraint, and covered by tests |
| No retries, timeouts, caching or tests | 429s and timeouts surface as empty recommendation lists. | exponential backoff with jitter, `Retry-After`, TTL cache, 47 tests |
| "600M+ entities" | Not stated in Qloo's public docs; treat as marketing copy rather than a spec. | not repeated as a technical claim |

Two design decisions are deliberate and worth arguing about:

- **No free-roaming ReAct loop.** The steps are fixed and typed. An agent that
  *may* skip the accessibility check is not an agent with a safety property.
  The trace shows every tool call and its result.
- **Unknown is a first-class outcome.** A venue with no attribute data is shown
  under "Worth a phone call", never under "Ready to book". An inferred value
  (LLM, review text) can never satisfy a hard constraint.

---

## How a request flows

```
profile.json
   │
   ├─ 1. resolve seeds      GET /v2/search        (name -> entity_id, typed)
   │                        GET /v2/tags          (genre/cuisine -> tag_id)
   │       weak lexical matches are rejected, not guessed
   ├─ 2. resolve cuisine    GET /v2/tags          (requirement -> filter tag)
   ├─ 3. per-member taste   GET /v2/insights      (seeds -> places + affinity)
   │       one call per member, so affinity stays attributable
   ├─ 4. merge              union of results, lower-bound for missing members
   ├─ 5. enrich             FactsProvider -> Claim(value, source, method)
   ├─ 6. constrain          PASS / FAIL / UNKNOWN (never PASS without evidence)
   └─ 7. rank               bridge = (1-f)·mean + f·min, plus quality + coverage
```

The trace printed at the end of every report is the audit log for a single plan.

### Taste seeds: entities vs tags

A seed is either a concrete work/person/brand (`kind: "entity"`, resolved via
`/v2/search`) or a genre/cuisine/mood (`kind: "tag"`, resolved via `/v2/tags`).
Tags go out as `signal.interests.tags`; entities as
`signal.interests.entities`. This matters because searching for
`"Mediterranean cuisine"` as an *entity* returns a restaurant called
*Mediterranean*, while searching it as a *tag* returns
`urn:tag:genre:place:restaurant:mediterranean` — a real taste signal.

```json
{ "query": "mediterranean", "domain": "place", "kind": "tag" }
```

A profile can also state `requirements.cuisine`, which is resolved to a tag and
sent as a hard `filter.tags` constraint rather than a soft signal. Live check:
`cuisine: "mediterranean"` narrows Bengaluru results to venues whose Qloo
description actually mentions Mediterranean food.

### Scoring

- `bridge = (1 - fairness) * mean(affinity) + fairness * min(affinity)` with
  `fairness = 0.6`. A crowd-pleaser that one person dislikes loses to a
  consensus venue.
- `final = 0.70 * bridge + 0.20 * quality + 0.10 * verification_coverage`.
  Weights live in `RankingWeights` and must sum to 1.0.
- A member whose lookup failed is **not** dropped from the average; they count
  as zero, so the plan degrades honestly.

---

## Provenance model

Qloo answers *"will this group like this place?"*. It does not answer *"is the
soup low-sodium and is the room quiet enough for a hearing aid?"*. Those are
separate questions with separate sources, so they are separate objects:

```python
Claim(value="low", source="printed low-sodium menu",
      method=ClaimMethod.DECLARED, confidence=0.9)
```

`method` is one of `inferred < crowdsourced < declared < measured`. Only
`declared` and `measured` at confidence ≥ 0.6 can satisfy a hard constraint.
`JsonFactsProvider` reads records like `examples/demo_facts.json`, which is the
shape a venue accessibility sheet or dietitian-reviewed menu would take. With
`NullFactsProvider` (the default when `--facts` is omitted) every dietary or
accessibility constraint reports `UNKNOWN`.

This is the part that cannot be solved by prompting. A real deployment needs a
data partnership (venue sheets, accessibility audits, menu feeds) or manual
confirmation; the code makes that gap visible instead of papering over it.

Records are matched on `venue.entity_id` first, then exact venue name.
`examples/demo_facts.json` is keyed by fixture venue names, so pairing it with
a **live** plan matches nothing — the report says so explicitly instead of
pretending the file was checked.

---

## What live Qloo actually returns

Verified against the hackathon host with the checks in `doctor`. These are
behaviours the code now encodes; all fixture tests still pass unchanged.

| Observed | Consequence in code |
| --- | --- |
| Hackathon keys 401 on `staging`/`api.qloo.com` | `QLOO_BASE_URL` defaults to the hackathon host in `.env.example`; `doctor` prints the endpoint it used |
| `/v2/tags` requires one of `filter.query`, `filter.tags`, `filter.popularity.*` — a bare call is a 400 | always send `filter.query`; tags are read from `results.tags` |
| `/v2/insights` for places needs a tag/query/popularity filter | dining tag filter is the default, so plans never hit the 400 |
| Explainability is `{"signal.interests.entities": [{entity_id, score}]}` with upper-case IDs | flattened to `{lowercase_id: score}` before matching against search results |
| `filter.external.exists=resy` returns 0 rows in Bengaluru | not used for ranking; booking links stay `UNKNOWN` |
| No menu, nutrition or noise fields exist | health constraints are `UNKNOWN` unless `--facts` supplies verified claims, so "Ready to book" is legitimately empty on a facts-less live run |
| Search can return the wrong entity for a genre string | name guard rejects it and the trace records `rejected_for_weak_name_match` |

Run `doctor` first when a plan looks wrong — it separates credential, tag and
insights failures instead of collapsing them into "no results".

---

## Configuration

| Variable | Default | Notes |
| --- | --- | --- |
| `QLOO_API_KEY` | — | required outside `--demo` |
| `QLOO_BASE_URL` | `https://api.qloo.com` | **hackathon keys: `https://hackathon.api.qloo.com`** |
| `QLOO_TIMEOUT_S` | `10` | per request |
| `QLOO_MAX_RETRIES` | `3` | 429/5xx only |
| `QLOO_CACHE_TTL_S` | `900` | in-process |
| `SOCIO_RADIUS_M` | `8000` | metres, Qloo max 800 000 |
| `SOCIO_MIN_NAME_MATCH` | `0.5` | minimum lexical overlap for a search/tag match |
| `SOCIO_DINING_TAGS` | `urn:tag:category:place:restaurant` | comma-separated hard tags for place results |
| `SOCIO_REQUIRE_VERIFICATION` | `true` | `false` lets unverified venues into "ready" |
| `OPENAI_API_KEY` | — | only for `--llm` |

CLI: `python -m sociosense plan --help` and `python -m sociosense doctor --help`.

---

## Privacy

- Member labels and ages never leave the process. Requests carry Qloo entity
  UUIDs only; the only free-text sent is the taste query needed for resolution
  (`GET /v2/search?query=...`, `GET /v2/tags?filter.query=...`).
- No profiles are persisted; the cache holds API responses keyed by request
  parameters, not by person.
- `--json` returns the full structured plan for storage on your side. Decide a
  retention policy before shipping that.

---

## Tests

```bash
python -m unittest discover -s tests -t . -v
```

47 tests covering: v2 request shape and auth header, 401/5xx behaviour (no
fabrication), 429 retry with `Retry-After`, caching, nested tag results,
tag-seed insights parameters, dining-shape filtering, explainability flattening,
name-match rejection, `UNKNOWN` vs `PASS`, inferred claims rejected as evidence,
fairness weighting, missing-member handling, the full fixture pipeline, and the
LLM outline guard.

No live responses are committed as fixtures; the live-shape tests use
hand-written payloads that mirror the documented fields.

---

## Layout

```
sociosense/
  agent.py        orchestration + trace
  matching.py     lexical name-match guard
  constraints.py  PASS/FAIL/UNKNOWN
  scoring.py      bridge / quality / coverage
  synth.py        markdown, JSON, guarded LLM phrasing
  transport.py    retries, backoff, Retry-After
  clients/
    qloo.py       v2 search + tags + insights
    facts.py      provenance-bearing venue attributes
  demo.py         synthetic taste graph (explicit mode only)
examples/         family profile, demo graph, sample facts
tests/
```

## Known limits

- Affinity is a relative taste estimate, not a quality or safety score. It is
  labelled that way in every report.
- Search resolution takes the best typed match above the name-match threshold;
  genuinely ambiguous tastes still need a specific title or artist in the
  profile. Unresolved seeds are listed in the trace rather than silently dropped.
- Qloo returns no cuisine as a structured field, only a free-text
  `cuisine_description`; the report labels it as a note, not a verified attribute.
- Cost is `seeds + members + 1` requests per plan; the cache helps repeated
  plans in the same area.
- The health layer is only as good as the facts source. The default is honest
  emptiness, not competence.
- Not medical advice. Low-sodium and texture requirements vary clinically;
  confirm with the venue and, where relevant, a clinician.
