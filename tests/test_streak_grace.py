import pytest
from datetime import datetime, timezone, timedelta
import config
from utils.database import init_db
from utils.streak_grace_service import calculate_streak_with_grace

def test_streak_with_grace_period():
    today = datetime.now(timezone.utc).date()
    # Active today, 2 days ago, and 3 days ago (missed yesterday)
    dates = [
        today.isoformat(),
        (today - timedelta(days=2)).isoformat(),
        (today - timedelta(days=3)).isoformat()
    ]
    res = calculate_streak_with_grace(dates, current_date_iso=today.isoformat(), grace_days_allowed=1)
    assert res["success"] is True
    assert res["current_streak"] == 3
    assert res["grace_used"] is True

def test_streak_grace_api(client):
    today = datetime.now(timezone.utc).date().isoformat()
    res = client.post("/api/preparation/streak", json={
        "activity_dates": [today]
    })
    assert res.status_code == 200
    assert res.get_json()["status"] == "success"
    assert res.get_json()["streak"]["current_streak"] == 1


def test_streak_navigation_page_and_calendar(client, tmp_path, monkeypatch):
    monkeypatch.setattr(config, "DATABASE_PATH", tmp_path / "test_streak_routes.db")
    init_db()
    with client.session_transaction() as sess:
        sess["user_id"] = "streak-route-student"

    page_response = client.get("/streak")
    assert page_response.status_code == 200
    assert b"Daily Preparation Streak Tracker" in page_response.data

    calendar_response = client.get("/api/streak/calendar?year=2026&month=11")
    assert calendar_response.status_code == 200
    calendar = calendar_response.get_json()["calendar"]
    assert calendar["year"] == 2026
    assert calendar["month"] == 11
