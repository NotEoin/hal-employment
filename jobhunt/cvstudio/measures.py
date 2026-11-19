"""A personal look as a set of measures, each one checked, and the CSS made from them by code.

A look matched from a person's own file (`match.py`) is never free CSS: it is the `Measures` below, and the
page's style is written from them here. Each measure is a number in a sane range, one of a few named
choices, or a colour of six hex digits (`FIELDS`), so no text from a file can reach the page's style and the
page's Content-Security-Policy (`render.POLICY`) stays as it is.

- `validate` refuses anything else: an unknown name, a wrong kind, a number out of range, a colour that is not
  `#rrggbb`. `clamped` is for the measurer: it brings a measured number into its range and leaves the rest to
  `validate`, so a file with an odd value still makes a look, and the person sees what Hal used.
- Fonts map to the two families Hal ships (`render.FONTS`); no font is embedded or fetched.
- The measures that change the page's markup, not only its style (how an entry is written, where its dates
  go, the contact line's separator), are read by `render.html_of` from the `Look` (`look_of`).
- `tighter` is the one step "Make it fit" takes: a little less line spacing and type size, within
  `TIGHTEN_STEPS` steps of what was measured and never below `READABLE_PT` (see its notes).
"""
from __future__ import annotations

import dataclasses
import re
from dataclasses import dataclass
from typing import Any

FAMILIES = {"sans": '"Source Sans 3", Arial, sans-serif', "serif": '"Source Serif 4", Georgia, serif'}
PAGES = {"a4": ("A4", 210.0, 297.0), "letter": ("letter", 215.9, 279.4)}     # CSS size name, width, height (mm)
SEPARATORS = {"bar": " | ", "dot": " \u00b7 ", "bullet": " \u2022 ", "comma": ", ", "dash": " - ", "space": "   "}
COLOUR = re.compile(r"#[0-9a-f]{6}")
RULE_PT = 0.6           # a section heading's rule, as the built-in Classic look draws it
RULE_GAP_MM = 0.6       # the space between a heading's words and its rule, as Classic
# One "Make it fit" step: a twentieth less line spacing and three tenths of a point less type, a change the
# eye barely sees (2026-11-19). Two steps at most, so a matched look stays close to the file it came from.
TIGHTEN_LINE = 0.05
TIGHTEN_PT = 0.3
TIGHTEN_STEPS = 2
# Nothing is made smaller than this: 8.5 pt is about the smallest body text a printed page is read in with
# comfort (common CV advice puts the floor between 8 and 10 pt).
READABLE_PT = 8.5
LINE_FLOOR = 1.1        # tighter line spacing than this lets accents and descenders touch
LETTER_LINE_MIN = 1.3   # a letter is paragraphs of long lines, which a CV's tight spacing makes hard to follow

# name: ("num", low, high) | ("enum", choices) | ("colour",) | ("bool",)
FIELDS: dict[str, tuple] = {
    "page": ("enum", tuple(PAGES)),
    "margin_top": ("num", 5.0, 40.0), "margin_right": ("num", 5.0, 40.0),         # mm; most printers clip
    "margin_bottom": ("num", 5.0, 40.0), "margin_left": ("num", 5.0, 40.0),       # under 5 mm
    "font": ("enum", tuple(FAMILIES)), "heading_font": ("enum", tuple(FAMILIES)),
    "size": ("num", 8.0, 14.0), "line_height": ("num", 1.0, 2.0),
    "name_size": ("num", 12.0, 36.0), "name_bold": ("bool",),
    "head_align": ("enum", ("left", "center")),
    "contact_size": ("num", 7.0, 14.0), "contact_sep": ("enum", tuple(SEPARATORS)), "contact_first": ("bool",),
    "heading_size": ("num", 8.0, 18.0), "heading_bold": ("bool",), "heading_caps": ("bool",),
    "heading_spacing": ("num", 0.0, 0.3), "heading_rule": ("bool",),                # spacing in em
    "heading_above": ("num", 0.0, 15.0), "heading_below": ("num", 0.0, 10.0),       # mm
    "entry_size": ("num", 8.0, 14.0), "entry_bold": ("bool",),
    # how an entry without a list under it is written, and one with a list (bullets): a file may do both
    "entry_form": ("enum", ("stacked", "line")), "bullet_form": ("enum", ("stacked", "line")),
    "dates": ("enum", ("right", "inline")),
    "entry_gap": ("num", 0.0, 10.0),                                                # mm
    "bullet_indent": ("num", 0.0, 15.0), "bullet_gap": ("num", 0.0, 4.0),           # mm
    "text_colour": ("colour",), "muted_colour": ("colour",), "heading_colour": ("colour",),
    "rule_colour": ("colour",), "link_colour": ("colour",), "link_underline": ("bool",),
}


class BadMeasure(ValueError):
    """A measure that is not one Hal can use, with its name."""

    def __init__(self, name: str, message: str) -> None:
        super().__init__(f"{name}: {message}")
        self.name = name


@dataclass(frozen=True)
class Measures:
    """One personal look. The defaults are the Classic look's own values, so a measure a file does not show
    keeps Hal's usual choice."""

    page: str = "a4"
    margin_top: float = 18.0
    margin_right: float = 18.0
    margin_bottom: float = 18.0
    margin_left: float = 18.0
    font: str = "sans"
    heading_font: str = "serif"
    size: float = 10.0
    line_height: float = 1.38
    name_size: float = 21.0
    name_bold: bool = True
    head_align: str = "left"
    contact_size: float = 9.0
    contact_sep: str = "bar"
    contact_first: bool = False
    heading_size: float = 10.0
    heading_bold: bool = True
    heading_caps: bool = True
    heading_spacing: float = 0.06
    heading_rule: bool = True
    heading_above: float = 4.2
    heading_below: float = 1.6
    entry_size: float = 10.0
    entry_bold: bool = True
    entry_form: str = "stacked"
    bullet_form: str = "stacked"
    dates: str = "right"
    entry_gap: float = 2.6
    bullet_indent: float = 4.2
    bullet_gap: float = 0.4
    text_colour: str = "#1d1d1b"
    muted_colour: str = "#4a4a46"
    heading_colour: str = "#1d1d1b"
    rule_colour: str = "#8a8a84"
    link_colour: str = "#1d1d1b"
    link_underline: bool = True

    def to_dict(self) -> dict:
        """The measures as stored (`cv_looks.measures`)."""
        return dataclasses.asdict(self)


def _check(name: str, value: Any) -> Any:
    spec = FIELDS[name]
    kind = spec[0]
    if kind == "num":
        if isinstance(value, bool) or not isinstance(value, int | float):
            raise BadMeasure(name, f"must be a number, not {value!r}")
        if not spec[1] <= value <= spec[2]:
            raise BadMeasure(name, f"must be from {spec[1]:g} to {spec[2]:g}, not {value:g}")
        return round(float(value), 2)
    if kind == "bool":
        if not isinstance(value, bool):
            raise BadMeasure(name, f"must be true or false, not {value!r}")
        return value
    if kind == "enum":
        if value not in spec[1]:
            raise BadMeasure(name, f"must be one of {', '.join(spec[1])}, not {value!r}")
        return value
    if not isinstance(value, str) or not COLOUR.fullmatch(value):
        raise BadMeasure(name, f"must be a colour written #rrggbb, not {value!r}")
    return value


def validate(data: dict) -> Measures:
    """The measures in `data`, every one checked; a missing one keeps its default. `BadMeasure` otherwise."""
    if not isinstance(data, dict):
        raise BadMeasure("measures", "must be a table of measures")
    unknown = sorted(set(data) - set(FIELDS))
    if unknown:
        raise BadMeasure(unknown[0], "is not a measure Hal knows")
    return Measures(**{name: _check(name, value) for name, value in data.items()})


def clamped(data: dict) -> dict:
    """`data` with each number brought into its measure's range, for values measured from a file."""
    out = dict(data)
    for name, value in data.items():
        spec = FIELDS.get(name)
        if spec and spec[0] == "num" and isinstance(value, int | float) and not isinstance(value, bool):
            out[name] = min(max(float(value), spec[1]), spec[2])
    return out


def separator(m: Measures) -> str:
    """The text between the contact line's parts."""
    return SEPARATORS[m.contact_sep]


def tighter(m: Measures, steps_taken: int) -> Measures | None:
    """The measures one "Make it fit" step tighter, or None when there is no room left.

    There is no room once `TIGHTEN_STEPS` steps were taken, or when neither the type size (above
    `READABLE_PT`) nor the line spacing (above `LINE_FLOOR`) can shrink any more.
    """
    if steps_taken >= TIGHTEN_STEPS:
        return None
    size = max(round(m.size - TIGHTEN_PT, 2), READABLE_PT) if m.size > READABLE_PT else m.size
    line = max(round(m.line_height - TIGHTEN_LINE, 2), LINE_FLOOR) if m.line_height > LINE_FLOOR else m.line_height
    if size == m.size and line == m.line_height:
        return None
    shrink = size / m.size
    smaller = {k: max(round(getattr(m, k) * shrink, 2), FIELDS[k][1])
               for k in ("entry_size", "contact_size", "heading_size")}
    return dataclasses.replace(m, size=size, line_height=line, **smaller)


# ---------------------------------------------------------------------------------------------- CSS

def _pt(value: float) -> str:
    return f"{value:g}pt"


def _mm(value: float) -> str:
    return f"{value:g}mm"


def css(m: Measures) -> str:
    """The CV page's style for these measures, laid over `looks/base.css`."""
    m = validate(m.to_dict())        # a Measures built by hand is checked too: nothing unchecked is written
    size_name, width, height = PAGES[m.page]
    margins = " ".join(_mm(x) for x in (m.margin_top, m.margin_right, m.margin_bottom, m.margin_left))
    align = "center" if m.head_align == "center" else "left"
    rule = (f"border-bottom: {RULE_PT}pt solid {m.rule_colour}; padding-bottom: {RULE_GAP_MM}mm;"
            if m.heading_rule else "border-bottom: 0; padding-bottom: 0;")
    return "\n".join([
        "/* A personal look, written by Hal from its measures (cvstudio/measures.py). */",
        f"@page {{ size: {size_name}; margin: {margins}; }}",
        f"body {{ font-family: {FAMILIES[m.font]}; font-size: {_pt(m.size)}; line-height: {m.line_height:g}; "
        f"color: {m.text_colour}; }}",
        f".cv-head {{ display: flex; flex-direction: column; text-align: {align}; }}",
        ".cv-name { order: 0; }",
        f".cv-contact {{ order: {1 if m.contact_first else 2}; font-size: {_pt(m.contact_size)}; "
        f"color: {m.muted_colour}; }}",
        f".cv-headline {{ order: {2 if m.contact_first else 1}; }}",
        f".cv-name {{ font-family: {FAMILIES[m.heading_font]}; font-size: {_pt(m.name_size)}; "
        f"font-weight: {600 if m.name_bold else 400}; color: {m.heading_colour}; }}",
        f".cv-sec {{ margin-top: {_mm(m.heading_above)}; }}",
        f".cv-sec h2 {{ font-family: {FAMILIES[m.heading_font]}; font-size: {_pt(m.heading_size)}; "
        f"font-weight: {600 if m.heading_bold else 400}; "
        f"text-transform: {'uppercase' if m.heading_caps else 'none'}; letter-spacing: {m.heading_spacing:g}em; "
        f"color: {m.heading_colour}; margin-bottom: {_mm(m.heading_below)}; {rule} }}",
        f".cv-top h3, .cv-line h3 {{ font-size: {_pt(m.entry_size)}; font-weight: {600 if m.entry_bold else 400}; }}",
        # the markup only a personal look writes (`render._one_line`, dates inline): kept here, not in
        # base.css, so the built-in looks' pages and their page counts stay as they were
        ".cv-line h3 { display: inline; }",
        ".cv-right { float: right; margin-left: 4mm; white-space: nowrap; }",
        f".cv-entry {{ margin-bottom: {_mm(m.entry_gap)}; }}",
        f".cv-dates, .cv-org, .cv-meta {{ color: {m.muted_colour}; font-size: {_pt(m.size)}; }}",
        f".cv-line .cv-dates {{ color: {m.muted_colour}; }}",
        f".cv-entry ul, .cv-sec > ul {{ padding-left: {_mm(m.bullet_indent)}; }}",
        f".cv-entry li {{ margin: {_mm(m.bullet_gap)} 0; }}",
        f".cv-sec a, .cv-contact a {{ color: {m.link_colour}; "
        f"text-decoration: {'underline' if m.link_underline else 'none'}; }}",
        f"@media screen {{ .page {{ width: {_mm(width)}; min-height: {_mm(height)}; padding: {margins}; }} }}",
    ]) + "\n"


def letter_css(m: Measures) -> str:
    """A cover letter's style in these measures: the CV's font, size and margins, the letter's own spacing."""
    m = validate(m.to_dict())
    line = max(m.line_height, LETTER_LINE_MIN)
    return "\n".join([
        "/* The letter in a personal look: the built-in looks' spacing, in the look's type. */",
        ".letter-date { margin-top: 6mm; }",
        ".letter-to { margin-top: 4mm; }",
        ".letter-subject { margin-top: 5mm; font-weight: 600; }",
        f".letter-body {{ margin-top: 4mm; font-size: {_pt(m.size)}; line-height: {line:g}; }}",
        ".letter-body p { margin: 0 0 3.2mm; }",
    ]) + "\n"
