# outreach_engine/core/email_sequences.py

from typing import Dict, List, Optional

# ---------------------------------------------------
# Example Email Sequences
# ---------------------------------------------------

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
    },
    # You can add more sequences here
    # "saas_launch": {...}
}

# ---------------------------------------------------
# Functions
# ---------------------------------------------------

def get_sequence(name: str) -> Optional[Dict]:
    """
    Retrieve the email sequence by name.
    Returns None if not found.
    """
    return EMAIL_SEQUENCES.get(name)


def get_email_for_step(sequence_name: str, step: int) -> Optional[str]:
    """
    Returns the template name for a given step in a sequence.
    """
    sequence = get_sequence(sequence_name)
    if not sequence:
        return None

    for s in sequence["steps"]:
        if s["step"] == step:
            return s["template"]

    return None