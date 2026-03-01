"""
FOA Ingestion + Semantic Tagging Pipeline
------------------------------------------
Fetches a single Funding Opportunity Announcement from Grants.gov or NSF,
applies rule-based keyword tagging against a controlled ontology, and writes
foa.json + foa.csv to the specified output directory.

Usage:
    python main.py --url "<URL>" [--out_dir ./out]
"""
import argparse
import csv
import json
import os
import re
import sys
import uuid
from datetime import datetime, timezone
from urllib.parse import urlparse, parse_qs

import requests
from bs4 import BeautifulSoup
from dateutil import parser as dateparser

# ── Constants ─────────────────────────────────────────────────────────────────

TIMEOUT = 30

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (X11; Linux x86_64; rv:120.0) "
        "Gecko/20100101 Firefox/120.0"
    ),
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "en-US,en;q=0.5",
}

# ── Ontology for rule-based tagging ───────────────────────────────────────────

ONTOLOGY = {
    "domains": {
        "Artificial Intelligence & Machine Learning": [
            "machine learning", "deep learning", "artificial intelligence",
            "neural network", "natural language processing", "nlp", "computer vision",
            "reinforcement learning", "large language model", "llm", "generative ai",
            "foundation model", "transformer", "autonomous systems",
        ],
        "Biomedical & Life Sciences": [
            "genomics", "proteomics", "bioinformatics", "drug discovery",
            "clinical trial", "therapeutics", "biomarker", "precision medicine",
            "vaccine", "immunology", "oncology", "neuroscience", "microbiome",
            "crispr", "gene editing", "cell biology", "molecular biology",
        ],
        "Public Health & Epidemiology": [
            "public health", "epidemiology", "health disparities", "mental health",
            "substance use", "opioid", "prevention", "intervention", "community health",
            "social determinants", "health equity", "surveillance", "infectious disease",
        ],
        "Climate & Environment": [
            "climate change", "climate resilience", "sustainability", "renewable energy",
            "carbon", "greenhouse gas", "ecosystem", "biodiversity", "conservation",
            "environmental justice", "clean energy", "solar", "wind energy",
            "water quality", "pollution", "adaptation", "mitigation",
        ],
        "Cybersecurity & Data Science": [
            "cybersecurity", "data science", "privacy", "encryption", "network security",
            "threat detection", "data analytics", "big data", "cloud computing",
            "distributed systems", "blockchain", "quantum computing",
        ],
        "Social & Behavioral Sciences": [
            "behavioral science", "psychology", "sociology", "economics",
            "political science", "education research", "workforce", "labor",
            "poverty", "inequality", "housing", "criminal justice",
        ],
        "Engineering & Physical Sciences": [
            "materials science", "nanotechnology", "robotics", "photonics",
            "semiconductor", "advanced manufacturing", "aerospace", "civil engineering",
            "structural engineering", "chemistry", "physics", "optics",
        ],
        "Agriculture & Food Systems": [
            "agriculture", "food security", "crop", "livestock", "aquaculture",
            "food safety", "nutrition", "rural development", "soil", "irrigation",
        ],
        "Education & Workforce Development": [
            "stem education", "higher education", "k-12", "workforce development",
            "training", "apprenticeship", "broadening participation",
            "diversity in stem", "graduate fellowship", "undergraduate research",
        ],
    },
    "methods": {
        "Randomized Controlled Trial": [
            "randomized controlled trial", "rct", "randomized trial",
            "placebo-controlled", "double-blind",
        ],
        "Computational & Modeling": [
            "computational modeling", "simulation", "agent-based model",
            "finite element", "numerical methods", "mathematical model",
            "statistical model", "predictive model",
        ],
        "Qualitative Research": [
            "qualitative", "ethnography", "focus group", "interview-based",
            "grounded theory", "case study", "participatory research",
        ],
        "Survey & Epidemiological Methods": [
            "survey", "longitudinal study", "cohort study", "cross-sectional",
            "meta-analysis", "systematic review", "registry",
        ],
        "Laboratory & Experimental": [
            "laboratory", "in vitro", "in vivo", "preclinical", "bench research",
            "experimental", "prototype", "proof of concept",
        ],
        "Data & Informatics": [
            "data integration", "data mining", "natural language processing",
            "image analysis", "remote sensing", "electronic health record",
            "ehr", "informatics",
        ],
        "Community-Based Methods": [
            "community-based participatory", "cbpr", "community engaged",
            "implementation science", "dissemination",
        ],
    },
    "populations": {
        "Underserved & Minority Communities": [
            "underserved", "underrepresented", "minority", "health disparities",
            "low-income", "disadvantaged", "marginalized", "equity",
        ],
        "Rural Populations": [
            "rural", "frontier", "remote communities", "agricultural communities",
        ],
        "Veterans & Military": [
            "veteran", "military", "service member", "armed forces",
            "department of defense",
        ],
        "Children & Youth": [
            "children", "youth", "adolescent", "pediatric", "k-12", "school-age",
        ],
        "Elderly & Aging": [
            "elderly", "older adult", "aging", "geriatric", "alzheimer",
            "dementia", "senior",
        ],
        "Women & Gender": [
            "women", "gender", "maternal", "prenatal", "reproductive health",
            "sex differences",
        ],
        "Small Businesses": [
            "small business", "sbir", "sttr", "startup", "entrepreneur", "small firm",
        ],
        "Indigenous & Tribal": [
            "indigenous", "tribal", "native american", "alaska native",
            "american indian", "first nations",
        ],
        "Persons with Disabilities": [
            "disability", "disabilities", "accessibility", "rehabilitation",
            "assistive technology",
        ],
    },
    "themes": {
        "Basic & Fundamental Research": [
            "basic research", "fundamental research", "discovery research",
            "exploratory research", "hypothesis-driven",
        ],
        "Translational Research": [
            "translational", "bench to bedside", "clinical translation",
            "technology transfer", "commercialization",
        ],
        "Innovation & Technology Development": [
            "innovation", "technology development", "emerging technology",
            "proof of concept", "prototype", "next-generation",
        ],
        "Capacity Building": [
            "capacity building", "infrastructure", "core facility",
            "institutional development", "resource development",
        ],
        "Training & Career Development": [
            "training", "career development", "fellowship", "mentorship",
            "postdoctoral", "early career", "graduate training",
        ],
        "Partnerships & Collaboration": [
            "partnership", "collaboration", "consortium", "multi-institutional",
            "public-private partnership", "industry collaboration",
        ],
        "Policy & Implementation": [
            "policy", "implementation", "dissemination", "scale-up",
            "evidence-based policy", "regulatory",
        ],
        "International & Global": [
            "international", "global health", "developing countries",
            "low and middle income", "lmic", "cross-border",
        ],
    },
}

# ── Text & date helpers ───────────────────────────────────────────────────────

def clean_text(text) -> str:
    """Strip HTML tags, decode common entities, normalize whitespace."""
    if not text:
        return ""
    t = re.sub(r"<[^>]+>", " ", str(text))
    t = (t.replace("&amp;", "&").replace("&lt;", "<").replace("&gt;", ">")
          .replace("&nbsp;", " ").replace("&quot;", '"').replace("&#39;", "'"))
    return re.sub(r"\s+", " ", t).strip()


def normalize_date(raw) -> str:
    """Parse any date string into ISO YYYY-MM-DD; return empty string on failure."""
    if not raw:
        return ""
    if isinstance(raw, (int, float)):
        try:
            return datetime.fromtimestamp(raw / 1000, tz=timezone.utc).strftime("%Y-%m-%d")
        except Exception:
            return ""
    s = str(raw).strip()
    if not s:
        return ""
    try:
        return str(dateparser.parse(s, fuzzy=True).date())
    except Exception:
        return s


# Strings the Grants.gov API returns when an award field is absent
_AWARD_NULLS = {"none", "n/a", "na", "0", "null", "-"}


def parse_award(v) -> str | None:
    """Normalize award value to a plain string or None."""
    if v is None:
        return None
    if isinstance(v, (int, float)):
        return str(int(v)) if v else None
    s = str(v).strip()
    return None if (not s or s.lower() in _AWARD_NULLS) else s


def _first(*args):
    """Return the first truthy argument, or empty string."""
    for a in args:
        if a:
            return a
    return ""

# ── Grants.gov ingestion ───────────────────────────────────────────────────────

def ingest_grants_gov(url: str) -> dict:
    # Extract numeric opportunity ID — query string first, then path
    _qs = parse_qs(urlparse(url).query)
    _id = (
        _qs.get("oppId", [None])[0]
        or _qs.get("opportunityId", [None])[0]
        or _qs.get("id", [None])[0]
    )
    if not _id:
        match = re.search(r"/(\d+)(?:[/?#]|$)", url)
        if not match:
            raise ValueError(f"Could not extract opportunity ID from URL: {url}")
        _id = match.group(1)
    opp_id = _id  # kept as string; cast to int only at the API payload site

    # POST to the Grants.gov public search API (v1).
    # Payload: {"opportunityId": <int>}  Response envelope: {"data": {"synopsis": {...}, ...}}
    api_url = "https://api.grants.gov/v1/api/fetchOpportunity"
    payload = {"opportunityId": int(opp_id)}

    resp = requests.post(
        api_url,
        headers={**HEADERS, "Content-Type": "application/json", "Accept": "application/json"},
        json=payload,
        timeout=TIMEOUT,
    )
    resp.raise_for_status()
    data = resp.json()

    # Unwrap response envelope
    d = data.get("data", {})
    synopsis = d.get("synopsis", {})

    return {
        "foa_id":      d.get("opportunityNumber") or opp_id,
        "title":       clean_text(d.get("opportunityTitle")),
        "agency":      clean_text(synopsis.get("agencyName") or d.get("owningAgencyCode")),
        "open_date":   normalize_date(synopsis.get("postingDateStr")),
        "close_date":  normalize_date(synopsis.get("responseDateStr")),
        "eligibility": clean_text(", ".join(
                           t.get("description", "") for t in (synopsis.get("applicantTypes") or [])
                       )),
        "description": clean_text(synopsis.get("synopsisDesc")),
        "award_min":   parse_award(synopsis.get("awardFloor")),
        "award_max":   parse_award(synopsis.get("awardCeiling")),
        "source_url":  url,
        "source":      "Grants.gov",
        "ingested_at": datetime.now(tz=timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
    }

# ── NSF ingestion ──────────────────────────────────────────────────────────────

def _ingest_nsf_award_api(awd_id: str, url: str) -> dict:
    """
    Fetch a funded NSF award via the public awards REST API.
    Used for nsf.gov/awardsearch URLs where the page is an Ember SPA
    and static scraping yields nothing.
    """
    print(f"[nsf] Awards API → id={awd_id}")
    api_url = f"https://api.nsf.gov/services/v1/awards/{awd_id}.json"
    resp = requests.get(api_url, headers=HEADERS, timeout=TIMEOUT)
    resp.raise_for_status()
    d = resp.json().get("response", {}).get("award", [{}])[0]

    if not d:
        raise ValueError(f"NSF Awards API returned no data for award {awd_id}")

    return {
        "foa_id":      d.get("id") or awd_id,
        "title":       clean_text(d.get("title", "")),
        "agency":      "National Science Foundation (NSF)",
        "open_date":   normalize_date(d.get("startDate")),
        "close_date":  normalize_date(d.get("expDate")),
        "eligibility": "",
        "description": clean_text(d.get("abstractText", "")),
        "award_min":   None,
        "award_max":   parse_award(d.get("estimatedTotalAmt")),
        "source_url":  url,
        "source":      "NSF",
        "ingested_at": datetime.now(tz=timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
    }


def _ingest_nsf_program_page(url: str) -> dict:
    """Ingest NSF program/solicitation page via HTML scraping."""
    print(f"[nsf] Fetching program page: {url}")
    # Smart redirect: follow only while staying on *.nsf.gov
    resp = requests.get(url, headers=HEADERS, timeout=TIMEOUT, allow_redirects=False)
    for _ in range(5):
        if resp.status_code not in (301, 302, 303, 307, 308):
            break
        loc = resp.headers.get("Location", "")
        if not loc or "nsf.gov" not in (urlparse(loc).hostname or ""):
            break
        resp = requests.get(loc, headers=HEADERS, timeout=TIMEOUT, allow_redirects=False)
    if resp.status_code == 404:
        alt = (url.replace("new.nsf.gov", "www.nsf.gov") if "new.nsf.gov" in url
               else url.replace("www.nsf.gov", "new.nsf.gov"))
        if alt != url:
            print(f"[nsf] 404 on original, trying: {alt}")
            alt_resp = requests.get(alt, headers=HEADERS, timeout=TIMEOUT)
            if alt_resp.status_code == 200:
                resp = alt_resp
    resp.raise_for_status()
    soup = BeautifulSoup(resp.text, "lxml")
    text = soup.get_text(separator=" ", strip=True)

    # ── NSF solicitation / program number ──
    qs = parse_qs(urlparse(url).query)
    nsf_id = qs.get("pims_id", [None])[0] or qs.get("programid", [None])[0]
    solnum = re.search(r"\b(NSF[-\s]?\d{2}[-\s]\d{3,})\b", text, re.IGNORECASE)
    foa_id = solnum.group(1).replace(" ", "-") if solnum else (nsf_id or f"NSF-{uuid.uuid4().hex[:6].upper()}")

    # ── __NEXT_DATA__ (new.nsf.gov is a Next.js SPA) ──
    nxt = re.search(r'<script[^>]+id=["\']__NEXT_DATA__["\'][^>]*>(.*?)</script>', resp.text, re.DOTALL)
    if nxt:
        try:
            pp = json.loads(nxt.group(1)).get("props", {}).get("pageProps", {})
            opp = (pp.get("opportunity") or pp.get("program") or pp.get("solicitation")
                   or pp.get("programDetail") or pp.get("data") or {})
            if opp:
                nxt_title = _first(opp.get("title"), opp.get("programTitle"), opp.get("name"))
                if not nxt_title:
                    m = re.search(r'"title"\s*:\s*"([^"]{5,})"', json.dumps(opp))
                    if m:
                        nxt_title = m.group(1)
                deadlines = opp.get("deadlines") or opp.get("dates") or []
                if isinstance(deadlines, list) and deadlines:
                    ds = sorted(dl.get("date") or dl.get("dueDate") or "" for dl in deadlines
                                if dl.get("date") or dl.get("dueDate"))
                    nxt_open  = ds[0]  if len(ds) > 1 else ""
                    nxt_close = ds[-1] if ds else ""
                else:
                    nxt_close = _first(opp.get("closeDate"), opp.get("dueDate"), opp.get("endDate"))
                    nxt_open  = _first(opp.get("openDate"), opp.get("postDate"), opp.get("startDate"))
                nxt_desc = _first(opp.get("description"), opp.get("synopsis"),
                                  opp.get("overview"), opp.get("programDescription"))
                nxt_elig = _first(opp.get("eligibility"), opp.get("eligibilityDesc"), opp.get("whoCanApply"))
                if nxt_title or nxt_desc:
                    print("[nsf] ✓ __NEXT_DATA__")
                    return {
                        "foa_id":      clean_text(_first(opp.get("programNumber"), opp.get("solicitationNumber"), opp.get("id"), foa_id)),
                        "title":       clean_text(nxt_title),
                        "agency":      "National Science Foundation (NSF)",
                        "open_date":   normalize_date(nxt_open),
                        "close_date":  normalize_date(nxt_close),
                        "eligibility": clean_text(nxt_elig),
                        "description": clean_text(nxt_desc),
                        "award_min":   parse_award(opp.get("awardFloor") or opp.get("minAward")),
                        "award_max":   parse_award(opp.get("awardCeiling") or opp.get("maxAward")),
                        "source_url":  url,
                        "source":      "NSF",
                        "ingested_at": datetime.now(tz=timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
                    }
        except Exception as e:
            print(f"[nsf] __NEXT_DATA__ parse error: {e}")

    # ── JSON-LD ──
    for script in soup.find_all("script", type="application/ld+json"):
        try:
            ld = json.loads(script.string or "")
            ld_title = clean_text(ld.get("name") or ld.get("headline") or "")
            ld_desc  = clean_text(ld.get("description") or "")
            if ld_title or ld_desc:
                print("[nsf] ✓ JSON-LD")
                return {
                    "foa_id":      foa_id,
                    "title":       ld_title,
                    "agency":      "National Science Foundation (NSF)",
                    "open_date":   normalize_date(ld.get("startDate", "")),
                    "close_date":  normalize_date(ld.get("endDate", "")),
                    "eligibility": "",
                    "description": ld_desc,
                    "award_min":   None,
                    "award_max":   None,
                    "source_url":  url,
                    "source":      "NSF",
                    "ingested_at": datetime.now(tz=timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
                }
        except Exception:
            pass

    # ── Title ──
    title = ""
    for sel in ["h1.page-title", "h1#page-title", ".program-title", "h1"]:
        el = soup.select_one(sel)
        if el:
            title = clean_text(el.get_text())
            break
    if not title:
        for meta in [soup.find("meta", property="og:title"),
                     soup.find("meta", attrs={"name": "title"})]:
            if meta and meta.get("content"):
                title = clean_text(meta["content"])
                break

    # ── Dates ──
    date_candidates = []
    for node in soup.find_all(string=re.compile(
        r"(due date|deadline|submission|open|close|posted|expir)", re.IGNORECASE
    )):
        ctx = str(node)
        if node.parent:
            ctx += " " + node.parent.get_text(separator=" ", strip=True)
        date_candidates += re.findall(
            r"\b(\w+\.?\s+\d{1,2},?\s+\d{4}|\d{1,2}/\d{1,2}/\d{4}|\d{4}-\d{2}-\d{2})\b",
            ctx
        )
    parsed_dates = []
    for d in date_candidates:
        try:
            parsed_dates.append(dateparser.parse(d, fuzzy=True).date())
        except Exception:
            pass
    parsed_dates.sort()
    open_date  = str(parsed_dates[0])  if len(parsed_dates) > 1 else ""
    close_date = str(parsed_dates[-1]) if parsed_dates else ""

    # ── Award amounts ──
    award_floor, award_ceiling = None, None
    award_match = re.search(
        r"\baward\b.{0,200}?\$[\d,]+(?:\.\d+)?(?:\s*[Mm]illion)?",
        text, re.IGNORECASE | re.DOTALL
    )
    if award_match:
        amounts = []
        for hit in re.findall(r"\$[\d,]+(?:\.\d+)?(?:\s*[Mm]illion)?", award_match.group(0)):
            num = re.sub(r"[,$]", "", hit).strip()
            try:
                val = float(num) * (1_000_000 if "million" in hit.lower() else 1)
                amounts.append(int(val))
            except Exception:
                pass
        if amounts:
            amounts.sort()
            award_floor   = str(amounts[0])  if len(amounts) > 1 else None
            award_ceiling = str(amounts[-1])

    # ── Eligibility ──
    eligibility = ""
    for heading in soup.find_all(["h2", "h3", "h4", "strong", "b"]):
        if re.search(r"elig", heading.get_text(), re.IGNORECASE):
            parts = []
            for sib in heading.find_next_siblings():
                if sib.name in ("h2", "h3", "h4"):
                    break
                parts.append(sib.get_text(separator=" ", strip=True))
            eligibility = clean_text(" ".join(parts))[:2000]
            break

    # ── Description ──
    description = ""
    for heading in soup.find_all(["h2", "h3", "h4"]):
        if any(kw in heading.get_text(strip=True).lower()
               for kw in ["overview", "introduction", "summary", "synopsis",
                           "abstract", "program description"]):
            parts = []
            for sib in heading.find_next_siblings():
                if sib.name in ("h2", "h3", "h4"):
                    break
                parts.append(sib.get_text(separator=" ", strip=True))
            description = clean_text(" ".join(parts))[:5000]
            break
    if not description:
        for meta in [soup.find("meta", property="og:description"),
                     soup.find("meta", attrs={"name": "description"})]:
            if meta and meta.get("content"):
                description = clean_text(meta["content"])
                break
    if not description:
        paras = [p.get_text(separator=" ", strip=True) for p in soup.find_all("p")]
        if paras:
            description = clean_text(max(paras, key=len))[:5000]

    return {
        "foa_id":      foa_id,
        "title":       title,
        "agency":      "National Science Foundation (NSF)",
        "open_date":   open_date,
        "close_date":  close_date,
        "eligibility": eligibility,
        "description": description,
        "award_min":   award_floor,
        "award_max":   award_ceiling,
        "source_url":  url,
        "source":      "NSF",
        "ingested_at": datetime.now(tz=timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
    }


def ingest_nsf(url: str) -> dict:
    """
    Route NSF URL to the correct ingestion strategy:
      - awardsearch / AWD_ID  → NSF public awards REST API (Ember SPA, not scrapable)
      - program / solicitation → HTML scraper
    """
    qs = parse_qs(urlparse(url).query)
    awd_id = qs.get("AWD_ID", [None])[0] or qs.get("awd_id", [None])[0]

    if awd_id:
        return _ingest_nsf_award_api(awd_id, url)
    return _ingest_nsf_program_page(url)

# ── Source router ──────────────────────────────────────────────────────────────

def ingest(url: str) -> dict:
    hostname = urlparse(url).hostname or ""
    if "grants.gov" in hostname:
        return ingest_grants_gov(url)
    elif "nsf.gov" in hostname:
        return ingest_nsf(url)
    else:
        raise ValueError(f"Unsupported source URL: {url}\nSupported: grants.gov, nsf.gov")

# ── Rule-based tagger ──────────────────────────────────────────────────────────

def tag(foa: dict) -> dict:
    """Match keywords against title + description + eligibility, return matched ontology tags."""
    corpus = " ".join([
        foa.get("title", "") or "",
        foa.get("description", "") or "",
        foa.get("eligibility", "") or "",
    ]).lower()

    return {
        category: [
            label
            for label, keywords in concepts.items()
            if any(kw in corpus for kw in keywords)
        ]
        for category, concepts in ONTOLOGY.items()
    }

# ── Export ─────────────────────────────────────────────────────────────────────

def export(foa: dict, out_dir: str) -> None:
    os.makedirs(out_dir, exist_ok=True)

    # JSON
    json_path = os.path.join(out_dir, "foa.json")
    with open(json_path, "w", encoding="utf-8") as f:
        json.dump(foa, f, indent=2, ensure_ascii=False)
    print(f"✓ JSON → {json_path}")

    # CSV — tags split into per-category columns (pipe-separated values)
    csv_path = os.path.join(out_dir, "foa.csv")
    tags = foa.get("tags", {})
    row = {k: v for k, v in foa.items() if k != "tags"}
    for cat, vals in tags.items():
        row[f"tags_{cat}"] = " | ".join(vals)

    base_fields = [
        "foa_id", "title", "agency", "source",
        "open_date", "close_date", "eligibility", "description",
        "award_min", "award_max", "source_url", "ingested_at",
        "tags_domains", "tags_methods", "tags_populations", "tags_themes",
    ]
    extra = [k for k in row if k not in base_fields]

    with open(csv_path, "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=base_fields + extra, extrasaction="ignore")
        writer.writeheader()
        writer.writerow(row)
    print(f"✓ CSV  → {csv_path}")

# ── Main ───────────────────────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(description="FOA Ingestion + Semantic Tagging Pipeline")
    parser.add_argument("--url",     required=True,  help="URL of the FOA (grants.gov or nsf.gov)")
    parser.add_argument("--out_dir", default="./out", help="Output directory for foa.json and foa.csv")
    args = parser.parse_args()

    print(f"⟳ Ingesting: {args.url}")
    foa = ingest(args.url)

    print("⟳ Applying semantic tags...")
    foa["tags"] = tag(foa)

    export(foa, args.out_dir)
    print("\nDone. FOA ID:", foa["foa_id"])

if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        print("\n[pipeline] Interrupted.")
        sys.exit(1)
    except Exception as e:
        print(f"\n[pipeline] ERROR: {e}")
        sys.exit(1)