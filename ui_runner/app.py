from __future__ import annotations

# ──────────────────────────────────────────────────────────────────────────────
# Windows UTF-8 fix — MUST be at the very top
# ──────────────────────────────────────────────────────────────────────────────
import os
import sys

os.environ.setdefault("PYTHONUTF8", "1")
os.environ.setdefault("PYTHONIOENCODING", "utf-8")

if sys.platform == "win32":
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
        sys.stderr.reconfigure(encoding="utf-8", errors="replace")
    except AttributeError:
        pass

import gzip
import io
import json
import time
import uuid
import threading
import subprocess
import urllib.error
import urllib.request
from urllib.parse import quote
from dataclasses import dataclass, field
from pathlib import Path
from typing import Dict, List, Optional, Any
from datetime import datetime

from flask import Flask, jsonify, render_template, request, send_from_directory, abort, make_response

# ──────────────────────────────────────────────────────────────────────────────
# Paths
# ──────────────────────────────────────────────────────────────────────────────
BASE_DIR      = Path(__file__).resolve().parent.parent  # repo root (one level above ui_runner/)
UI_DIR        = Path(__file__).resolve().parent         # all UI assets live here (ui_runner/)
CONFIG_PATH   = UI_DIR / "commands.json"
TEMPLATES_DIR = UI_DIR / "templates"
ARCHIVE_DIR   = TEMPLATES_DIR / "archive"
STATIC_DIR    = UI_DIR / "static"

# Pipeline output paths (used by status + slate endpoints)
NBA_DIR       = BASE_DIR / "NBA"
CBB_DIR       = BASE_DIR / "CBB"
NHL_DIR       = BASE_DIR / "NHL"
SOCCER_DIR    = BASE_DIR / "Soccer"
MLB_DIR       = BASE_DIR / "MLB"
NBA_FLAG      = NBA_DIR / "RUN_COMPLETE.flag"
NBA_SLATE     = NBA_DIR / "step8_all_direction_clean.xlsx"
NBA1H_SLATE   = NBA_DIR / "step8_nba1h_direction_clean.xlsx"
NBA1Q_SLATE   = NBA_DIR / "step8_nba1q_direction_clean.xlsx"
NBA_TICKETS   = NBA_DIR / "best_tickets.xlsx"
NBA1H_TICKETS = NBA_DIR / "best_tickets_nba1h.xlsx"
NBA1Q_TICKETS = NBA_DIR / "best_tickets_nba1q.xlsx"
CBB_SLATE     = CBB_DIR / "step6_ranked_cbb.xlsx"
# Same paths as scripts/run_pipeline.ps1 (sport root, not sport/outputs/)
NHL_SLATE     = NHL_DIR / "step8_nhl_direction_clean.xlsx"
NHL_TICKETS   = NHL_DIR / "nhl_best_tickets.xlsx"
SOCCER_SLATE  = SOCCER_DIR / "step8_soccer_direction_clean.xlsx"
SOCCER_TICKETS= SOCCER_DIR / "soccer_best_tickets.xlsx"
MLB_SLATE     = MLB_DIR / "step8_mlb_direction_clean.xlsx"
MLB_TICKETS   = MLB_DIR / "mlb_best_tickets.xlsx"
COMBINED_OUT  = BASE_DIR  # combined_slate_tickets_YYYY-MM-DD.xlsx lives here

app = Flask(
    __name__,
    template_folder=str(TEMPLATES_DIR),
    static_folder=str(STATIC_DIR) if STATIC_DIR.exists() else None,
)

# ── Response compression + static caching ─────────────────────────────────────
_COMPRESSIBLE = ("text/", "application/json", "application/javascript")
_STATIC_EXTS  = (".css", ".js", ".png", ".jpg", ".jpeg", ".webp", ".gif", ".svg", ".ico", ".woff", ".woff2")

@app.after_request
def post_process_response(response):
    # Long-lived cache for static assets (images, CSS, JS)
    if request.path.startswith("/static/") and any(request.path.endswith(e) for e in _STATIC_EXTS):
        if "Cache-Control" not in response.headers:
            response.headers["Cache-Control"] = "public, max-age=86400"

    # Gzip compress eligible text responses
    if (
        response.direct_passthrough
        or response.status_code < 200
        or response.status_code >= 300
        or "Content-Encoding" in response.headers
        or not any(t in response.content_type for t in _COMPRESSIBLE)
        or "gzip" not in request.headers.get("Accept-Encoding", "")
    ):
        return response
    data = response.get_data()
    if len(data) < 500:
        return response
    buf = io.BytesIO()
    with gzip.GzipFile(mode="wb", fileobj=buf, compresslevel=6) as gz:
        gz.write(data)
    compressed = buf.getvalue()
    if len(compressed) >= len(data):
        return response
    response.set_data(compressed)
    response.headers["Content-Encoding"] = "gzip"
    response.headers["Content-Length"]   = len(compressed)
    response.headers["Vary"]             = "Accept-Encoding"
    return response

# ──────────────────────────────────────────────────────────────────────────────
# Job Model
# ──────────────────────────────────────────────────────────────────────────────
@dataclass
class RunJob:
    job_id:      str
    label:       str
    started_at:  float         = field(default_factory=time.time)
    ended_at:    Optional[float] = None
    status:      str           = "RUNNING"   # RUNNING | OK | FAIL
    return_code: Optional[int] = None
    lines:       List[str]     = field(default_factory=list)
    # Step-level progress (populated for chain jobs)
    steps:       List[dict]    = field(default_factory=list)

JOBS: Dict[str, RunJob] = {}
LOCK = threading.Lock()


# ──────────────────────────────────────────────────────────────────────────────
# Helpers
# ──────────────────────────────────────────────────────────────────────────────
_config_cache: Optional[dict] = None


def load_config() -> dict:
    global _config_cache
    if _config_cache is not None:
        return _config_cache
    if not CONFIG_PATH.exists():
        raise FileNotFoundError(f"commands.json not found at: {CONFIG_PATH}")
    _config_cache = json.loads(CONFIG_PATH.read_text(encoding="utf-8-sig"))
    return _config_cache


_json_file_cache: dict[str, dict[str, Any]] = {}


def read_json_cached(path: Path, ttl: float = 300.0) -> Any:
    """Load JSON from disk with an in-process TTL (template payloads written by pipeline)."""
    key = str(path.resolve())
    now = time.time()
    entry = _json_file_cache.get(key)
    if entry is not None and now - entry["ts"] <= ttl:
        return entry["data"]
    data = json.loads(path.read_text(encoding="utf-8-sig"))
    _json_file_cache[key] = {"data": data, "ts": now}
    return data


# ── Pre-serialized + pre-gzipped response cache ───────────────────────────────
# Avoids re-serializing and re-compressing large payloads on every request.
_gz_cache: dict[str, tuple[bytes, float]] = {}
_GZ_CACHE_LOCK = threading.Lock()


def _gz_json_response(key: str, build_fn, ttl: float = 300.0):
    """
    Call build_fn() once per TTL, serialize+gzip the result, serve from cache after.
    Handles both gzip-capable and plain clients.
    """
    now = time.time()
    gz_bytes = None
    with _GZ_CACHE_LOCK:
        entry = _gz_cache.get(key)
        if entry and now - entry[1] < ttl:
            gz_bytes = entry[0]

    if gz_bytes is None:
        data = build_fn()
        raw  = json.dumps(data, separators=(",", ":")).encode("utf-8")
        buf  = io.BytesIO()
        with gzip.GzipFile(mode="wb", fileobj=buf, compresslevel=6) as f:
            f.write(raw)
        gz_bytes = buf.getvalue()
        with _GZ_CACHE_LOCK:
            _gz_cache[key] = (gz_bytes, time.time())

    if "gzip" in request.headers.get("Accept-Encoding", ""):
        resp = app.response_class(gz_bytes, status=200, mimetype="application/json")
        resp.headers["Content-Encoding"] = "gzip"
        resp.headers["Content-Length"]   = len(gz_bytes)
        resp.headers["Vary"]             = "Accept-Encoding"
        return resp
    # Non-gzip client: decompress inline (rare — all modern browsers support gzip)
    with gzip.GzipFile(fileobj=io.BytesIO(gz_bytes)) as f:
        return app.response_class(f.read(), status=200, mimetype="application/json")


def safe_tail(lines: List[str], max_lines: int = 2500) -> List[str]:
    return lines if len(lines) <= max_lines else lines[-max_lines:]


def _build_subprocess_env() -> dict:
    env = os.environ.copy()
    env["PYTHONUTF8"]                    = "1"
    env["PYTHONIOENCODING"]              = "utf-8"
    env["PYTHONLEGACYWINDOWSSTDIO"]      = ""
    env["PYTHONLEGACYWINDOWSFSENCODING"] = ""
    return env


def _maybe_wrap_powershell(cmd: List[str]) -> List[str]:
    if not cmd:
        return cmd
    exe = cmd[0].lower()
    if exe not in ("powershell", "powershell.exe", "pwsh", "pwsh.exe"):
        return cmd
    lower_args = [a.lower() for a in cmd]
    utf8_setup = (
        "[Console]::OutputEncoding = [System.Text.Encoding]::UTF8; "
        "$OutputEncoding = [System.Text.Encoding]::UTF8; "
        "chcp 65001 | Out-Null; "
    )
    if "-command" in lower_args:
        idx = lower_args.index("-command")
        cmd = list(cmd)
        cmd[idx + 1] = utf8_setup + cmd[idx + 1]
        return cmd
    if "-file" in lower_args:
        idx = lower_args.index("-file")
        script_path = cmd[idx + 1]
        extra_args  = cmd[idx + 2:]
        extra_str   = " ".join(f'"{a}"' for a in extra_args) if extra_args else ""
        ps_body = (
            utf8_setup +
            f"& '{script_path}' {extra_str}".strip()
        )
        pre_flags: List[str] = []
        i = 1
        while i < idx:
            pre_flags.append(cmd[i])
            i += 1
        return [cmd[0], "-NoProfile"] + pre_flags + ["-Command", ps_body]
    return cmd


def _auto_wrap_script_if_needed(cmd: List[str], workdir: Path) -> List[str]:
    if not cmd:
        return cmd
    first = cmd[0]
    first_lower = first.lower()
    if first_lower in ("powershell", "powershell.exe", "pwsh", "pwsh.exe"):
        return cmd
    if first_lower.endswith(".ps1"):
        script_path = Path(first)
        if not script_path.is_absolute():
            script_path = (workdir / script_path).resolve()
        try:
            rel = script_path.relative_to(workdir)
            script_for_ps = str(rel)
        except Exception:
            script_for_ps = str(script_path)
        return ["powershell", "-ExecutionPolicy", "Bypass", "-File", script_for_ps] + cmd[1:]
    return cmd


def _run_process(job: RunJob, cmd: List[str], workdir: Path) -> None:
    try:
        if not isinstance(cmd, list) or not all(isinstance(x, str) for x in cmd):
            raise ValueError(f"cmd must be List[str]. Got: {type(cmd)}")
        if not workdir.exists():
            raise FileNotFoundError(f"workdir does not exist: {workdir}")

        env      = _build_subprocess_env()
        cmd2     = _auto_wrap_script_if_needed(cmd, workdir)
        safe_cmd = _maybe_wrap_powershell(cmd2)

        proc = subprocess.Popen(
            safe_cmd,
            cwd=str(workdir),
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            encoding="utf-8",
            errors="replace",
            bufsize=1,
            env=env,
        )

        assert proc.stdout is not None
        for raw_line in proc.stdout:
            line = raw_line.rstrip("\r\n").replace("\x00", "")
            with LOCK:
                job.lines.append(line)
                job.lines = safe_tail(job.lines)

        rc = proc.wait()
        with LOCK:
            job.return_code = rc
            job.ended_at    = time.time()
            job.status      = "OK" if rc == 0 else "FAIL"

    except Exception as exc:
        with LOCK:
            job.lines.append(f"[ERROR] {type(exc).__name__}: {exc}")
            job.ended_at    = time.time()
            job.status      = "FAIL"
            job.return_code = -1


def start_job(label: str, cmd: List[str], workdir: Path) -> str:
    job_id = str(uuid.uuid4())
    job    = RunJob(job_id=job_id, label=label)
    with LOCK:
        JOBS[job_id] = job
    threading.Thread(target=_run_process, args=(job, cmd, workdir), daemon=True).start()
    return job_id


def resolve_command(config: dict, pipeline_name: str, command_id: str) -> Dict[str, Any]:
    pipelines = config.get("pipelines") or {}
    if pipeline_name not in pipelines:
        raise KeyError(f"Unknown pipeline '{pipeline_name}'. Available: {list(pipelines.keys())}")

    pipe          = pipelines[pipeline_name]
    commands_list = pipe.get("commands") or []
    cmds          = {c.get("id"): c for c in commands_list if isinstance(c, dict) and c.get("id")}

    if command_id not in cmds:
        raise KeyError(f"Unknown command_id '{command_id}' for pipeline '{pipeline_name}'.")

    c = cmds[command_id]
    if "cmd_chain" in c:
        chain_ids = c.get("cmd_chain") or []
        expanded  = []
        for x in chain_ids:
            if x not in cmds:
                raise KeyError(f"cmd_chain references missing command id '{x}'.")
            expanded.append(cmds[x])
        return {"type": "chain", "items": expanded, "label": c.get("label", command_id)}

    return {"type": "single", "item": c, "label": c.get("label", command_id)}


def subst_tokens(cmd: List[str], config: Optional[dict] = None) -> List[str]:
    today     = datetime.now().strftime("%Y-%m-%d")
    now_ts    = datetime.now().strftime("%Y-%m-%d_%H%M%S")
    repo_root = ""
    if config and isinstance(config, dict):
        repo_root = (
            str(Path(config.get("repo_root", "")).resolve())
            if config.get("repo_root") else ""
        )
    out: List[str] = []
    for x in cmd:
        y = x.replace("{TODAY}", today).replace("{NOW}", now_ts)
        if repo_root:
            y = y.replace("{REPO_ROOT}", repo_root)
        out.append(y)
    return out



def _file_info(path: Path) -> dict:
    """Return size + modified time for a file, or None flags if missing."""
    if not path.exists():
        return {"exists": False, "modified": None, "size_kb": None}
    stat = path.stat()
    return {
        "exists":   True,
        "modified": datetime.fromtimestamp(stat.st_mtime).strftime("%Y-%m-%d %H:%M:%S"),
        "size_kb":  round(stat.st_size / 1024, 1),
    }


def _slate_counts() -> tuple[dict[str, int], dict]:
    """
    Return ({sport_key: row_count}, file_info_for_slate_latest_json).
    """
    path = TEMPLATES_DIR / "slate_latest.json"
    info = _file_info(path)
    if not info.get("exists"):
        return {}, info
    try:
        payload = read_json_cached(path)
        sports = payload.get("sports") or {}
        counts = {str(k).lower(): len(v or []) for k, v in sports.items()}
        return counts, info
    except Exception:
        return {}, info


def _file_info_with_slate_fallback(path: Path, sport_key: str, counts: dict[str, int], fallback_info: dict) -> dict:
    direct = _file_info(path)
    if direct.get("exists"):
        return direct
    count = int(counts.get(sport_key.lower(), 0))
    if count > 0:
        return {
            "exists": True,
            "modified": fallback_info.get("modified"),
            "size_kb": fallback_info.get("size_kb"),
        }
    return direct


# ──────────────────────────────────────────────────────────────────────────────
# Pages
# ──────────────────────────────────────────────────────────────────────────────
@app.get("/")
def home():
    return render_template("index.html", config=load_config())


@app.get("/ping")
def ping():
    """Lightweight health check for uptime pingers (no DB / no template work)."""
    return "ok", 200


def _no_store_headers(resp):
    resp.headers["Cache-Control"] = "no-store, no-cache, must-revalidate, max-age=0"
    resp.headers["Pragma"] = "no-cache"
    resp.headers["Expires"] = "0"
    return resp


@app.get("/tickets_latest.json")
def serve_tickets_latest_json():
    """Expose JSON at site root so the link on /tickets works (relative URLs break under /tickets)."""
    path = TEMPLATES_DIR / "tickets_latest.json"
    if not path.exists():
        abort(404)
    return _no_store_headers(
        send_from_directory(str(TEMPLATES_DIR), "tickets_latest.json", mimetype="application/json")
    )


@app.get("/tickets")
def page_tickets():
    """
    Prefer static tickets_latest.html from build_ticket_eval.py (graded legs, pipeline step).
    If missing, render from tickets_latest.json via combined_slate_tickets.render_tickets_html (dev / pre-eval).
    """
    import importlib.util

    json_path = TEMPLATES_DIR / "tickets_latest.json"
    html_static = TEMPLATES_DIR / "tickets_latest.html"
    if html_static.exists():
        return _no_store_headers(
            send_from_directory(str(TEMPLATES_DIR), "tickets_latest.html")
        )
    if not json_path.exists():
        return "tickets_latest.json not found. Run the pipeline with --write-web first.", 404
    try:
        payload = read_json_cached(json_path)
    except Exception as e:
        return f"Invalid tickets_latest.json: {e}", 500

    cst_path = BASE_DIR / "scripts" / "combined_slate_tickets.py"
    try:
        if not cst_path.exists():
            raise FileNotFoundError("scripts/combined_slate_tickets.py not in repo")
        spec = importlib.util.spec_from_file_location("combined_slate_tickets", cst_path)
        if spec is None or spec.loader is None:
            raise RuntimeError("could not load combined_slate_tickets spec")
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)
        html = mod.render_tickets_html(payload)
    except Exception:
        return (
            "Could not render tickets (import/renderer error). Add tickets_latest.html (build_ticket_eval.py) "
            "or fix combined_slate_tickets import.",
            500,
        )

    return _no_store_headers(make_response(html))


@app.get("/payout")
def page_payout():
    r = make_response(render_template("payout_calculator.html"))
    r.headers["Cache-Control"] = "no-store, no-cache, must-revalidate, max-age=0"
    r.headers["Pragma"] = "no-cache"
    r.headers["Expires"] = "0"
    return r


@app.get("/grades")
def page_grades():
    r = make_response(render_template("indexGrades.html"))
    r.headers["Cache-Control"] = "no-store, no-cache, must-revalidate, max-age=0"
    r.headers["Pragma"] = "no-cache"
    r.headers["Expires"] = "0"
    return r


@app.route("/grades/slate_eval_<date>.html", methods=("GET", "HEAD"))
def serve_grade_report(date: str):
    """Serve individual slate_eval_YYYY-MM-DD.html files for the grades iframe."""
    fname = f"slate_eval_{date}.html"
    if TEMPLATES_DIR.exists() and (TEMPLATES_DIR / fname).exists():
        response = send_from_directory(str(TEMPLATES_DIR), fname)
        response.headers["Cache-Control"] = "no-store, no-cache, must-revalidate, max-age=0"
        response.headers["Pragma"] = "no-cache"
        response.headers["Expires"] = "0"
        return response
    if ARCHIVE_DIR.exists() and (ARCHIVE_DIR / fname).exists():
        response = send_from_directory(str(ARCHIVE_DIR), fname)
        response.headers["Cache-Control"] = "no-store, no-cache, must-revalidate, max-age=0"
        response.headers["Pragma"] = "no-cache"
        response.headers["Expires"] = "0"
        return response
    abort(404)


@app.route("/grades/ticket_eval_<date>.html", methods=("GET", "HEAD"))
def serve_ticket_eval_report(date: str):
    """Serve individual ticket_eval_YYYY-MM-DD.html files for the ticket evaluation iframe."""
    fname = f"ticket_eval_{date}.html"
    if TEMPLATES_DIR.exists() and (TEMPLATES_DIR / fname).exists():
        response = send_from_directory(str(TEMPLATES_DIR), fname)
        response.headers["Cache-Control"] = "no-store, no-cache, must-revalidate, max-age=0"
        response.headers["Pragma"] = "no-cache"
        response.headers["Expires"] = "0"
        return response
    if ARCHIVE_DIR.exists() and (ARCHIVE_DIR / fname).exists():
        response = send_from_directory(str(ARCHIVE_DIR), fname)
        response.headers["Cache-Control"] = "no-store, no-cache, must-revalidate, max-age=0"
        response.headers["Pragma"] = "no-cache"
        response.headers["Expires"] = "0"
        return response
    abort(404)



# ──────────────────────────────────────────────────────────────────────────────
# NEW: Pipeline Status API
# Returns health of all pipeline outputs so UI can show green/red indicators
# ──────────────────────────────────────────────────────────────────────────────
@app.get("/api/pipeline/status")
def api_pipeline_status():
    today = datetime.now().strftime("%Y-%m-%d")
    slate_counts, slate_json_info = _slate_counts()
    combined_path = next(
        iter(sorted(BASE_DIR.glob(f"combined_slate_tickets_{today}*.xlsx"), reverse=True)),
        None
    )
    return jsonify({
        "nba": {
            "run_complete_flag": NBA_FLAG.exists(),
            "slate":   _file_info_with_slate_fallback(NBA_SLATE, "nba", slate_counts, slate_json_info),
            "tickets": _file_info(NBA_TICKETS),
        },
        "nba1h": {
            "slate":   _file_info_with_slate_fallback(NBA1H_SLATE, "nba1h", slate_counts, slate_json_info),
            "tickets": _file_info(NBA1H_TICKETS),
        },
        "nba1q": {
            "slate":   _file_info_with_slate_fallback(NBA1Q_SLATE, "nba1q", slate_counts, slate_json_info),
            "tickets": _file_info(NBA1Q_TICKETS),
        },
        "cbb": {
            "slate": _file_info_with_slate_fallback(CBB_SLATE, "cbb", slate_counts, slate_json_info),
        },
        "nhl": {
            "slate":   _file_info_with_slate_fallback(NHL_SLATE, "nhl", slate_counts, slate_json_info),
            "tickets": _file_info(NHL_TICKETS),
        },
        "soccer": {
            "slate":   _file_info_with_slate_fallback(SOCCER_SLATE, "soccer", slate_counts, slate_json_info),
            "tickets": _file_info(SOCCER_TICKETS),
        },
        "mlb": {
            "slate":   _file_info_with_slate_fallback(MLB_SLATE, "mlb", slate_counts, slate_json_info),
            "tickets": _file_info(MLB_TICKETS),
        },
        "combined": {
            "slate": _file_info(combined_path) if combined_path else {"exists": False},
        },
        "as_of": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
    })


# ──────────────────────────────────────────────────────────────────────────────
# NEW: Active Job Status (quick poll for running job count)
# ──────────────────────────────────────────────────────────────────────────────
@app.get("/api/pipeline/running")
def api_pipeline_running():
    with LOCK:
        running = [j for j in JOBS.values() if j.status == "RUNNING"]
    return jsonify({
        "running": len(running),
        "jobs": [{"job_id": j.job_id, "label": j.label, "started_at": j.started_at}
                 for j in running]
    })


# ──────────────────────────────────────────────────────────────────────────────
# API: Run Command
# ──────────────────────────────────────────────────────────────────────────────

# ── API: Config endpoint ──────────────────────────────────────────────────────
@app.get("/api/config")
def api_config():
    try:
        return jsonify(load_config())
    except Exception as exc:
        return jsonify({"error": str(exc)}), 500


@app.post("/api/run")
def api_run():
    data       = request.get_json(force=True) or {}
    pipeline   = data.get("pipeline")
    command_id = data.get("command_id")

    if not pipeline or not command_id:
        return jsonify({"error": "missing_pipeline_or_command_id"}), 400

    try:
        config      = load_config()
        repo_root   = Path(config["repo_root"]).expanduser().resolve()
        cmd_def     = resolve_command(config, pipeline, command_id)
        workdir_rel = (config["pipelines"][pipeline].get("workdir") or "").strip()
        workdir     = (repo_root / workdir_rel).resolve()
    except Exception as exc:
        return jsonify({"error": "config_or_command_error", "detail": str(exc)}), 400

    # ── Chain ──
    if cmd_def["type"] == "chain":
        parent_id = str(uuid.uuid4())
        parent    = RunJob(job_id=parent_id, label=cmd_def["label"])
        # Pre-populate step list for progress tracking
        parent.steps = [
            {"id": item.get("id"), "label": item.get("label", item.get("id")), "status": "PENDING"}
            for item in cmd_def["items"]
        ]
        with LOCK:
            JOBS[parent_id] = parent

        def chain_runner() -> None:
            ok = True
            for i, item in enumerate(cmd_def["items"]):
                label   = item.get("label") or item.get("id") or "STEP"
                raw_cmd = item.get("cmd")

                # Update step status to RUNNING
                with LOCK:
                    if i < len(parent.steps):
                        parent.steps[i]["status"] = "RUNNING"
                    parent.lines.append("")
                    parent.lines.append(f"=== {label} ===")

                if not isinstance(raw_cmd, list):
                    with LOCK:
                        parent.lines.append(f"[ERROR] Bad cmd for '{label}': expected list")
                        if i < len(parent.steps):
                            parent.steps[i]["status"] = "FAIL"
                    ok = False
                    break

                cmd   = subst_tokens(raw_cmd, config=config)
                child = RunJob(job_id=str(uuid.uuid4()), label=label)
                _run_process(child, cmd, workdir)

                with LOCK:
                    parent.lines.extend(child.lines)
                    parent.lines = safe_tail(parent.lines)
                    step_ok = (child.return_code == 0)
                    if i < len(parent.steps):
                        parent.steps[i]["status"] = "OK" if step_ok else "FAIL"
                    if not step_ok:
                        ok = False
                        parent.lines.append("[CHAIN] Stopping — step failed.")
                        # Mark remaining steps as SKIPPED
                        for j in range(i + 1, len(parent.steps)):
                            parent.steps[j]["status"] = "SKIPPED"
                        break

            with LOCK:
                parent.ended_at    = time.time()
                parent.status      = "OK" if ok else "FAIL"
                parent.return_code = 0 if ok else 1

        threading.Thread(target=chain_runner, daemon=True).start()
        return jsonify({"job_id": parent_id})

    # ── Single ──
    item    = cmd_def["item"]
    raw_cmd = item.get("cmd")
    if not isinstance(raw_cmd, list):
        return jsonify({"error": "bad_cmd_type", "detail": f"Expected list, got {type(raw_cmd)}"}), 400

    cmd    = subst_tokens(raw_cmd, config=config)
    job_id = start_job(cmd_def["label"], cmd, workdir)
    return jsonify({"job_id": job_id})


# ──────────────────────────────────────────────────────────────────────────────
# API: Job Status (with step progress for chain jobs)
# ──────────────────────────────────────────────────────────────────────────────
@app.get("/api/job/<job_id>")
def api_job(job_id: str):
    with LOCK:
        j = JOBS.get(job_id)
        if not j:
            return jsonify({"error": "not_found"}), 404
        return jsonify({
            "job_id":      j.job_id,
            "label":       j.label,
            "status":      j.status,
            "return_code": j.return_code,
            "started_at":  j.started_at,
            "ended_at":    j.ended_at,
            "lines":       j.lines[-400:],
            "steps":       j.steps,   # NEW: step-level progress
            "elapsed_s":   round((j.ended_at or time.time()) - j.started_at, 1),
        })


@app.get("/api/jobs")
def api_jobs():
    with LOCK:
        out = [
            {
                "job_id":      j.job_id,
                "label":       j.label,
                "status":      j.status,
                "started_at":  j.started_at,
                "ended_at":    j.ended_at,
                "return_code": j.return_code,
                "elapsed_s":   round((j.ended_at or time.time()) - j.started_at, 1),
                "steps":       j.steps,
            }
            for j in JOBS.values()
        ]
    out.sort(key=lambda x: x["started_at"], reverse=True)
    return jsonify(out[:25])


# ──────────────────────────────────────────────────────────────────────────────
# Main
# ──────────────────────────────────────────────────────────────────────────────

@app.get("/api/tickets-latest")
def api_tickets_latest():
    """Full tickets payload (groups, filters, date) for debugging and clients."""
    json_path = TEMPLATES_DIR / "tickets_latest.json"
    if not json_path.exists():
        return jsonify({"error": "tickets_latest.json not found", "groups": []}), 404
    try:
        data = read_json_cached(json_path)
        return jsonify(data)
    except Exception as e:
        return jsonify({"error": str(e), "groups": []}), 500


# API: Slate picks - deduped unique picks from tickets_latest.json
@app.get("/api/slate")
def api_slate():
    json_path = TEMPLATES_DIR / "tickets_latest.json"
    if not json_path.exists():
        return jsonify({"picks": [], "generated_at": None, "date": None})

    def _build_picks():
        data = read_json_cached(json_path)
        seen = set()
        picks = []
        for group in (data.get("groups") or []):
            for ticket in (group.get("tickets") or []):
                for leg in (ticket.get("legs") or []):
                    key = (leg.get("player"), leg.get("prop_type"), leg.get("direction"), leg.get("line"))
                    if key in seen:
                        continue
                    seen.add(key)
                    picks.append({
                        "sport":      leg.get("sport", ""),
                        "initials":   leg.get("initials", ""),
                        "player":     leg.get("player", ""),
                        "prop":       leg.get("prop_type", ""),
                        "line":       leg.get("line", 0),
                        "pick":       leg.get("pick_type", "Standard"),
                        "dir":        leg.get("direction", "OVER"),
                        "hit":        round((leg.get("hit_rate") or 0) * 100),
                        "edge":       leg.get("edge") or 0,
                        "l5_over":    leg.get("l5_over"),
                        "l5_under":   leg.get("l5_under"),
                        "l10_over":   leg.get("l10_over"),
                        "l10_under":  leg.get("l10_under"),
                        "l5_avg":     leg.get("l5_avg"),
                        "season_avg": leg.get("season_avg"),
                    })
        picks.sort(key=lambda p: abs(p["edge"]), reverse=True)
        return {"picks": picks, "generated_at": data.get("generated_at"), "date": data.get("date")}

    try:
        return _gz_json_response("slate-picks", _build_picks, ttl=300.0)
    except Exception as e:
        return jsonify({"error": str(e), "picks": []}), 500


# ──────────────────────────────────────────────────────────────────────────────
# API: Full per-sport slate from combined Excel (openpyxl, no pandas needed)
# ──────────────────────────────────────────────────────────────────────────────
# ──────────────────────────────────────────────────────────────────────────────
# API: Full per-sport slate from slate_latest.json (written by pipeline)
# ──────────────────────────────────────────────────────────────────────────────
@app.get("/api/slate-sport")
def api_slate_sport():
    slate_path = TEMPLATES_DIR / "slate_latest.json"
    if not slate_path.exists():
        return jsonify({"error": "slate_latest.json not found — run pipeline first", "sports": {}}), 404
    try:
        return _gz_json_response("slate-sport", lambda: read_json_cached(slate_path), ttl=300.0)
    except Exception as e:
        return jsonify({"error": str(e), "sports": {}}), 500


# ──────────────────────────────────────────────────────────────────────────────
# API: Screenshot → Google Gemini vision (server proxy; key in GOOGLE_API_KEY).
#
# Get a free Gemini API key at: https://aistudio.google.com/apikey
# Set in Railway dashboard: GOOGLE_API_KEY = your key
# Set locally: $env:GOOGLE_API_KEY = "your key"
#
# Optional: $env:GEMINI_VISION_MODEL = "gemini-2.5-flash"   # override default model id
#
# Quota errors ("free_tier ... limit: 0" or RESOURCE_EXHAUSTED):
#   - Prefer a current model (default: gemini-2.5-flash-lite). Deprecated models
#     like gemini-2.0-flash may show zero free-tier quota.
#   - In Google Cloud Console, enable "Generative Language API" for the key's project.
#   - AI Studio → check usage: https://ai.google.dev/gemini-api/docs/rate-limits
# ──────────────────────────────────────────────────────────────────────────────
_DEFAULT_GEMINI_VISION_MODEL = "gemini-2.5-flash-lite"


@app.post("/api/vision/screenshot")
def api_vision_screenshot():
    key = (os.environ.get("GOOGLE_API_KEY") or "").strip()
    if not key:
        return jsonify({"error": "GOOGLE_API_KEY not set"}), 503

    model_id = (os.environ.get("GEMINI_VISION_MODEL") or "").strip() or _DEFAULT_GEMINI_VISION_MODEL
    _mid_ok = set("abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789._-")
    if not model_id or not all(c in _mid_ok for c in model_id):
        model_id = _DEFAULT_GEMINI_VISION_MODEL

    payload = request.get_json(force=True, silent=True) or {}
    image_base64 = payload.get("image_base64")
    media_type = payload.get("media_type") or "image/jpeg"
    prompt = payload.get("prompt")

    if not image_base64 or not isinstance(image_base64, str):
        return jsonify({"error": "missing image_base64"}), 400
    if not prompt or not isinstance(prompt, str):
        return jsonify({"error": "missing prompt"}), 400
    if not isinstance(media_type, str):
        media_type = "image/jpeg"

    gemini_body = {
        "contents": [
            {
                "parts": [
                    {
                        "inline_data": {
                            "mime_type": media_type,
                            "data": image_base64,
                        }
                    },
                    {"text": prompt},
                ]
            }
        ]
    }

    url = (
        f"https://generativelanguage.googleapis.com/v1beta/models/{model_id}:generateContent"
        f"?key={quote(key, safe='')}"
    )
    req = urllib.request.Request(
        url,
        data=json.dumps(gemini_body).encode("utf-8"),
        method="POST",
        headers={"Content-Type": "application/json"},
    )
    try:
        with urllib.request.urlopen(req, timeout=120) as resp:
            raw = json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as e:
        err_body = e.read().decode("utf-8")
        try:
            err_json = json.loads(err_body)
            return app.response_class(
                response=json.dumps(err_json),
                status=e.code,
                mimetype="application/json",
            )
        except json.JSONDecodeError:
            return jsonify({"error": err_body or e.reason}), e.code
    except urllib.error.URLError as e:
        return jsonify({"error": f"Upstream request failed: {e.reason}"}), 502
    except Exception as e:
        return jsonify({"error": str(e)}), 502

    text_chunks: List[str] = []
    for cand in raw.get("candidates") or []:
        content = cand.get("content") or {}
        for part in content.get("parts") or []:
            t = part.get("text")
            if isinstance(t, str) and t:
                text_chunks.append(t)
    merged = "".join(text_chunks).strip()
    if not merged:
        return jsonify({"error": "No text returned from Gemini (empty candidates)"}), 502

    normalized = {"content": [{"type": "text", "text": merged}]}
    return app.response_class(
        response=json.dumps(normalized),
        status=200,
        mimetype="application/json",
    )


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=8787, debug=False)
