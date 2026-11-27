"""Build plain-English explanations for the dashboard's aggregate scores."""

from typing import Any, Dict, List

from utils.skill_evidence_mapper import extract_contextual_snippet


def _clean_list(value: Any) -> List[str]:
    if isinstance(value, str):
        return [value.strip()] if value.strip() else []
    if isinstance(value, list):
        return [str(item).strip() for item in value if str(item).strip()]
    return []


def _skill_evidence(resume_text: str, skills: List[str], limit: int = 2) -> List[str]:
    evidence = []
    for skill in skills:
        snippet = extract_contextual_snippet(resume_text, skill)
        if snippet:
            evidence.append(f"{skill}: “{snippet}”")
        if len(evidence) >= limit:
            break
    return evidence


def build_score_explanations(
    resume_analysis: Dict[str, Any],
    job_match: Dict[str, Any],
    resume_text: str = "",
) -> Dict[str, List[Dict[str, Any]]]:
    """Return concise score factors without presenting them as hiring predictions."""
    matched_skills = _clean_list(
        job_match.get("matched_skills") or job_match.get("matching_skills")
    )
    missing_skills = _clean_list(job_match.get("missing_skills"))
    ats_missing = _clean_list(resume_analysis.get("missing_skills"))
    formatting = _clean_list(resume_analysis.get("formatting_suggestions"))
    weaknesses = _clean_list(resume_analysis.get("weaknesses"))
    strengths = _clean_list(resume_analysis.get("strengths"))
    certifications = _clean_list(job_match.get("required_certifications"))

    keyword_evidence = _skill_evidence(resume_text, matched_skills)
    if matched_skills and missing_skills:
        keyword_detail = (
            f"{len(matched_skills)} requested skill(s) were matched, while "
            f"{len(missing_skills)} were not found in the analysis."
        )
    elif matched_skills:
        keyword_detail = f"{len(matched_skills)} relevant skill(s) were matched."
    else:
        keyword_detail = "Few or no target skills were matched in the analysis."

    ats_factors: List[Dict[str, Any]] = [
        {
            "title": "Relevant keywords and skills",
            "detail": keyword_detail,
            "evidence": keyword_evidence,
            "gap": (
                f"Not found: {', '.join(ats_missing[:4])}."
                if ats_missing
                else "No missing resume skills were reported."
            ),
        },
        {
            "title": "Resume structure and readability",
            "detail": (
                "The analysis flagged formatting items that can make resume content "
                "harder for an ATS to parse."
                if formatting
                else "No formatting problems were flagged in the resume analysis."
            ),
            "evidence": formatting[:2],
            "gap": "",
        },
        {
            "title": "Clarity of candidate value",
            "detail": (
                "The analysis found strengths in the resume content."
                if strengths
                else "The analysis found limited positive evidence to support this area."
            ),
            "evidence": strengths[:2],
            "gap": weaknesses[0] if weaknesses else "",
        },
    ]

    match_factors: List[Dict[str, Any]] = [
        {
            "title": "Required skill coverage",
            "detail": (
                f"{len(matched_skills)} skill(s) align with the target role."
                if matched_skills
                else "No matching skills were identified for the target role."
            ),
            "evidence": keyword_evidence,
            "gap": (
                f"Still missing: {', '.join(missing_skills[:4])}."
                if missing_skills
                else "No missing target skills were reported."
            ),
        },
        {
            "title": "Role-specific requirements",
            "detail": (
                "The score also reflects how closely the resume content matches "
                "the supplied job description."
                if job_match
                else "A job-match analysis was not available."
            ),
            "evidence": _clean_list(job_match.get("recommended_improvements"))[:2],
            "gap": "",
        },
        {
            "title": "Certifications requested by the role",
            "detail": (
                f"{len(certifications)} certification requirement(s) were identified."
                if certifications
                else "No certification requirements were identified."
            ),
            "evidence": certifications[:2],
            "gap": "",
        },
    ]

    return {"ats": ats_factors, "job_match": match_factors}
