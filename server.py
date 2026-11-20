import argparse
import csv
import html
import io
import json
import mimetypes
import os
import re
import secrets
import sqlite3
import sys
import threading
import time
import uuid
from datetime import datetime, timezone
from http import HTTPStatus
from http.cookies import SimpleCookie
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.parse import parse_qs, unquote, urlsplit
from urllib.request import Request, urlopen


ROOT = Path(__file__).resolve().parent


def load_local_environment(path=None):
    env_path = Path(path) if path else ROOT / ".env"
    if not env_path.is_file():
        return
    for line in env_path.read_text(encoding="utf-8-sig").splitlines():
        entry = line.strip()
        if not entry or entry.startswith("#") or "=" not in entry:
            continue
        key, value = entry.split("=", 1)
        key = key.strip()
        value = value.strip()
        if not re.fullmatch(r"[A-Z][A-Z0-9_]*", key):
            continue
        if len(value) >= 2 and value[0] == value[-1] and value[0] in {"'", '"'}:
            value = value[1:-1]
        if value and not os.environ.get(key):
            os.environ[key] = value


load_local_environment()
DB_PATH = Path(os.environ.get("APP_DB_PATH", ROOT / "registrations.sqlite3"))
SESSION_COOKIE = "employee_registration_admin"
SESSION_TTL_SECONDS = 8 * 60 * 60
ADMIN_SESSIONS = {}
MAX_REQUEST_BYTES = 16 * 1024
EMAILJS_API_URL = "https://api.emailjs.com/api/v1.0/email/send"
EMAILJS_MIN_REQUEST_INTERVAL = 1.1
EMAILJS_SEND_LOCK = threading.Lock()
EMAILJS_LAST_REQUEST_AT = 0.0
EVENT_TITLE = "Kyndryl Year End Celebrations 2026"
EVENT_DATE_TIME = "Friday, December 18, 2026 at 6:30 PM IST"
EVENT_VENUE = "The Grand Ballroom, Kyndryl Technology Center"
EVENT_GATES = "Gates 1 & 2 (Kyndryl Fast-Track Check-In Desk)"


def _demo_mode():
    return os.environ.get("DEMO_MODE", "").strip().lower() in {"1", "true", "yes"}


DIETARY_OPTIONS = {
    "No preference",
    "Vegetarian",
    "Vegan",
    "Jain",
    "Halal",
    "Kosher",
    "Other",
}
TRAVEL_OPTIONS = {
    "public_transport",
    "taxi_rideshare",
    "event_transport",
    "self_drive",
    "other",
}


class DuplicateRegistrationError(Exception):
    pass


class RegistrationNotFoundError(Exception):
    pass


class AlreadyCheckedInError(Exception):
    pass


class CredentialMismatchError(Exception):
    pass


def utc_now():
    return datetime.now(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")


def connect_db():
    connection = sqlite3.connect(DB_PATH, timeout=5)
    connection.row_factory = sqlite3.Row
    return connection


def initialize_db():
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    with connect_db() as connection:
        connection.execute(
            """
            CREATE TABLE IF NOT EXISTS registrations (
                id TEXT PRIMARY KEY,
                credential TEXT NOT NULL UNIQUE,
                full_name TEXT NOT NULL,
                mobile TEXT NOT NULL,
                official_email TEXT NOT NULL COLLATE NOCASE UNIQUE,
                emergency_contact_name TEXT NOT NULL,
                emergency_contact_mobile TEXT NOT NULL,
                dietary_preference TEXT NOT NULL,
                alcohol_preference TEXT NOT NULL,
                travel_mode TEXT,
                terms_accepted INTEGER NOT NULL,
                attendance_confirmed INTEGER NOT NULL,
                created_at TEXT NOT NULL,
                attendance_at TEXT,
                email_status TEXT NOT NULL DEFAULT 'pending'
            )
            """
        )


def _required_text(data, key, label, max_length):
    value = data.get(key)
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{label} is required.")
    value = value.strip()
    if len(value) > max_length:
        raise ValueError(f"{label} must be {max_length} characters or fewer.")
    return value


def _valid_phone(value, label):
    if not re.fullmatch(r"\+?[0-9][0-9\s()-]{5,18}[0-9]", value):
        raise ValueError(f"Enter a valid {label}.")
    return value


def validate_registration(data):
    if not isinstance(data, dict):
        raise ValueError("Registration details must be sent as an object.")

    full_name = _required_text(data, "fullName", "Name", 100)
    mobile = _valid_phone(
        _required_text(data, "mobile", "Mobile number", 24), "mobile number"
    )
    official_email = _required_text(data, "officialEmail", "Official Kyndryl email", 254).lower()
    if not re.fullmatch(r"[A-Z0-9.!#$%&'*+/=?^_`{|}~-]+@kyndryl\.com", official_email, re.I):
        raise ValueError("Use a valid @kyndryl.com email address.")

    emergency_contact_name = _required_text(
        data, "emergencyContactName", "Emergency contact name", 100
    )
    emergency_contact_mobile = _valid_phone(
        _required_text(data, "emergencyContactMobile", "Emergency contact mobile", 24),
        "emergency contact mobile number",
    )

    dietary_preference = data.get("dietaryPreference")
    if dietary_preference not in DIETARY_OPTIONS:
        raise ValueError("Select a valid dietary preference.")
    alcohol_preference = data.get("alcoholPreference")
    if alcohol_preference not in {"yes", "no"}:
        raise ValueError("Select whether you plan to drink alcohol.")

    travel_mode = data.get("travelMode") or None
    if travel_mode is not None and travel_mode not in TRAVEL_OPTIONS:
        raise ValueError("Select a valid mode of travel.")
    if alcohol_preference == "yes" and not travel_mode:
        raise ValueError("Select a mode of travel if you plan to drink.")
    if alcohol_preference == "yes" and travel_mode == "self_drive":
        raise ValueError("Self drive is not allowed when you plan to drink.")

    if data.get("attendanceConfirmed") is not True:
        raise ValueError("Confirm that you will attend the event.")
    if data.get("termsAccepted") is not True:
        raise ValueError("Accept the event terms and conditions to register.")

    return {
        "full_name": full_name,
        "mobile": mobile,
        "official_email": official_email,
        "emergency_contact_name": emergency_contact_name,
        "emergency_contact_mobile": emergency_contact_mobile,
        "dietary_preference": dietary_preference,
        "alcohol_preference": alcohol_preference,
        "travel_mode": travel_mode,
    }


def create_registration(registration):
    registration_id = uuid.uuid4().hex
    credential = "KY-" + secrets.token_urlsafe(7).replace("_", "X").replace("-", "Y").upper()
    created_at = utc_now()
    try:
        with connect_db() as connection:
            connection.execute(
                """
                INSERT INTO registrations (
                    id, credential, full_name, mobile, official_email,
                    emergency_contact_name, emergency_contact_mobile,
                    dietary_preference, alcohol_preference, travel_mode,
                    terms_accepted, attendance_confirmed, created_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 1, 1, ?)
                """,
                (
                    registration_id,
                    credential,
                    registration["full_name"],
                    registration["mobile"],
                    registration["official_email"],
                    registration["emergency_contact_name"],
                    registration["emergency_contact_mobile"],
                    registration["dietary_preference"],
                    registration["alcohol_preference"],
                    registration["travel_mode"],
                    created_at,
                ),
            )
    except sqlite3.IntegrityError as exc:
        with connect_db() as connection:
            duplicate = connection.execute(
                "SELECT 1 FROM registrations WHERE official_email = ?",
                (registration["official_email"],),
            ).fetchone()
        if duplicate:
            raise DuplicateRegistrationError from exc
        raise

    return {
        "id": registration_id,
        "credential": credential,
        **registration,
        "attendanceConfirmed": True,
        "createdAt": created_at,
    }


def create_demo_registration(registration):
    credential = "DEMO-" + secrets.token_urlsafe(7).replace("_", "X").replace("-", "Y").upper()
    return {
        "id": uuid.uuid4().hex,
        "credential": credential,
        "full_name": registration["full_name"],
        "official_email": registration["official_email"],
    }


def _emailjs_configuration():
    required_names = (
        "EMAILJS_SERVICE_ID",
        "EMAILJS_TEMPLATE_ID",
        "EMAILJS_PUBLIC_KEY",
    )
    names = (*required_names, "EMAILJS_PRIVATE_KEY")
    configuration = {name: os.environ.get(name, "").strip() for name in names}
    if not any(configuration.values()):
        return None
    missing = [name for name in required_names if not configuration[name]]
    if missing:
        raise ValueError(f"Incomplete EmailJS configuration. Set: {', '.join(missing)}.")
    return configuration


def send_confirmation_email(registration):
    if _demo_mode():
        return "not_configured", (
            "Email sending is disabled in this public demo. Your demo pass is not stored."
        )
    try:
        configuration = _emailjs_configuration()
    except ValueError as exc:
        print(str(exc), file=sys.stderr)
        return "failed", (
            "EmailJS settings are incomplete. Keep the entry code and contact the event administrator."
        )
    if configuration is None:
        return "not_configured", (
            "Email delivery is not configured. Keep your entry code and contact the event administrator."
        )

    travel_mode = registration.get("travel_mode")
    travel_labels = {
        "public_transport": "Public Transport",
        "taxi_rideshare": "Taxi or Ride-share",
        "event_transport": "Event Transport",
        "other": "Other safe transport",
        "self_drive": "Self Drive",
    }
    alcohol_preference = (
        "Yes" if registration["alcohol_preference"] == "yes" else "No"
    )
    template_values = {
        "to_name_html": registration["full_name"].split()[0],
        "full_name_html": registration["full_name"],
        "official_email_html": registration["official_email"],
        "mobile_html": registration["mobile"],
        "dietary_preference_html": registration["dietary_preference"],
        "alcohol_preference_html": alcohol_preference,
        "travel_mode_html": travel_labels.get(travel_mode, "Not applicable"),
        "entry_code_html": registration["credential"],
        "event_name_html": EVENT_TITLE,
        "event_date_time_html": EVENT_DATE_TIME,
        "event_venue_html": EVENT_VENUE,
        "event_gates_html": EVENT_GATES,
    }
    template_params = {
        "to_email": registration["official_email"],
        "to_name": registration["full_name"],
        **{
            name: html.escape(value, quote=True)
            for name, value in template_values.items()
        },
    }
    request_payload = {
        "service_id": configuration["EMAILJS_SERVICE_ID"],
        "template_id": configuration["EMAILJS_TEMPLATE_ID"],
        "user_id": configuration["EMAILJS_PUBLIC_KEY"],
        "template_params": template_params,
    }
    if configuration["EMAILJS_PRIVATE_KEY"]:
        request_payload["accessToken"] = configuration["EMAILJS_PRIVATE_KEY"]
    request = Request(
        EMAILJS_API_URL,
        data=json.dumps(request_payload).encode("utf-8"),
        headers={
            "Content-Type": "application/json",
            "User-Agent": "eRegisteronWeb/1.0",
        },
        method="POST",
    )

    global EMAILJS_LAST_REQUEST_AT
    with EMAILJS_SEND_LOCK:
        delay = EMAILJS_MIN_REQUEST_INTERVAL - (
            time.monotonic() - EMAILJS_LAST_REQUEST_AT
        )
        if delay > 0:
            time.sleep(delay)
        EMAILJS_LAST_REQUEST_AT = time.monotonic()
        try:
            with urlopen(request, timeout=10) as response:
                if response.status != HTTPStatus.OK:
                    print(
                        f"EmailJS returned unexpected HTTP status {response.status}.",
                        file=sys.stderr,
                    )
                    return "failed", (
                        "EmailJS did not accept the confirmation. Keep the entry code and contact the event administrator."
                    )
                response.read()
        except HTTPError as exc:
            if exc.code == HTTPStatus.FORBIDDEN:
                error_detail = exc.read(4096).decode("utf-8", errors="replace")
                if "API access from non-browser environments is currently disabled" in error_detail:
                    print(
                        "EmailJS server-side API access is disabled in account security settings.",
                        file=sys.stderr,
                    )
                    return "failed", (
                        "EmailJS is blocking server-side sends. Enable 'API access from "
                        "non-browser environments' in EmailJS Account > Security, then retry."
                    )
                if "API access in strict mode, but no Private Key was provided" in error_detail:
                    print(
                        "EmailJS strict mode requires a private key in local configuration.",
                        file=sys.stderr,
                    )
                    return "failed", (
                        "EmailJS strict mode requires EMAILJS_PRIVATE_KEY. Add the private "
                        "key to your local .env file, restart the server, and retry."
                    )
            print(f"EmailJS returned HTTP status {exc.code}.", file=sys.stderr)
            return "failed", (
                "EmailJS could not send the confirmation. Keep the entry code and contact the event administrator."
            )
        except (URLError, OSError) as exc:
            print(f"EmailJS request failed: {exc}.", file=sys.stderr)
            return "failed", (
                "EmailJS could not be reached. Keep the entry code and contact the event administrator."
            )

    return "sent", "A confirmation email has been sent to your Kyndryl email address."


def set_email_status(registration_id, status):
    with connect_db() as connection:
        connection.execute(
            "UPDATE registrations SET email_status = ? WHERE id = ?",
            (status, registration_id),
        )


def resend_confirmation_email(registration_id):
    with connect_db() as connection:
        row = connection.execute(
            "SELECT * FROM registrations WHERE id = ?", (registration_id,)
        ).fetchone()
    if row is None:
        raise RegistrationNotFoundError
    status, message = send_confirmation_email(dict(row))
    set_email_status(registration_id, status)
    return status, message


def _registration_row(row):
    if row is None:
        return None
    result = dict(row)
    result["attendanceConfirmed"] = bool(result.pop("attendance_confirmed"))
    result["termsAccepted"] = bool(result.pop("terms_accepted"))
    result["attendanceStatus"] = "checked-in" if result.get("attendance_at") else "pending"
    return result


def list_registrations(filters):
    clauses = []
    values = []
    search = filters.get("search", "").strip()
    if search:
        term = f"%{search}%"
        clauses.append("(full_name LIKE ? OR official_email LIKE ?)")
        values.extend([term, term])
    for parameter, column in (
        ("diet", "dietary_preference"),
        ("alcohol", "alcohol_preference"),
    ):
        value = filters.get(parameter, "").strip()
        if value:
            clauses.append(f"{column} = ?")
            values.append(value)
    attendance = filters.get("attendance", "").strip()
    if attendance == "checked-in":
        clauses.append("attendance_at IS NOT NULL")
    elif attendance == "pending":
        clauses.append("attendance_at IS NULL")

    query = "SELECT * FROM registrations"
    if clauses:
        query += " WHERE " + " AND ".join(clauses)
    query += " ORDER BY created_at DESC"
    with connect_db() as connection:
        rows = connection.execute(query, values).fetchall()
    return [_registration_row(row) for row in rows]


def registration_stats():
    with connect_db() as connection:
        totals = connection.execute(
            """
            SELECT COUNT(*) AS registered,
                   SUM(CASE WHEN attendance_at IS NOT NULL THEN 1 ELSE 0 END) AS checked_in
            FROM registrations
            """
        ).fetchone()
        dietary = connection.execute(
            """
            SELECT dietary_preference, COUNT(*) AS count
            FROM registrations GROUP BY dietary_preference ORDER BY dietary_preference
            """
        ).fetchall()
    registered = totals["registered"] or 0
    checked_in = totals["checked_in"] or 0
    return {
        "registered": registered,
        "checkedIn": checked_in,
        "awaiting": registered - checked_in,
        "dietary": [{"preference": row["dietary_preference"], "count": row["count"]} for row in dietary],
    }


def mark_attendance(registration_id):
    with connect_db() as connection:
        row = connection.execute(
            "SELECT * FROM registrations WHERE id = ?", (registration_id,)
        ).fetchone()
        if row is None:
            raise RegistrationNotFoundError
        if row["attendance_at"]:
            raise AlreadyCheckedInError
        timestamp = utc_now()
        cursor = connection.execute(
            """
            UPDATE registrations
            SET attendance_at = ?
            WHERE id = ? AND attendance_at IS NULL
            """,
            (timestamp, registration_id),
        )
        if cursor.rowcount != 1:
            raise AlreadyCheckedInError
        updated = connection.execute(
            "SELECT * FROM registrations WHERE id = ?", (registration_id,)
        ).fetchone()
    return _registration_row(updated)


def mark_attendance_by_identity(full_name, official_email):
    normalized_name = full_name.strip().casefold()
    normalized_email = official_email.strip().casefold()
    with connect_db() as connection:
        row = connection.execute(
            "SELECT id, full_name FROM registrations WHERE official_email = ?",
            (normalized_email,),
        ).fetchone()
    if row is None or row["full_name"].strip().casefold() != normalized_name:
        raise CredentialMismatchError
    return mark_attendance(row["id"])


def _admin_session(handler):
    cookie_header = handler.headers.get("Cookie", "")
    cookie = SimpleCookie()
    cookie.load(cookie_header)
    morsel = cookie.get(SESSION_COOKIE)
    if morsel is None:
        return False
    expires_at = ADMIN_SESSIONS.get(morsel.value)
    if expires_at is None or expires_at < time.time():
        ADMIN_SESSIONS.pop(morsel.value, None)
        return False
    return True


class RegistrationHandler(BaseHTTPRequestHandler):
    server_version = "EmployeeRegistration/1.0"

    def end_headers(self):
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("Referrer-Policy", "same-origin")
        self.send_header(
            "Content-Security-Policy",
            "default-src 'self'; script-src 'self'; style-src 'self'; "
            "connect-src 'self'; img-src 'self' data:; base-uri 'none'; "
            "frame-ancestors 'none'; form-action 'self'",
        )
        super().end_headers()

    def log_message(self, format_string, *args):
        if self.path.startswith("/api/register"):
            return
        super().log_message(format_string, *args)

    def _send_json(self, status, payload, headers=None):
        body = json.dumps(payload).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Cache-Control", "no-store")
        self.send_header("Content-Length", str(len(body)))
        if headers:
            for name, value in headers:
                self.send_header(name, value)
        self.end_headers()
        self.wfile.write(body)

    def _read_json(self):
        try:
            length = int(self.headers.get("Content-Length", "0"))
        except ValueError as exc:
            raise ValueError("Invalid request length.") from exc
        if length < 0 or length > MAX_REQUEST_BYTES:
            raise ValueError("Request body is too large.")
        try:
            payload = json.loads(self.rfile.read(length))
        except (json.JSONDecodeError, UnicodeDecodeError) as exc:
            raise ValueError("Request body must be valid JSON.") from exc
        if not isinstance(payload, dict):
            raise ValueError("Request body must be a JSON object.")
        return payload

    def _require_admin(self):
        if _admin_session(self):
            return True
        self._send_json(HTTPStatus.UNAUTHORIZED, {"error": "Administrator sign-in is required."})
        return False

    def do_GET(self):
        parsed = urlsplit(self.path)
        if parsed.path == "/api/public/event":
            return self._send_json(
                HTTPStatus.OK,
                {
                    "title": EVENT_TITLE,
                    "date": EVENT_DATE_TIME,
                    "venue": EVENT_VENUE,
                    "gates": EVENT_GATES,
                    "demoMode": _demo_mode(),
                    "emergency": "Event-day emergency contacts will be published here.",
                    "guidelines": [
                        "Bring your registration code for eligibility verification.",
                        "Carry your physical Kyndryl RFID badge for identity verification.",
                        "If consuming alcohol, bring valid government photo ID for age verification.",
                        "Drinking and self-driving is prohibited; arrange safe transport.",
                    ],
                },
            )
        if parsed.path == "/api/admin/session":
            return self._send_json(
                HTTPStatus.OK, {"authenticated": _admin_session(self)}
            )
        if parsed.path == "/api/admin/registrations":
            if not self._require_admin():
                return
            filters = {
                key: values[0]
                for key, values in parse_qs(parsed.query, keep_blank_values=True).items()
                if values
            }
            return self._send_json(HTTPStatus.OK, {"registrations": list_registrations(filters)})
        if parsed.path == "/api/admin/stats":
            if not self._require_admin():
                return
            return self._send_json(HTTPStatus.OK, registration_stats())
        if parsed.path == "/api/admin/export.csv":
            if not self._require_admin():
                return
            return self._send_csv()
        if parsed.path.startswith("/api/"):
            return self._send_json(HTTPStatus.NOT_FOUND, {"error": "API route not found."})
        self._send_static(parsed.path)

    def do_POST(self):
        parsed = urlsplit(self.path)
        try:
            payload = self._read_json()
        except ValueError as exc:
            return self._send_json(HTTPStatus.BAD_REQUEST, {"error": str(exc)})

        if parsed.path == "/api/register":
            try:
                validated = validate_registration(payload)
                registration = (
                    create_demo_registration(validated)
                    if _demo_mode()
                    else create_registration(validated)
                )
            except ValueError as exc:
                return self._send_json(HTTPStatus.BAD_REQUEST, {"error": str(exc)})
            except DuplicateRegistrationError:
                return self._send_json(
                    HTTPStatus.CONFLICT,
                    {"error": "A registration already exists for this Kyndryl email address."},
                )
            except sqlite3.Error as exc:
                print(f"Registration storage failed: {exc}", file=sys.stderr)
                return self._send_json(
                    HTTPStatus.INTERNAL_SERVER_ERROR,
                    {"error": "Registration could not be saved. Please try again later."},
                )

            if _demo_mode():
                email_status = "not_configured"
                email_message = (
                    "Public demo only: no registration was stored and no email was sent. "
                    "Use synthetic information."
                )
            else:
                email_status, email_message = send_confirmation_email(registration)
                try:
                    set_email_status(registration["id"], email_status)
                except sqlite3.Error as exc:
                    print(f"Email status could not be saved: {exc}", file=sys.stderr)
                    email_status = "status_unknown"
                    email_message = (
                        "Registration saved. Email delivery status could not be recorded; "
                        "keep your entry code."
                    )
            return self._send_json(
                HTTPStatus.CREATED,
                {
                    "registration": registration,
                    "emailStatus": email_status,
                    "message": email_message,
                },
            )

        if parsed.path == "/api/admin/login":
            expected_password = os.environ.get("APP_ADMIN_PASSWORD")
            if not expected_password:
                return self._send_json(
                    HTTPStatus.SERVICE_UNAVAILABLE,
                    {"error": "Administrator sign-in is disabled until APP_ADMIN_PASSWORD is configured."},
                )
            supplied = payload.get("password")
            if not isinstance(supplied, str) or not secrets.compare_digest(
                supplied, expected_password
            ):
                return self._send_json(
                    HTTPStatus.UNAUTHORIZED, {"error": "Incorrect administrator password."}
                )
            token = secrets.token_urlsafe(32)
            ADMIN_SESSIONS[token] = time.time() + SESSION_TTL_SECONDS
            return self._send_json(
                HTTPStatus.OK,
                {"authenticated": True},
                [
                    (
                        "Set-Cookie",
                        f"{SESSION_COOKIE}={token}; HttpOnly; SameSite=Strict; "
                        f"Path=/; Max-Age={SESSION_TTL_SECONDS}",
                    )
                ],
            )

        if parsed.path == "/api/admin/logout":
            cookie = SimpleCookie()
            cookie.load(self.headers.get("Cookie", ""))
            morsel = cookie.get(SESSION_COOKIE)
            if morsel:
                ADMIN_SESSIONS.pop(morsel.value, None)
            return self._send_json(
                HTTPStatus.OK,
                {"authenticated": False},
                [
                    (
                        "Set-Cookie",
                        f"{SESSION_COOKIE}=; HttpOnly; SameSite=Strict; Path=/; Max-Age=0",
                    )
                ],
            )

        if parsed.path == "/api/admin/email/resend":
            if not self._require_admin():
                return
            try:
                registration_id = _required_text(
                    payload, "registrationId", "Registration", 64
                )
                email_status, message = resend_confirmation_email(registration_id)
            except ValueError as exc:
                return self._send_json(HTTPStatus.BAD_REQUEST, {"error": str(exc)})
            except RegistrationNotFoundError:
                return self._send_json(
                    HTTPStatus.NOT_FOUND, {"error": "Registration not found."}
                )
            except sqlite3.Error as exc:
                print(f"Confirmation email status could not be saved: {exc}", file=sys.stderr)
                return self._send_json(
                    HTTPStatus.INTERNAL_SERVER_ERROR,
                    {"error": "Email status could not be saved. Check the server log."},
                )
            return self._send_json(
                HTTPStatus.OK,
                {"emailStatus": email_status, "message": message},
            )

        if parsed.path in {"/api/admin/attendance", "/api/admin/attendance/manual"}:
            if not self._require_admin():
                return
            try:
                if parsed.path.endswith("/manual"):
                    full_name = _required_text(payload, "fullName", "Employee name", 100)
                    official_email = _required_text(
                        payload, "officialEmail", "Official Kyndryl email", 254
                    ).lower()
                    registration = mark_attendance_by_identity(full_name, official_email)
                else:
                    credential = _required_text(payload, "credential", "Entry code", 64).upper()
                    with connect_db() as connection:
                        row = connection.execute(
                            "SELECT id FROM registrations WHERE credential = ?",
                            (credential,),
                        ).fetchone()
                    if row is None:
                        return self._send_json(
                            HTTPStatus.NOT_FOUND,
                            {"error": "Entry code not found. Verify the code or use manual verification."},
                        )
                    registration = mark_attendance(row["id"])
            except ValueError as exc:
                return self._send_json(HTTPStatus.BAD_REQUEST, {"error": str(exc)})
            except CredentialMismatchError:
                return self._send_json(
                    HTTPStatus.NOT_FOUND,
                    {"error": "No matching registration found. Check the name and official email."},
                )
            except RegistrationNotFoundError:
                return self._send_json(HTTPStatus.NOT_FOUND, {"error": "Registration not found."})
            except AlreadyCheckedInError:
                return self._send_json(
                    HTTPStatus.CONFLICT, {"error": "Attendance has already been recorded for this employee."}
                )
            return self._send_json(HTTPStatus.OK, {"registration": registration})

        return self._send_json(HTTPStatus.NOT_FOUND, {"error": "API route not found."})

    def _send_csv(self):
        fields = [
            "credential",
            "full_name",
            "mobile",
            "official_email",
            "emergency_contact_name",
            "emergency_contact_mobile",
            "dietary_preference",
            "alcohol_preference",
            "travel_mode",
            "created_at",
            "attendance_at",
            "email_status",
        ]
        with connect_db() as connection:
            rows = connection.execute(
                f"SELECT {', '.join(fields)} FROM registrations ORDER BY created_at DESC"
            ).fetchall()
        output = io.StringIO(newline="")
        writer = csv.writer(output)
        writer.writerow(fields)
        for row in rows:
            writer.writerow([_spreadsheet_safe(row[field]) for field in fields])
        body = output.getvalue().encode("utf-8-sig")
        self.send_response(HTTPStatus.OK)
        self.send_header("Content-Type", "text/csv; charset=utf-8")
        self.send_header("Content-Disposition", 'attachment; filename="registrations.csv"')
        self.send_header("Cache-Control", "no-store")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _send_static(self, requested_path):
        path_part = unquote(requested_path)
        if path_part in {"", "/"}:
            path_part = "/index.html"
        candidate = (ROOT / path_part.lstrip("/")).resolve()
        try:
            candidate.relative_to(ROOT)
        except ValueError:
            return self._send_json(HTTPStatus.NOT_FOUND, {"error": "Page not found."})
        if not candidate.is_file():
            return self._send_json(HTTPStatus.NOT_FOUND, {"error": "Page not found."})
        content_type = mimetypes.guess_type(candidate.name)[0] or "application/octet-stream"
        body = candidate.read_bytes()
        self.send_response(HTTPStatus.OK)
        self.send_header("Content-Type", f"{content_type}; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)


def _spreadsheet_safe(value):
    text = "" if value is None else str(value)
    if text.startswith(("=", "+", "-", "@")):
        return "'" + text
    return text


def main():
    parser = argparse.ArgumentParser(description="Kyndryl employee event registration app")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8000)
    args = parser.parse_args()

    initialize_db()
    if not os.environ.get("APP_ADMIN_PASSWORD"):
        print("Admin access is disabled. Set APP_ADMIN_PASSWORD to enable it.", file=sys.stderr)
    try:
        emailjs_configuration = _emailjs_configuration()
    except ValueError as exc:
        parser.error(str(exc))
    if emailjs_configuration is None and not _demo_mode():
        print(
            "EmailJS is not configured; confirmation emails will not be sent.",
            file=sys.stderr,
        )
    server = ThreadingHTTPServer((args.host, args.port), RegistrationHandler)
    print(f"Employee registration app running at http://{args.host}:{args.port}")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\nStopping server.")
    finally:
        server.server_close()


if __name__ == "__main__":
    main()
