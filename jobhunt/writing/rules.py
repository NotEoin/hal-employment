"""The writing rules for CVs, cover letters and form answers, as data.

Each rule has an id, the rule itself in plain words (shown to the writer when it revises a draft, and to the
person in a critique), the kinds of document it applies to, a severity and, for the field add-ons, the kind
of job it belongs to. `writing.check` enforces the rules a regular expression or a count can see; the
prompts and the recruiter's read cover the ones only a reader can judge.

Design decisions, each with its reason:

- Severity decides what happens. A `high` hit makes the writer revise its draft once; `high` and `medium`
  hits are listed under "Check before sending"; `low` hits are advice, shown only in a critique. A model
  gets one revision, never a loop, so a draft is never held up by the checker.
- The word lists are facts about how CVs and letters read now, not quotations: the rule wording here is
  Hal's own, written for CVs and letters. The catalogues that informed it are credited in
  THIRD_PARTY_NOTICES.md.
- A word on a list that the person uses in their own writing is theirs to keep (`check(..., allowed=)`),
  because a list cannot tell a habit of machines from the right word for one person.
- The field add-ons are advice for a kind of job, not rules a checker enforces; `writing.fields` picks
  them for a job and the prompts receive them with the facts, so a humanities graduate never gets advice
  written for another field.
"""
from __future__ import annotations

from dataclasses import dataclass

LETTER, CV, ANSWER = "letter", "cv", "answer"
KINDS = (LETTER, CV, ANSWER)
HIGH, MEDIUM, LOW = "high", "medium", "low"
SEVERITIES = (HIGH, MEDIUM, LOW)
FLAGGED = (HIGH, MEDIUM)        # the severities listed under "Check before sending"


@dataclass(frozen=True)
class Rule:
    """One writing rule: what it says, where it applies and how much a break of it matters."""

    id: str
    name: str                    # a few words for a flag: "a stock phrase"
    rule: str                    # the rule in plain words, as the writer and the person read it
    applies: tuple[str, ...]     # the document kinds it applies to
    severity: str


RULES = (
    # Truth and provenance
    Rule("T1", "number not in the CV or advert",
         "Use only numbers the facts give; where a result has no number, say what it made possible or who it "
         "served, and never supply a figure.", KINDS, HIGH),
    Rule("T4", "a size word the facts do not give",
         "Do not add size words such as significantly, dramatically or greatly unless the facts use them.",
         KINDS, MEDIUM),
    # Language, for every document
    Rule("L1", "a word that reads as machine-written or stock",
         "Leave out the words recruiters now read as machine-written or stock (delve, leverage, spearheaded, "
         "passionate, team player and the like); say what was done in plain words.", KINDS, HIGH),
    Rule("L2", "several fashionable words together",
         "Do not cluster fashionable words (robust, seamless, foster, navigate, showcase); one is a word, two "
         "in one paragraph read as a template.", KINDS, MEDIUM),
    Rule("L3", "one word used over and over",
         "Do not lean on one evaluative word (significant, effective, exceptional) throughout.", KINDS, LOW),
    Rule("L4", "a filler phrase",
         "Cut filler phrases (in order to, due to the fact that, going forward, in terms of); use the short "
         "form.", KINDS, MEDIUM),
    Rule("L5", "a clause ending in -ing that claims meaning",
         "Do not end a sentence with an -ing clause that tells the reader what it means (\", showcasing my "
         "skills\"); state the result instead.", KINDS, MEDIUM),
    Rule("L6", "puffery",
         "Leave out puffery (a testament to, plays a vital role, invaluable experience, unique opportunity).",
         KINDS, MEDIUM),
    Rule("L7", "a roundabout verb",
         "Use is and has rather than serves as, stands as or boasts.", KINDS, LOW),
    Rule("L8", "\"not just X but Y\"",
         "Do not stage a contrast (\"not just a job but a calling\", \"It's not X. It's Y.\"); say the one "
         "thing that is true.", KINDS, HIGH),
    Rule("L9", "a list of three qualities",
         "Do not stack three qualities (\"creative, organised and driven\"); show one with an example.",
         (LETTER, ANSWER, CV), MEDIUM),
    Rule("L10", "a spelling from the other side of the Atlantic",
         "Keep to one spelling throughout, the one set in Settings.", KINDS, MEDIUM),
    Rule("L11", "a dash used as punctuation",
         "Use commas, colons or full stops instead of long dashes.", KINDS, HIGH),
    Rule("L12", "bold used for emphasis",
         "Do not bold words for emphasis inside sentences or bullets.", KINDS, LOW),
    Rule("L13", "chatbot words or a placeholder",
         "Send no chatbot words (\"Certainly\", \"I hope this helps\") and no placeholder such as [Company].",
         KINDS, HIGH),
    Rule("L16", "a stock opening",
         "Open with the point, not with a stock opening (\"In today's world\", \"With a strong background "
         "in\", \"Furthermore\").", (LETTER, ANSWER), MEDIUM),
    Rule("L17", "hedging",
         "Say what was done without hedging (\"I believe I could\", \"I feel that\", \"hopefully\").",
         (LETTER, ANSWER), LOW),
    # CV lines
    Rule("B1", "a bullet over 35 words",
         "One idea per bullet, at most about 35 words.", (CV,), MEDIUM),
    Rule("B3", "a bullet that starts with a duty",
         "Start a bullet with what was done (organised, wrote, led), never with a duty (responsible for, "
         "helped with, involved in).", (CV,), MEDIUM),
    Rule("B4", "I, my or we in a CV line",
         "Write CV lines without I, me, my, we or our.", (CV,), MEDIUM),
    Rule("B7", "many bullets with the same first word",
         "Vary the first word: no more than three bullets start the same way.", (CV,), LOW),
    # Cover letters
    Rule("C1", "a letter of the wrong length",
         "Keep a letter to one page: about the length set in Settings, 3 to 5 paragraphs.", (LETTER, ANSWER),
         MEDIUM),
    Rule("C2", "a stock first line",
         "Open with the role and one reason from the advert, never \"I am writing to express my interest\".",
         (LETTER,), HIGH),
    Rule("C5", "a sentence that repeats the CV",
         "Add context the CV cannot give; never repeat a CV line in the letter.", (LETTER,), MEDIUM),
    Rule("C6", "a stock closing line",
         "Close by asking for the next step in one short sentence, not with \"I would be a valuable asset\".",
         (LETTER,), MEDIUM),
    Rule("C8", "a greeting and sign-off that do not match",
         "In a UK letter, \"Dear\" and a name ends \"Yours sincerely\"; \"Dear Sir or Madam\" or a team ends "
         "\"Yours faithfully\".", (LETTER,), MEDIUM),
    Rule("C11", "too many sentences starting with I",
         "Start no more than half the sentences with I.", (LETTER,), LOW),
)
BY_ID = {r.id: r for r in RULES}


@dataclass(frozen=True)
class Field:
    """A kind of job with conventions of its own, and the words that show an advert or a goal belongs to it."""

    id: str
    name: str
    words: tuple[str, ...]           # lower-case words and phrases that point to this field
    letter: tuple[str, ...] = ()     # conventions for a cover letter or statement
    cv: tuple[str, ...] = ()         # conventions for the CV
    answer: tuple[str, ...] = ()     # conventions for form answers
    background: tuple[str, ...] = ()  # advice when the person comes from this field and the job is in another


GENERAL = Field(
    "general", "General", (),
    letter=("Tie each piece of evidence to a requirement the advert states, in the advert's own words.",
            "Say why this employer in a sentence that would not fit any other employer."),
    cv=("Put the most relevant section first after the profile, newest entries first within each section.",
        "Give each entry one or two lines on what was done and what came of it."),
    answer=("Answer the question asked, with one real example where it asks for one.",),
)

FIELDS = (
    Field("humanities", "Humanities, heritage and the written word",
          ("heritage", "museum", "archive", "archivist", "library", "librarian", "collections", "curator",
           "curatorial", "editorial", "publishing", "historian", "humanities", "research assistant",
           "cataloguing", "catalogue", "conservation", "gallery education", "charity", "communications officer"),
          letter=("Show each transferable skill with one concrete piece of work, never as a bare list.",
                  "Treat the dissertation or a long essay as an independent research project: its sources, "
                  "scope, length and method, and the mark if it is strong.",
                  "Writing is part of the job: plain, exact sentences are themselves evidence."),
          cv=("Show the dissertation as a research project: topic, sources or collections used, method.",
              "List modules only where they bear on the job, at most six.",
              "Volunteering, societies and part-time work count as experience when they say what came of it."),
          answer=("Prove research, writing and organisation with one named piece of work each.",),
          background=("Name the transferable skill in each example and show it with a concrete piece of work "
                      "from the degree, such as the dissertation's sources, scope and method.",)),
    Field("arts", "Arts, design and creative work",
          ("portfolio", "designer", "illustrator", "artist", "exhibition", "photographer", "animation",
           "animator", "theatre", "film", "creative agency", "art director", "arts council",
           "commission"),
          letter=("Name one piece of work that fits the brief and give the portfolio link.",
                  "Speak to the employer's own work or audience, not to creativity in general."),
          cv=("Put the portfolio link under the name.",
              "List exhibitions, commissions, publications and performances as dated entries.",
              "Keep a plain, single-column version for online forms even when a designed CV is used."),
          answer=("Point to the portfolio piece that shows what the question asks.",),
          background=("Give the portfolio link and describe one piece of work in terms of its brief and "
                      "outcome.",)),
    Field("public_sector", "Civil Service and public sector",
          ("civil service", "success profiles", "behaviours", "government department", "department for",
           "ministry of", "local authority", "council", "executive officer", "administrative officer",
           "higher executive officer", "public sector", "hmrc", "policy adviser", "policy advisor"),
          letter=("Write the personal statement against each essential criterion in turn, with one example "
                  "for each.",
                  "Spell out every acronym and explain the setting: do not assume the reader knows it.",
                  "Keep identity out of the body (no name, age or school names in the statement), as the "
                  "application may be read anonymised."),
          cv=("Match entries to the essential criteria; the CV may be read with the name removed.",),
          answer=("Answer a behaviour question with one example in the STAR order: the Situation and Task in "
                  "a sentence or two, then most of the words on the Action the applicant took, then the "
                  "Result. Use STAR unless the advert asks for something else.",
                  "Keep each behaviour example within the stated limit, often about 250 words.",
                  "Name the behaviour being shown (for example Working Together, Delivering at Pace) only "
                  "where the advert names it.")),
    Field("healthcare", "NHS and healthcare",
          ("nhs", "nhs jobs", "agenda for change", "band 2", "band 3", "band 4", "band 5", "band 6", "patient",
           "patients", "clinical", "ward", "healthcare", "hospital", "care home", "nmc", "hcpc", "gp surgery"),
          letter=("Write the supporting information against the person specification: each essential "
                  "criterion as a heading, then each desirable one, in its order, with one real example "
                  "each.",
                  "Stay within the box limit of the form."),
          cv=("Give registration numbers and the DBS check where the role asks for them, and mandatory "
              "training with dates.",),
          answer=("Answer from the person specification's wording, with one real example per criterion.",)),
    Field("academia", "Academic posts",
          ("lecturer", "postdoctoral", "postdoc", "research fellow", "research associate", "faculty",
           "principal investigator", "teaching fellow", "senior lecturer", "academic", "publications"),
          letter=("Address the person specification and the department's research, naming where the "
                  "applicant's work fits it.",
                  "Give the research plan and teaching in specific terms: courses, levels, outputs."),
          cv=("Order: qualifications, research and publications, grants (date, funder, amount; external "
              "before internal), teaching (course and role), talks, service and memberships.",
              "Three to five pages is normal; pages one and two carry what the post asks for.",
              "No personal profile is needed; list publications newest first, accepted and in press "
              "included, in one citation style."),
          answer=("Answer with the outputs, courses and grants the facts give, each named.",)),
    Field("teaching", "Teaching and schools",
          ("teacher", "teaching assistant", "pgce", "qts", "pupils", "classroom", "ofsted", "key stage",
           "early years", "school"),
          letter=("Give a classroom example: the age group, what was taught and how pupils responded.",
                  "Mention safeguarding training and the DBS check where the facts give them."),
          cv=("Put teaching and school experience first, with age groups and subjects.",
              "Give two current referees where the form asks, one from a school."),
          answer=("Answer with one classroom or school example: who, what was done, what changed.",)),
    Field("law", "Law",
          ("solicitor", "training contract", "paralegal", "law firm", "legal assistant", "sqe", "barrister",
           "pupillage", "vacation scheme", "legal"),
          letter=("Show commercial awareness through the firm's own clients, sectors or recent work.",
                  "Be exact: a legal reader checks every claim and every comma."),
          cv=("Usually no profile: education first with relevant modules and awards, then legal work "
              "experience.",),
          answer=("Answer precisely and briefly, with one example, as a lawyer would draft it.",)),
    Field("finance", "Finance, banking and accountancy",
          ("investment bank", "banking", "audit", "accountancy", "accountant", "acca", "cima", "cfa",
           "asset management", "corporate finance", "financial analyst", "trading", "actuarial", "tax",
           "financial services", "fca", "consumer duty", "lending", "credit risk", "financial ombudsman"),
          letter=("Give one result with its figure from the facts, and why this firm and division.",),
          cv=("One page for graduate and analyst roles; education first while a student, with the degree "
              "class and A-level grades.",
              "Single column, no tables or text boxes; quantitative modules where there are any."),
          answer=("Answer with numbers the facts give and the decision they led to.",)),
    Field("graduate_scheme", "Graduate schemes",
          ("graduate scheme", "graduate programme", "graduate program", "graduate intake", "rotations",
           "rotational programme"),
          letter=("Name the scheme and the stream, and why it suits the applicant now.",),
          cv=("Put the degree class (or predicted class) and dates near the top.",),
          answer=("Answer each competency question with one example in the STAR order (Situation, Task, "
                  "Action, Result), most of the words on the Action, within the form's limit.",
                  "Be specific: name the setting, the people involved and what came of it.")),
    Field("hospitality_retail", "Hospitality, retail and customer service",
          ("retail", "store", "shop floor", "customer assistant", "sales assistant", "barista", "hospitality",
           "restaurant", "hotel", "front of house", "bar staff", "waiting staff", "kitchen", "till"),
          letter=("Keep it short and practical: availability, shifts and one example of serving customers "
                  "well.",),
          cv=("Put work experience before education; practical tasks over degree detail.",
              "Say how busy it was and what was handled: covers, tills, stock, complaints resolved."),
          answer=("Answer with one real moment with a customer: what happened and what was done.",)),
    Field("trades", "Trades, apprenticeships and technical work on site",
          ("electrician", "plumber", "apprentice", "apprenticeship", "carpenter", "joiner", "mechanic",
           "cscs", "nvq", "city and guilds", "construction", "maintenance technician", "bricklayer", "welder"),
          letter=("Say which qualifications and tickets are held or under way, and when the applicant can "
                  "start.",),
          cv=("List qualifications, cards and licences with dates first; then jobs and the tools and tasks "
              "used on them.",),
          answer=("Answer with one job done, the tools used and the result, safely and to time.",)),
    Field("tech", "Software, data and IT",
          ("developer", "software", "python", "javascript", "typescript", "data analyst", "data engineer",
           "devops", "cloud", "sql", "programming", "full stack", "frontend", "backend", "it support",
           "machine learning"),
          letter=("Name the most relevant project, the tools it used and what it achieved, in words a "
                  "non-technical screener can follow.",),
          cv=("Give a key skills section, and show every listed tool in use in an entry or project.",
              "Link to code or projects; reviewers open them.",
              "Make job titles, dates and years of experience easy to find."),
          answer=("Answer with one project or task: the problem, what was built or fixed, and the result.",),
          background=("Explain technical work in plain words for a reader outside the field.",)),
)
BY_FIELD = {f.id: f for f in (GENERAL, *FIELDS)}
