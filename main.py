import os
import logging
import subprocess
from datetime import datetime
from typing import Union

import pytz
import requests
from requests import Response
from requests.auth import HTTPBasicAuth
from dotenv import load_dotenv

# -------------------------------------------------------------------
# Environment
# -------------------------------------------------------------------
load_dotenv()

# -------------------------------------------------------------------
# Configuration
# -------------------------------------------------------------------
JIRA_URL: str = os.getenv("JIRA_URL", "https://example.atlassian.net").rstrip("/")
ISSUE_KEY: str = os.getenv("ISSUE_KEY", "PROY-123").strip()
JIRA_EMAIL: str = os.getenv("JIRA_EMAIL", "user@example.com").strip()
JIRA_API_TOKEN: str = os.getenv("JIRA_API_TOKEN", "").strip()

VERIFY_SSL_ENV: str = os.getenv("JIRA_VERIFY_SSL", "true").strip().lower()
CA_BUNDLE_PATH: str = os.getenv("JIRA_CA_BUNDLE", "").strip()

TZ_NAME: str = os.getenv("WORKLOG_TZ", "Europe/Madrid").strip()
START_HOUR: int = int(os.getenv("WORKLOG_START_HOUR", "8"))
START_MIN: int = int(os.getenv("WORKLOG_START_MIN", "30"))
END_HOUR: int = int(os.getenv("WORKLOG_END_HOUR", "15"))
END_MIN: int = int(os.getenv("WORKLOG_END_MIN", "30"))

# -------------------------------------------------------------------
# Logging
# -------------------------------------------------------------------
logging.basicConfig(level=logging.INFO, format="%(levelname)s: %(message)s")
logger = logging.getLogger("jira-worklog")

# -------------------------------------------------------------------
# HTTP defaults
# -------------------------------------------------------------------
HEADERS = {"Accept": "application/json", "Content-Type": "application/json"}


# -------------------------------------------------------------------
# Helpers
# -------------------------------------------------------------------
def _resolve_verify() -> Union[bool, str]:
    if CA_BUNDLE_PATH:
        return CA_BUNDLE_PATH
    if VERIFY_SSL_ENV in {"false", "0", "no"}:
        return False
    return True


def _format_started(dt: datetime) -> str:
    return dt.strftime("%Y-%m-%dT%H:%M:%S.%f")[:-3] + dt.strftime("%z")


def _compute_times(tz_name: str, sh: int, sm: int, eh: int, em: int) -> tuple[datetime, datetime, int]:
    tz = pytz.timezone(tz_name)
    now = datetime.now(tz)
    start = now.replace(hour=sh, minute=sm, second=0, microsecond=0)
    end = start.replace(hour=eh, minute=em, second=0, microsecond=0)
    if end <= start:
        raise ValueError("END must be after START")
    minutes = int((end - start).total_seconds() // 60)
    return start, end, minutes


def _require_env() -> None:
    if not JIRA_URL:
        raise RuntimeError("JIRA_URL is required")
    if not JIRA_EMAIL:
        raise RuntimeError("JIRA_EMAIL is required")
    if not JIRA_API_TOKEN:
        raise RuntimeError("JIRA_API_TOKEN is required")


def _check_issue(session: requests.Session, verify: Union[bool, str], issue_key: str) -> str:
    r = session.get(f"{JIRA_URL}/rest/api/3/issue/{issue_key}?fields=summary", verify=verify)
    if r.status_code == 404:
        raise RuntimeError(f"Issue '{issue_key}' not found or not visible")
    if r.status_code == 401:
        raise RuntimeError("Unauthorized: check JIRA_EMAIL and JIRA_API_TOKEN")
    if r.status_code == 403:
        raise RuntimeError("Forbidden: missing permissions to browse the issue")
    r.raise_for_status()
    return (r.json().get("fields") or {}).get("summary", "")


def _post_worklog(session: requests.Session, verify: Union[bool, str], issue_key: str, started: datetime, minutes: int) -> Response:
    payload = {"timeSpent": f"{minutes}m", "started": _format_started(started)}
    return session.post(f"{JIRA_URL}/rest/api/3/issue/{issue_key}/worklog", json=payload, verify=verify)


def _confirm_popup(issue_key: str, summary: str, start: datetime, end: datetime, minutes: int) -> bool:
    h, m = divmod(minutes, 60)
    human = f"{h}h {m}m" if m else f"{h}h"
    date_str = start.strftime('%a, %d %b %Y')
    issue_line = f"Issue: {summary} ({issue_key})" if summary else f"Issue: {issue_key}"
    msg = (
        "Confirm time entry for today:\n"
        f"{issue_line}\n"
        f"Time: {start.strftime('%H:%M')}–{end.strftime('%H:%M')} ({human})\n"
        f"Date: {date_str}"
    ).replace('"', '\\"')
    clock_icns = "/System/Applications/Clock.app/Contents/Resources/AppIcon.icns"
    if os.path.exists(clock_icns):
        icon_clause = f'with icon file (POSIX file "{clock_icns}")'
    else:
        icon_clause = 'with icon note'
    script = (
        f'display dialog "{msg}" '
        'buttons {"Cancel","Confirm"} default button "Confirm" '
        f'{icon_clause} with title "Jira Worklog"'
    )
    try:
        res = subprocess.run(["osascript", "-e", script], capture_output=True, text=True)
        if res.returncode != 0:
            logging.warning("osascript returned non-zero: %s, stderr=%s", res.returncode, (res.stderr or "").strip())
            return False
        out = (res.stdout or "").strip()
        accepted = {"confirm", "yes", "ok", "sí", "si", "aceptar"}
        if ":" in out:
            label = out.split(":", 1)[1].strip().lower()
            if label in accepted:
                return True
            logging.info("Popup dismissed (label=%s, stdout=%r)", label, out)
            return False
        if any(tok in out.lower() for tok in accepted):
            return True
        logging.info("Popup dismissed (stdout=%r, stderr=%r)", out, (res.stderr or "").strip())
        return False
    except Exception:
        return False


# -------------------------------------------------------------------
# Entry point
# -------------------------------------------------------------------
def main() -> int:
    issue_key = ISSUE_KEY
    try:
        _require_env()
    except RuntimeError as e:
        logger.error(str(e))
        return 1
    verify = _resolve_verify()
    session = requests.Session()
    session.headers.update(HEADERS)
    session.auth = HTTPBasicAuth(JIRA_EMAIL, JIRA_API_TOKEN)
    try:
        started, ended, minutes = _compute_times(TZ_NAME, START_HOUR, START_MIN, END_HOUR, END_MIN)
    except ValueError as e:
        logger.error(str(e))
        return 1
    try:
        summary = _check_issue(session, verify, issue_key)
        logger.info("Issue OK: %s%s", issue_key, f" — {summary}" if summary else "")
    except (RuntimeError, requests.RequestException) as e:
        logger.error("%s", e)
        return 1
    if not _confirm_popup(issue_key, summary, started, ended, minutes):
        logger.info("Worklog canceled by user.")
        return 0
    try:
        resp = _post_worklog(session, verify, issue_key, started, minutes)
        if resp.status_code == 201:
            h, m = divmod(minutes, 60)
            human = f"{h}h {m}m" if m else f"{h}h"
            logger.info("Worklog submitted: %s to %s (%s–%s)", human, issue_key, started.strftime("%H:%M"), ended.strftime("%H:%M"))
            return 0
        logger.error("Error logging work: %s %s", resp.status_code, resp.text)
        return 1
    except requests.RequestException as e:
        logger.error("Network error while posting worklog: %s", e)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())