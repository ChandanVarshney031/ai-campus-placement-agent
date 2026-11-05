"""
Unit and integration tests for CoordinatorAgent and specialized sub-agents:
ResumeAgent, JobMatchAgent, SkillGapAgent, RoadmapAgent, and InterviewAgent.

Mocks the Gemini client in utils/gemini.py using fixed JSON fixtures so tests run
deterministically offline with zero network calls and no API key required.
Covers happy paths, missing keys, empty responses, API errors, and edge cases.
"""

import json
import pytest
from google.genai.errors import APIError

from agents.coordinator import CoordinatorAgent
from agents.interview_agent import InterviewAgent
from agents.job_match_agent import JobMatchAgent
from agents.resume_agent import ResumeAgent
from agents.roadmap_agent import RoadmapAgent
from agents.skill_gap_agent import SkillGapAgent
from utils.gemini import (
    AIResponseFormatError,
    AIRateLimitError,
    AIServiceUnavailableError,
    AIAnalysisError,
)


# ==============================================================================
# 1. CoordinatorAgent Tests
# ==============================================================================

class TestCoordinatorAgent:
    """Test suite for CoordinatorAgent orchestration and delegation."""

    def test_coordinator_agent_analyze_happy_path(self, mock_gemini_client, sample_coordinator_json):
        """
        Happy path: Gemini client returns full fixed JSON fixture.
        Verifies coordinator orchestrates all sub-agents and constructs the full coordinated payload.
        """
        mock_gemini_client(payload=sample_coordinator_json)
        coordinator = CoordinatorAgent()

        progress_events = []
        def on_progress(step_id, agent_name, description, percent):
            progress_events.append((step_id, agent_name, percent))

        result = coordinator.analyze(
            resume_text="Experienced Python backend developer with SQL and REST API skills.",
            job_description="Seeking Backend SDE with Python, Docker, and distributed systems experience.",
            company_name="Google",
            on_progress=on_progress
        )

        # Verify all main sections are present
        assert "resume_analysis" in result
        assert "job_match" in result
        assert "skill_gap" in result
        assert "learning_roadmap" in result
        assert "interview_questions" in result
        assert "overall_recommendation_summary" in result
        assert "conflicts" in result

        # Verify sub-agent delegations
        assert result["resume_analysis"]["ats_score"] == 85
        assert "Python" in result["resume_analysis"]["strengths"]
        assert result["job_match"]["match_percentage"] == 78
        assert len(result["job_match"]["top_5_resume_changes"]) <= 5
        assert result["job_match"]["company_benchmark"]["tier_name"].startswith("Tier 1")
        assert len(result["skill_gap"]) == 2
        assert result["skill_gap"][0]["skill"] == "Docker & Containerization"
        assert len(result["learning_roadmap"]) == 6
        assert len(result["interview_questions"]["hr"]) == 5
        assert len(result["interview_questions"]["technical"]) == 5
        assert len(result["interview_questions"]["behavioral"]) == 5

        # Verify progress callback invoked across all 5 steps
        step_ids = [e[0] for e in progress_events]
        assert step_ids == ["step2", "step3", "step4", "step5", "step6"]

    def test_coordinator_agent_analyze_missing_keys_in_json(self, mock_gemini_client):
        """
        Failure case: Gemini returns an empty JSON object {}.
        Verifies coordinator and sub-agents apply resilient defaults without crashing.
        """
        mock_gemini_client(payload={})
        coordinator = CoordinatorAgent()

        result = coordinator.analyze(
            resume_text="Minimal text",
            company_name="General"
        )

        assert result["resume_analysis"]["ats_score"] == 0
        assert result["job_match"]["match_percentage"] == 0
        assert len(result["job_match"]["top_5_resume_changes"]) == 5
        assert result["skill_gap"] == []
        assert len(result["learning_roadmap"]) == 6
        assert len(result["interview_questions"]["hr"]) == 5
        assert len(result["interview_questions"]["technical"]) == 5
        assert len(result["interview_questions"]["behavioral"]) == 5
        assert result["conflicts"] == []

    def test_coordinator_agent_analyze_empty_response(self, mock_gemini_client):
        """
        Failure case: Gemini client returns empty text.
        Verifies generate_json_response raises AIResponseFormatError and coordinator propagates it.
        """
        mock_gemini_client(text="")
        coordinator = CoordinatorAgent()

        with pytest.raises(AIResponseFormatError):
            coordinator.analyze(resume_text="Some resume text")

    def test_coordinator_agent_analyze_api_error_rate_limit(self, mock_gemini_client):
        """
        Failure case: Gemini returns HTTP 429 quota exhausted.
        Verifies AIRateLimitError is raised.
        """
        api_err = APIError(
            code=429,
            response_json={"error": {"message": "Resource has been exhausted (e.g. check quota)."}},
            response=None
        )
        mock_gemini_client(side_effect=api_err)
        coordinator = CoordinatorAgent()

        with pytest.raises(AIRateLimitError):
            coordinator.analyze(resume_text="Some resume text")

    def test_coordinator_agent_analyze_api_error_service_unavailable(self, mock_gemini_client):
        """
        Failure case: Gemini returns HTTP 503 backend overload.
        Verifies AIServiceUnavailableError is raised.
        """
        api_err = APIError(
            code=503,
            response_json={"error": {"message": "The service is currently unavailable or overloaded."}},
            response=None
        )
        mock_gemini_client(side_effect=api_err)
        coordinator = CoordinatorAgent()

        with pytest.raises(AIServiceUnavailableError):
            coordinator.analyze(resume_text="Some resume text")

    def test_coordinator_agent_analyze_non_dict_response(self, mock_gemini_client):
        """
        Failure case: Gemini returns a JSON list instead of a root dictionary.
        Verifies coordinator gracefully treats non-dict response as empty dict with defaults.
        """
        mock_gemini_client(text='[{"key": "value"}]')
        coordinator = CoordinatorAgent()

        result = coordinator.analyze(resume_text="Resume content")
        assert result["resume_analysis"]["ats_score"] == 0
        assert len(result["learning_roadmap"]) == 6

    def test_coordinator_agent_conflict_detection(self):
        """
        Verifies cross-section conflict detection logic:
        1. matched_skill_in_skill_gap: skill is both matched in job_match and present in skill_gap
        2. matched_skill_in_missing_skills: skill is both matched and missing in job_match
        """
        coordinator = CoordinatorAgent()

        job_match = {
            "matched_skills": ["Python", "SQL", "Docker"],
            "missing_skills": ["SQL", "Kubernetes"]  # 'SQL' is both matched and missing
        }
        skill_gap = [
            {"skill": "Docker", "importance": "High"},  # 'Docker' is matched and in skill_gap
            {"skill": "General Technical Concepts", "importance": "Low"}  # Generic fallback ignored
        ]
        resume = {"ats_score": 75}

        conflicts = coordinator._detect_conflicts(job_match, skill_gap, resume)
        conflict_types = {(c["conflict_type"], c["skill"].lower()) for c in conflicts}

        assert ("matched_skill_in_skill_gap", "docker") in conflict_types
        assert ("matched_skill_in_missing_skills", "sql") in conflict_types
        # Verify generic fallback was ignored
        assert not any("general technical concepts" in c["skill"].lower() for c in conflicts)


# ==============================================================================
# 2. ResumeAgent Tests
# ==============================================================================

class TestResumeAgent:
    """Test suite for ResumeAgent validation and sanitization."""

    def test_resume_agent_happy_path(self):
        """Happy path: Valid input dictionary with all fields properly populated."""
        agent = ResumeAgent()
        raw = {
            "ats_score": "88",
            "summary": "Experienced full-stack engineer with Python and React skills.",
            "strengths": ["Python", "System Design", "Unit Testing"],
            "weaknesses": ["Cloud Architecture"],
            "grammar_suggestions": ["Strengthen action verbs in bullet points."],
            "formatting_suggestions": ["Use consistent font sizing throughout."],
            "project_feedback": "Projects demonstrate clean architecture.",
            "experience_feedback": "Bullet points quantify business impact.",
            "missing_skills": ["Kubernetes"],
            "overall_recommendation": "Strong placement readiness profile."
        }
        processed = agent.process(raw)

        assert processed["ats_score"] == 88
        assert processed["summary"] == "Experienced full-stack engineer with Python and React skills."
        assert processed["strengths"] == ["Python", "System Design", "Unit Testing"]
        assert processed["weaknesses"] == ["Cloud Architecture"]
        assert "Kubernetes" in processed["missing_skills"]
        assert "Strong placement readiness" in processed["overall_recommendation"]

    def test_resume_agent_missing_keys(self):
        """Failure case: Empty dictionary with missing keys applies safe defaults."""
        agent = ResumeAgent()
        processed = agent.process({})

        assert processed["ats_score"] == 0
        assert "unavailable" in processed["summary"].lower()
        assert processed["strengths"] == []
        assert processed["weaknesses"] == []
        assert processed["grammar_suggestions"] == []
        assert processed["missing_skills"] == []
        assert isinstance(processed["overall_recommendation"], str)

    def test_resume_agent_handles_invalid_data(self):
        """Failure case: Non-dict inputs (None, string, list, int) do not raise."""
        agent = ResumeAgent()

        res_none = agent.process(None)
        assert res_none["ats_score"] == 0
        assert isinstance(res_none["strengths"], list)

        res_str = agent.process("invalid string")
        assert res_str["ats_score"] == 0

        res_list = agent.process([1, 2, 3])
        assert res_list["ats_score"] == 0

    def test_resume_agent_ats_score_clamping_and_types(self):
        """Edge case: Clamps negative and excessive ATS scores; parses numeric strings."""
        agent = ResumeAgent()

        assert agent.process({"ats_score": -15})["ats_score"] == 0
        assert agent.process({"ats_score": 150})["ats_score"] == 100
        assert agent.process({"ats_score": "92"})["ats_score"] == 92
        assert agent.process({"ats_score": "not_a_number"})["ats_score"] == 0
        assert agent.process({"ats_score": None})["ats_score"] == 0

    def test_resume_agent_sanitize_string_list(self):
        """Edge case: Handles string input as single item list; strips whitespace; drops empties."""
        agent = ResumeAgent()

        assert agent._sanitize_string_list("  Single Skill  ") == ["Single Skill"]
        assert agent._sanitize_string_list([" Python ", "", None, "  SQL  "]) == ["Python", "SQL"]
        assert agent._sanitize_string_list([]) == []
        assert agent._sanitize_string_list(None) == []


# ==============================================================================
# 3. JobMatchAgent Tests
# ==============================================================================

class TestJobMatchAgent:
    """Test suite for JobMatchAgent and recruitment tier benchmarking."""

    def test_job_match_agent_happy_path(self):
        """Happy path: Valid input dictionary with matched skills and company benchmarks."""
        agent = JobMatchAgent()
        raw = {
            "match_percentage": 75,
            "matched_skills": ["Python", "Flask", "SQL"],
            "missing_skills": ["AWS", "Docker"],
            "required_certifications": ["AWS Solutions Architect"],
            "recommended_improvements": ["Add containerized capstone project."],
            "top_5_resume_changes": [
                "Highlight Python project first",
                "Add metrics to internship bullets"
            ]
        }
        processed = agent.process(raw, company_name="Google", ats_score=85)

        assert processed["match_percentage"] == 75
        assert processed["matched_skills"] == ["Python", "Flask", "SQL"]
        assert "AWS" in processed["missing_skills"]
        assert len(processed["top_5_resume_changes"]) <= 5
        assert "company_benchmark" in processed
        assert processed["company_benchmark"]["company_name"] == "Google"

    def test_job_match_agent_missing_keys(self):
        """Failure case: Empty dictionary with missing keys applies defaults and top 5 changes."""
        agent = JobMatchAgent()
        processed = agent.process({})

        assert processed["match_percentage"] == 0
        assert processed["matched_skills"] == []
        assert processed["missing_skills"] == []
        assert len(processed["top_5_resume_changes"]) == 5
        assert "company_benchmark" in processed

    def test_job_match_agent_handles_invalid_data(self):
        """Failure case: Non-dict input (None, string, int) handled gracefully."""
        agent = JobMatchAgent()

        res_none = agent.process(None)
        assert res_none["match_percentage"] == 0
        assert len(res_none["top_5_resume_changes"]) == 5

        res_str = agent.process("not a dict")
        assert res_str["match_percentage"] == 0

    def test_job_match_agent_match_percentage_clamping(self):
        """Edge case: Clamps match_percentage between 0 and 100."""
        agent = JobMatchAgent()

        assert agent.process({"match_percentage": -50})["match_percentage"] == 0
        assert agent.process({"match_percentage": 250})["match_percentage"] == 100
        assert agent.process({"match_percentage": "80"})["match_percentage"] == 80
        assert agent.process({"match_percentage": "invalid"})["match_percentage"] == 0

    def test_job_match_agent_company_tier_benchmarks(self):
        """Verifies company recruitment tier classification and benchmark statuses."""
        agent = JobMatchAgent()

        # Tier 1 Exceeded
        res_t1 = agent.process({"match_percentage": 85}, company_name="Microsoft", ats_score=90)
        bm_t1 = res_t1["company_benchmark"]
        assert "Tier 1" in bm_t1["tier_name"]
        assert bm_t1["status"] == "Benchmark Exceeded"
        assert bm_t1["badge"] == "success"

        # Tier 2 Met
        res_t2 = agent.process({"match_percentage": 65}, company_name="Flipkart", ats_score=72)
        bm_t2 = res_t2["company_benchmark"]
        assert "Tier 2" in bm_t2["tier_name"]
        assert bm_t2["status"] in ("Benchmark Met", "Benchmark Exceeded")

        # Tier 3 Below Benchmark
        res_t3 = agent.process({"match_percentage": 40}, company_name="TCS", ats_score=50)
        bm_t3 = res_t3["company_benchmark"]
        assert "Tier 3" in bm_t3["tier_name"]
        assert bm_t3["status"] == "Below Benchmark"
        assert bm_t3["badge"] == "warning"

        # General / Startup tier
        res_gen = agent.process({"match_percentage": 70}, company_name="Acme Startup", ats_score=75)
        bm_gen = res_gen["company_benchmark"]
        assert "General" in bm_gen["tier_name"]

    def test_job_match_agent_caps_top_5_resume_changes(self):
        """Edge case: If more than 5 changes are provided, trims to exactly 5."""
        agent = JobMatchAgent()
        raw = {
            "top_5_resume_changes": [f"Change {i}" for i in range(1, 10)]
        }
        processed = agent.process(raw)
        assert len(processed["top_5_resume_changes"]) == 5


# ==============================================================================
# 4. SkillGapAgent Tests
# ==============================================================================

class TestSkillGapAgent:
    """Test suite for SkillGapAgent processing and enum sanitization."""

    def test_skill_gap_agent_happy_path(self):
        """Happy path: Valid list of skill gap records with clean enums."""
        agent = SkillGapAgent()
        raw = [
            {
                "skill": "Docker",
                "importance": "high",
                "difficulty": "medium",
                "learning_time": "1 week",
                "learning_resource": "Docker Docs",
                "project_suggestion": "Containerize Flask app",
                "priority": "high",
            },
            {
                "skill": "System Design",
                "importance": "Medium",
                "difficulty": "Hard",
                "learning_time": "3 weeks",
                "learning_resource": "Designing Data-Intensive Applications",
                "project_suggestion": "Design URL Shortener",
                "priority": "Medium",
            }
        ]
        processed = agent.process(raw)

        assert len(processed) == 2
        assert processed[0]["skill"] == "Docker"
        assert processed[0]["importance"] == "High"
        assert processed[0]["difficulty"] == "Medium"
        assert processed[1]["difficulty"] == "Hard"

    def test_skill_gap_agent_missing_keys_in_items(self):
        """Failure case: Item dictionaries missing keys receive standard default values."""
        agent = SkillGapAgent()
        processed = agent.process([{}])

        assert len(processed) == 1
        item = processed[0]
        assert item["skill"] == "General Technical Concepts"
        assert item["importance"] == "Medium"
        assert item["difficulty"] == "Medium"
        assert item["priority"] == "Medium"
        assert "Coursera" in item["learning_resource"]

    def test_skill_gap_agent_handles_non_list_inputs(self):
        """Failure case: Non-list inputs (None, dict, string) return empty list."""
        agent = SkillGapAgent()

        assert agent.process(None) == []
        assert agent.process({}) == []
        assert agent.process("not a list") == []
        assert agent.process(123) == []

    def test_skill_gap_agent_skips_malformed_items(self):
        """Failure case: List containing non-dict items skips invalid entries safely."""
        agent = SkillGapAgent()
        raw = [None, "string entry", 42, {"skill": "Kubernetes", "importance": "High"}]
        processed = agent.process(raw)

        assert len(processed) == 1
        assert processed[0]["skill"] == "Kubernetes"

    def test_skill_gap_agent_enum_sanitization(self):
        """Edge case: Unknown enum values fall back to defaults; case is standardized."""
        agent = SkillGapAgent()
        assert agent._sanitize_enum("easy", ["Easy", "Medium", "Hard"], "Medium") == "Easy"
        assert agent._sanitize_enum("UNKNOWN_VAL", ["High", "Medium", "Low"], "Medium") == "Medium"
        assert agent._sanitize_enum(None, ["High", "Medium", "Low"], "Medium") == "Medium"


# ==============================================================================
# 5. RoadmapAgent Tests
# ==============================================================================

class TestRoadmapAgent:
    """Test suite for RoadmapAgent 6-week curriculum enforcement."""

    def test_roadmap_agent_happy_path(self):
        """Happy path: Preserves valid 6-week curriculum."""
        agent = RoadmapAgent()
        raw = [
            {
                "week": f"Week {i}",
                "topics": [f"Topic {i}"],
                "coding_practice": [f"Problem {i}"],
                "mini_projects": [f"Project {i}"],
                "interview_questions": [f"Question {i}"],
                "revision_tasks": [f"Task {i}"],
                "estimated_hours": 10 + i
            }
            for i in range(1, 7)
        ]
        processed = agent.process(raw)

        assert len(processed) == 6
        for idx, week in enumerate(processed, 1):
            assert week["week"] == f"Week {idx}"
            assert week["topics"] == [f"Topic {idx}"]
            assert week["estimated_hours"] == 10 + idx

    def test_roadmap_agent_missing_keys_and_empty_list(self):
        """Failure case: Empty list produces complete 6-week syllabus with default data."""
        agent = RoadmapAgent()
        processed = agent.process([])

        assert len(processed) == 6
        for idx, week in enumerate(processed, 1):
            assert week["week"] == f"Week {idx}"
            assert len(week["topics"]) >= 1
            assert len(week["coding_practice"]) >= 1
            assert week["estimated_hours"] == 10

    def test_roadmap_agent_handles_non_list_inputs(self):
        """Failure case: Non-list inputs (None, dict) enforce 6 default weeks without crashing."""
        agent = RoadmapAgent()

        res_none = agent.process(None)
        assert len(res_none) == 6
        assert res_none[0]["week"] == "Week 1"

        res_dict = agent.process({"week": "Week 1"})
        assert len(res_dict) == 6

    def test_roadmap_agent_partial_weeks_filled(self):
        """Failure case: Partial input (e.g. only 2 weeks provided) supplements weeks 3 to 6."""
        agent = RoadmapAgent()
        partial = [
            {"week": "Week 1", "topics": ["Custom DSA"], "estimated_hours": 15},
            {"week": "Week 2", "topics": ["Custom Graphs"], "estimated_hours": 12}
        ]
        processed = agent.process(partial)

        assert len(processed) == 6
        assert processed[0]["topics"] == ["Custom DSA"]
        assert processed[1]["topics"] == ["Custom Graphs"]
        assert processed[2]["week"] == "Week 3"
        assert processed[5]["week"] == "Week 6"

    def test_roadmap_agent_estimated_hours_clamping(self):
        """Edge case: Clamps estimated_hours between 1 and 80; defaults non-integers to 10."""
        agent = RoadmapAgent()
        raw = [
            {"week": "Week 1", "estimated_hours": -5},
            {"week": "Week 2", "estimated_hours": 200},
            {"week": "Week 3", "estimated_hours": "invalid"},
            {"week": "Week 4", "estimated_hours": "18"}
        ]
        processed = agent.process(raw)

        assert processed[0]["estimated_hours"] == 1
        assert processed[1]["estimated_hours"] == 80
        assert processed[2]["estimated_hours"] == 10
        assert processed[3]["estimated_hours"] == 18


# ==============================================================================
# 6. InterviewAgent Tests
# ==============================================================================

class TestInterviewAgent:
    """Test suite for InterviewAgent question enforcement, answer eval, and scoring."""

    def test_interview_agent_process_happy_path(self):
        """Happy path: Valid question payload with 5 questions per section preserved."""
        agent = InterviewAgent()
        raw = {
            "hr": [f"HR Q{i}" for i in range(1, 6)],
            "technical": [f"Tech Q{i}" for i in range(1, 6)],
            "behavioral": [f"Behav Q{i}" for i in range(1, 6)]
        }
        processed = agent.process(raw)

        assert len(processed["hr"]) == 5
        assert len(processed["technical"]) == 5
        assert len(processed["behavioral"]) == 5
        assert processed["hr"][0] == "HR Q1"

    def test_interview_agent_process_missing_keys(self):
        """Failure case: Empty dictionary enforces exactly 5 fallback questions per category."""
        agent = InterviewAgent()
        processed = agent.process({})

        assert len(processed["hr"]) == 5
        assert len(processed["technical"]) == 5
        assert len(processed["behavioral"]) == 5
        assert "walk me through your resume" in processed["hr"][0]

    def test_interview_agent_process_handles_invalid_data(self):
        """Failure case: Non-dict inputs (None, list) load system defaults without error."""
        agent = InterviewAgent()

        res_none = agent.process(None)
        assert len(res_none["hr"]) == 5
        assert len(res_none["technical"]) == 5

        res_list = agent.process(["not", "a", "dict"])
        assert len(res_list["behavioral"]) == 5

    def test_interview_agent_process_short_and_long_lists(self):
        """Edge case: Short lists padded to 5; long lists trimmed to 5."""
        agent = InterviewAgent()
        raw = {
            "hr": ["Q1", "Q2"],  # Short
            "technical": [f"Tech {i}" for i in range(10)],  # Long
            "behavioral": []  # Empty
        }
        processed = agent.process(raw)

        assert len(processed["hr"]) == 5
        assert processed["hr"][:2] == ["Q1", "Q2"]
        assert len(processed["technical"]) == 5
        assert len(processed["behavioral"]) == 5

    def test_interview_agent_evaluate_answer_happy_path(self, mock_gemini_client, sample_interview_eval_json):
        """
        Happy path: Evaluates candidate answer via mocked Gemini client.
        Verifies scores, verbal metrics, and STAR method analysis.
        """
        mock_gemini_client(payload=sample_interview_eval_json)
        agent = InterviewAgent()

        res = agent.evaluate_answer(
            question_type="Technical",
            question="How does Redis caching reduce database load?",
            answer="In our system, we cached user session tokens in Redis with TTLs, reducing SQL query volume by 70%."
        )

        assert res["confidence_score"] == 88
        assert res["communication_score"] == 85
        assert res["technical_accuracy"] == 92
        assert res["verbal_clarity_score"] == 86
        assert res["filler_word_count"] == 2
        assert "like" in res["filler_words_detected"]
        assert res["star_method_analysis"]["used_star"] is True
        assert len(res["suggestions"]) == 2

    def test_interview_agent_evaluate_answer_empty_input(self):
        """Failure case: Empty or whitespace answer returns 0 scores without calling Gemini."""
        agent = InterviewAgent()

        res = agent.evaluate_answer("Technical", "What is an index?", "")
        assert res["confidence_score"] == 0
        assert res["communication_score"] == 0
        assert res["technical_accuracy"] == 0
        assert res["verbal_clarity_score"] == 0
        assert res["star_method_analysis"]["used_star"] is False
        assert "empty" in res["suggestions"][0].lower()

    def test_interview_agent_evaluate_answer_api_error_fallback(self, mock_gemini_client):
        """
        Failure case: When Gemini raises an APIError or network error,
        evaluate_answer gracefully returns safe fallback scores without crashing.
        """
        api_err = APIError(
            code=500,
            response_json={"error": {"message": "Internal AI server error."}},
            response=None
        )
        mock_gemini_client(side_effect=api_err)
        agent = InterviewAgent()

        res = agent.evaluate_answer("HR", "Why join our company?", "I admire your mission and work culture.")
        assert res["confidence_score"] == 50
        assert res["communication_score"] == 50
        assert res["technical_accuracy"] == 50
        assert "unable to analyze" in res["feedback"].lower()

    def test_interview_agent_parse_score_formats(self):
        """Edge case: parse_score accurately parses integers, floats, percentages, and fractions."""
        agent = InterviewAgent()

        assert agent.parse_score(85) == 85
        assert agent.parse_score(82.6) == 83
        assert agent.parse_score("90%") == 90
        assert agent.parse_score(" 75 % ") == 75
        assert agent.parse_score("4/5") == 80
        assert agent.parse_score("1/2") == 50
        assert agent.parse_score(150) == 100
        assert agent.parse_score(-10) == 0
        assert agent.parse_score("invalid_string", default=50) == 50
        assert agent.parse_score(None, default=50) == 50

    def test_interview_agent_calculate_session_summary(self):
        """Verifies session aggregation across question categories and overall readiness verdict."""
        agent = InterviewAgent()

        # Empty answers list
        empty_summary = agent.calculate_session_summary([])
        assert empty_summary["total_answered"] == 0
        assert empty_summary["overall_score"] == 0
        assert empty_summary["readiness_verdict"] == "No answers recorded"

        # Answered session with strong performance
        answers = [
            {
                "type": "HR",
                "evaluation": {"technical_accuracy": 85, "communication_score": 90, "confidence_score": 85}
            },
            {
                "type": "Technical",
                "evaluation": {"technical_accuracy": 90, "communication_score": 85, "confidence_score": 90}
            },
            {
                "type": "Behavioral",
                "evaluation": {
                    "technical_accuracy": 80,
                    "communication_score": 85,
                    "confidence_score": 80,
                    "star_method_analysis": {"used_star": True}
                }
            }
        ]

        summary = agent.calculate_session_summary(answers)
        assert summary["total_answered"] == 3
        assert summary["overall_score"] >= 80
        assert summary["readiness_verdict"] == "Placement Ready"
        assert "HR" in summary["category_scores"]
        assert "Technical" in summary["category_scores"]
        assert "Behavioral" in summary["category_scores"]
        assert summary["verbal_metrics"]["star_adherence_rate"] == "33%"
