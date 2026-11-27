"""
Unit and API integration tests for Career Preferences and Tailored Recommendations (#task-career-preferences).
"""

import pytest
from utils.database import init_db, get_student_profile
from utils.career_preferences_service import (
    get_available_preference_options,
    get_career_preferences,
    save_career_preferences,
    get_career_recommendations,
)


@pytest.fixture(autouse=True)
def setup_test_database():
    """Ensure clean schema for tests."""
    init_db()


def test_get_available_preference_options():
    """Verify standard preference taxonomy options are returned."""
    options = get_available_preference_options()
    assert "roles" in options
    assert "companies" in options
    assert "locations" in options
    assert "work_modes" in options
    assert "role_types" in options
    assert "Software Engineer" in options["roles"]
    assert "Google" in options["companies"]
    assert "Bengaluru" in options["locations"]


def test_get_career_preferences_defaults():
    """Verify default preferences are provided when user has not saved any yet."""
    student_id = "test_student_prefs_default"
    prefs = get_career_preferences(student_id)
    assert prefs["student_id"] == student_id
    assert isinstance(prefs["target_roles"], list)
    assert isinstance(prefs["target_companies"], list)
    assert isinstance(prefs["preferred_locations"], list)
    assert prefs["expected_salary_min"] >= 0.0
    assert prefs["is_configured"] is False


def test_save_and_retrieve_career_preferences():
    """Verify persisting and retrieving custom candidate preferences."""
    student_id = "test_student_save_prefs_1"
    payload = {
        "target_roles": ["Software Engineer", "Backend Developer"],
        "target_companies": ["Google", "Stripe"],
        "preferred_locations": ["Bengaluru", "Remote"],
        "preferred_work_modes": ["Remote", "Hybrid"],
        "preferred_role_types": ["Full-time"],
        "expected_salary_min": 18.5,
        "primary_skills": ["Python", "FastAPI", "PostgreSQL", "Docker"],
        "preferred_industries": ["Fintech & Banking", "Product & SaaS"],
        "notes": "Targeting high-scale distributed backend teams."
    }

    saved = save_career_preferences(student_id, payload)
    assert saved["is_configured"] is True
    assert "Software Engineer" in saved["target_roles"]
    assert "Backend Developer" in saved["target_roles"]
    assert "Google" in saved["target_companies"]
    assert "Stripe" in saved["target_companies"]
    assert saved["expected_salary_min"] == 18.5
    assert "FastAPI" in saved["primary_skills"]
    assert saved["notes"] == "Targeting high-scale distributed backend teams."

    # Retrieve again
    retrieved = get_career_preferences(student_id)
    assert retrieved["is_configured"] is True
    assert retrieved["target_roles"] == ["Software Engineer", "Backend Developer"]
    assert retrieved["target_companies"] == ["Google", "Stripe"]


def test_save_career_preferences_syncs_with_student_profile():
    """Verify updating career preferences syncs primary role, company, and skills with student_profiles."""
    student_id = "test_student_sync_profile"
    payload = {
        "target_roles": ["Data Scientist", "Machine Learning Engineer"],
        "target_companies": ["Microsoft"],
        "primary_skills": ["Python", "PyTorch", "SQL"]
    }
    save_career_preferences(student_id, payload)

    prof = get_student_profile(student_id)
    assert prof["target_role"] == "Data Scientist"
    assert prof["target_company"] == "Microsoft"
    assert "PyTorch" in prof["skills"]


def test_get_career_recommendations_matches_preferences():
    """Verify recommendations score and prioritize jobs and tracks matching user selections."""
    student_id = "test_student_recomms"
    prefs = {
        "target_roles": ["Software Engineer"],
        "target_companies": ["Google"],
        "preferred_locations": ["Bengaluru"],
        "preferred_work_modes": ["On-site", "Hybrid"],
        "preferred_role_types": ["Full-time"],
        "expected_salary_min": 20.0,
        "primary_skills": ["Python", "C++", "Data Structures", "Algorithms"]
    }

    recs = get_career_recommendations(student_id, preferences=prefs)
    assert recs["status"] == "success"
    assert recs["total_jobs_matched"] > 0
    assert len(recs["recommended_jobs"]) > 0

    # Top job should be Google Software Engineer opening
    top_job = recs["recommended_jobs"][0]
    assert "Google" in top_job["company"]
    assert top_job["preference_match_score"] >= 70
    assert "fit_reason" in top_job
    assert "Target role" in top_job["fit_reason"] or "Google" in top_job["fit_reason"]

    # Role tracks
    assert len(recs["recommended_role_tracks"]) > 0
    role_names = [t["role_name"] for t in recs["recommended_role_tracks"]]
    assert "Software Engineer" in role_names

    # High-yield skills
    assert len(recs["top_focus_skills"]) > 0


def test_api_get_and_post_career_preferences(client):
    """Verify REST API GET and POST endpoints for career preferences."""
    # GET preferences
    resp_get = client.get("/api/career-preferences")
    assert resp_get.status_code == 200
    data_get = resp_get.get_json()
    assert data_get["status"] == "success"
    assert "preferences" in data_get
    assert "options" in data_get

    # POST preferences
    resp_post = client.post(
        "/api/career-preferences",
        json={
            "target_roles": ["Backend Developer"],
            "target_companies": ["Amazon"],
            "preferred_locations": ["Hyderabad"],
            "expected_salary_min": 15.0
        }
    )
    assert resp_post.status_code == 200
    data_post = resp_post.get_json()
    assert data_post["status"] == "success"
    assert "Backend Developer" in data_post["preferences"]["target_roles"]
    assert "Amazon" in data_post["preferences"]["target_companies"]
    assert "recommendations" in data_post


def test_api_career_recommendations_endpoint(client):
    """Verify GET /api/career-preferences/recommendations endpoint."""
    resp = client.get("/api/career-preferences/recommendations")
    assert resp.status_code == 200
    data = resp.get_json()
    assert data["status"] == "success"
    assert "recommended_jobs" in data
    assert "recommended_role_tracks" in data
    assert "top_focus_skills" in data


def test_career_preferences_view_renders(client):
    """Verify HTML view for career preferences renders form and recommendations."""
    resp = client.get("/career-preferences")
    assert resp.status_code == 200
    html = resp.get_data(as_text=True)
    assert "Career Preferences" in html
    assert "Manage Career Preferences" in html
    assert "Target Job Roles" in html
    assert "Recommendations Based on Your Selections" in html

    # Submit form
    post_resp = client.post(
        "/career-preferences",
        data={
            "target_roles": ["Software Engineer"],
            "target_companies": ["Google", "Microsoft"],
            "preferred_locations": ["Bengaluru"],
            "expected_salary_min": "14"
        },
        follow_redirects=True
    )
    assert post_resp.status_code == 200
    post_html = post_resp.get_data(as_text=True)
    assert "Career preferences updated successfully" in post_html
