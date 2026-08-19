"""מעקב אחר צריכת Cloud Run מול המכסה החינמית (180,000 vCPU-שניות/חודש -
זו המגבלה המחייבת בפועל בתצורה הנוכחית של 1 vCPU / 1GB, לא מגבלת הזיכרון).

בניגוד ל-usage_tracker.py (שסופר Firestore בעצמו), כאן אי אפשר לספור עצמאית
- הזמן החייב בתשלום נמדד ע"י הפלטפורמה עצמה (run.googleapis.com/container/
billable_instance_time), אז שולפים אותו ישירות מ-Cloud Monitoring.

נקרא פעם ביום ע"י Cloud Scheduler (POST /admin/publish-usage-metric),
כותב את האחוז כ-custom metric, ומדיניות התראה ב-Cloud Monitoring (מוגדרת
בנפרד, לא כאן) שולחת SMS כשהוא חוצה 50%.

משתמשים ב-ADC (זהות הריצה של Cloud Run, שכבר יש לה roles/editor) ולא
ב-service-account.json של Firestore - כדי לא להצטרך להוסיף לו הרשאות
Monitoring, ולא ב-google-cloud-monitoring (חבילה כבדה) אלא ב-REST גולמי
עם requests, כמו ב-drive.py:stream_file.
"""

import datetime

import google.auth
import google.auth.transport.requests
import requests

from app.config import settings

_MONITORING_API = "https://monitoring.googleapis.com/v3"
_FREE_TIER_VCPU_SECONDS_PER_MONTH = 180_000
_CUSTOM_METRIC_TYPE = "custom.googleapis.com/meeting_log/free_tier_usage_pct"


def _access_token() -> str:
    credentials, _ = google.auth.default(
        scopes=["https://www.googleapis.com/auth/cloud-platform"]
    )
    credentials.refresh(google.auth.transport.requests.Request())
    return credentials.token


def _month_start_utc() -> datetime.datetime:
    now = datetime.datetime.now(datetime.timezone.utc)
    return now.replace(day=1, hour=0, minute=0, second=0, microsecond=0)


def _month_to_date_billable_seconds(token: str) -> float:
    now = datetime.datetime.now(datetime.timezone.utc)
    start = _month_start_utc()
    project = settings.google_cloud_project
    resp = requests.get(
        f"{_MONITORING_API}/projects/{project}/timeSeries",
        headers={"Authorization": f"Bearer {token}"},
        params={
            "filter": (
                'metric.type="run.googleapis.com/container/billable_instance_time" '
                'resource.type="cloud_run_revision" '
                'resource.labels.service_name="meeting-log-backend"'
            ),
            "interval.startTime": start.isoformat(),
            "interval.endTime": now.isoformat(),
            "aggregation.alignmentPeriod": "86400s",
            "aggregation.perSeriesAligner": "ALIGN_SUM",
            "aggregation.crossSeriesReducer": "REDUCE_SUM",
            "view": "FULL",
        },
        timeout=30,
    )
    resp.raise_for_status()
    data = resp.json()
    total = 0.0
    for series in data.get("timeSeries", []):
        for point in series.get("points", []):
            total += float(point["value"].get("doubleValue", 0.0))
    return total


def _write_usage_percent_metric(token: str, percent: float) -> None:
    now = datetime.datetime.now(datetime.timezone.utc).isoformat()
    project = settings.google_cloud_project
    resp = requests.post(
        f"{_MONITORING_API}/projects/{project}/timeSeries",
        headers={"Authorization": f"Bearer {token}"},
        json={
            "timeSeries": [
                {
                    "metric": {"type": _CUSTOM_METRIC_TYPE},
                    "resource": {
                        "type": "global",
                        "labels": {"project_id": project},
                    },
                    "points": [
                        {
                            "interval": {"endTime": now},
                            "value": {"doubleValue": percent},
                        }
                    ],
                }
            ]
        },
        timeout=30,
    )
    resp.raise_for_status()


def publish_free_tier_usage_metric() -> dict:
    """שולף את הצריכה החודשית-עד-כה, כותב אותה כ-custom metric, ומחזיר סיכום."""
    token = _access_token()
    seconds = _month_to_date_billable_seconds(token)
    percent = round(100 * seconds / _FREE_TIER_VCPU_SECONDS_PER_MONTH, 2)
    _write_usage_percent_metric(token, percent)
    return {
        "billable_seconds_month_to_date": seconds,
        "free_tier_limit_seconds": _FREE_TIER_VCPU_SECONDS_PER_MONTH,
        "percent_of_free_tier": percent,
    }
