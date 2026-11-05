"""
Shared pytest fixtures and Gemini API client mocking utilities for agent test suite.
"""

import json
import pytest
from google.genai.errors import APIError


class MockGeminiResponse:
    """Mock of google.genai.types.GenerateContentResponse."""
    def __init__(self, text: str):
        self.text = text


class MockGeminiModels:
    """Mock of google.genai.Client.models service."""
    def __init__(self, response_text: str = "", side_effect: Exception = None):
        self.response_text = response_text
        self.side_effect = side_effect
        self.last_prompt = None
        self.last_config = None

    def generate_content(self, model=None, contents=None, config=None):
        self.last_prompt = contents
        self.last_config = config
        if self.side_effect:
            raise self.side_effect
        return MockGeminiResponse(self.response_text)


class MockGeminiClient:
    """Mock of google.genai.Client."""
    def __init__(self, response_text: str = "", side_effect: Exception = None):
        self.models = MockGeminiModels(response_text=response_text, side_effect=side_effect)


@pytest.fixture
def sample_coordinator_json():
    """Fixed JSON fixture representing a complete structured Gemini placement analysis response."""
    return {
        "resume_analysis": {
            "ats_score": 85,
            "summary": "Demonstrates strong foundational knowledge in backend software engineering.",
            "strengths": ["Python", "Flask", "REST APIs", "SQL"],
            "weaknesses": ["Kubernetes", "AWS Cloud Services"],
            "grammar_suggestions": ["Use stronger action verbs in bullet points."],
            "formatting_suggestions": ["Keep consistent font size across headings."],
            "project_feedback": "Projects show good understanding of API design and data modeling.",
            "experience_feedback": "Professional experience highlights relevant engineering contributions.",
            "missing_skills": ["Docker", "Kubernetes"],
            "overall_recommendation": "Tailor resume keywords to target enterprise roles."
        },
        "job_match": {
            "match_percentage": 78,
            "matched_skills": ["Python", "Flask", "SQL"],
            "missing_skills": ["Docker", "Kubernetes"],
            "required_certifications": ["AWS Certified Developer"],
            "recommended_improvements": ["Build a containerized microservice project."],
            "top_5_resume_changes": [
                "Highlight backend API project at the top.",
                "Quantify impact with percentage improvements.",
                "List Python and SQL in the primary skills section.",
                "Mention unit testing experience.",
                "Add link to GitHub repository."
            ]
        },
        "skill_gap": [
            {
                "skill": "Docker & Containerization",
                "importance": "High",
                "difficulty": "Medium",
                "learning_time": "2 weeks",
                "learning_resource": "Official Docker Documentation",
                "project_suggestion": "Containerize a Flask application with docker-compose.",
                "priority": "High"
            },
            {
                "skill": "Kubernetes Orchestration",
                "importance": "Medium",
                "difficulty": "Hard",
                "learning_time": "3 weeks",
                "learning_resource": "Kubernetes Basics Tutorial",
                "project_suggestion": "Deploy a container cluster on minikube.",
                "priority": "Medium"
            }
        ],
        "learning_roadmap": [
            {
                "week": "Week 1",
                "topics": ["Advanced Data Structures & Python Internals"],
                "coding_practice": ["LeetCode 1: Two Sum", "LeetCode 20: Valid Parentheses"],
                "mini_projects": ["In-memory caching utility"],
                "interview_questions": ["Explain Python GIL and memory management"],
                "revision_tasks": ["Review time complexities of Python dict operations"],
                "estimated_hours": 12
            },
            {
                "week": "Week 2",
                "topics": ["RESTful API Architecture & Authentication"],
                "coding_practice": ["LeetCode 206: Reverse Linked List"],
                "mini_projects": ["JWT-authenticated REST API"],
                "interview_questions": ["Explain idempotency in HTTP methods"],
                "revision_tasks": ["Practice HTTP status code selection"],
                "estimated_hours": 10
            },
            {
                "week": "Week 3",
                "topics": ["Relational Database Design & Indexing"],
                "coding_practice": ["LeetCode 146: LRU Cache"],
                "mini_projects": ["Database schema design for e-commerce"],
                "interview_questions": ["Explain B-tree indexing and query optimization"],
                "revision_tasks": ["Write complex SQL joins and aggregations"],
                "estimated_hours": 14
            },
            {
                "week": "Week 4",
                "topics": ["Docker & Microservices Architecture"],
                "coding_practice": ["LeetCode 200: Number of Islands"],
                "mini_projects": ["Multi-container web service"],
                "interview_questions": ["Difference between container and virtual machine"],
                "revision_tasks": ["Practice writing optimized Dockerfiles"],
                "estimated_hours": 12
            },
            {
                "week": "Week 5",
                "topics": ["System Design & Scalability Patterns"],
                "coding_practice": ["LeetCode 23: Merge k Sorted Lists"],
                "mini_projects": ["URL shortener system design"],
                "interview_questions": ["How would you design a distributed rate limiter?"],
                "revision_tasks": ["Review CAP theorem and consistency models"],
                "estimated_hours": 15
            },
            {
                "week": "Week 6",
                "topics": ["Mock Interviews & Behavioral STAR Stories"],
                "coding_practice": ["Timed technical mock interview problems"],
                "mini_projects": ["Portfolio presentation polish"],
                "interview_questions": ["Tell me about a time you handled a production outage."],
                "revision_tasks": ["Rehearse STAR responses for past accomplishments"],
                "estimated_hours": 10
            }
        ],
        "interview_questions": {
            "hr": [
                "Tell me about yourself and your technical background.",
                "Why are you interested in this engineering role?",
                "What are your greatest technical strengths and areas for growth?",
                "Where do you see yourself technically in three to five years?",
                "How do you prioritize competing deadlines during project crunches?"
            ],
            "technical": [
                "Explain how Python handles memory allocation and garbage collection.",
                "What are the differences between optimistic and pessimistic concurrency control?",
                "How does database indexing impact write performance versus read latency?",
                "Describe the architecture of a resilient microservice system.",
                "How would you diagnose and resolve a memory leak in a Flask service?"
            ],
            "behavioral": [
                "Describe a time you had a technical disagreement with a team member.",
                "Tell me about a challenging bug you diagnosed under strict deadline pressure.",
                "Give an example of when you had to quickly learn a new language or framework.",
                "Describe a project deliverable that did not go according to plan.",
                "How do you handle critical code review feedback on your pull requests?"
            ]
        },
        "overall_recommendation_summary": "The candidate has strong backend engineering foundations with clear progression paths in containerization and system design."
    }


@pytest.fixture
def sample_interview_eval_json():
    """Fixed JSON fixture representing Gemini live interview answer evaluation response."""
    return {
        "confidence_score": 88,
        "communication_score": 85,
        "technical_accuracy": 92,
        "verbal_clarity_score": 86,
        "filler_words_detected": ["um", "like"],
        "filler_word_count": 2,
        "star_method_analysis": {
            "used_star": True,
            "feedback": "STAR method clearly observed with structured situation and measurable impact."
        },
        "suggestions": [
            "Elaborate more on distributed caching trade-offs.",
            "Quantify throughput improvements in numbers."
        ],
        "feedback": "Articulate explanation of caching strategies with solid technical reasoning."
    }


@pytest.fixture
def mock_gemini_client(monkeypatch):
    """
    Factory fixture to mock utils.gemini.get_gemini_client with fixed JSON or exceptions.
    Returns a helper function configure_mock(payload=..., side_effect=...).
    """
    def _configure(payload=None, text=None, side_effect=None):
        if payload is not None:
            response_text = json.dumps(payload)
        elif text is not None:
            response_text = text
        else:
            response_text = "{}"

        fake_client = MockGeminiClient(response_text=response_text, side_effect=side_effect)
        monkeypatch.setattr("utils.gemini.get_gemini_client", lambda: fake_client)
        return fake_client

    return _configure
