"""
Career Preferences & Tailored Placement Recommendations Service (#task-career-preferences)
==========================================================================================
Enables candidates to manage their career preferences:
- Target job roles & career tracks
- Target / dream hiring companies
- Preferred geographic locations & remote/hybrid work modes
- Desired role types (Full-time vs Internship) & minimum compensation
- Primary technical skill interests & target industry domains

Provides intelligent, multi-dimensional recommendations based on selections:
1. Preference-tailored campus recruitment openings with fit explanations
2. Role preparation learning tracks from the role preparation catalog
3. High-yield skill gap recommendations to bridge qualifications for dream roles
"""

import json
import logging
import re
from pathlib import Path
from typing import Dict, Any, List, Optional, Set, Union

import config
from utils.database import get_db_connection, save_student_profile, get_student_profile
from utils.job_data_source import JsonFileJobDataSource

logger = logging.getLogger(__name__)

# Standard option taxonomy for candidate selections
DEFAULT_PREFERENCE_OPTIONS: Dict[str, List[str]] = {
    "roles": [
        "Software Engineer",
        "Backend Developer",
        "Frontend Developer",
        "Full Stack Developer",
        "Data Analyst",
        "Data Scientist",
        "Machine Learning Engineer",
        "Cloud / DevOps Engineer",
        "Mobile Application Developer",
        "QA / Automation Engineer"
    ],
    "companies": [
        "Google",
        "Microsoft",
        "Amazon",
        "Stripe",
        "Uber",
        "Adobe",
        "Meta",
        "Salesforce",
        "Goldman Sachs",
        "Oracle",
        "Cisco",
        "Infosys",
        "TCS",
        "Wipro"
    ],
    "locations": [
        "Bengaluru",
        "Hyderabad",
        "Pune",
        "Mumbai",
        "Delhi NCR",
        "Chennai",
        "Kolkata",
        "Remote",
        "Any Location"
    ],
    "work_modes": [
        "Remote",
        "Hybrid",
        "On-site",
        "Any"
    ],
    "role_types": [
        "Full-time",
        "Internship",
        "Any"
    ],
    "industries": [
        "Product & SaaS",
        "Fintech & Banking",
        "AI & Machine Learning",
        "E-Commerce & Retail",
        "Enterprise Cloud & Infrastructure",
        "EdTech & HealthTech"
    ],
    "popular_skills": [
        "Python",
        "Java",
        "C++",
        "JavaScript",
        "TypeScript",
        "React",
        "Node.js",
        "FastAPI",
        "Django",
        "SQL",
        "PostgreSQL",
        "Docker",
        "Kubernetes",
        "AWS",
        "System Design",
        "Data Structures",
        "Algorithms",
        "Machine Learning"
    ]
}


def _clean_list(raw_val: Any) -> List[str]:
    """Normalizes input list, comma-separated string, or JSON string into clean string list."""
    if not raw_val:
        return []

    if isinstance(raw_val, str):
        raw_val = raw_val.strip()
        if raw_val.startswith("[") and raw_val.endswith("]"):
            try:
                parsed = json.loads(raw_val)
                if isinstance(parsed, list):
                    return [str(item).strip() for item in parsed if str(item).strip()]
            except Exception:
                pass
        # Comma-separated fallback
        return [p.strip() for p in raw_val.split(",") if p.strip()]

    if isinstance(raw_val, (list, tuple, set)):
        result: List[str] = []
        seen = set()
        for item in raw_val:
            s = str(item).strip()
            if s and s.lower() not in seen:
                seen.add(s.lower())
                result.append(s)
        return result

    return [str(raw_val).strip()]


def get_available_preference_options() -> Dict[str, Any]:
    """Returns catalog of standard preference options for forms and dropdowns."""
    return DEFAULT_PREFERENCE_OPTIONS


def get_career_preferences(student_id: str) -> Dict[str, Any]:
    """
    Retrieves candidate's career preferences from database.
    Falls back gracefully to student profile target_role, target_company, and skills
    if no preferences record exists yet.
    """
    s_id = (student_id or "default_student").strip()
    conn = get_db_connection()
    try:
        cursor = conn.cursor()
        cursor.execute("SELECT * FROM career_preferences WHERE student_id = ?", (s_id,))
        row = cursor.fetchone()

        if row:
            rec = dict(row)
            return {
                "student_id": s_id,
                "target_roles": _clean_list(rec.get("target_roles")),
                "target_companies": _clean_list(rec.get("target_companies")),
                "preferred_locations": _clean_list(rec.get("preferred_locations")),
                "preferred_work_modes": _clean_list(rec.get("preferred_work_modes")),
                "preferred_role_types": _clean_list(rec.get("preferred_role_types")),
                "expected_salary_min": float(rec.get("expected_salary_min") or 0.0),
                "preferred_industries": _clean_list(rec.get("preferred_industries")),
                "primary_skills": _clean_list(rec.get("primary_skills")),
                "notes": rec.get("notes") or "",
                "updated_at": rec.get("updated_at") or "",
                "is_configured": True
            }

        # Fallback to student_profiles record for seamless initial setup
        prof = get_student_profile(s_id) or {}
        role = (prof.get("target_role") or "").strip()
        comp = (prof.get("target_company") or "").strip()
        skills = prof.get("skills") or []

        return {
            "student_id": s_id,
            "target_roles": [role] if role else ["Software Engineer"],
            "target_companies": [comp] if comp else ["Google"],
            "preferred_locations": ["Bengaluru", "Remote"],
            "preferred_work_modes": ["Hybrid", "Remote"],
            "preferred_role_types": ["Full-time"],
            "expected_salary_min": 10.0,
            "preferred_industries": ["Product & SaaS"],
            "primary_skills": skills if skills else ["Python", "SQL", "Data Structures"],
            "notes": "",
            "updated_at": "",
            "is_configured": False
        }
    except Exception as e:
        logger.error(f"Error fetching career preferences for {student_id}: {e}")
        return {
            "student_id": s_id,
            "target_roles": ["Software Engineer"],
            "target_companies": ["Google"],
            "preferred_locations": ["Bengaluru", "Remote"],
            "preferred_work_modes": ["Hybrid", "Remote"],
            "preferred_role_types": ["Full-time"],
            "expected_salary_min": 8.0,
            "preferred_industries": ["Product & SaaS"],
            "primary_skills": ["Python", "SQL", "Data Structures"],
            "notes": "",
            "updated_at": "",
            "is_configured": False
        }
    finally:
        conn.close()


def save_career_preferences(student_id: str, payload: Dict[str, Any]) -> Dict[str, Any]:
    """
    Validates and persists candidate career preferences in database.
    Synchronizes primary target_role, target_company, and skills with student_profiles
    to preserve 100% compatibility across ATS, report, and dashboard evaluations.
    """
    if not payload or not isinstance(payload, dict):
        raise ValueError("Preferences payload must be a non-empty dictionary.")

    s_id = (student_id or "default_student").strip()

    target_roles = _clean_list(payload.get("target_roles") or payload.get("roles") or payload.get("target_role"))
    target_companies = _clean_list(payload.get("target_companies") or payload.get("companies") or payload.get("target_company"))
    preferred_locations = _clean_list(payload.get("preferred_locations") or payload.get("locations") or payload.get("location"))
    preferred_work_modes = _clean_list(payload.get("preferred_work_modes") or payload.get("work_modes") or payload.get("work_mode"))
    preferred_role_types = _clean_list(payload.get("preferred_role_types") or payload.get("role_types") or payload.get("role_type"))
    preferred_industries = _clean_list(payload.get("preferred_industries") or payload.get("industries"))
    primary_skills = _clean_list(payload.get("primary_skills") or payload.get("skills"))
    notes = str(payload.get("notes") or "").strip()

    raw_salary = payload.get("expected_salary_min") if payload.get("expected_salary_min") is not None else payload.get("salary_min", 0.0)
    try:
        expected_salary_min = max(0.0, float(raw_salary))
    except (ValueError, TypeError):
        expected_salary_min = 0.0

    conn = get_db_connection()
    try:
        cursor = conn.cursor()
        cursor.execute("""
            INSERT INTO career_preferences (
                student_id, target_roles, target_companies, preferred_locations,
                preferred_work_modes, preferred_role_types, expected_salary_min,
                preferred_industries, primary_skills, notes, updated_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, CURRENT_TIMESTAMP)
            ON CONFLICT(student_id) DO UPDATE SET
                target_roles = excluded.target_roles,
                target_companies = excluded.target_companies,
                preferred_locations = excluded.preferred_locations,
                preferred_work_modes = excluded.preferred_work_modes,
                preferred_role_types = excluded.preferred_role_types,
                expected_salary_min = excluded.expected_salary_min,
                preferred_industries = excluded.preferred_industries,
                primary_skills = excluded.primary_skills,
                notes = excluded.notes,
                updated_at = CURRENT_TIMESTAMP
        """, (
            s_id,
            json.dumps(target_roles),
            json.dumps(target_companies),
            json.dumps(preferred_locations),
            json.dumps(preferred_work_modes),
            json.dumps(preferred_role_types),
            expected_salary_min,
            json.dumps(preferred_industries),
            json.dumps(primary_skills),
            notes
        ))
        conn.commit()

        # Sync primary role, target company, and skills with student_profiles
        try:
            primary_role = target_roles[0] if target_roles else None
            primary_comp = target_companies[0] if target_companies else None
            save_student_profile(
                student_id=s_id,
                target_role=primary_role,
                target_company=primary_comp,
                skills=primary_skills if primary_skills else None
            )
        except Exception as sync_err:
            logger.warning(f"Could not sync career preferences with student profile: {sync_err}")

        return get_career_preferences(s_id)
    finally:
        conn.close()


def _extract_salary_number(salary_str: str) -> float:
    """Extracts numeric LPA salary estimate from salary string e.g. '₹24 - ₹38 LPA' -> 24.0."""
    if not salary_str:
        return 0.0
    matches = re.findall(r"\b(\d+(?:\.\d+)?)\b", salary_str)
    if matches:
        return float(matches[0])
    return 0.0


def get_career_recommendations(
    student_id: str,
    preferences: Optional[Dict[str, Any]] = None,
    limit: int = 12
) -> Dict[str, Any]:
    """
    Computes tailored recommendations based on candidate's career preferences:
    1. Preference-tailored active recruitment jobs scored and explained
    2. Curated role preparation learning tracks aligned with target roles
    3. High-yield skill gap recommendations to bridge dream role requirements
    """
    s_id = (student_id or "default_student").strip()
    prefs = preferences if (preferences and isinstance(preferences, dict)) else get_career_preferences(s_id)

    target_roles = [r.lower() for r in prefs.get("target_roles", [])]
    target_companies = [c.lower() for c in prefs.get("target_companies", [])]
    preferred_locs = [l.lower() for l in prefs.get("preferred_locations", [])]
    preferred_work_modes = [w.lower() for w in prefs.get("preferred_work_modes", [])]
    preferred_role_types = [t.lower() for t in prefs.get("preferred_role_types", [])]
    expected_salary = float(prefs.get("expected_salary_min") or 0.0)
    primary_skills = {s.lower() for s in prefs.get("primary_skills", [])}

    # 1. Job Recommendations from live campus drive feed
    data_source = JsonFileJobDataSource()
    all_jobs = data_source.get_jobs()
    scored_jobs: List[Dict[str, Any]] = []

    for job in all_jobs:
        job_title = (job.get("title") or "").lower()
        job_comp = (job.get("company") or "").lower()
        job_loc = (job.get("location") or "").lower()
        job_wm = (job.get("work_mode") or "").lower()
        job_rt = (job.get("role_type") or "").lower()
        req_skills = [s.strip() for s in job.get("required_skills", [])]
        req_skills_lower = {s.lower() for s in req_skills}

        match_score = 0
        match_reasons: List[str] = []

        # A. Target Role Alignment (+35 points)
        role_matched = False
        matched_role_name = ""
        for r in target_roles:
            if r in job_title or any(r in s for s in req_skills_lower):
                role_matched = True
                matched_role_name = r.title()
                break
        if role_matched:
            match_score += 35
            match_reasons.append(f"Target role: {matched_role_name}")
        elif not target_roles:
            match_score += 20

        # B. Dream / Target Company (+25 points)
        comp_matched = False
        for c in target_companies:
            if c in job_comp or job_comp in c:
                comp_matched = True
                break
        if comp_matched:
            match_score += 25
            match_reasons.append(f"Dream company: {job.get('company')}")
        elif not target_companies:
            match_score += 15

        # C. Location Preference (+15 points)
        any_loc = any("any" in l for l in preferred_locs)
        loc_matched = any_loc or any(l in job_loc or job_loc in l for l in preferred_locs)
        if loc_matched:
            match_score += 15
            match_reasons.append(f"Preferred location: {job.get('location')}")
        elif not preferred_locs:
            match_score += 10

        # D. Work Mode Alignment (+10 points)
        any_wm = any("any" in w for w in preferred_work_modes)
        wm_matched = any_wm or any(w in job_wm for w in preferred_work_modes)
        if wm_matched:
            match_score += 10
            match_reasons.append(f"{job.get('work_mode')} mode")

        # E. Role Type Alignment (+10 points)
        any_rt = any("any" in t for t in preferred_role_types)
        rt_matched = any_rt or any(t in job_rt for t in preferred_role_types)
        if rt_matched:
            match_score += 10
            match_reasons.append(f"{job.get('role_type')}")

        # F. Expected Salary (+5 points)
        job_salary_num = _extract_salary_number(job.get("salary_range", ""))
        if expected_salary <= 0 or job_salary_num >= expected_salary:
            match_score += 5

        # Skill overlap evaluation
        matched_skills = [s for s in req_skills if s.lower() in primary_skills]
        missing_skills = [s for s in req_skills if s.lower() not in primary_skills]

        # Fit summary
        if match_reasons:
            fit_reason = f"Matches your selections: {', '.join(match_reasons)}."
        else:
            fit_reason = "Aligns with entry-level technical requirements."

        # Tier assignment
        if match_score >= 80:
            badge = "success"
            tier = "Top Match"
        elif match_score >= 60:
            badge = "primary"
            tier = "Strong Fit"
        elif match_score >= 40:
            badge = "info"
            tier = "Moderate Match"
        else:
            badge = "secondary"
            tier = "Opportunity"

        scored_jobs.append({
            **job,
            "preference_match_score": match_score,
            "tier_badge": badge,
            "tier_label": tier,
            "fit_reason": fit_reason,
            "matched_preferences": match_reasons,
            "matched_skills": matched_skills,
            "missing_skills": missing_skills,
            "is_target_company": comp_matched,
            "is_target_role": role_matched
        })

    # Sort descending by preference match score, then salary/company
    scored_jobs.sort(
        key=lambda j: (j.get("preference_match_score", 0), j.get("is_target_company", False)),
        reverse=True
    )

    # 2. Role Preparation Tracks
    from agents.role_prep_agent import SUPPORTED_ROLES, ROLE_CATALOG
    recommended_tracks: List[Dict[str, Any]] = []

    # Map target roles to catalog
    for target_role_str in prefs.get("target_roles", []):
        matched_role = None
        for r_entry in SUPPORTED_ROLES:
            if r_entry["name"].lower() in target_role_str.lower() or target_role_str.lower() in r_entry["name"].lower():
                matched_role = r_entry
                break
        if not matched_role and SUPPORTED_ROLES:
            matched_role = SUPPORTED_ROLES[0]

        if matched_role and matched_role["name"] in ROLE_CATALOG:
            cat_data = ROLE_CATALOG[matched_role["name"]]
            recommended_tracks.append({
                "id": matched_role["id"],
                "role_name": matched_role["name"],
                "category": matched_role.get("category", "Engineering"),
                "tagline": cat_data.get("tagline", matched_role.get("tagline")),
                "icon": matched_role.get("icon", "fa-solid fa-code"),
                "core_technologies": cat_data.get("core_technologies", [])[:5],
                "important_concepts": cat_data.get("important_concepts", [])[:3]
            })

    if not recommended_tracks and SUPPORTED_ROLES:
        # Default track
        first_role = SUPPORTED_ROLES[0]
        cat_data = ROLE_CATALOG.get(first_role["name"], {})
        recommended_tracks.append({
            "id": first_role["id"],
            "role_name": first_role["name"],
            "category": first_role.get("category", "Engineering"),
            "tagline": cat_data.get("tagline", first_role.get("tagline")),
            "icon": first_role.get("icon", "fa-solid fa-code"),
            "core_technologies": cat_data.get("core_technologies", [])[:5],
            "important_concepts": cat_data.get("important_concepts", [])[:3]
        })

    # 3. High-Yield Skill Gaps
    # Tally required skills across the top matching jobs
    skill_frequency: Dict[str, int] = {}
    for j in scored_jobs[:10]:
        for s in j.get("required_skills", []):
            skill_frequency[s] = skill_frequency.get(s, 0) + 1

    sorted_freq = sorted(skill_frequency.items(), key=lambda x: -x[1])
    top_focus_skills: List[Dict[str, Any]] = [
        {
            "skill": item[0],
            "frequency_in_preferred_jobs": item[1],
            "already_acquired": item[0].lower() in primary_skills
        }
        for item in sorted_freq[:8]
    ]

    total_matched = len([j for j in scored_jobs if j.get("preference_match_score", 0) >= 30])

    return {
        "status": "success",
        "preferences": prefs,
        "total_jobs_matched": total_matched,
        "recommended_jobs": scored_jobs[:limit],
        "recommended_role_tracks": recommended_tracks,
        "top_focus_skills": top_focus_skills,
        "summary": f"Identified {total_matched} matching campus openings and {len(recommended_tracks)} preparation tracks tailored to your career selections."
    }
