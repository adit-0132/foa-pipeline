# FOA Ingestion Pipeline

Fetches a single Funding Opportunity Announcement from Grants.gov or NSF,
applies rule-based semantic tagging against a controlled keyword ontology,
and writes `foa.json` and `foa.csv` to the specified output directory.

---

## Overview

Two federal funding sources are supported:

- **Grants.gov** — via the `api.grants.gov/v1/api/fetchOpportunity` JSON API
- **NSF** — via the public NSF Awards REST API (for awarded grants) or
  HTML/Next.js extraction (for program and solicitation pages)

After ingestion, records are tagged by scanning a lowercase corpus of
`title + description + eligibility` against a four-category ontology:
domains, methods, target populations, and research themes. Matching is
plain case-insensitive substring — no ML, no embeddings.

---

## Pipeline Architecture

```
CLI (--url, --out_dir)
  │
  ▼
ingest(url)                    ← dispatch by hostname
  │
  ├─ grants.gov  →  ingest_grants_gov()
  │                   POST /v1/api/fetchOpportunity
  │                   → normalized dict
  │
  └─ nsf.gov     →  ingest_nsf()
                      │
                      ├─ ?AWD_ID=  →  _ingest_nsf_award_api()
                      │               GET api.nsf.gov/awards/{id}.json
                      │
                      └─ program   →  _ingest_nsf_program_page()
                                       ├─ __NEXT_DATA__ JSON
                                       ├─ JSON-LD
                                       └─ static HTML scraping
  │
  ▼
tag(foa)                       ← keyword scan against ONTOLOGY
  │
  ▼
export(foa, out_dir)
  ├─ foa.json
  └─ foa.csv
```

---

## Module Breakdown

### `ingest_grants_gov(url)`

Extracts the opportunity ID from the URL — checks query string params
`oppId`, `opportunityId`, `id` first, then falls back to a digit pattern
in the path. POSTs to `api.grants.gov/v1/api/fetchOpportunity` with
`{"opportunityId": <int>}`.

The response envelope is `{"data": {"synopsis": {...}, ...}}`. `synopsis`
holds dates, eligibility types, and the description. `applicantTypes` is a
list of objects; the `description` fields are joined into the eligibility
string.

### `ingest_nsf(url)`

Router. Checks for `AWD_ID` (or `awd_id`) in the query string and
dispatches to `_ingest_nsf_award_api`. All other NSF URLs go to
`_ingest_nsf_program_page`.

### `_ingest_nsf_award_api(awd_id, url)`

Hits `api.nsf.gov/services/v1/awards/{id}.json`. Maps:
- `abstractText` → `description`
- `estimatedTotalAmt` → `award_max`
- `startDate` / `expDate` → `open_date` / `close_date`

Award records have no eligibility data (field is left empty).

### `_ingest_nsf_program_page(url)`

HTML scraper for NSF program and solicitation pages. Tries three strategies
in order, returning on the first that yields a title or description:

1. **`__NEXT_DATA__`** — `new.nsf.gov` is a Next.js SPA. The full data tree
   is embedded in `<script id="__NEXT_DATA__">`. Several key name variants
   are checked: `opportunity`, `program`, `solicitation`, `programDetail`, `data`.

2. **JSON-LD** — `<script type="application/ld+json">` structured data. Uses
   `name`/`headline` for title, `description` for body.

3. **Static HTML** — heading-based traversal: finds the first heading matching
   eligibility/overview keywords and collects following siblings. Falls back to
   `<meta>` tags and longest `<p>` for description. Dates are extracted via
   regex from heading-adjacent text. Award amounts are extracted from prose
   matching `\baward\b ... $X`.

Before scraping, redirects are followed manually — only while the redirect
target stays on `*.nsf.gov`. On 404, the alternate domain (`new.nsf.gov` ↔
`www.nsf.gov`) is tried once.

### `tag(foa)`

Builds `corpus = (title + description + eligibility).lower()`. Scans against
`ONTOLOGY` using `any(kw in corpus for kw in keywords)` per label. Returns:

```python
{
  "domains":     ["Artificial Intelligence & Machine Learning", ...],
  "methods":     ["Computational & Modeling", ...],
  "populations": ["Small Businesses", ...],
  "themes":      ["Innovation & Technology Development", ...]
}
```

### `export(foa, out_dir)`

Writes two files (always overwritten):

- **`foa.json`** — full nested dict including the `tags` sub-object
- **`foa.csv`** — flat row; `tags` is expanded to four pipe-separated columns:
  `tags_domains`, `tags_methods`, `tags_populations`, `tags_themes`

### Text / Date Helpers

| Function | Behavior |
|---|---|
| `clean_text(text)` | Strips HTML tags, decodes 6 common entities, normalizes whitespace |
| `normalize_date(raw)` | Handles epoch-ms integers, ISO strings, free-form via `dateutil`. Falls back to raw string on parse failure |
| `parse_award(v)` | int/float/string → plain string, `None` for missing, zero, or sentinel strings (`"none"`, `"n/a"`, `"null"`, etc.) |

---

## Output Schema

```
foa_id        opportunity number (string)
title         cleaned title text
agency        issuing agency name
source        "Grants.gov" or "NSF"
open_date     YYYY-MM-DD or empty
close_date    YYYY-MM-DD or empty
eligibility   cleaned eligibility text
description   cleaned description / abstract
award_min     string or null
award_max     string or null
source_url    original input URL
ingested_at   UTC timestamp (ISO 8601)
tags          {domains, methods, populations, themes} — JSON only
tags_domains       pipe-separated — CSV only
tags_methods       pipe-separated — CSV only
tags_populations   pipe-separated — CSV only
tags_themes        pipe-separated — CSV only
```

---

## Requirements

Python 3.10+

```
requests
python-dateutil
beautifulsoup4
lxml
```

```bash
pip install -r requirements.txt
```

---

## Usage

```bash
python main.py --url "<URL>" [--out_dir ./out]
```

| Argument | Required | Default | Description |
|---|---|---|---|
| `--url` | yes | — | FOA URL (grants.gov or nsf.gov) |
| `--out_dir` | no | `./out` | Output directory |

Output files are always overwritten. The directory is created if it does not exist.

---

## Examples

```bash
# Grants.gov opportunity page (path-style URL)
python main.py --url "https://www.grants.gov/search-results-detail/355964" --out_dir ./out

# Grants.gov opportunity page (query string ID)
python main.py --url "https://www.grants.gov/search-results-detail/355964?oppId=355964" --out_dir ./out

# NSF awarded grant (uses Awards REST API)
python main.py --url "https://www.nsf.gov/awardsearch/show-award?AWD_ID=2517085" --out_dir ./out

# NSF awarded grant (different award, AI/ML domain)
python main.py --url "https://www.nsf.gov/awardsearch/show-award?AWD_ID=2319592" --out_dir ./out
```

---

## Known Limitations

**Grants.gov**

- `api.grants.gov/v1/api/fetchOpportunity` has no documented versioning or
  SLA. If the endpoint changes schema, field extraction silently degrades.
- If the API returns HTTP 200 with an error body (e.g. `{"error": "..."}`),
  the pipeline produces empty fields without raising. There is no envelope
  validation beyond `raise_for_status()`.
- `applicantTypes` is the only source of eligibility data. If the field is
  absent, eligibility is empty.

**NSF program pages**

- `__NEXT_DATA__` field names have changed across NSF redesigns. The current
  extraction tries several key variants; an undocumented schema change could
  cause silent fallthrough to HTML scraping.
- Date extraction from static HTML is heuristic. Pages with multiple deadline
  tables (e.g. Letter of Intent + Full Proposal) may produce incorrect
  open/close pairs.
- Award amount extraction from prose text is fragile. Only simple patterns
  like `award ... $500,000` are matched.

**Tagging**

- Matching is pure substring. Short keywords (`"solar"`, `"crop"`, `"labor"`,
  `"survey"`, `"training"`) produce false positives on long description fields.
- Keywords are not anchored to word boundaries (exception: the award regex).
  `"networking"` matches `"network security"`, `"educational"` matches
  `"education research"`.

**General**

- Single URL only. No batch mode.
- `TIMEOUT = 30` applies to both connect and read. There is no separate
  connect timeout.
- No retry logic. A transient 5xx returns immediately as an error.
- `normalize_date` returns the raw string on parse failure — date columns may
  contain non-ISO values.
- Output files are always overwritten with no backup.

---

## Suggested Improvements

**Robustness**

- Separate connect and read timeouts: `timeout=(5, 30)`. The current flat 30s
  cannot distinguish a connection hang from a slow response.
- Validate the Grants.gov response envelope before field extraction. Check
  that `data.get("data")` is a non-empty dict and surface a clear error if not.
- Add one retry with exponential backoff on 429 and 5xx responses.

**Correctness**

- Move `ingested_at` out of the ingestors and into `main()`. Ingestors would
  return pure data dicts; the timestamp would be stamped once after the call.
  This makes ingestors independently testable and removes clock calls from
  inside extraction logic.
- Compile ONTOLOGY keywords as `re.compile` patterns with `\b` word boundaries
  at module load time. Eliminates false positives on short terms without
  changing the matching interface.

**Features**

- Batch mode: accept a newline-delimited file of URLs via `--url_file`, write
  multi-row CSV with append or overwrite flag.
- NIH Grants / DARPA / DoD CDMRP support: all have structured pages or APIs
  that fit the same ingestor pattern.
- Configurable output fields: a `--fields` argument or config file to control
  which columns appear in the CSV.
