"""
Placement Cell Readiness Analytics Service (#886).

Provides privacy-preserving cohort readiness analytics for placement officers:
- Aggregates by branch (department) and graduation year:
  * Number of active students
  * Average ATS resume match score
  * Most common missing skills (skill gap deficits)
  * Practice activity volume (coding submissions + mock interviews)
  * Application funnel metrics (Applied -> Assessment -> Int -> Offer -> Reject)
- Enforces strict k-anonymity (minimum group size rule, default 5):
  * Groups below min size merged into 'Other (Cohorts < 5)' or suppressed.
  * Individual student identities, names, emails, and IDs are NEVER exposed.
- Formula-injection protected CSV exports for placement officers.
- Strict administrator / placement coordinator access control.
"""

from __future__ import annotations

import csv
import io
import json
import logging
import sqlite3
from collections import Counter
from typing import Any, Dict, List, Optional, Tuple, Union

from utils.database import get_db_connection

logger = logging.getLogger(__name__)

DEFAULT_MIN_GROUP_SIZE = 5

# Branch / Department normalization map
BRANCH_NORMALIZATION_MAP: Dict[str, str] = {
    "cs": "Computer Science",
    "cse": "Computer Science",
    "computer science": "Computer Science",
    "computer science engineering": "Computer Science",
    "computer science & engineering": "Computer Science",
    "it": "Information Technology",
    "information technology": "Information Technology",
    "ece": "Electronics & Communication",
    "electronics": "Electronics & Communication",
    "electronics & comm": "Electronics & Communication",
    "electronics and communication": "Electronics & Communication",
    "ee": "Electrical Engineering",
    "eee": "Electrical Engineering",
    "electrical": "Electrical Engineering",
    "electrical engineering": "Electrical Engineering",
    "me": "Mechanical Engineering",
    "mech": "Mechanical Engineering",
    "mechanical": "Mechanical Engineering",
    "mechanical engineering": "Mechanical Engineering",
    "ce": "Civil Engineering",
    "civil": "Civil Engineering",
    "civil engineering": "Civil Engineering",
    "ai": "Data Science & AI",
    "ds": "Data Science & AI",
    "ai/ds": "Data Science & AI",
    "ai & ds": "Data Science & AI",
    "data science": "Data Science & AI",
    "data science & ai": "Data Science & AI",
    "general": "General Engineering",
}


def normalize_branch_name(dept: Optional[str]) -> str:
    """Standardizes branch / department names across disparate input forms."""
    if not dept or not str(dept).strip():
        return "General Engineering"
    raw = str(dept).strip()
    key = raw.lower()
    if key in BRANCH_NORMALIZATION_MAP:
        return BRANCH_NORMALIZATION_MAP[key]
    return raw.strip().title()


def sanitize_csv_field(val: Any) -> str:
    """
    Guards against CSV formula injection (OWASP CSV Injection).
    Prepends a single quote if string begins with =, +, -, @, \\t, or \\r.
    """
    s = str(val if val is not None else "")
    if s and s[0] in ("=", "+", "-", "@", "\t", "\r"):
        return f"'{s}"
    return s


def categorize_funnel_status(raw_status: Optional[str]) -> str:
    """Categorizes raw job application status into a standard funnel stage."""
    if not raw_status:
        return "Applied"
    st = raw_status.strip().lower()
    if any(k in st for k in ["offer", "placed", "selected", "accepted"]):
        return "Offer"
    if any(k in st for k in [
        "assessment", "screening", "oa", "test", "exam", "coding challenge"
    ]):
        return "Assessment"
    if any(k in st for k in ["interview", "round", "technical", "hr"]):
        return "Interview"
    if any(k in st for k in ["reject", "declined", "unsuccessful"]):
        return "Rejected"
    return "Applied"


class PlacementReadinessService:
    """
    Computes privacy-preserving aggregate readiness statistics
    for placement officers.
    """

    @classmethod
    def get_readiness_analytics(
        cls,
        conn: Optional[sqlite3.Connection] = None,
        filter_department: Optional[str] = None,
        filter_year: Optional[Union[int, str]] = None,
        min_group_size: int = DEFAULT_MIN_GROUP_SIZE,
        merge_small_groups: bool = True,
    ) -> Dict[str, Any]:
        """
        Calculates branch & batch year aggregates with strict k-anonymity.
        Zero student PII (no IDs, names, or emails) is returned.
        """
        close_after = False
        if conn is None:
            conn = get_db_connection()
            close_after = True

        try:
            cursor = conn.cursor()

            # 1. Discover registered students and branch + grad year
            cursor.execute(
                "SELECT name FROM sqlite_master WHERE type='table' "
                "AND name='student_batch_profiles'"
            )
            has_batch_profiles = bool(cursor.fetchone())

            cursor.execute(
                "SELECT name FROM sqlite_master WHERE type='table' "
                "AND name='student_profiles'"
            )
            has_student_profiles = bool(cursor.fetchone())

            cursor.execute(
                "SELECT name FROM sqlite_master WHERE type='table' "
                "AND name='users'"
            )
            has_users = bool(cursor.fetchone())

            student_cohort_map: Dict[str, Dict[str, Any]] = {}

            # Gather student accounts from users table
            if has_users:
                cursor.execute(
                    "SELECT id, role FROM users WHERE role NOT IN "
                    "('admin', 'coordinator') OR role IS NULL"
                )
                for row in cursor.fetchall():
                    sid = str(row[0]).strip()
                    if sid and sid not in student_cohort_map:
                        student_cohort_map[sid] = {
                            "department": "General Engineering",
                            "graduation_year": 2026,
                        }

            # Enrich from student_profiles
            if has_student_profiles:
                cursor.execute("PRAGMA table_info(student_profiles)")
                sp_cols = [c[1] for c in cursor.fetchall()]
                year_col = (
                    "grad_year" if "grad_year" in sp_cols
                    else ("graduation_year" if "graduation_year" in sp_cols else None)
                )
                query = (
                    f"SELECT student_id, department"
                    f"{', ' + year_col if year_col else ''} FROM student_profiles"
                )
                cursor.execute(query)
                for row in cursor.fetchall():
                    sid = str(row[0]).strip()
                    dept = row[1]
                    gy = row[2] if year_col and len(row) > 2 else None
                    if not sid:
                        continue
                    if sid not in student_cohort_map:
                        student_cohort_map[sid] = {
                            "department": normalize_branch_name(dept),
                            "graduation_year": int(gy) if gy else 2026,
                        }
                    else:
                        if dept:
                            student_cohort_map[sid]["department"] = (
                                normalize_branch_name(dept)
                            )
                        if gy:
                            try:
                                student_cohort_map[sid]["graduation_year"] = int(gy)
                            except (ValueError, TypeError):
                                pass

            # Enrich from student_batch_profiles if present
            if has_batch_profiles:
                cursor.execute(
                    "SELECT user_id, department, graduation_year "
                    "FROM student_batch_profiles"
                )
                for row in cursor.fetchall():
                    uid = str(row[0]).strip()
                    dept = row[1]
                    gy = row[2]
                    if not uid:
                        continue
                    if uid not in student_cohort_map:
                        student_cohort_map[uid] = {
                            "department": normalize_branch_name(dept),
                            "graduation_year": int(gy) if gy else 2026,
                        }
                    else:
                        if dept:
                            student_cohort_map[uid]["department"] = (
                                normalize_branch_name(dept)
                            )
                        if gy:
                            try:
                                student_cohort_map[uid]["graduation_year"] = int(gy)
                            except (ValueError, TypeError):
                                pass

            # Also ensure students present in analyses have a cohort entry
            cursor.execute(
                "SELECT name FROM sqlite_master WHERE type='table' "
                "AND name='analyses'"
            )
            has_analyses = bool(cursor.fetchone())
            if has_analyses:
                cursor.execute(
                    "SELECT DISTINCT student_id FROM analyses "
                    "WHERE student_id IS NOT NULL"
                )
                for row in cursor.fetchall():
                    sid = str(row[0]).strip()
                    if (
                        sid
                        and sid not in student_cohort_map
                        and sid not in ("default_student", "demo_visitor", "admin")
                    ):
                        student_cohort_map[sid] = {
                            "department": "General Engineering",
                            "graduation_year": 2026,
                        }

            # 2. Gather Resume ATS Scores & Missing Skills from analyses
            student_ats_scores: Dict[str, List[float]] = {}
            student_missing_skills: Dict[str, List[str]] = {}

            if has_analyses:
                cursor.execute(
                    "SELECT student_id, results_json FROM analyses "
                    "WHERE student_id IS NOT NULL"
                )
                for row in cursor.fetchall():
                    sid = str(row[0]).strip()
                    raw_json = row[1]
                    if not sid or not raw_json:
                        continue

                    res_dict = {}
                    if isinstance(raw_json, str):
                        try:
                            res_dict = json.loads(raw_json)
                        except Exception:
                            res_dict = {}
                    elif isinstance(raw_json, dict):
                        res_dict = raw_json

                    score = None
                    if "ats_score" in res_dict and isinstance(
                        res_dict["ats_score"], (int, float)
                    ):
                        score = float(res_dict["ats_score"])
                    elif "ats_breakdown" in res_dict and isinstance(
                        res_dict["ats_breakdown"], dict
                    ):
                        ats_bd = res_dict["ats_breakdown"]
                        if "total_score" in ats_bd and isinstance(
                            ats_bd["total_score"], (int, float)
                        ):
                            score = float(ats_bd["total_score"])
                    elif "overall_score" in res_dict and isinstance(
                        res_dict["overall_score"], (int, float)
                    ):
                        score = float(res_dict["overall_score"])
                    elif "score" in res_dict and isinstance(
                        res_dict["score"], (int, float)
                    ):
                        score = float(res_dict["score"])

                    if score is not None and 0.0 <= score <= 100.0:
                        student_ats_scores.setdefault(sid, []).append(score)

                    skills_found: List[str] = []
                    sg = res_dict.get("skill_gap")
                    if isinstance(sg, dict):
                        missing = sg.get("missing_skills") or sg.get("gaps") or []
                        if isinstance(missing, list):
                            for m in missing:
                                if isinstance(m, str) and m.strip():
                                    skills_found.append(m.strip())
                                elif isinstance(m, dict):
                                    name = (
                                        m.get("skill")
                                        or m.get("name")
                                        or m.get("skill_name")
                                    )
                                    if name and isinstance(name, str):
                                        skills_found.append(name.strip())

                    top_missing = res_dict.get("missing_skills")
                    if isinstance(top_missing, list):
                        for m in top_missing:
                            if isinstance(m, str) and m.strip():
                                skills_found.append(m.strip())
                            elif isinstance(m, dict):
                                name = m.get("skill") or m.get("name")
                                if name and isinstance(name, str):
                                    skills_found.append(name.strip())

                    if skills_found:
                        student_missing_skills.setdefault(sid, []).extend(skills_found)

            # 3. Practice Activity: Coding Submissions & Mock Interviews
            student_coding_counts: Dict[str, int] = {}
            cursor.execute(
                "SELECT name FROM sqlite_master WHERE type='table' "
                "AND name='coding_submissions'"
            )
            if cursor.fetchone():
                cursor.execute(
                    "SELECT student_id, COUNT(*) FROM coding_submissions "
                    "WHERE student_id IS NOT NULL GROUP BY student_id"
                )
                for row in cursor.fetchall():
                    sid = str(row[0]).strip()
                    student_coding_counts[sid] = row[1]

            student_interview_counts: Dict[str, int] = {}
            cursor.execute(
                "SELECT name FROM sqlite_master WHERE type='table' "
                "AND name='interviews'"
            )
            if cursor.fetchone():
                cursor.execute("PRAGMA table_info(interviews)")
                int_cols = [c[1] for c in cursor.fetchall()]
                if "student_id" in int_cols:
                    cursor.execute(
                        "SELECT student_id, COUNT(*) FROM interviews "
                        "WHERE student_id IS NOT NULL GROUP BY student_id"
                    )
                    for row in cursor.fetchall():
                        sid = str(row[0]).strip()
                        student_interview_counts[sid] = row[1]
                elif has_analyses:
                    cursor.execute("""
                        SELECT a.student_id, COUNT(i.id)
                        FROM interviews i
                        JOIN analyses a ON i.analysis_id = a.id
                        WHERE a.student_id IS NOT NULL
                        GROUP BY a.student_id
                    """)
                    for row in cursor.fetchall():
                        sid = str(row[0]).strip()
                        student_interview_counts[sid] = row[1]

            # 4. Application Funnel Counts from job_applications
            student_funnel_counts: Dict[str, Dict[str, int]] = {}
            cursor.execute(
                "SELECT name FROM sqlite_master WHERE type='table' "
                "AND name='job_applications'"
            )
            if cursor.fetchone():
                cursor.execute(
                    "SELECT student_id, status FROM job_applications "
                    "WHERE student_id IS NOT NULL"
                )
                for row in cursor.fetchall():
                    sid = str(row[0]).strip()
                    stage = categorize_funnel_status(row[1])
                    if sid not in student_funnel_counts:
                        student_funnel_counts[sid] = {
                            "Applied": 0,
                            "Assessment": 0,
                            "Interview": 0,
                            "Offer": 0,
                            "Rejected": 0,
                            "Total": 0,
                        }
                    student_funnel_counts[sid][stage] += 1
                    student_funnel_counts[sid]["Total"] += 1

            # 5. Group Students by (Department, Graduation Year)
            raw_groups: Dict[Tuple[str, int], List[str]] = {}
            for sid, info in student_cohort_map.items():
                dept = info.get("department", "General Engineering")
                year = info.get("graduation_year", 2026)
                raw_groups.setdefault((dept, year), []).append(sid)

            all_departments = sorted(list({k[0] for k in raw_groups.keys()}))
            all_years = sorted(list({k[1] for k in raw_groups.keys()}), reverse=True)

            filtered_groups: Dict[Tuple[str, int], List[str]] = {}
            for (dept, year), sids in raw_groups.items():
                if filter_department and filter_department.strip().lower() != "all":
                    if normalize_branch_name(filter_department).lower() != dept.lower():
                        continue
                if filter_year and str(filter_year).strip().lower() != "all":
                    try:
                        if int(filter_year) != year:
                            continue
                    except (ValueError, TypeError):
                        pass
                filtered_groups[(dept, year)] = sids

            # 6. Apply Minimum Group Size Rule (k-anonymity)
            distinct_cohorts: List[Dict[str, Any]] = []
            small_cohort_records: List[Dict[str, Any]] = []

            for (dept, year), sids in sorted(
                filtered_groups.items(), key=lambda x: (x[0][0], -x[0][1])
            ):
                count = len(sids)

                ats_list: List[float] = []
                for s in sids:
                    ats_list.extend(student_ats_scores.get(s, []))
                avg_ats = (
                    round(sum(ats_list) / len(ats_list), 1) if ats_list else 0.0
                )

                coding_subs = sum(student_coding_counts.get(s, 0) for s in sids)
                mock_ints = sum(student_interview_counts.get(s, 0) for s in sids)
                total_practice = coding_subs + mock_ints

                skills_counter: Counter[str] = Counter()
                for s in sids:
                    for sk in student_missing_skills.get(s, []):
                        clean_sk = sk.strip()
                        if clean_sk:
                            skills_counter[clean_sk] += 1
                top_skills = [
                    {"skill": skill, "count": freq}
                    for skill, freq in skills_counter.most_common(5)
                ]

                funnel = {
                    "applied": sum(
                        student_funnel_counts.get(s, {}).get("Applied", 0)
                        for s in sids
                    ),
                    "assessment": sum(
                        student_funnel_counts.get(s, {}).get("Assessment", 0)
                        for s in sids
                    ),
                    "interview": sum(
                        student_funnel_counts.get(s, {}).get("Interview", 0)
                        for s in sids
                    ),
                    "offer": sum(
                        student_funnel_counts.get(s, {}).get("Offer", 0)
                        for s in sids
                    ),
                    "rejected": sum(
                        student_funnel_counts.get(s, {}).get("Rejected", 0)
                        for s in sids
                    ),
                    "total": sum(
                        student_funnel_counts.get(s, {}).get("Total", 0)
                        for s in sids
                    ),
                }

                cohort_payload = {
                    "department": dept,
                    "graduation_year": year,
                    "active_students": count,
                    "average_ats_score": avg_ats,
                    "ats_evaluations_count": len(ats_list),
                    "practice_activity_count": total_practice,
                    "coding_submissions_count": coding_subs,
                    "interview_sessions_count": mock_ints,
                    "top_missing_skills": top_skills,
                    "funnel": funnel,
                    "is_other": False,
                }

                if count >= min_group_size:
                    distinct_cohorts.append(cohort_payload)
                else:
                    small_cohort_records.append(cohort_payload)

            # Consolidate small groups (< min_group_size) into 'Other'
            merged_other: Optional[Dict[str, Any]] = None
            if small_cohort_records and merge_small_groups:
                other_active = sum(c["active_students"] for c in small_cohort_records)
                other_ats_scores = []
                for c in small_cohort_records:
                    if c["average_ats_score"] > 0 and c["ats_evaluations_count"] > 0:
                        other_ats_scores.extend(
                            [c["average_ats_score"]] * c["ats_evaluations_count"]
                        )
                other_avg_ats = (
                    round(sum(other_ats_scores) / len(other_ats_scores), 1)
                    if other_ats_scores else 0.0
                )

                other_coding = sum(
                    c["coding_submissions_count"] for c in small_cohort_records
                )
                other_interviews = sum(
                    c["interview_sessions_count"] for c in small_cohort_records
                )

                other_skills_counter: Counter[str] = Counter()
                for c in small_cohort_records:
                    for sk_entry in c["top_missing_skills"]:
                        other_skills_counter[sk_entry["skill"]] += sk_entry["count"]
                other_top_skills = [
                    {"skill": skill, "count": freq}
                    for skill, freq in other_skills_counter.most_common(5)
                ]

                other_funnel = {
                    "applied": sum(
                        c["funnel"]["applied"] for c in small_cohort_records
                    ),
                    "assessment": sum(
                        c["funnel"]["assessment"] for c in small_cohort_records
                    ),
                    "interview": sum(
                        c["funnel"]["interview"] for c in small_cohort_records
                    ),
                    "offer": sum(
                        c["funnel"]["offer"] for c in small_cohort_records
                    ),
                    "rejected": sum(
                        c["funnel"]["rejected"] for c in small_cohort_records
                    ),
                    "total": sum(
                        c["funnel"]["total"] for c in small_cohort_records
                    ),
                }

                merged_other = {
                    "department": "Other (Cohorts < 5)",
                    "graduation_year": "Combined",
                    "active_students": other_active,
                    "average_ats_score": other_avg_ats,
                    "ats_evaluations_count": len(other_ats_scores),
                    "practice_activity_count": other_coding + other_interviews,
                    "coding_submissions_count": other_coding,
                    "interview_sessions_count": other_interviews,
                    "top_missing_skills": other_top_skills,
                    "funnel": other_funnel,
                    "is_other": True,
                    "suppressed_cohorts_count": len(small_cohort_records),
                }
                distinct_cohorts.append(merged_other)

            total_students_monitored = sum(
                c["active_students"] for c in distinct_cohorts
            )
            all_ats_values = []
            for c in distinct_cohorts:
                if c["average_ats_score"] > 0 and c.get("ats_evaluations_count", 0) > 0:
                    all_ats_values.extend(
                        [c["average_ats_score"]] * c["ats_evaluations_count"]
                    )
            overall_avg_ats = (
                round(sum(all_ats_values) / len(all_ats_values), 1)
                if all_ats_values else 0.0
            )

            total_practice = sum(
                c["practice_activity_count"] for c in distinct_cohorts
            )
            total_applications = sum(
                c["funnel"]["total"] for c in distinct_cohorts
            )
            total_offers = sum(
                c["funnel"]["offer"] for c in distinct_cohorts
            )

            summary = {
                "total_cohorts": len(distinct_cohorts),
                "total_students": total_students_monitored,
                "overall_average_ats_score": overall_avg_ats,
                "total_practice_activity": total_practice,
                "total_applications": total_applications,
                "total_offers": total_offers,
                "min_group_size": min_group_size,
                "suppressed_small_cohorts_count": len(small_cohort_records),
            }

            return {
                "status": "success",
                "cohorts": distinct_cohorts,
                "summary": summary,
                "filters": {
                    "departments": all_departments,
                    "graduation_years": all_years,
                    "selected_department": filter_department or "all",
                    "selected_year": filter_year or "all",
                },
                "privacy_guarantee": {
                    "k_anonymity_threshold": min_group_size,
                    "individual_pii_exposed": False,
                    "small_groups_merged": merge_small_groups,
                },
            }
        finally:
            if close_after:
                conn.close()

    @classmethod
    def export_readiness_csv(
        cls,
        conn: Optional[sqlite3.Connection] = None,
        filter_department: Optional[str] = None,
        filter_year: Optional[Union[int, str]] = None,
        min_group_size: int = DEFAULT_MIN_GROUP_SIZE,
    ) -> str:
        """
        Generates CSV export of readiness aggregates with CSV injection escaping.
        Exposes zero individual student PII.
        """
        analytics = cls.get_readiness_analytics(
            conn=conn,
            filter_department=filter_department,
            filter_year=filter_year,
            min_group_size=min_group_size,
            merge_small_groups=True,
        )

        output = io.StringIO()
        writer = csv.writer(output, lineterminator="\n")

        writer.writerow([
            "Branch / Department",
            "Graduation Year",
            "Active Students",
            "Average ATS Score",
            "Practice Activity Count",
            "Coding Submissions",
            "Mock Interviews",
            "Top Missing Skills",
            "Applications Submitted",
            "Assessment / Screening",
            "Interviews Reached",
            "Offers / Placed",
            "Rejected",
            "Total Applications",
        ])

        for cohort in analytics.get("cohorts", []):
            top_skills_str = ", ".join(
                f"{s['skill']} ({s['count']})"
                for s in cohort.get("top_missing_skills", [])
            )
            funnel = cohort.get("funnel", {})

            writer.writerow([
                sanitize_csv_field(cohort.get("department")),
                sanitize_csv_field(cohort.get("graduation_year")),
                sanitize_csv_field(cohort.get("active_students")),
                sanitize_csv_field(cohort.get("average_ats_score")),
                sanitize_csv_field(cohort.get("practice_activity_count")),
                sanitize_csv_field(cohort.get("coding_submissions_count")),
                sanitize_csv_field(cohort.get("interview_sessions_count")),
                sanitize_csv_field(top_skills_str),
                sanitize_csv_field(funnel.get("applied", 0)),
                sanitize_csv_field(funnel.get("assessment", 0)),
                sanitize_csv_field(funnel.get("interview", 0)),
                sanitize_csv_field(funnel.get("offer", 0)),
                sanitize_csv_field(funnel.get("rejected", 0)),
                sanitize_csv_field(funnel.get("total", 0)),
            ])

        return output.getvalue()
