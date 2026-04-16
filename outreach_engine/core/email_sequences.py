# outreach_engine/core/email_sequences.py

from typing import Dict, Optional

EMAIL_SEQUENCES: Dict[str, Dict] = {
    "automation_outreach": {
        "name": "automation_outreach",
        "steps": [
            {"step": 0, "template": "cold_email"},
            {"step": 1, "template": "followup_1"},
            {"step": 2, "template": "case_study"},
            {"step": 3, "template": "value_email"},
            {"step": 4, "template": "final_nudge"}
        ]
    }
}


def get_sequence(name: str) -> Optional[Dict]:
    return EMAIL_SEQUENCES.get(name)


def get_email_for_step(sequence_name: str, step: int) -> Optional[str]:
    sequence = get_sequence(sequence_name)
    if not sequence:
        return None

    steps = sequence.get("steps", [])
    if not isinstance(steps, list):
        return None

    # strict match first
    for s in steps:
        if s.get("step") == step:
            return s.get("template")

    # fallback: closest lower step
    valid_steps = [s for s in steps if isinstance(s.get("step"), int)]
    valid_steps.sort(key=lambda x: x["step"])

    fallback = None
    for s in valid_steps:
        if s["step"] <= step:
            fallback = s.get("template")

    return fallback