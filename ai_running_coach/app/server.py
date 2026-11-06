"""Home Assistant ingress UI for the private coach runtime."""

import argparse
from datetime import date, datetime
import hashlib
import hmac
from http.cookies import CookieError, SimpleCookie
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
from pathlib import Path
import re
import secrets
import threading
from urllib.parse import urlsplit


WEB = Path(__file__).with_name("web")
INGRESS_PEER = "172.30.32.2"
BODY_LIMIT = 16 * 1024
COOKIE = "coach_ingress_session"
ASSETS = {
    "/": ("index.html", "text/html; charset=utf-8"),
    "/assets/coach.css": ("coach.css", "text/css; charset=utf-8"),
    "/assets/coach.js": ("coach.js", "text/javascript; charset=utf-8"),
}
WEEKDAYS = ("Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday")
PROFILE_FIELDS = {
    "name": "Athlete Name", "experience": "Sporting Background", "goal": "Coaching Style",
    "injuries": "Active Injuries",
}
GOAL_FIELDS = {"event": None, "race_date": "Date", "target_time": "Target Time"}
PRIORITIES = ("A", "B", "C")
GOAL_METADATA = "<!-- coach-goal "
TRAINING_GOAL_PREFIX = "Training milestone: "
OUTCOME_LABELS = {"A": "A (ideal)", "B": "B (main)", "C": "C (fallback)"}
ZONE_NAMES = ("Recovery", "Endurance", "Tempo", "Hard", "Max")


def profile_fields(markdown):
    """Recover the supported form fields from the core's private profile."""
    fields = {}
    for key, label in PROFILE_FIELDS.items():
        match = re.search(r"^\*\*" + re.escape(label) + r":\*\*[ \t]*(.*)$", markdown, re.M)
        fields[key] = match[1] if match else ""
    fields["goal"] = fields["goal"].partition("Training goal: ")[2]
    fields["goals"] = {}
    for priority in PRIORITIES:
        race = re.search(r"^\*\*" + priority + r"-Race:\*\*[ \t]*(.*)(?:\n(?!\*\*[ABC]-Race:\*\*).*)*", markdown, re.M | re.I)
        goal = {"event": race[1] if race else ""}
        for key in ("race_date", "target_time"):
            match = re.search(r"^\*\*" + GOAL_FIELDS[key] + r":\*\*[ \t]*(.*)$", race[0], re.M) if race else None
            goal[key] = match[1] if match else ""
        goal["kind"] = "race" if goal["race_date"] or goal["target_time"] else "training"
        goal["outcomes"] = {}
        if race:
            metadata = next((line for line in race[0].splitlines() if line.startswith(GOAL_METADATA) and line.endswith(" -->")), None)
            if metadata:
                try:
                    stored = json.loads(metadata[len(GOAL_METADATA):-4])
                    if stored["kind"] in ("race", "training") and isinstance(stored["target_time"], str) and isinstance(stored["outcomes"], dict):
                        goal["kind"] = stored["kind"]
                        if goal["kind"] == "training":
                            goal["event"] = goal["event"].removeprefix(TRAINING_GOAL_PREFIX)
                        goal["target_time"] = stored["target_time"]
                        goal["outcomes"] = {
                            key: value for key, value in stored["outcomes"].items()
                            if key in PRIORITIES and isinstance(value, str)
                        }
                except (ValueError, KeyError, TypeError):
                    pass
        try:
            goal["race_date"] = datetime.strptime(goal["race_date"], "%B %d, %Y").date().isoformat()
        except ValueError:
            pass
        fields["goals"][priority] = goal
    availability = re.search(r"^\*\*Weekly Availability:\*\*[ \t]*(.*)$", markdown, re.M)
    fields["days"] = [day for day in WEEKDAYS if availability and day in availability[1]]
    fields["zones"] = {}
    for match in re.finditer(r"^\*\*Zone ([1-5]) \([^\n]+\):\*\*\s*(\d+)\s*-\s*(\d+) bpm$", markdown, re.M):
        fields["zones"][match[1]] = [int(match[2]), int(match[3])]
    learning = re.search(r"^\*\*HR Zone Learning:\*\*\s*Enabled\s*$", markdown, re.M)
    fields["zone_mode"] = "learn" if learning else "known" if fields["zones"] else "unknown"
    return fields


def profile_markdown(data, existing=""):
    if set(data) - {*PROFILE_FIELDS, *GOAL_FIELDS, "goals", "days", "zones", "zone_mode"}:
        raise ValueError("Unknown athlete profile field.")
    fields = {}
    for key in PROFILE_FIELDS:
        value = data.get(key, "")
        if not isinstance(value, str) or len(value) > 500:
            raise ValueError("Keep each profile field under 500 characters.")
        fields[key] = " ".join(value.replace("*", "").split())
    if not fields["name"]:
        raise ValueError("Enter an athlete name.")
    saved = profile_fields(existing)
    goal_updates = data.get("goals", {})
    if not isinstance(goal_updates, dict) or set(goal_updates) - set(PRIORITIES):
        raise ValueError("Choose A, B or C for each goal.")
    if set(data) & GOAL_FIELDS.keys():
        if "goals" in data:
            raise ValueError("Submit each goal once.")
        goal_updates = {"A": {key: data.get(key, "") for key in GOAL_FIELDS}}
    goals = saved["goals"]
    for priority, updates in goal_updates.items():
        if not isinstance(updates, dict) or set(updates) - {*GOAL_FIELDS, "kind", "outcomes"}:
            raise ValueError("Enter a goal name, optional date and target.")
        goal = {}
        for key in GOAL_FIELDS:
            value = updates.get(key, "")
            if not isinstance(value, str) or len(value) > 500:
                raise ValueError("Keep goal fields under 500 characters.")
            goal[key] = " ".join(value.replace("*", "").split())
        if goal["race_date"]:
            if not re.fullmatch(r"\d{4}-\d{2}-\d{2}", goal["race_date"]):
                raise ValueError("Use a valid goal date.")
            date.fromisoformat(goal["race_date"])
        goal["kind"] = updates.get("kind", "race" if goal["race_date"] or goal["target_time"] else "training")
        if goal["kind"] not in ("race", "training"):
            raise ValueError("Choose race or training goal.")
        outcomes = updates.get("outcomes", {})
        if not isinstance(outcomes, dict) or set(outcomes) - set(PRIORITIES):
            raise ValueError("Use A, B and C outcome fields.")
        goal["outcomes"] = {}
        for outcome, value in outcomes.items():
            if not isinstance(value, str) or len(value) > 250:
                raise ValueError("Keep race outcomes under 250 characters.")
            cleaned = " ".join(value.replace("*", "").split())
            if cleaned:
                goal["outcomes"][outcome] = cleaned
        if goal["outcomes"] and goal["kind"] != "race":
            raise ValueError("Choose race as the goal type when entering race outcomes.")
        if not goal["event"] and (goal["race_date"] or goal["target_time"] or goal["outcomes"]):
            raise ValueError(f"Enter a name for goal {priority} or clear its date and target.")
        goals[priority] = goal
    days = data.get("days", [])
    if not isinstance(days, list) or any(day not in WEEKDAYS for day in days):
        raise ValueError("Select available training days.")
    zones = data.get("zones", {})
    zone_mode = data.get("zone_mode", "known" if zones else "unknown")
    if zone_mode not in ("unknown", "known", "learn"):
        raise ValueError("Choose a heart rate zone mode.")
    if zone_mode == "learn":
        zones = {}
    elif zone_mode == "unknown":
        zones = {}
    if not isinstance(zones, dict) or set(zones) - set("12345") or zones and "2" not in zones:
        raise ValueError("Enter your configured Zone 2 or choose unknown zones.")
    if zone_mode == "known" and "2" not in zones:
        raise ValueError("Enter your configured Zone 2 or choose unknown zones.")
    lines = ["# Athlete profile", f"**Athlete Name:** {fields['name']}"]
    style = re.search(r"^\*\*Coaching Style:\*\*[ \t]*(.*)$", existing, re.M)
    previous_style = style[1].partition("Training goal: ")[0].strip() if style else ""
    for key in ("experience", "goal", "injuries"):
        if fields[key]:
            value = "Training goal: " + fields[key] if key == "goal" else fields[key]
            if key == "goal" and previous_style:
                value = previous_style + " " + value
            lines.append(f"**{PROFILE_FIELDS[key]}:** {value}")
    if previous_style and not fields["goal"]:
        lines.append("**Coaching Style:** " + previous_style)
    if days:
        lines.append("**Weekly Availability:** " + ", ".join(day for day in WEEKDAYS if day in days))
    if not zones and (zone_mode != "learn" or not re.search(r"^\*\*Zone [1-5] \(", existing, re.M)):
        lines.append("**Heart Rate Zones:** Not configured")
    previous = 0
    for number, bounds in sorted(zones.items()):
        if (
            not isinstance(bounds, list) or len(bounds) != 2
            or any(type(bound) is not int for bound in bounds)
            or not previous < bounds[0] <= bounds[1] <= 300
        ):
            raise ValueError("Enter ascending, non-overlapping heart rate ranges in bpm.")
        previous = bounds[1]
        lines.append(f"**Zone {number} ({ZONE_NAMES[int(number)-1]}):** {bounds[0]} - {bounds[1]} bpm")
    known = set(PROFILE_FIELDS.values())
    known.update(("Weekly Availability", "Heart Rate Zones", "HR Zone Learning"))
    race_priority = None
    zone_lines = []
    extras = []
    for line in existing.splitlines():
        if line == "# Athlete profile":
            continue
        match = re.match(r"^\*\*([^:]+):\*\*", line)
        label = match[1] if match else None
        if label and label.upper() in ("A-RACE", "B-RACE", "C-RACE"):
            race_priority = label
            continue
        if label in known or (race_priority and (label in ("Date", "Target Time") or line.startswith(GOAL_METADATA))):
            continue
        if label and re.match(r"Zone [1-5] \(", label):
            zone_lines.append(line)
            continue
        extras.append(line)
    # Retain an open-ended range from a pre-existing profile when the form
    # otherwise keeps the configured zones unchanged.
    if zone_mode == "learn":
        lines.append("**HR Zone Learning:** Enabled")
    if zone_mode == "learn" or zones and zones == saved["zones"]:
        for line in zone_lines:
            match = re.match(r"^\*\*Zone ([1-5])", line)
            if match[1] not in zones:
                lines.append(line)
    lines.extend(extras)
    for priority in PRIORITIES:
        goal = goals[priority]
        if goal["event"]:
            event = goal["event"]
            if goal["kind"] == "training":
                event = TRAINING_GOAL_PREFIX + event
            lines.append(f"**{priority}-Race:** {event}")
            if goal["race_date"]:
                lines.append(f"**Date:** {goal['race_date']}")
            targets = [f"{OUTCOME_LABELS[key]}: {goal['outcomes'][key]}" for key in PRIORITIES if key in goal["outcomes"]]
            if goal["target_time"]:
                targets.append(goal["target_time"])
            if goal["kind"] == "race" and not targets:
                targets.append("Just finish")
            if targets:
                lines.append("**Target Time:** " + " | ".join(targets))
            stored = {key: goal[key] for key in ("kind", "target_time", "outcomes")}
            metadata = json.dumps(stored, ensure_ascii=True).replace("<", "\\u003c").replace(">", "\\u003e")
            lines.append(GOAL_METADATA + metadata + " -->")
    return "\n".join(lines).rstrip() + "\n"


class CoachServer(ThreadingHTTPServer):
    def __init__(self, address, runtime, development=False):
        if development and address[0] != "127.0.0.1":
            raise ValueError("Development mode must bind to IPv4 loopback.")
        self.runtime = runtime
        self.development = development
        self.session_secret = secrets.token_bytes(32)
        super().__init__(address, CoachHandler)


class CoachHandler(BaseHTTPRequestHandler):
    server_version = "AI Running Coach"
    sys_version = ""

    def setup(self):
        self.request.settimeout(12)
        super().setup()

    def log_message(self, *_args):
        """Keep request paths, headers and payloads out of logs."""

    def route(self):
        path = urlsplit(self.path).path
        prefix = self.headers.get("X-Ingress-Path", "").rstrip("/")
        if self.client_address[0] == INGRESS_PEER and prefix.startswith("/") and path.startswith(prefix + "/"):
            path = path[len(prefix):]
        return path

    def permitted(self, path):
        peer = self.client_address[0]
        if self.server.development:
            return peer == "127.0.0.1"
        return peer == INGRESS_PEER or (path == "/healthz" and peer == "127.0.0.1")

    def respond(self, status, content, kind, *, cookie=None, dashboard=False, download=None):
        self.send_response(status)
        self.send_header("Content-Type", kind)
        self.send_header("Content-Length", str(len(content)))
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("Referrer-Policy", "no-referrer")
        if download:
            self.send_header("Content-Disposition", f'attachment; filename="{download}"')
        if dashboard:
            policy = (
                "sandbox allow-scripts allow-downloads; default-src 'none'; "
                "script-src 'unsafe-inline' 'unsafe-eval'; style-src 'unsafe-inline'; "
                "img-src data:; font-src data:; frame-ancestors 'self'; base-uri 'none'; form-action 'none'"
            )
        else:
            policy = (
                "default-src 'self'; script-src 'self'; style-src 'self'; frame-src 'self'; "
                "frame-ancestors 'self'; object-src 'none'; base-uri 'none'; form-action 'self'"
            )
        self.send_header("Content-Security-Policy", policy)
        if cookie:
            secure = "; Secure" if self.headers.get("X-Forwarded-Proto") == "https" else ""
            self.send_header("Set-Cookie", f"{COOKIE}={cookie}; HttpOnly; SameSite=Strict{secure}")
        self.end_headers()
        self.wfile.write(content)

    def send_json(self, status, data, *, cookie=None):
        self.respond(status, json.dumps(data).encode("utf-8"), "application/json; charset=utf-8", cookie=cookie)

    def session(self):
        cookies = SimpleCookie()
        try:
            cookies.load(self.headers.get("Cookie", ""))
        except CookieError:
            return None
        value = cookies[COOKIE].value if COOKIE in cookies else ""
        return value if re.fullmatch(r"[a-f0-9]{64}", value) else None

    def csrf(self, session):
        return hmac.new(self.server.session_secret, session.encode(), hashlib.sha256).hexdigest()

    def do_GET(self):
        self.dispatch(self.get_route)

    def do_POST(self):
        self.dispatch(self.post_route)

    def dispatch(self, handler):
        try:
            handler()
        except (BrokenPipeError, ConnectionResetError):
            self.close_connection = True
        except Exception:
            self.close_connection = True
            try:
                self.send_json(500, {"error": "The coach could not complete this request."})
            except OSError:
                pass

    def get_route(self):
        path = self.route()
        if not self.permitted(path):
            self.send_json(403, {"error": "Use Home Assistant ingress to open the coach."})
            return
        runtime = self.server.runtime
        if path == "/healthz":
            healthy = runtime.status().get("worker_ok", True)
            self.send_json(200 if healthy else 503, {"ok": healthy})
        elif path in ASSETS:
            filename, kind = ASSETS[path]
            self.respond(200, (WEB / filename).read_bytes(), kind)
        elif path == "/api/status":
            session = self.session()
            new_cookie = None if session else secrets.token_hex(32)
            self.send_json(200, {**runtime.status(), "csrf": self.csrf(session or new_cookie)}, cookie=new_cookie)
        elif path == "/api/profile":
            self.send_json(200, profile_fields(runtime.profile()))
        elif path == "/api/profile-summary":
            markdown = runtime.profile()
            visible = '\n'.join(line for line in markdown.splitlines() if not line.startswith(GOAL_METADATA))
            self.send_json(200, {"profile": profile_fields(markdown), "markdown": visible})
        elif path in ("/exports/athlete.md", "/exports/training_plan.md", "/exports/training_calendar.pdf"):
            filename = path.rsplit('/', 1)[1]
            try:
                content = runtime.export(filename)
            except FileNotFoundError:
                self.send_json(404, {"error": "Save an athlete profile and generate a plan before downloading this file."})
            except (RuntimeError, ValueError):
                self.send_json(409, {"error": "The saved export is unavailable. Wait for the current run to finish and check the saved plan."})
            else:
                kind = "application/pdf" if filename.endswith('.pdf') else "text/markdown; charset=utf-8"
                self.respond(200, content, kind, download=filename)
        elif path == "/dashboard":
            content = runtime.dashboard()
            if content is None:
                self.send_json(404, {"error": "Generate a plan to open the dashboard."})
            else:
                # The pinned dashboard has an offline display branch. Use it
                # inside the opaque frame, where its status poll cannot run.
                content = content.replace(
                    b"if (!['http:','https:'].includes(location.protocol)) {",
                    b"if (true) {",
                    1,
                )
                # Old saved dashboards retain their DOM for legacy Today links,
                # while the removed view stays inaccessible without a new AI run.
                content += b'<style>#toggleViewBtn,#tableView{display:none!important}</style>'
                self.respond(200, content, "text/html; charset=utf-8", dashboard=True)
        elif path == "/coach-status.json":
            self.send_json(200, runtime.health())
        else:
            self.send_json(404, {"error": "Page not found."})

    def post_route(self):
        path = self.route()
        if not self.permitted(path):
            self.send_json(403, {"error": "Use Home Assistant ingress to open the coach."})
            return
        runtime = self.server.runtime
        actions = {
            "/api/config": runtime.save_config,
            "/api/profile": lambda payload: runtime.save_profile(profile_markdown(payload, runtime.profile())),
            "/api/baseline-feedback": lambda payload: runtime.save_baseline_feedback(payload),
            "/api/action": lambda payload: self.run_action(runtime, payload),
            "/api/schedule": lambda payload: self.save_schedule(runtime, payload),
        }
        if path not in actions:
            self.send_json(404, {"error": "Action not found."})
            return
        lengths = self.headers.get_all("Content-Length", [])
        if self.headers.get("Transfer-Encoding") or len(lengths) != 1 or not re.fullmatch(r"[0-9]{1,9}", lengths[0]):
            self.send_json(400, {"error": "Invalid request length."})
            return
        length = int(lengths[0])
        if length > BODY_LIMIT:
            self.send_json(413, {"error": "Request is too large."})
            return
        if self.headers.get_content_type() != "application/json":
            self.send_json(415, {"error": "Use a JSON request."})
            return
        try:
            # Read the bounded body before responding. Closing with unread TCP
            # data can discard the response, notably on Windows.
            body = self.rfile.read(length)
            if len(body) != length:
                raise ValueError("Incomplete body.")
            session = self.session()
            token = self.headers.get("X-Coach-CSRF", "")
            if not session or not re.fullmatch(r"[a-f0-9]{64}", token) or not hmac.compare_digest(token, self.csrf(session)):
                self.send_json(403, {"error": "Your page session expired. Refresh and try again."})
                return
            payload = json.loads(body)
            if not isinstance(payload, dict):
                raise ValueError("Expected a JSON object.")
        except (ValueError, TimeoutError, RecursionError):
            self.send_json(400, {"error": "Invalid JSON request."})
            return
        try:
            result = actions[path](payload)
        except ValueError as error:
            self.send_json(400, {"error": str(error)})
        except RuntimeError as error:
            self.send_json(409, {"error": str(error)})
        else:
            self.send_json(200, result)

    @staticmethod
    def run_action(runtime, payload):
        if set(payload) not in ({"action"}, {"action", "manual_extra"}) or payload["action"] not in ("new_plan", "run_now"):
            raise ValueError("Choose a supported coach action.")
        if "manual_extra" in payload:
            if payload["manual_extra"] is None:
                raise ValueError("Confirm one extra AI request before continuing.")
            return runtime.enqueue(payload["action"], manual_extra=payload["manual_extra"])
        return runtime.enqueue(payload["action"])

    @staticmethod
    def save_schedule(runtime, payload):
        if set(payload) != {"enabled", "time", "timezone"}:
            raise ValueError("Choose the daily schedule, time and timezone.")
        if type(payload["enabled"]) is not bool or not all(isinstance(payload[key], str) for key in ("time", "timezone")):
            raise ValueError("Choose a valid daily schedule.")
        return runtime.set_schedule(payload["enabled"], payload["time"], payload["timezone"])


def main():
    from .runtime import Runtime

    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dev", action="store_true", help="Permit local browser access on 127.0.0.1.")
    parser.add_argument("--port", type=int, default=8099)
    parser.add_argument("--data-dir", type=Path, default=Path("/data"))
    args = parser.parse_args()
    runtime = Runtime(args.data_dir)
    stop = threading.Event()
    worker = threading.Thread(target=runtime.run_worker, args=(stop,), daemon=True)
    with CoachServer(("127.0.0.1" if args.dev else "0.0.0.0", args.port), runtime, args.dev) as server:
        worker.start()
        try:
            server.serve_forever()
        except KeyboardInterrupt:
            pass
        finally:
            stop.set()


if __name__ == "__main__":
    main()
