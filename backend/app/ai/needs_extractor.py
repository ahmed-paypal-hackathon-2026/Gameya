"""Needs Extractor (REQ-AI-02, part 1): member free text -> NeedExtraction.

Two paths, same output:
- LLM (when a client is passed): schema-constrained JSON over the wrapped,
  untrusted member text, then grounded against the text (an amount the member
  never wrote is dropped; an implausible date is dropped).
- Rules (no client, timeout, any LLM error): deterministic parser for English,
  Egyptian Arabic and Levantine Arabic. Good enough on its own because the free
  LLM quota is ~20 requests/day (PIT-04).

Dates resolve to the next occurrence on/after the circle start; with no day of
the month we use the 1st. Relative dates count from the circle start. Amounts
are only taken in US dollars: EGP and other currencies are never converted.
"""

from __future__ import annotations

import asyncio
import re
from dataclasses import dataclass, field
from datetime import date, timedelta
from decimal import Decimal, InvalidOperation
from functools import cache
from pathlib import Path
from typing import Any, Literal

from app.ai._llm_port import LLM_TIMEOUT_S, LLMClient, wrap_untrusted
from app.schemas.contracts import CircleSnapshot, Locale, NeedCategory, NeedExtraction

PROMPT_PATH = Path(__file__).parent / "prompts" / "needs_extract.md"
Flexibility = Literal["low", "medium", "high"]
MAX_TEXT_CHARS = 1000  # member text beyond this is ignored by both paths
MAX_NEED_AMOUNT = Decimal("1000000.00")  # larger "needs" are noise; also fits NUMERIC(12,2)
HORIZON_DAYS = 3 * 366  # a need date must fall within ~3 years of the circle start

# ---------- normalisation ----------
_DIGITS = str.maketrans("٠١٢٣٤٥٦٧٨٩۰۱۲۳۴۵۶۷۸۹", "0123456789" * 2)
_ARABIC_FOLD = str.maketrans(
    {
        "أ": "ا",
        "إ": "ا",
        "آ": "ا",
        "ٱ": "ا",
        "ة": "ه",
        "ى": "ي",
        "ـ": None,
        "،": ",",
        "٬": ",",
        "٫": ".",
        "’": "'",  # phones type curly apostrophes: "isn’t"
        "‘": "'",
    }
)
_DIACRITICS = re.compile(r"[ً-ْٰ]")


def normalise(text: str) -> str:
    """Western digits, folded Arabic letter variants, no diacritics, lower case."""
    t = _DIACRITICS.sub("", text.translate(_DIGITS).translate(_ARABIC_FOLD))
    return re.sub(r"\s+", " ", t).strip().lower()


def _is_ascii(s: str) -> bool:
    return s.isascii()


# Arabic attaches prepositions/articles and pronoun suffixes to words.
_AR_PREFIX = r"(?:[وفبلك])?(?:ال|لل)?"
_AR_SUFFIX = r"(?:ي|ه|ها|هم|ك|نا|تي|ين)?"


def _phrase_pattern(phrase: str) -> str:
    if phrase.endswith("*"):  # English stem: renovat* matches renovate, renovation
        return rf"(?<![a-z]){re.escape(phrase[:-1])}"
    p = re.escape(phrase)
    if _is_ascii(phrase):
        return rf"(?<![a-z]){p}(?:s|es)?(?![a-z])"
    return rf"(?<!\w){_AR_PREFIX}{p}{_AR_SUFFIX}(?!\w)"


def _compile(phrases: tuple[str, ...]) -> re.Pattern[str]:
    normalised = sorted({normalise(p) for p in phrases}, key=len, reverse=True)
    return re.compile("|".join(_phrase_pattern(p) for p in normalised))


# ---------- category ----------
_CATEGORY_WORDS: dict[NeedCategory, tuple[str, ...]] = {
    NeedCategory.MEDICAL: (
        "surgery", "operation", "hospital", "doctor", "medical", "medicine", "treatment",
        "dental", "dentist", "clinic", "baby", "birth", "pregnan*",
        "عملية", "ولادة", "دكتور", "مستشفى", "علاج", "دوا", "دواء", "كشف", "أسنان", "سنان",
    ),
    NeedCategory.WEDDING: (
        "wedding", "marry", "marriage", "married", "getting married", "engagement",
        "engaged", "bride", "groom",
        "فرح", "جواز", "زواج", "خطوبة", "خطوبتي", "عرس", "عروسة", "عريس", "شبكة", "كتب كتاب",
    ),
    NeedCategory.EDUCATION: (
        "school", "tuition", "university", "college", "course", "exam", "semester",
        "مدارس", "مدرسة", "جامعة", "كلية", "دروس", "كورس", "امتحان", "امتحانات", "دراسة",
    ),
    NeedCategory.HOUSING: (
        "rent", "apartment", "flat", "house", "deposit", "furniture", "renovat*", "moving",
        "شقة", "إيجار", "أجار", "عفش", "فرش", "تشطيب", "سكن", "بيت",
    ),
    NeedCategory.BUSINESS: (
        "business", "shop", "store", "inventory", "stock", "equipment", "startup", "supplier",
        "مشروع", "محل", "بضاعة", "تجارة", "معدات",
    ),
    NeedCategory.TRAVEL: (
        "travel", "trip", "flight", "ticket", "visa", "hajj", "umrah", "vacation", "holiday",
        "passport",
        "سفر", "سافر", "مسافر", "رحلة", "تذكرة", "تذاكر", "فيزا", "حج", "عمرة", "مصيف",
    ),
}  # fmt: skip
# Tie-break when two categories match equally often.
_CATEGORY_PRIORITY = tuple(_CATEGORY_WORDS)
_CATEGORY_PATTERNS = {cat: _compile(words) for cat, words in _CATEGORY_WORDS.items()}
_FIXED_DATE_CATEGORIES = frozenset(
    {NeedCategory.WEDDING, NeedCategory.EDUCATION, NeedCategory.MEDICAL}
)


def _category(text: str) -> NeedCategory:
    text = text.replace("جواز سفر", "passport")  # Egyptian "passport", not "marriage"
    counts = {cat: len(p.findall(text)) for cat, p in _CATEGORY_PATTERNS.items()}
    best = max(_CATEGORY_PRIORITY, key=lambda c: (counts[c], -_CATEGORY_PRIORITY.index(c)))
    return best if counts[best] else NeedCategory.OTHER


# ---------- dates ----------
_MONTH_NAMES: dict[int, tuple[str, ...]] = {
    1: ("january", "jan", "يناير", "كانون الثاني"),
    2: ("february", "feb", "فبراير", "شباط"),
    3: ("march", "مارس", "آذار"),
    4: ("april", "apr", "أبريل", "ابريل", "نيسان"),
    5: ("may", "مايو", "أيار"),
    6: ("june", "jun", "يونيو", "يونيه", "حزيران"),
    7: ("july", "jul", "يوليو", "يوليه", "تموز"),
    8: ("august", "aug", "أغسطس", "آب"),
    9: ("september", "sept", "sep", "سبتمبر", "أيلول"),
    10: ("october", "oct", "أكتوبر", "تشرين الأول"),
    11: ("november", "nov", "نوفمبر", "تشرين الثاني"),
    12: ("december", "dec", "ديسمبر", "كانون الأول"),
}
# "may" is usually the verb ("I may need..."); only accept it in date context.
_MAY_CONTEXT = re.compile(
    r"(?:in|on|by|before|until|early|late|mid|end of|for|\d{1,2}(?:st|nd|rd|th)?)\s+$"
)


def _month_pattern(name: str) -> str:
    p = re.escape(name)
    if _is_ascii(name):
        return rf"(?<![a-z]){p}(?![a-z])"
    if len(name) < 4:  # e.g. "اب" (Aug) is also "father": no prefixes allowed
        return rf"(?<!\w){p}(?!\w)"
    return rf"(?<!\w)(?:[وفبل])?{p}(?!\w)"


_MONTH_PATTERNS = [
    (month, re.compile(_month_pattern(normalise(name))))
    for month, names in _MONTH_NAMES.items()
    for name in names
]
_DAY_AFTER = re.compile(r"^\s*(\d{1,2})(?:st|nd|rd|th)?(?!\d)")
_DAY_BEFORE = re.compile(r"(?<!\d)(\d{1,2})(?:st|nd|rd|th)?\s*(?:of\s+)?$")
_YEAR_AFTER = re.compile(r"^\s*,?\s*(20[2-3]\d)(?!\d)")  # "March 2027", "March 15, 2027"
_NUMERIC_DATE = re.compile(r"(?<!\d)(\d{1,2})/(\d{1,2})(?:/(\d{2}|\d{4}))?(?!\d)")  # d/m(/y)
_MONTH_NUMBER = re.compile(r"شهر\s*(\d{1,2})(?!\d)")  # Egyptian "شهر ٤" = April

_NUMBER_WORDS = {
    "a": 1, "one": 1, "two": 2, "three": 3, "four": 4, "five": 5, "six": 6,
    "seven": 7, "eight": 8, "nine": 9, "ten": 10, "eleven": 11, "twelve": 12,
    "واحد": 1, "اتنين": 2, "اثنين": 2, "تلات": 3, "تلاته": 3, "ثلاث": 3, "ثلاثه": 3,
    "اربع": 4, "اربعه": 4, "خمس": 5, "خمسه": 5, "ست": 6, "سته": 6, "سبع": 7,
    "سبعه": 7, "تمن": 8, "ثمان": 8, "تسع": 9, "تسعه": 9, "عشر": 10, "عشره": 10,
}  # fmt: skip
_N = r"(\d{1,2}|" + "|".join(sorted(_NUMBER_WORDS, key=len, reverse=True)) + ")"
_RELATIVE: list[tuple[re.Pattern[str], int | None]] = [
    (re.compile(r"next month|الشهر (?:الجاي|القادم|اللي جاي|الي جاي)"), 1),
    (re.compile(rf"(?:in|after|within)\s+{_N}\s+months?(?![a-z])"), None),
    (re.compile(r"بعد\s+شهرين"), 2),
    (re.compile(rf"بعد\s+{_N}\s+(?:شهور|اشهر|شهر)(?!\w)"), None),
    (re.compile(r"بعد\s+شهر(?!\w)"), 1),
]
_SEASONS: list[tuple[re.Pattern[str], int]] = [
    (
        re.compile(r"back to school|new school year|دخول المدارس|بدايه (?:الدراسه|المدارس)"),
        9,
    ),
    (re.compile(r"(?<![a-z])summer|(?<!\w)(?:ال)?صيف(?!\w)"), 7),
    (re.compile(r"(?<![a-z])spring|(?<!\w)(?:ال)?ربيع(?!\w)"), 4),
    (re.compile(r"(?:start|beginning) of (?:the |next )?year|(?:اول|بدايه) السنه"), 1),
]
_END_OF_YEAR = re.compile(r"end of (?:the |this )?year|(?:اخر|نهايه) السنه")


def _add_months(d: date, n: int) -> date:
    y, m = divmod(d.month - 1 + n, 12)
    return date(d.year + y, m + 1, 1)


def _next_occurrence(month: int, day: int, start: date) -> date | None:
    for year in (start.year, start.year + 1):
        try:
            d = date(year, month, day)
        except ValueError:  # e.g. 29 February in a non-leap year: try the next year
            continue
        if d >= start:
            return d
    return None


def _on(year: int, month: int, day: int) -> date | None:
    try:
        return date(year, month, day)
    except ValueError:
        return None


def _month_hits(text: str) -> list[tuple[int, int, re.Match[str]]]:
    """(position, month, match) for every month name in normalised `text`."""
    found: list[tuple[int, int, re.Match[str]]] = []
    for month, pattern in _MONTH_PATTERNS:
        for hit in pattern.finditer(text):
            if hit[0] == "may" and not (
                _MAY_CONTEXT.search(text[: hit.start()]) or _DAY_AFTER.match(text[hit.end() :])
            ):
                continue
            found.append((hit.start(), month, hit))
    return found


def month_mentions(text: str) -> set[int]:
    """Months (1-12) named in normalised `text`, in English or Arabic."""
    return {month for _, month, _ in _month_hits(text)}


def _parse_date(text: str, start: date) -> tuple[date | None, str | None]:
    """(target_date, matched phrase) in priority order: d/m, شهر N, month name, relative."""
    if m := _NUMERIC_DATE.search(text):
        day, month, year = int(m[1]), int(m[2]), m[3]
        if 1 <= month <= 12:
            if year:
                full = int(year) + 2000 if len(year) == 2 else int(year)
                return _on(full, month, day), m[0]
            return _next_occurrence(month, day, start), m[0]

    if m := _MONTH_NUMBER.search(text):
        month = int(m[1])
        if 1 <= month <= 12:
            return _next_occurrence(month, 1, start), m[0]

    found = _month_hits(text)
    if found:
        _, month, hit = min(found, key=lambda f: f[0])
        day, phrase = 1, hit[0]
        rest = text[hit.end() :]
        if d := _DAY_AFTER.match(rest):
            day, phrase, rest = int(d[1]), phrase + d[0], rest[d.end() :]
        elif d := _DAY_BEFORE.search(text[: hit.start()]):
            day, phrase = int(d[1]), d[0] + phrase
        if not 1 <= day <= 31:
            day = 1
        # Only a year attached to this date counts ("saving since 2024" elsewhere does not).
        if y := _YEAR_AFTER.match(rest):
            return _on(int(y[1]), month, day), f"{phrase} {y[1]}"
        return _next_occurrence(month, day, start), phrase

    for pattern, months in _RELATIVE:
        if m := pattern.search(text):
            n = months if months is not None else _to_int(m[1])
            if n is not None and 0 < n <= 36:
                return _add_months(start, n), m[0]

    if m := _END_OF_YEAR.search(text):
        return _next_occurrence(12, 1, start), m[0]
    for pattern, month in _SEASONS:
        if m := pattern.search(text):
            return _next_occurrence(month, 1, start), m[0]
    return None, None


def _to_int(token: str) -> int | None:
    return int(token) if token.isdigit() else _NUMBER_WORDS.get(token)


# ---------- amount (US dollars only) ----------
_NUM = r"(\d{1,3}(?:,\d{3})+(?:\.\d+)?|\d+(?:\.\d+)?)"
_MULT = r"\s*(k|الف|آلاف|الاف)?"
_USD = r"(?:\$|usd|dollars?|دولار(?:ات)?)"
_AMOUNT_PATTERNS = (
    re.compile(rf"\$\s*{_NUM}{_MULT}"),
    re.compile(rf"usd\s*{_NUM}{_MULT}"),
    re.compile(rf"{_NUM}{_MULT}\s*{_USD}"),
)


def usd_amounts(text: str) -> list[Decimal]:
    """Every US-dollar amount the member wrote, in order of appearance."""
    hits: list[tuple[int, Decimal]] = []
    for pattern in _AMOUNT_PATTERNS:
        for m in pattern.finditer(text):
            try:
                value = Decimal(m[1].replace(",", ""))
            except InvalidOperation:
                continue
            if m[2]:
                value *= 1000
            value = value.quantize(Decimal("0.01"))
            if Decimal(0) < value <= MAX_NEED_AMOUNT:
                hits.append((m.start(), value))
    return [v for _, v in sorted(hits)]


def within_horizon(target: date | None, start: date) -> bool:
    """A usable need date: not before the circle starts, not years after it.

    Past dates are rejected rather than kept: a past date looks maximally late
    to the optimiser and would let a member claim slot 1.
    """
    return target is not None and start <= target <= start + timedelta(days=HORIZON_DAYS)


# ---------- urgency / flexibility ----------
_LOW_URGENCY = _compile(
    ("no rush", "not urgent", "whenever", "any time", "anytime", "no hurry",
     "مش مستعجل", "مش ضروري", "مفيش استعجال", "مش لازم", "أي وقت", "براحتي")
)  # fmt: skip
_EMERGENCY = _compile(
    ("emergency", "asap", "as soon as possible", "immediately", "right away",
     "طوارئ", "عاجل", "حالا", "فورا", "ضروري جدا")
)  # fmt: skip
_URGENT = _compile(
    (
        "urgent",
        "must",
        "have to",
        "can't wait",
        "cannot wait",
        "لازم",
        "ضروري",
        "مستعجل",
    )
)
_FLEXIBLE = _compile(("flexible", "flexibility", "مرن", "مش فارقة"))
_NOT_FLEXIBLE = re.compile(
    r"(?<![a-z])inflexible|"
    r"(?<![a-z])(?:not|isn't|isnt|is not|never)\s+(?:very\s+|really\s+)?flexible"
    r"|(?<!\w)(?:مش|مو|ليس)\s"
    r"+مرن"
)
_URGENCY_CUES = (
    "emergency", "urgent", "asap", "must", "have to", "immediately",
    "طوارئ", "عاجل", "لازم", "ضروري", "مستعجل", "فورا",
)  # fmt: skip
# "not an emergency", "isn't urgent", "don't have to", "مش طوارئ" are not urgency.
_NEGATED_URGENCY = re.compile(
    r"(?:(?<![a-z])(?:not|isn't|isnt|is not|no|don't|dont|do not|doesn't|never)"
    r"|(?<!\w)(?:مش|مو|ليس|مفيش))\s+(?:an?\s+|really\s+|very\s+|that\s+|so\s+|محتاج\s+)?"
    r"(?:" + "|".join(re.escape(normalise(c)) for c in _URGENCY_CUES) + r")"
)


def _urgency_and_flexibility(
    text: str, category: NeedCategory, has_date: bool
) -> tuple[int, Flexibility]:
    if _LOW_URGENCY.search(text):
        return 1, "high"
    not_flexible = bool(_NOT_FLEXIBLE.search(text))
    text = _NOT_FLEXIBLE.sub(" ", _NEGATED_URGENCY.sub(" ", text))
    urgency = 3 if has_date else 2
    if _EMERGENCY.search(text):
        urgency = 5
    elif _URGENT.search(text):
        urgency = 4
    if category is NeedCategory.MEDICAL:
        urgency = max(urgency, 4)
    if not_flexible:
        return urgency, "low"
    if _FLEXIBLE.search(text):
        return min(urgency, 2), "high"
    if urgency >= 4 or (has_date and category in _FIXED_DATE_CATEGORIES):
        return urgency, "low"
    return urgency, "medium" if has_date else "high"


# ---------- public API ----------
@dataclass(frozen=True)
class ExtractionResult:
    """The extraction plus how it was produced, for the AgentDecision audit row."""

    need: NeedExtraction
    source: Literal["llm", "rules"]
    model: str | None = None
    notes: list[str] = field(default_factory=list)


def extract_need_rules(raw_text: str, circle: CircleSnapshot) -> NeedExtraction:
    """Deterministic extraction; never raises on member text."""
    text = normalise(raw_text[:MAX_TEXT_CHARS])
    category = _category(text)
    target, hint = _parse_date(text, circle.start_date)
    if not within_horizon(target, circle.start_date):
        target, hint = None, None
    amounts = usd_amounts(text)
    amount = amounts[0] if amounts else None
    urgency, flexibility = _urgency_and_flexibility(text, category, target is not None)
    confidence = (
        0.35
        + 0.2 * (category is not NeedCategory.OTHER)
        + 0.2 * (target is not None)
        + 0.1 * (amount is not None)
    )
    return NeedExtraction(
        category=category,
        target_date=target,
        amount_needed=amount,
        urgency=urgency,
        flexibility=flexibility,
        confidence=round(confidence, 2),
        raw_month_hint=hint,
    )


_LLM_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "category": {"type": "string", "enum": [c.value for c in NeedCategory]},
        "target_date": {"type": ["string", "null"], "format": "date"},
        "amount_needed": {"type": ["string", "null"]},
        "urgency": {"type": "integer", "minimum": 1, "maximum": 5},
        "flexibility": {"type": "string", "enum": ["low", "medium", "high"]},
        "confidence": {"type": "number", "minimum": 0, "maximum": 1},
    },
    "required": [
        "category",
        "target_date",
        "amount_needed",
        "urgency",
        "flexibility",
        "confidence",
    ],
}


@cache
def _prompt_template() -> str:
    return PROMPT_PATH.read_text(encoding="utf-8")


def build_prompt(raw_text: str, locale: Locale, circle: CircleSnapshot) -> str:
    template = _prompt_template()
    return (
        template.replace("{{start_date}}", circle.start_date.isoformat())
        .replace("{{locale}}", locale)
        .replace("{{member_text}}", wrap_untrusted(raw_text))
    )


def _ground(
    data: dict[str, Any], raw_text: str, circle: CircleSnapshot
) -> tuple[NeedExtraction, list[str]]:
    """Validate LLM JSON and drop any fact the member's text does not support."""
    notes: list[str] = []
    clean = {k: data.get(k) for k in _LLM_SCHEMA["properties"]}
    if isinstance(clean["amount_needed"], (int, float)):
        clean["amount_needed"] = str(clean["amount_needed"])
    if isinstance(clean["confidence"], (int, float)):
        clean["confidence"] = min(max(float(clean["confidence"]), 0.0), 1.0)
    need = NeedExtraction.model_validate(clean)

    if need.amount_needed is not None and need.amount_needed not in usd_amounts(
        normalise(raw_text[:MAX_TEXT_CHARS])
    ):
        notes.append(f"dropped amount {need.amount_needed}: not a dollar amount in the text")
        need = need.model_copy(update={"amount_needed": None})

    if need.target_date is not None and not within_horizon(need.target_date, circle.start_date):
        notes.append(f"dropped target_date {need.target_date}: outside the circle's horizon")
        need = need.model_copy(update={"target_date": None})
    return need, notes


async def extract_need_detailed(
    raw_text: str, locale: Locale, circle: CircleSnapshot, llm: LLMClient | None = None
) -> ExtractionResult:
    raw_text = raw_text[:MAX_TEXT_CHARS]
    if llm is not None:
        try:
            prompt = build_prompt(raw_text, locale, circle)
            data = await asyncio.wait_for(llm.generate_json(prompt, _LLM_SCHEMA), LLM_TIMEOUT_S)
            need, notes = _ground(data, raw_text, circle)
            return ExtractionResult(need, "llm", llm.model_name, notes)
        except Exception as exc:  # noqa: BLE001 - any LLM failure falls back to the rules
            note = f"llm failed ({type(exc).__name__}); used rules"
            return ExtractionResult(extract_need_rules(raw_text, circle), "rules", None, [note])
    return ExtractionResult(extract_need_rules(raw_text, circle), "rules")


async def extract_need(
    raw_text: str, locale: Locale, circle: CircleSnapshot, llm: LLMClient | None = None
) -> NeedExtraction:
    """Interface 1 (plan §7.4)."""
    return (await extract_need_detailed(raw_text, locale, circle, llm)).need
