"""The CV patterns Hal's CV checks share, and a CV matched against one job's advert.

`cvstudio.checks` reads its patterns (headings parsers know, cliches, weak openings, US spellings, contact
details) and the PDF readers from here. Against a job, `keywords` finds which of the advert's skills the CV
names, and `requirements` asks the model for the advert's requirements and the CV's evidence for each,
keeping only quotes that really are in the text. The PDF is read with pypdfium2 (`cvstudio/pdfread.py`),
so nothing here needs poppler.
"""
import re
from pathlib import Path

from . import ai, record
from .cvstudio import pdfread

# Section names parsers know; a heading outside these may not be recognised as a section at all.
STANDARD_HEADINGS = {"education", "experience", "work experience", "employment", "projects", "skills",
                     "technical skills", "summary", "profile", "additional", "achievements", "awards",
                     "leadership", "positions of responsibility", "interests", "publications", "volunteering",
                     "certifications", "research and projects", "research", "languages", "skills and interests",
                     "employment history", "work history", "education and qualifications"}
CLICHES = re.compile(r"\b(passionate|results[- ]driven|team player|hard[- ]working|go[- ]getter|self[- ]starter|"
                     r"motivated|dynamic|detail[- ]oriented|think outside the box|synergy|best of breed|innovative|"
                     r"excellent communication skills|proven track record|fast learner|leverag\w*|spearhead\w*|"
                     r"cutting[- ]edge|seamless\w*|guru|ninja|rockstar)\b", re.I)
DUTIES = re.compile(r"^(responsible for|duties (included|include)|tasks?:|in charge of)", re.I)
WEAK_VERBS = re.compile(r"^(helped|assisted|worked on|worked with|participated|was involved|involved in|"
                        r"exposed to|gained experience|utili[sz]ed|tasked with|contributed to)\b", re.I)
AMERICAN = re.compile(r"\b(optimiz\w*|analyz\w*|organiz\w*|prioritiz\w*|visualiz\w*|customiz\w*|summariz\w*|"
                      r"minimiz\w*|maximiz\w*|recogniz\w*|color\w*|behavior\w*|modeled|modeling|centers?|centered|"
                      r"defense|catalog)\b", re.I)
NUMBER = re.compile(r"\d")
EMAIL = re.compile(r"[\w.+-]+@[\w-]+\.[\w.]+")
PHONE = re.compile(r"(\+44\s?7\d{3}|\(?07\d{3}\)?)\s?\d{3}\s?\d{3}")
PRIVATE = re.compile(r"\b(date of birth|d\.o\.b|dob|born on|age:|marital status|married|religion|"
                     r"national insurance|ni number|passport number|driving licence number)\b", re.I)
MODULE_CODE = re.compile(r"\b[A-Z]{3,4}\d{3,4}\b")
ODD_CHAR = re.compile("[ﬀ-ﬆ-�]|\\(cid:\\d+\\)")


def pdf_text(pdf: Path, layout: bool = False) -> str:
    """The PDF's text as a parser reads it; "" when it cannot be read."""
    return pdfread.text(pdf, layout=layout)


def pdf_pages(pdf: Path) -> int:
    """How many pages the PDF has; 0 when it cannot be read."""
    return pdfread.pages(pdf)


def lines_of(text: str) -> list[str]:
    """An entry's lines: its bullets when written as "- " lines, else its sentences."""
    text = re.sub(r"\*\*", "", text or "")
    rows = [r.strip() for r in text.splitlines() if r.strip()]
    if rows and all(r.startswith("- ") for r in rows):
        return [r[2:].strip() for r in rows]
    return [x.strip() for x in re.split(r"(?<=[.!?])\s+(?=[A-Z])", " ".join(rows)) if len(x.strip()) > 3]


# Matching a job -------------------------------------------------------------------------
# The model finds requirements and evidence; every quote it gives is checked against the text.

AI_TIMEOUT_S = 600
AI_CTX = 16384     # a two-page CV, an advert and the record's passages fit in this many tokens


def _ask(prompt_id: str, user: str) -> dict:
    """One JSON request with prompt `prompt_id`; raises `ai.LLMUnavailable` when the AI cannot answer now."""
    from .ai import prompts

    return ai.chat_json(prompts.text(prompt_id), user, role="checker", timeout=AI_TIMEOUT_S, ctx=AI_CTX,
                        purpose=prompt_id)


# Names that mean the same thing to a recruiter, so a CV is never told to add what it already has.
ALIASES = {"js": "javascript", "ts": "typescript", "node.js": "node", "nodejs": "node", "c++": "cpp", "c#": "csharp",
           ".net": "dotnet", "golang": "go", "python3": "python", "reactjs": "react", "react.js": "react",
           "next.js": "next", "nextjs": "next", "vue.js": "vue", "postgres": "postgresql", "psql": "postgresql",
           "mongo": "mongodb", "k8s": "kubernetes", "amazon web services": "aws", "gcp": "google cloud",
           "google cloud platform": "google cloud", "microsoft azure": "azure", "restful": "rest", "rest api": "rest",
           "rest apis": "rest", "ci/cd": "ci", "continuous integration": "ci", "github actions": "ci",
           "machine learning": "ml", "artificial intelligence": "ai", "large language models": "llm", "llms": "llm",
           "retrieval-augmented generation": "rag", "scikit-learn": "sklearn", "unit testing": "testing",
           "test-driven development": "tdd", "object-oriented": "oop", "object oriented": "oop",
           "data structures and algorithms": "algorithms", "linux/unix": "linux", "unix": "linux",
           "bash": "shell", "shell scripting": "shell", "mysql/mariadb": "mysql", "sqlite3": "sqlite"}
SKILLS = sorted({*ALIASES.values(), "python", "java", "javascript", "typescript", "go", "rust", "c", "cpp",
                 "csharp", "sql", "lua", "kotlin", "swift", "scala", "ruby", "php", "react", "angular", "flask",
                 "fastapi", "django", "spring", "docker", "kubernetes", "terraform", "aws", "azure", "linux", "git",
                 "graphql", "grpc", "kafka", "redis", "postgresql", "mysql", "sqlite", "mongodb", "pytorch",
                 "tensorflow", "numpy", "pandas", "sklearn", "ml", "ai", "llm", "rag", "nlp", "computer vision",
                 "distributed systems", "microservices", "concurrency", "multithreading", "networking", "security",
                 "cryptography", "testing", "pytest", "tdd", "ci", "agile", "scrum", "oop", "algorithms",
                 "data structures", "api", "rest", "html", "css", "tailwind", "electron", "embedded", "spark",
                 "airflow", "dbt", "snowflake", "excel", "tableau", "power bi", "statistics", "shell", "jira"}
                - {"go", "c"})  # ordinary words too often ("go live", "a, b, c") to count as skills
REQUIREMENTS_HEADING = re.compile(r"requirement|qualification|must[- ]have|about you|you have|you will have|"
                                  r"what we('re| are) looking for|skills|experience", re.I)


def _canon(text: str, aliases: dict[str, str] | None = None) -> str:
    table = ALIASES if aliases is None else aliases
    text = f" {text.lower()} "
    for alias in sorted(table, key=len, reverse=True):
        text = re.sub(rf"(?<![\w+#.]){re.escape(alias)}(?![\w+#])", f" {table[alias]} ", text)
    return text


def _has(term: str, text: str) -> int:
    return len(re.findall(rf"(?<![\w+#]){re.escape(term)}(?![\w+#])", text))


def keywords(advert: str, cv_text: str, terms: list[str] | None = None,
             aliases: dict[str, list[str]] | None = None) -> dict:
    """Which of the advert's skills the CV names, weighted by how much the advert leans on them.

    `terms` and `aliases` come from the CV type's `[keywords]`; without them the built-in list of
    software skills is used."""
    table = None if terms is None else {a.lower(): k.lower() for k, names in (aliases or {}).items() for a in names}
    skills = SKILLS if terms is None else sorted({t.lower() for t in terms} | set(table.values()))
    mine = _canon(cv_text, table)
    # Terms under a requirements heading count for more than those in the company blurb.
    lines, weight_of, boost = advert.splitlines(), {}, 1.0
    for line in lines:
        if len(line) < 60 and REQUIREMENTS_HEADING.search(line):
            boost = 1.5
        for term in skills:
            n = _has(term, _canon(line, table))
            if n:
                weight_of[term] = weight_of.get(term, 0) + n * boost
    terms = sorted(weight_of, key=lambda t: -weight_of[t])[:25]
    have = [t for t in terms if _has(t, mine)]
    thin = [t for t in have if _has(t, mine) == 1]
    total = sum(weight_of[t] for t in terms)
    return {"covered": round(100 * sum(weight_of[t] for t in have) / total) if total else None,
            "have": have, "missing": [t for t in terms if t not in have], "thin": thin, "of": len(terms)}


def _norm(text: str) -> str:
    return " ".join(re.sub(r"[^\w%+#.]", " ", text.lower()).split())


def _quoted(quote: str, source: str) -> bool:
    """True when a quote the model gave really is in the source, allowing for spacing and punctuation."""
    q = _norm(quote)
    return len(q) >= 3 and q in _norm(source)


def requirements(job, cv_text: str, facts: str = "") -> tuple[list[dict], str]:
    """The advert's requirements and the CV's evidence for each, in two separate passes."""
    advert = (job["description"] or "")[:12000]
    reason = ai.ready(purpose="cv_needs")
    if reason or not advert.strip():
        return [], reason or "the job has no advert text"
    try:
        needs = _ask("cv_needs", f"Job: {job['title']} at {job['company']}\n<<<ADVERT\n{advert}\nADVERT>>>"
                     ).get("requirements", [])
        needs = [n for n in needs if isinstance(n, dict) and n.get("need") and _quoted(str(n.get("quote", "")), advert)][:12]
        if not needs:
            return [], "the model found no requirements it could quote from the advert"
        listing = "\n".join(f"{i}. {n['need']}" for i, n in enumerate(needs, 1))
        kept = record.context([n["need"] for n in needs])
        found = _ask("cv_evidence", f"Requirements:\n{listing}\n\n<<<CV\n{cv_text}\nCV>>>"
                     f"\n\n<<<FACTS\n{facts}\nFACTS>>>\n\n<<<RECORD\n{kept}\nRECORD>>>").get("evidence", [])
    except ai.LLMUnavailable as e:
        return [], str(e)
    by_id = {}
    for e in found:
        try:
            by_id[int(e.get("id"))] = e
        except (TypeError, ValueError, AttributeError):
            continue
    out = []
    for i, n in enumerate(needs, 1):
        e = by_id.get(i, {})
        quote = str(e.get("quote") or "")
        match = e.get("match") if e.get("match") in ("direct", "adjacent") else "missing"
        # Where the quote really is: the CV (or the letter sent with it), the record only, or nowhere.
        where = "cv" if _quoted(quote, f"{cv_text}\n{facts}") else "record" if kept and _quoted(quote, kept) else ""
        if match != "missing" and not where:
            match, quote = "missing", ""  # evidence the CV does not hold is no evidence
        kind = n.get("kind") if n.get("kind") in ("knockout", "must", "nice") else "must"
        try:
            weight = max(1, min(3, int(n.get("weight") or 2)))
        except (TypeError, ValueError):
            weight = 2
        out.append({"need": str(n["need"]), "kind": kind, "weight": weight, "advert": str(n["quote"]),
                    "match": match, "evidence": quote, "where": where if match != "missing" else ""})
    return out, ""

