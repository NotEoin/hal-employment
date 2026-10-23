"""Where each old config key lives now: as data.

The config shim (`jobhunt/config.py`) answers `CONFIG.get(old_key)` from live settings through this map,
and both importers (the owner's repo, the USB stick) use it to carry old values over. Old keys are
written as they appear in the old files: top-level keys bare (`alert_score`), table keys with their table
(`applicant.city`, `claude.main_model`), environment variables as `env:NAME`.

Design notes:
- `store` says where a value goes, because not every old key becomes a setting: some go to the private or
  secrets store, some only feed the importer, some are removed with the feature they served.
- `values` maps old values onto the new choices where the words changed (`extension` became `helper`).
- `parse` marks values a person typed by hand on the stick (U), which go through `settings.parse`, so
  "22,000" and "15 miles" are read rather than refused.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Literal

Store = Literal["settings", "private", "secret", "sources", "importer", "removed", "developer_env"]
Layout = Literal["L", "U"]


@dataclass(frozen=True)
class LegacyKey:
    """One old config key and where its value goes."""

    old: str                              # "alert_score", "applicant.city", "env:JOBHUNT_APPLY_BROWSER"
    setting: str | None                   # the setting key, private key or secret name; None when not kept
    store: Store = "settings"
    values: tuple[tuple[Any, Any], ...] = ()   # (old value, new value) where the words changed
    parse: bool = False                   # read with settings.parse (typed by hand)
    layouts: tuple[Layout, ...] = ("L", "U")
    note: str = ""


def _u(old: str, setting: str | None, **kw: Any) -> LegacyKey:
    """A stick-only key; its values were typed by hand in the stick's setup, so they are parsed."""
    kw.setdefault("parse", True)
    return LegacyKey(old, setting, layouts=("U",), **kw)


# U [applicant] keys that keep their name under `you.`.
_U_APPLICANT_SAME = ("name", "preferred_name", "email", "phone", "location_line", "nationality", "start",
                     "relocate", "office")
_U_EDUCATION = ("degree", "subject", "university", "grade", "start_year", "dissertation")

LEGACY_KEYS: tuple[LegacyKey, ...] = (
    LegacyKey("alert_score", "notify.alert_score"),
    LegacyKey("ntfy_topic", "notify.ntfy_topic", store="removed",
              note="not imported: a new random topic is generated"),
    LegacyKey("hal_root", "addon.hal_root", layouts=("L",)),
    LegacyKey("hal_vault", "addon.vault", layouts=("L",)),
    LegacyKey("speak_command", "addon.speak_command", layouts=("L",)),
    LegacyKey("record_vault", "record.folder"),
    LegacyKey("web_port", "advanced.port"),
    LegacyKey("queue_size", "scoring.prepare_ahead"),
    LegacyKey("queue_min_score", "scoring.min_show"),
    LegacyKey("apply_browser", "applying.mode",
              values=(("extension", "helper"), ("window", "window"), ("headless", "headless"))),
    LegacyKey("apply_window", "applying.window", values=(("separate", "separate"), ("same", "this"))),
    LegacyKey("signed_in_apply", None, store="removed",
              note="removed: always [] because Hal never presses a signed-in Apply"),
    LegacyKey("gpu_ignore", "ai.gpu_ignore"),
    LegacyKey("applicant.city", "you.city"),
    LegacyKey("applicant.notice", "you.notice"),
    LegacyKey("applicant.diversity", "you.diversity", values=(("Prefer not to say", "prefer_not"),),
              note="any other wording is reported by the importer"),
    LegacyKey("applicant.salary", "you.salary_answer"),
    LegacyKey("applicant.carer", "carer", store="private",
              note="never in you.*, so CONFIG.get('applicant') and profile_for() never hold it"),
    *(_u(f"applicant.{key}", f"you.{key}") for key in _U_APPLICANT_SAME),
    _u("applicant.country", None, store="removed", note="locked to the United Kingdom for now"),
    _u("applicant.right_to_work", "you.right_to_work",
       note="with applicant.sponsorship: sponsorship needed -> need_sponsorship, else uk_no_visa"),
    _u("applicant.sponsorship", "you.right_to_work", note="read together with applicant.right_to_work"),
    _u("applicant.linkedin", "you.links.linkedin"),
    _u("applicant.website", "you.links.website"),
    *(_u(f"applicant.{key}", f"you.education.{key}") for key in _U_EDUCATION),
    _u("applicant.grad_year", "goal.study_end"),
    LegacyKey("cv_site", None, store="importer", layouts=("L",), note="read once by the importer"),
    LegacyKey("course_vault", None, store="removed", layouts=("L",), note="removed with coursework.py"),
    LegacyKey("ollama_url", "ai.ollama_url"),
    LegacyKey("main_model", "ai.model.checker"),
    LegacyKey("writer_model", "ai.model.writer"),
    LegacyKey("fast_model", "ai.model.quick"),
    LegacyKey("record_embed_model", "ai.model.embed"),
    LegacyKey("digest_to", "notify.daily_to"),
    _u("claude.main_model", "ai.model.checker"),
    _u("claude.writer_model", "ai.model.writer"),
    _u("claude.fast_model", "ai.model.quick"),
    _u("claude.parallel", "ai.parallel"),
    _u("search.queries", None, store="sources", note="search words of the aggregator sources"),
    _u("search.places", "goal.places"),
    _u("search.radius_miles", "goal.max_distance"),
    _u("search.roles", "goal.roles"),
    _u("search.avoid_titles", "goal.avoid_roles"),
    _u("search.interests", "goal.interests"),
    _u("search.remote", "goal.pattern", values=((True, "any"), (False, "office"), ("only", "remote"))),
    _u("search.salary_floor", "goal.min_pay"),
    _u("search.exclude_sectors", "goal.avoid_sectors"),
    _u("search.exclude_defence", "goal.avoid_sectors", values=((True, "arms and defence"),),
       note="adds 'arms and defence' to goal.avoid_sectors when true"),
    _u("search.sources", None, store="sources", note="which sources are switched on"),
    _u("search.jobsacuk_queries", None, store="sources", note="the jobs.ac.uk source's search words"),
    _u("schedule.discover", "schedule.search_times"),
    _u("schedule.mail_every_minutes", "schedule.mail_every"),
    _u("schedule.mail_hours", "schedule.mail_hours"),
    _u("schedule.digest", "notify.daily_summary"),
    LegacyKey("env:JOBHUNT_APPLY_BROWSER", "applying.mode", store="developer_env",
              values=(("extension", "helper"), ("window", "window"), ("headless", "headless")),
              note="honoured only in developer mode, logged once"),
    LegacyKey("env:JOBHUNT_MAIN_MODEL", "ai.model.checker", store="developer_env"),
    LegacyKey("env:JOBHUNT_WRITER_MODEL", "ai.model.writer", store="developer_env"),
    LegacyKey("env:JOBHUNT_FAST_MODEL", "ai.model.quick", store="developer_env"),
)

BY_OLD: dict[str, LegacyKey] = {k.old: k for k in LEGACY_KEYS}

# `CONFIG.get(section, {})` answers with a dict view of the settings under this prefix.
SECTION_VIEWS: dict[str, str] = {"applicant": "you.", "search": "goal."}


def lookup(old: str) -> LegacyKey:
    """The map entry for an old key; KeyError names the key when the map does not know it."""
    try:
        return BY_OLD[old]
    except KeyError:
        raise KeyError(f"no setting is mapped from the old config key {old!r}") from None


def new_value(entry: LegacyKey, value: Any) -> Any:
    """The old value in the new setting's terms (`extension` -> `helper`); unchanged when no mapping applies."""
    for old, new in entry.values:
        if type(old) is type(value) and old == value:
            return new
    return value


def for_setting(key: str) -> list[LegacyKey]:
    """Every old key that feeds `key`."""
    return [k for k in LEGACY_KEYS if k.setting == key]
