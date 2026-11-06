"""Message copy for Smart Chase (plan §13.4)."""

from __future__ import annotations

from typing import Literal

ChaseKind = Literal["nudge", "invoice", "reminder"]
Locale = Literal["en", "ar-EG"]

# `{amount}` is the 2-dp amount, `{due}` is YYYY-MM-DD, `{circle}` is the circle name.
TEMPLATES: dict[tuple[ChaseKind, Locale], str] = {
    ("nudge", "en"): (
        "Hi {name}, your Gam'eya contribution of ${amount} for cycle {cycle_index} is due on "
        "{due}. Paying on time keeps the circle on track for everyone."
    ),
    ("nudge", "ar-EG"): (
        "أهلاً {name}، قسط الجمعية ${amount} للدورة {cycle_index} ميعاده {due}. "
        "الدفع في الميعاد بيخلّي الجمعية ماشية لكل الناس."
    ),
    ("invoice", "en"): (
        "Hi {name}, this invoice covers your unpaid Gam'eya contribution of ${amount} for "
        "cycle {cycle_index} of {circle}."
    ),
    ("invoice", "ar-EG"): (
        "أهلاً {name}، الفاتورة دي عن قسط الجمعية ${amount} اللي لسه مدفعش للدورة "
        "{cycle_index} في {circle}."
    ),
    ("reminder", "en"): (
        "Friendly reminder: ${amount} for cycle {cycle_index} of {circle} is still open."
    ),
    ("reminder", "ar-EG"): (
        "تذكير بسيط: لسه فيه ${amount} للدورة {cycle_index} في {circle} مستني الدفع."
    ),
}
