# processing/people_extractor.py

from core.performance import timer

TITLE_SCORES = {
    "founder": 5,
    "ceo": 4,
    "chief executive": 4,
    "operations manager": 4,
    "head of growth": 4,
    "marketing director": 3,
    "director": 3,
    "manager": 2,
}

EXCLUDED_KEYWORDS = [
    "assistant",
    "intern",
    "junior",
    "trainee",
    "student"
]

def calculate_seniority_score(title: str) -> int:
    if not title:
        return 0
    title_lower = title.lower()
    if any(excluded in title_lower for excluded in EXCLUDED_KEYWORDS):
        return 0
    for keyword, score in TITLE_SCORES.items():
        if keyword in title_lower:
            return score
    return 0

@timer("Extract Decision Makers")
def extract_decision_makers(company: dict, people_list: list) -> list:
    """
    Extract top decision-makers based on title seniority.
    Wrapped with performance timer.
    """
    qualified = []
    for person in people_list:
        title = person.get("title", "")
        seniority_score = calculate_seniority_score(title)
        if seniority_score > 0:
            qualified.append({
                "company": company.get("company"),
                "website": company.get("website"),
                "country": company.get("country"),
                "person_name": person.get("name"),
                "title": title,
                "seniority_score": seniority_score,
                "person_score": seniority_score * 2
            })

    qualified.sort(key=lambda x: x["seniority_score"], reverse=True)
    return qualified[:2]  # top 2