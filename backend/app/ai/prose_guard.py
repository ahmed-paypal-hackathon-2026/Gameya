"""Checks for the one sentence of LLM prose we ever show to members.

Design rule: the engine writes every number, date and name. The LLM may only
add a short "why" sentence, so that sentence must contain no digits, no month,
no member name, nothing that reads like a link or an instruction, and nothing
about reliability. Anything else is replaced by a fixed template.
"""

from __future__ import annotations

import re

from app.ai.needs_extractor import month_mentions, normalise

MAX_PROSE_CHARS = 240

_DIGIT = re.compile(r"\d")
# Spelled-out numbers can contradict the engine just as well as digits can.
_NUMBER_WORD = re.compile(
    r"(?<![a-z])(?:zero|one|two|three|four|five|six|seven|eight|nine|ten|eleven|twelve|"
    r"twenty|thirty|forty|fifty|sixty|seventy|eighty|ninety|hundred|thousand|million|"
    r"dozen|half|percent|once|twice|single|double|triple)(?![a-z])|"
    r"(?<!\w)(?:واحد|اتنين|اثنين|تلات|تلاته|ثلاث|ثلاثه|اربع|اربعه|خمس|خمسه|ست|سته|سبع|"
    r"سبعه|تمن|تمانيه|ثمان|تسع|تسعه|عشر|عشره|عشرين|مية|ميه|مائه|الف|مليون|نص|نصف|"
    r"ربع|بالميه)(?!\w)"
)
# Links, markup and contact details could turn a card into a phishing message.
_UNSAFE = re.compile(r"https?:|www\.|<|>|@|\[|\]\(|\$|%")
# Reliability is private; an explanation must never hint at it.
_PRIVATE = re.compile(
    r"reliab|risk|score|credit|trust|late pa|history|"
    r"موثوق|مخاطر|تقييم|سجل|تاخير"
)
# Explanations inform; they never ask a member to do anything.
_INSTRUCTION = re.compile(
    # English: whole words only, so "shared" or "asked" in a normal sentence is fine.
    r"(?<![a-z])(?:password|passcode|pin|otp|login|log in|verify|confirm|reply|send|"
    r"transfer|pay (?:to|at|now)|click|whatsapp|telegram|call|contact|share|account|"
    r"details|cancel(?:l?ed)?|on hold|refund|ask|bank|card|iban|message|dm)(?![a-z])|"
    r"كلمه السر|باسورد|الرقم السري|ابعت|شارك|حساب|بيانات|الغي|اتلغي|استرد|اسال|كارت|بنك|"
    r"حول (?:ل|ال|علي)|اتصل|(?<!\w)كلم(?:ني|ه|ها)?(?!\w)|واتس"
)


def prose_problem(text: str, names: list[str]) -> str | None:
    """Why `text` may not be shown, or None if it is a safe, fact-free sentence."""
    if not text.strip():
        return "empty"
    if len(text) > MAX_PROSE_CHARS:
        return "too long"
    norm = normalise(text)
    if _DIGIT.search(norm):
        return "contains a number (the engine writes all numbers)"
    if _NUMBER_WORD.search(norm):
        return "contains a number word (the engine writes all numbers)"
    if month_mentions(norm):
        return "contains a month (the engine writes all dates)"
    if _UNSAFE.search(norm):
        return "contains a link, markup, contact detail or symbol"
    if _PRIVATE.search(norm):
        return "mentions private reliability information"
    if _INSTRUCTION.search(norm):
        return "reads like an instruction to the member"
    for name in names:
        if name.strip() and normalise(name) in norm:
            return "mentions a member by name"
    return None
