from __future__ import annotations

# ──────────────────────────────────────────────────────────────────────────────
# Windows UTF-8 fix — MUST be at the very top
# ──────────────────────────────────────────────────────────────────────────────
import csv
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
import re
import sqlite3
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
from datetime import date, datetime, timedelta, timezone
from zoneinfo import ZoneInfo

from flask import (
    Flask,
    abort,
    current_app,
    jsonify,
    make_response,
    render_template,
    request,
    send_from_directory,
)
from markupsafe import Markup

# ──────────────────────────────────────────────────────────────────────────────
# Paths
# ──────────────────────────────────────────────────────────────────────────────
BASE_DIR      = Path(__file__).resolve().parent.parent  # repo root (one level above ui_runner/)
if str(BASE_DIR) not in sys.path:
    sys.path.insert(0, str(BASE_DIR))  # monorepo bootstrap until `pip install -e .`

from utils.prop_reconcile import reconcile_props_history_dict

UI_DIR        = Path(__file__).resolve().parent         # all UI assets live here (ui_runner/)
CONFIG_PATH   = UI_DIR / "commands.json"
TEMPLATES_DIR = UI_DIR / "templates"
ARCHIVE_DIR   = TEMPLATES_DIR / "archive"
STATIC_DIR    = UI_DIR / "static"
# Bundled graded-prop exports for deploy hosts without data/cache/*_props_history.db (see scripts/export_grades_props_bundle.py).
GRADES_PROPS_EXPORT_DIR = UI_DIR / "data" / "grades_props"

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
WCBB_SLATE    = CBB_DIR / "step6_ranked_wcbb.xlsx"
# NHL pipeline writes under NHL/outputs/ (same as run_pipeline.ps1).
NHL_SLATE     = NHL_DIR / "outputs" / "step8_nhl_direction_clean.xlsx"
NHL_TICKETS   = NHL_DIR / "outputs" / "nhl_best_tickets.xlsx"
SOCCER_SLATE  = SOCCER_DIR / "step8_soccer_direction_clean.xlsx"
SOCCER_TICKETS= SOCCER_DIR / "soccer_best_tickets.xlsx"
MLB_SLATE     = MLB_DIR / "step8_mlb_direction_clean.xlsx"
MLB_TICKETS   = MLB_DIR / "mlb_best_tickets.xlsx"
COMBINED_OUT  = BASE_DIR  # combined_slate_tickets_YYYY-MM-DD.xlsx may live here or under outputs/
OUTPUTS_ROOT  = BASE_DIR / "outputs"

app = Flask(
    __name__,
    template_folder=str(TEMPLATES_DIR),
    static_folder=str(STATIC_DIR) if STATIC_DIR.exists() else None,
)

# Visible on every response (curl -I); bump when you need to confirm Railway shipped new code.
_UI_BUILD_ID = "2026-04-12-grades-toolbar-type-down-2"

# ── Response compression + static caching ─────────────────────────────────────
_COMPRESSIBLE = ("text/", "application/json", "application/javascript")
_STATIC_EXTS  = (".css", ".js", ".png", ".jpg", ".jpeg", ".webp", ".gif", ".svg", ".ico", ".woff", ".woff2")

@app.after_request
def post_process_response(response):
    response.headers.setdefault("X-PropOracle-Build", _UI_BUILD_ID)
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
_JSON_FILE_CACHE_LOCK = threading.Lock()

# If set, fetch data from these URLs instead of baked-in files (avoids Docker layer cache).
# Optional on Railway — auto-defaults apply when RAILWAY_* is set (see below).
#   TICKETS_JSON_URL = https://raw.githubusercontent.com/halekevi/PropORACLE/main/ui_runner/templates/tickets_latest.json
#   SLATE_JSON_URL   = https://raw.githubusercontent.com/halekevi/PropORACLE/main/ui_runner/templates/slate_latest.json
#   PIPELINE_JSON_TTL_SEC = 45   # optional; on Railway default is 60 if unset (fresher slates)
#   GITHUB_JSON_FETCH_BUST = 0   # set to 0 to disable ?nocache= on raw GitHub fetches (not recommended)
#   DISABLE_AUTO_GITHUB_JSON = 1  # opt out of Railway → GitHub raw auto URLs
#   PROPORACLE_RAW_JSON_BASE = https://raw.githubusercontent.com/USER/REPO/BRANCH/ui_runner/templates
#   PROPORACLE_SLATE_DATE = 2026-03-28   # optional; prefer outputs/THIS_DATE/ step8_*_THIS_DATE.xlsx
#   SLATE_SPORT_SOURCE = auto|slate_latest|ticket_eval   # auto prefers slate_latest.json (full slate); ticket_eval = eval subset only
#   TICKET_EVAL_SLATE_JSON_URL = https://.../ticket_eval_slate_latest.json
#
# On Railway, baked-in .xlsx mtimes are often old while main-branch JSON is current. We default missing URLs to
# raw GitHub so /api/pipeline/status and slate endpoints stay fresh without manual env setup.
def _running_on_railway() -> bool:
    return bool(
        os.environ.get("RAILWAY_ENVIRONMENT")
        or os.environ.get("RAILWAY_SERVICE_ID")
        or os.environ.get("RAILWAY_PROJECT_ID")
        or os.environ.get("RAILWAY_PUBLIC_DOMAIN")
        or os.environ.get("RAILWAY_STATIC_URL")
    )


_JSON_BASE_DEFAULT = (
    "https://raw.githubusercontent.com/halekevi/PropORACLE/main/ui_runner/templates"
)

_TICKETS_JSON_URL = os.environ.get("TICKETS_JSON_URL", "").strip()
_SLATE_JSON_URL = os.environ.get("SLATE_JSON_URL", "").strip()
_TICKET_EVAL_SLATE_JSON_URL = os.environ.get("TICKET_EVAL_SLATE_JSON_URL", "").strip()

if not os.environ.get("DISABLE_AUTO_GITHUB_JSON", "").strip() and _running_on_railway():
    _base = os.environ.get("PROPORACLE_RAW_JSON_BASE", _JSON_BASE_DEFAULT).rstrip("/")
    if not _SLATE_JSON_URL:
        _SLATE_JSON_URL = f"{_base}/slate_latest.json"
    if not _TICKETS_JSON_URL:
        _TICKETS_JSON_URL = f"{_base}/tickets_latest.json"
    if not _TICKET_EVAL_SLATE_JSON_URL:
        _TICKET_EVAL_SLATE_JSON_URL = f"{_base}/ticket_eval_slate_latest.json"

_DATA_FILE_URL_MAP: dict[str, str] = {}
if _TICKETS_JSON_URL:
    _DATA_FILE_URL_MAP["tickets_latest.json"] = _TICKETS_JSON_URL
if _SLATE_JSON_URL:
    _DATA_FILE_URL_MAP["slate_latest.json"] = _SLATE_JSON_URL
if _TICKET_EVAL_SLATE_JSON_URL:
    _DATA_FILE_URL_MAP["ticket_eval_slate_latest.json"] = _TICKET_EVAL_SLATE_JSON_URL


def _template_json_available(filename: str) -> bool:
    """True if JSON can be loaded from disk or from a configured remote URL (Railway)."""
    return (TEMPLATES_DIR / filename).exists() or bool(_DATA_FILE_URL_MAP.get(filename))


if _running_on_railway() and "PIPELINE_JSON_TTL_SEC" not in os.environ:
    _PIPELINE_JSON_TTL = 60.0
else:
    _PIPELINE_JSON_TTL = float(os.environ.get("PIPELINE_JSON_TTL_SEC", "300"))


def _github_raw_fetch_url(url: str) -> str:
    """
    raw.githubusercontent.com is often cached at the edge; client Cache-Control is ignored.
    Append a unique query param on each HTTP fetch so new pipeline commits are visible quickly.
    """
    if os.environ.get("GITHUB_JSON_FETCH_BUST", "1").strip() in ("0", "false", "no"):
        return url
    sep = "&" if "?" in url else "?"
    return f"{url}{sep}nocache={int(time.time() * 1000)}"


def read_json_cached(path: Path, ttl: float | None = None) -> Any:
    """Load JSON from disk (or remote URL) with an in-process TTL."""
    if ttl is None:
        ttl = _PIPELINE_JSON_TTL
    key = str(path.resolve())
    now = time.time()
    with _JSON_FILE_CACHE_LOCK:
        entry = _json_file_cache.get(key)
        if entry is not None and now - entry["ts"] <= ttl:
            return entry["data"]

        url = _DATA_FILE_URL_MAP.get(path.name)
        if url:
            try:
                fetch_url = _github_raw_fetch_url(url)
                req = urllib.request.Request(
                    fetch_url,
                    headers={
                        "Cache-Control": "no-cache",
                        "Pragma": "no-cache",
                    },
                )
                with urllib.request.urlopen(req, timeout=25) as resp:
                    data = json.loads(resp.read().decode("utf-8"))
                _json_file_cache[key] = {"data": data, "ts": time.time()}
                return data
            except Exception:
                if not path.exists():
                    raise
        data = json.loads(path.read_text(encoding="utf-8-sig"))
        _json_file_cache[key] = {"data": data, "ts": time.time()}
        return data


# ── Pre-serialized + pre-gzipped response cache ───────────────────────────────
# Avoids re-serializing and re-compressing large payloads on every request.
_gz_cache: dict[str, tuple[bytes, float]] = {}
_GZ_CACHE_LOCK = threading.Lock()


def _gz_json_response(key: str, build_fn, ttl: float = 300.0):
    """
    Call build_fn() once per TTL, serialize+gzip the result, serve from cache after.
    Handles both gzip-capable and plain clients.

    Build must run under the same lock as the cache check: with gthread, several
    threads could otherwise miss cache together and each load multi‑MB JSON → OOM
    (Railway 502 / worker SIGKILL).
    """
    now = time.time()
    with _GZ_CACHE_LOCK:
        entry = _gz_cache.get(key)
        if entry and now - entry[1] < ttl:
            gz_bytes = entry[0]
        else:
            data = build_fn()
            raw = json.dumps(data, separators=(",", ":")).encode("utf-8")
            buf = io.BytesIO()
            with gzip.GzipFile(mode="wb", fileobj=buf, compresslevel=6) as f:
                f.write(raw)
            gz_bytes = buf.getvalue()
            _gz_cache[key] = (gz_bytes, time.time())

    _NO_CACHE = "no-store, no-cache, must-revalidate, max-age=0"
    if "gzip" in request.headers.get("Accept-Encoding", ""):
        resp = app.response_class(gz_bytes, status=200, mimetype="application/json")
        resp.headers["Content-Encoding"] = "gzip"
        resp.headers["Content-Length"]   = len(gz_bytes)
        resp.headers["Vary"]             = "Accept-Encoding"
        resp.headers["Cache-Control"]    = _NO_CACHE
        resp.headers["Pragma"]           = "no-cache"
        return resp
    # Non-gzip client: decompress inline (rare — all modern browsers support gzip)
    with gzip.GzipFile(fileobj=io.BytesIO(gz_bytes)) as f:
        resp = app.response_class(f.read(), status=200, mimetype="application/json")
        resp.headers["Cache-Control"] = _NO_CACHE
        resp.headers["Pragma"]        = "no-cache"
        return resp


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


def _mtime_ts(mod_str: str | None) -> float:
    if not mod_str or len(mod_str) < 19:
        return 0.0
    try:
        return datetime.strptime(mod_str[:19], "%Y-%m-%d %H:%M:%S").timestamp()
    except ValueError:
        return 0.0


def _payload_timestamp_meta(payload: dict | None) -> tuple[float | None, str | None]:
    """Parse generated_at / date from slate or tickets JSON (authoritative build time on Railway)."""
    if not isinstance(payload, dict):
        return None, None
    ga = (payload.get("generated_at") or "").strip()
    if ga:
        core = ga.replace(" UTC", "").strip()
        prefix = core[:19].replace("T", " ")
        try:
            dt = datetime.strptime(prefix, "%Y-%m-%d %H:%M:%S")
            return dt.timestamp(), dt.strftime("%Y-%m-%d %H:%M:%S")
        except ValueError:
            pass
    ds = (payload.get("date") or "").strip()[:10]
    if len(ds) == 10:
        try:
            dt = datetime.strptime(ds, "%Y-%m-%d").replace(tzinfo=timezone.utc)
            return dt.timestamp(), dt.strftime("%Y-%m-%d %H:%M:%S")
        except ValueError:
            pass
    return None, None


def _fresher_meta(
    a: tuple[float | None, str | None],
    b: tuple[float | None, str | None],
) -> tuple[float | None, str | None]:
    """Use whichever payload (slate vs tickets) has the newer timestamp for status UI."""
    at, ad = a
    bt, bd = b
    av = float(at or 0.0)
    bv = float(bt or 0.0)
    if bv > av:
        return bt, bd
    return at, ad


def _slate_day_candidates(preferred_date: str | None) -> list[str]:
    """
    Newest-first YYYY-MM-DD list: optional tickets/slate date, env override, then rolling days.
    Used to find outputs/{date}/step8_*_{date}.xlsx style artifacts.
    """
    seen: set[str] = set()
    out: list[str] = []

    def _add(d: str) -> None:
        d = (d or "").strip()[:10]
        if len(d) == 10 and d not in seen:
            seen.add(d)
            out.append(d)

    _add(os.environ.get("PROPORACLE_SLATE_DATE", "").strip() or "")
    _add(preferred_date or "")
    for i in range(14):
        _add((date.today() - timedelta(days=i)).strftime("%Y-%m-%d"))
    return out


def _resolve_outputs_artifact(
    days: list[str],
    filename_fmt: str,
    *legacy: Path,
) -> Path:
    """
    Prefer outputs/{{d}}/filename_fmt.format(d=d), then first existing legacy path.
    filename_fmt example: step8_nba_direction_clean_{d}.xlsx
    """
    for d in days:
        p = OUTPUTS_ROOT / d / filename_fmt.format(d=d)
        if p.exists():
            return p
    for leg in legacy:
        if leg.exists():
            return leg
    if legacy:
        return legacy[0]
    d0 = days[0] if days else date.today().strftime("%Y-%m-%d")
    return OUTPUTS_ROOT / d0 / filename_fmt.format(d=d0)


def _count_slate_sport_rows(payload: dict) -> int:
    return sum(len(v or []) for v in (payload.get("sports") or {}).values())


def _selected_slate_sport_payload() -> dict:
    """
    Home slate explorer (/api/slate-sport).

    - slate_latest: full per-sport grids from the pipeline (use for explorer).
    - ticket_eval: small subset aligned with ticket HTML (use when forcing eval-only).
    - auto: prefer slate_latest whenever it has rows so every sport panel (NHL, 1H, …) fills;
      ticket_eval alone only lists a few sports/legs and left most panels empty.
    """
    src = os.environ.get("SLATE_SPORT_SOURCE", "auto").strip().lower()
    te_name = "ticket_eval_slate_latest.json"
    sl_name = "slate_latest.json"

    def _load(name: str) -> dict | None:
        if not _template_json_available(name):
            return None
        try:
            return read_json_cached(TEMPLATES_DIR / name)
        except Exception:
            return None

    if src == "slate_latest":
        d = _load(sl_name)
        if not d:
            raise ValueError("slate_latest.json unavailable")
        return d
    if src == "ticket_eval":
        d = _load(te_name)
        if not d:
            raise ValueError("ticket_eval_slate_latest.json unavailable")
        return d

    sl = _load(sl_name)
    if sl and _count_slate_sport_rows(sl) > 0:
        return sl
    te = _load(te_name)
    if te and _count_slate_sport_rows(te) > 0:
        return te
    if sl:
        return sl
    raise ValueError("no slate json available")


def _slate_counts() -> tuple[dict[str, int], dict]:
    """
    Return ({sport_key: row_count}, file_info for slate_latest.json on disk, if present).
    Counts follow the same source as /api/slate-sport when possible.
    """
    path = TEMPLATES_DIR / "slate_latest.json"
    disk_info = _file_info(path)
    try:
        payload = _selected_slate_sport_payload()
    except Exception:
        return {}, disk_info
    sports = payload.get("sports") or {}
    counts = {str(k).lower(): len(v or []) for k, v in sports.items()}
    return counts, disk_info


def _sport_slate_status(
    path: Path,
    sport_key: str,
    counts: dict[str, int],
    slate_disk_info: dict,
    json_ts: float | None,
    json_disp: str | None,
) -> dict:
    """
    Status row for Slate Explorer cards.

    json_disp is the unified web timestamp (card_disp: slate / tickets / ticket_eval).

    When this sport has rows in the live slate JSON (same source as the explorer UI), the
    timestamp should follow the merged slate/tickets JSON build time — not the step8 Excel
    mtime. On Railway, Excel shipped under outputs/ or from a later UTC day can be *newer*
    than the JSON pulled from GitHub, so cards showed e.g. 2026-04-04 00:43 while COMBINED
    still showed tickets_latest from 2026-04-03 (looked like a tomorrow slate).

    If the sport is absent from JSON (cnt == 0), fall back to Excel / disk like before.
    """
    key = sport_key.lower()
    cnt = int(counts.get(key, 0))
    if key == "cbb":
        cnt += int(counts.get("wcbb", 0))

    direct = _file_info(path)
    if cnt > 0 and json_disp:
        if direct.get("exists"):
            return {**direct, "modified": json_disp}
        return {
            "exists": True,
            "modified": json_disp,
            "size_kb": slate_disk_info.get("size_kb"),
        }

    if direct.get("exists"):
        if json_ts is not None and json_disp and json_ts > _mtime_ts(direct.get("modified")):
            return {**direct, "modified": json_disp}
        return direct
    if cnt > 0:
        return {
            "exists": True,
            "modified": json_disp or slate_disk_info.get("modified"),
            "size_kb": slate_disk_info.get("size_kb"),
        }
    return direct


# ──────────────────────────────────────────────────────────────────────────────
# Pages
# ──────────────────────────────────────────────────────────────────────────────
@app.get("/")
def home():
    resp = make_response(
        render_template(
            "index.html",
            config=load_config(),
            ui_build_id=_UI_BUILD_ID,
            deploy_git_sha=(os.environ.get("RAILWAY_GIT_COMMIT_SHA") or os.environ.get("GIT_COMMIT") or "")[:40],
        )
    )
    resp.headers["Cache-Control"] = "no-store, no-cache, must-revalidate, max-age=0"
    resp.headers["Pragma"] = "no-cache"
    return resp


@app.get("/ping")
def ping():
    """Lightweight health check. Use /ping?json=1 for deploy metadata (same as /api/build)."""
    if request.args.get("json") == "1":
        r = jsonify({
            "ok": True,
            "ui_build_id": _UI_BUILD_ID,
            "railway": _running_on_railway(),
            "slate_json_remote": bool(_DATA_FILE_URL_MAP.get("slate_latest.json")),
            "ticket_eval_slate_json_remote": bool(
                _DATA_FILE_URL_MAP.get("ticket_eval_slate_latest.json")
            ),
            "slate_sport_source": os.environ.get("SLATE_SPORT_SOURCE", "auto").strip() or "auto",
            "tickets_json_remote": bool(_DATA_FILE_URL_MAP.get("tickets_latest.json")),
            "deploy_git_sha": (os.environ.get("RAILWAY_GIT_COMMIT_SHA") or os.environ.get("GIT_COMMIT") or "")[:40],
        })
        r.headers["Cache-Control"] = "no-store, no-cache, must-revalidate, max-age=0"
        return r
    return "ok", 200


@app.get("/api/build")
def api_build():
    """Confirm which propOracle UI revision is deployed (Railway / CI debugging)."""
    r = jsonify({
        "ui_build_id": _UI_BUILD_ID,
        "railway": _running_on_railway(),
        "slate_json_remote": bool(_DATA_FILE_URL_MAP.get("slate_latest.json")),
        "ticket_eval_slate_json_remote": bool(
            _DATA_FILE_URL_MAP.get("ticket_eval_slate_latest.json")
        ),
        "slate_sport_source": os.environ.get("SLATE_SPORT_SOURCE", "auto").strip() or "auto",
        "tickets_json_remote": bool(_DATA_FILE_URL_MAP.get("tickets_latest.json")),
        "deploy_git_sha": (os.environ.get("RAILWAY_GIT_COMMIT_SHA") or os.environ.get("GIT_COMMIT") or "")[:40],
    })
    r.headers["Cache-Control"] = "no-store, no-cache, must-revalidate, max-age=0"
    return r


def _no_store_headers(resp):
    resp.headers["Cache-Control"] = "no-store, no-cache, must-revalidate, max-age=0"
    resp.headers["Pragma"] = "no-cache"
    resp.headers["Expires"] = "0"
    return resp


def _tickets_built_slips_missing_html() -> Any:
    """HTML 404 when tickets_latest.json is unavailable (never serve graded tickets_latest.html here)."""
    body = """<!DOCTYPE html>
<html lang="en"><head><meta charset="utf-8"/><meta name="viewport" content="width=device-width,initial-scale=1"/>
<title>PropOracle — Built tickets</title>
<style>
body{font-family:system-ui,-apple-system,sans-serif;background:#050505;color:#e8e8f0;max-width:720px;margin:48px auto;padding:0 24px;line-height:1.55;}
h1{font-size:1.35rem;font-weight:700;margin-bottom:12px;}
p{margin:0 0 14px;}
a{color:#00e5ff;} code{background:#1a1a2e;padding:2px 7px;border-radius:4px;font-size:0.9em;}
</style></head><body>
<h1>Built slips not available</h1>
<p>This page shows <strong>today&rsquo;s generated slips</strong> from <code>tickets_latest.json</code>. The file was not found on disk and no remote JSON URL is configured (on Railway, <code>TICKETS_JSON_URL</code> defaults to raw GitHub when <code>RAILWAY_*</code> env is set).</p>
<p><strong>Graded</strong> results (actuals, hits/misses, ticket KPI bar) are under <a href="/grades">Grades</a> &rarr; Ticket evaluation — not here.</p>
<p>Run the combined slate script with <code>--write-web</code>, commit <code>ui_runner/templates/tickets_latest.json</code>, and redeploy.</p>
<p><a href="/">Home</a></p>
</body></html>"""
    r = make_response(body, 404)
    r.headers["Content-Type"] = "text/html; charset=utf-8"
    return r


@app.get("/tickets_latest.json")
def serve_tickets_latest_json():
    """Expose JSON at site root so the link on /tickets works (relative URLs break under /tickets)."""
    path = TEMPLATES_DIR / "tickets_latest.json"
    if not _template_json_available("tickets_latest.json"):
        abort(404)
    try:
        data = read_json_cached(path)
    except Exception:
        abort(404)
    r = jsonify(data)
    r.headers["Content-Type"] = "application/json; charset=utf-8"
    return _no_store_headers(r)


@app.get("/tickets")
def page_tickets():
    """
    Today's built ticket slips from tickets_latest.json (combined_slate_tickets --write-web).

    Graded legs, actuals, and hit/miss summaries live under Grades → Ticket evaluation
    (static ticket_eval_YYYY-MM-DD.html from build_ticket_eval.py), not on this route.
    """
    import importlib.util

    json_path = TEMPLATES_DIR / "tickets_latest.json"
    has_json = _template_json_available("tickets_latest.json")

    def _render_slips_from_json() -> str | None:
        if not has_json:
            return None
        payload = read_json_cached(json_path)
        cst_path = BASE_DIR / "scripts" / "combined_slate_tickets.py"
        if not cst_path.exists():
            raise FileNotFoundError("scripts/combined_slate_tickets.py not in repo")
        scripts_dir = str(BASE_DIR / "scripts")
        path_inserted = False
        if scripts_dir not in sys.path:
            sys.path.insert(0, scripts_dir)
            path_inserted = True
        try:
            spec = importlib.util.spec_from_file_location("combined_slate_tickets", cst_path)
            if spec is None or spec.loader is None:
                raise RuntimeError("could not load combined_slate_tickets spec")
            mod = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(mod)
            body, page_title = mod.render_tickets_body_html(payload)
            return render_template(
                "tickets_built.html",
                tickets_body=Markup(body),
                page_title=page_title,
                ui_build_id=_UI_BUILD_ID,
                deploy_git_sha=(
                    os.environ.get("RAILWAY_GIT_COMMIT_SHA") or os.environ.get("GIT_COMMIT") or ""
                )[:40],
            )
        finally:
            if path_inserted and sys.path and sys.path[0] == scripts_dir:
                sys.path.pop(0)

    try:
        html = _render_slips_from_json()
        if html:
            return _no_store_headers(make_response(html))
    except Exception as e:
        current_app.logger.warning("/tickets: render from tickets_latest.json failed: %s", e)
        if has_json:
            return _no_store_headers(
                make_response(
                    (
                        "Could not render built slips from tickets_latest.json (see server log). "
                        f"Error: {e!s}"
                    ),
                    500,
                )
            )

    if not has_json:
        return _no_store_headers(_tickets_built_slips_missing_html())
    return _no_store_headers(
        make_response(
            "Could not render /tickets from tickets_latest.json. Check combined_slate_tickets.render_tickets_body_html "
            "and JSON shape.",
            500,
        )
    )


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


@app.get("/api/graded-props")
def api_graded_props():
    """JSON list of graded props for a slate date (from graded_props_YYYY-MM-DD.json)."""
    date_q = (request.args.get("date") or "").strip()
    if not date_q or not re.match(r"^\d{4}-\d{2}-\d{2}$", date_q):
        return jsonify(
            {"error": "missing_or_invalid_date", "detail": "Use ?date=YYYY-MM-DD"}
        ), 400
    fname = f"graded_props_{date_q}.json"
    path = TEMPLATES_DIR / fname
    if not path.exists() and ARCHIVE_DIR.exists():
        alt = ARCHIVE_DIR / fname
        if alt.exists():
            path = alt
    if not path.exists():
        return jsonify(
            {"date": date_q, "count": 0, "props": [], "missing": True}
        )
    try:
        data = json.loads(path.read_text(encoding="utf-8-sig"))
        if isinstance(data, dict):
            return jsonify(data)
        return jsonify({"error": "invalid_shape", "detail": "expected object"}), 500
    except Exception as exc:
        return jsonify({"error": "read_failed", "detail": str(exc)}), 500


def _iter_props_history_db_paths():
    cache = BASE_DIR / "data" / "cache"
    if not cache.is_dir():
        return []
    return sorted(cache.glob("*_props_history.db"))


def _ensure_props_history_void_reason_column(conn: sqlite3.Connection) -> None:
    """Older props_history DBs lack void_reason; add it so SELECT lists stay valid."""
    try:
        cur = conn.execute("PRAGMA table_info(props_history)")
        cols = {r[1] for r in cur.fetchall()}
        if cols and "void_reason" not in cols:
            conn.execute("ALTER TABLE props_history ADD COLUMN void_reason TEXT")
            conn.commit()
    except Exception:
        pass


def _grades_insights_payload() -> dict:
    """Calibration (ml_prob buckets), edge-bucket hit rates, CLV summary from local SQLite archives."""
    rows_ml: list[tuple[float, float | None, str]] = []
    for dbp in _iter_props_history_db_paths():
        try:
            conn = sqlite3.connect(str(dbp))
            cur = conn.execute(
                "SELECT ml_prob, edge, result FROM props_history "
                "WHERE result IN ('HIT','MISS') AND ml_prob IS NOT NULL"
            )
            rows_ml.extend((float(r[0]), r[1], str(r[2])) for r in cur.fetchall())
            conn.close()
        except Exception:
            try:
                conn.close()
            except Exception:
                pass

    cal_bins: list[tuple[int, int, str]] = [
        (50, 55, "50-55%"),
        (55, 60, "55-60%"),
        (60, 65, "60-65%"),
        (65, 70, "65-70%"),
        (70, 75, "70-75%"),
        (75, 80, "75-80%"),
        (80, 85, "80-85%"),
        (85, 90, "85-90%"),
        (90, 101, "90-100%"),
    ]
    cal_agg = {label: {"hits": 0, "n": 0} for _lo, _hi, label in cal_bins}
    for ml, _e, res in rows_ml:
        pct = float(ml) * 100.0 if float(ml) <= 1.0 else float(ml)
        pct = max(0.0, min(100.0, pct))
        hit = 1 if res.upper() == "HIT" else 0
        for lo, hi, label in cal_bins:
            if lo <= pct < hi:
                cal_agg[label]["n"] += 1
                cal_agg[label]["hits"] += hit
                break

    calibration = []
    for _lo, _hi, label in cal_bins:
        v = cal_agg[label]
        if v["n"] == 0:
            calibration.append({"bucket": label, "n": 0, "hit_rate": None})
        else:
            calibration.append({"bucket": label, "n": v["n"], "hit_rate": round(v["hits"] / v["n"], 4)})

    edge_bins = {"0-3%": {"hits": 0, "n": 0}, "3-6%": {"hits": 0, "n": 0}, "6%+": {"hits": 0, "n": 0}}
    for ml, edge, res in rows_ml:
        if edge is None:
            continue
        try:
            ef = float(edge)
        except (TypeError, ValueError):
            continue
        if abs(ef) > 1.5:
            ef = ef / 100.0
        aef = abs(ef)
        if aef < 0.03:
            bk = "0-3%"
        elif aef < 0.06:
            bk = "3-6%"
        else:
            bk = "6%+"
        edge_bins[bk]["n"] += 1
        edge_bins[bk]["hits"] += 1 if res.upper() == "HIT" else 0

    edge_roi = []
    for k, v in edge_bins.items():
        if v["n"] == 0:
            edge_roi.append({"bucket": k, "n": 0, "hit_rate": None})
        else:
            edge_roi.append({"bucket": k, "n": v["n"], "hit_rate": round(v["hits"] / v["n"], 4)})

    clv_by_sport = []
    clv_by_prop_type: list[dict] = []
    clv_by_tier: list[dict] = []
    for dbp in _iter_props_history_db_paths():
        sport_key = dbp.stem.replace("_props_history", "").upper()
        try:
            conn = sqlite3.connect(str(dbp))
            row = conn.execute(
                "SELECT AVG(clv_delta), COUNT(*) FROM clv_log WHERE clv_delta IS NOT NULL"
            ).fetchone()
            if row and row[1] and row[0] is not None:
                clv_by_sport.append(
                    {"sport": sport_key, "avg_clv": round(float(row[0]), 6), "n": int(row[1])}
                )
            for q, key in (
                (
                    "SELECT prop_type, AVG(clv_delta), COUNT(*) AS n FROM clv_log "
                    "WHERE clv_delta IS NOT NULL AND prop_type IS NOT NULL AND TRIM(prop_type) != '' "
                    "GROUP BY prop_type ORDER BY n DESC LIMIT 12",
                    "prop_type",
                ),
                (
                    "SELECT tier, AVG(clv_delta), COUNT(*) AS n FROM clv_log "
                    "WHERE clv_delta IS NOT NULL AND tier IS NOT NULL AND TRIM(tier) != '' "
                    "GROUP BY tier ORDER BY n DESC LIMIT 12",
                    "tier",
                ),
            ):
                try:
                    cur = conn.execute(q)
                    for r in cur.fetchall():
                        label = str(r[0])
                        entry = {
                            "sport": sport_key,
                            key: label,
                            "avg_clv": round(float(r[1]), 6) if r[1] is not None else None,
                            "n": int(r[2]),
                        }
                        if key == "prop_type":
                            clv_by_prop_type.append(entry)
                        else:
                            clv_by_tier.append(entry)
                except Exception:
                    pass
            conn.close()
        except Exception:
            try:
                conn.close()
            except Exception:
                pass

    return {
        "calibration": calibration,
        "edge_bucket_hit_rate": edge_roi,
        "clv_by_sport": clv_by_sport,
        "clv_by_prop_type": clv_by_prop_type,
        "clv_by_tier": clv_by_tier,
    }


def _grades_props_payload(date_str: str) -> dict[str, Any]:
    """
    Per-prop graded rows for the Grades hub Prop Evaluation tab.

    Primary: scripts/step_archive.py → data/cache/{sport}_props_history.db (local, gitignored).
    Fallback: ui_runner/data/grades_props/YYYY-MM-DD.json (committed; for Railway / hosts without SQLite archives).
    """
    props: list[dict[str, Any]] = []
    for dbp in _iter_props_history_db_paths():
        conn: sqlite3.Connection | None = None
        try:
            conn = sqlite3.connect(str(dbp))
            _ensure_props_history_void_reason_column(conn)
            conn.row_factory = sqlite3.Row
            cur = conn.execute(
                """
                SELECT sport, grade_date, player_name, prop_type, line, direction,
                       actual_value, result, margin, opp_team, team, pick_type, tier, edge, ml_prob,
                       void_reason
                FROM props_history
                WHERE grade_date = ?
                ORDER BY sport, player_name, prop_type, direction
                """,
                (date_str,),
            )
            for row in cur.fetchall():
                item = {k: row[k] for k in row.keys()}
                props.append(item)
        except Exception:
            pass
        finally:
            if conn is not None:
                try:
                    conn.close()
                except Exception:
                    pass

    from_bundle = False
    if not props and GRADES_PROPS_EXPORT_DIR.is_dir():
        bundle = GRADES_PROPS_EXPORT_DIR / f"{date_str}.json"
        if bundle.is_file():
            try:
                raw = json.loads(bundle.read_text(encoding="utf-8"))
                props = list(raw.get("props") or [])
                from_bundle = True
            except Exception:
                props = []

    props = [reconcile_props_history_dict(p) for p in props]

    props.sort(
        key=lambda r: (
            str(r.get("sport") or "").upper(),
            str(r.get("player_name") or "").lower(),
            str(r.get("prop_type") or "").lower(),
            str(r.get("direction") or "").upper(),
        )
    )
    n_total = len(props)
    n_hit = sum(1 for p in props if str(p.get("result") or "").upper() == "HIT")
    n_miss = sum(1 for p in props if str(p.get("result") or "").upper() == "MISS")
    cap = 8000
    truncated = n_total > cap
    if truncated:
        props = props[:cap]
    out: dict[str, Any] = {
        "date": date_str,
        "n": n_total,
        "n_returned": len(props),
        "n_hit": n_hit,
        "n_miss": n_miss,
        "n_other": max(0, n_total - n_hit - n_miss),
        "truncated": truncated,
        "props": props,
    }
    if from_bundle:
        out["from_bundle"] = True
    return out


def _grades_archive_dates_payload(max_dates: int = 40) -> dict[str, Any]:
    """
    Distinct grade_date values in local props_history DBs (newest first).
    Helps the Grades UI when the report date (often browser \"yesterday\") does not match
    archived slate dates (e.g. machine clock vs outputs/YYYY-MM-DD used when grading).
    """
    counts: dict[str, int] = {}
    for dbp in _iter_props_history_db_paths():
        conn: sqlite3.Connection | None = None
        try:
            conn = sqlite3.connect(str(dbp))
            cur = conn.execute(
                "SELECT grade_date, COUNT(*) FROM props_history GROUP BY grade_date"
            )
            for gd, n in cur.fetchall():
                if not gd:
                    continue
                key = str(gd).strip()[:10]
                if len(key) != 10 or key[4] != "-" or key[7] != "-":
                    continue
                counts[key] = counts.get(key, 0) + int(n)
        except Exception:
            pass
        finally:
            if conn is not None:
                try:
                    conn.close()
                except Exception:
                    pass
    # Bundled JSON exports (deploy)
    if GRADES_PROPS_EXPORT_DIR.is_dir():
        for p in sorted(GRADES_PROPS_EXPORT_DIR.glob("*.json")):
            mm = re.fullmatch(r"(\d{4}-\d{2}-\d{2})\.json", p.name)
            if not mm:
                continue
            key = mm.group(1)
            try:
                raw = json.loads(p.read_text(encoding="utf-8"))
                n = len(raw.get("props") or [])
            except Exception:
                n = 0
            if n <= 0:
                continue
            counts[key] = max(counts.get(key, 0), n)

    ordered = sorted(counts.keys(), reverse=True)[:max_dates]
    return {
        "dates": ordered,
        "row_counts": {d: counts[d] for d in ordered},
    }


@app.get("/api/grades/insights")
def api_grades_insights():
    """JSON for Grades hub: calibration, CLV by sport, edge-bucket hit rates (from props_history + clv_log)."""
    return jsonify(_grades_insights_payload())


@app.get("/api/grades/props")
def api_grades_props():
    """Graded prop rows for a report date (props_history archives)."""
    raw = (request.args.get("date") or "").strip()
    date_str = raw[:10]
    if len(date_str) != 10 or date_str[4] != "-" or date_str[7] != "-":
        return jsonify({"error": "invalid date; use YYYY-MM-DD", "props": []}), 400
    try:
        y, m, d = int(date_str[0:4]), int(date_str[5:7]), int(date_str[8:10])
        datetime(y, m, d)
    except (TypeError, ValueError):
        return jsonify({"error": "invalid date", "props": []}), 400
    r = jsonify(_grades_props_payload(date_str))
    r.headers["Cache-Control"] = "no-store, no-cache, must-revalidate, max-age=0"
    r.headers["Pragma"] = "no-cache"
    return r


@app.get("/api/grades/archive_dates")
def api_grades_archive_dates():
    """Newest grade_date keys present in data/cache/*_props_history.db (for Prop Evaluation date hints)."""
    r = jsonify(_grades_archive_dates_payload())
    r.headers["Cache-Control"] = "no-store, no-cache, must-revalidate, max-age=0"
    r.headers["Pragma"] = "no-cache"
    return r


_SLATE_EVAL_FN_RE = re.compile(r"^slate_eval_(\d{4}-\d{2}-\d{2})\.html$")
_TICKET_EVAL_FN_RE = re.compile(r"^ticket_eval_(\d{4}-\d{2}-\d{2})\.html$")


def _grade_report_dates_on_disk(which: str) -> list[str]:
    """
    List YYYY-MM-DD values for which static report HTML exists (templates/ or templates/archive/).
    which: 'slate' | 'ticket'
    """
    pat = _SLATE_EVAL_FN_RE if which == "slate" else _TICKET_EVAL_FN_RE
    found: set[str] = set()
    for base in (TEMPLATES_DIR, ARCHIVE_DIR):
        if not base.is_dir():
            continue
        try:
            for p in base.iterdir():
                if not p.is_file():
                    continue
                m = pat.match(p.name)
                if m:
                    found.add(m.group(1))
        except OSError:
            continue
    return sorted(found)


@app.get("/api/grades/report_dates")
def api_grades_report_dates():
    """
    Dates that have slate_eval_*.html / ticket_eval_*.html on the server filesystem.

    The Grades hub uses this so date pills work even when many parallel HEAD requests
    fail through a CDN/proxy. Deploy must include committed files under ui_runner/templates/.
    """
    r = jsonify(
        {
            "slate_eval_dates": _grade_report_dates_on_disk("slate"),
            "ticket_eval_dates": _grade_report_dates_on_disk("ticket"),
        }
    )
    r.headers["Cache-Control"] = "no-store, no-cache, must-revalidate, max-age=0"
    r.headers["Pragma"] = "no-cache"
    return r


# ──────────────────────────────────────────────────────────────────────────────
# NEW: Pipeline Status API
# Returns health of all pipeline outputs so UI can show green/red indicators
# ──────────────────────────────────────────────────────────────────────────────
@app.get("/api/pipeline/status")
def api_pipeline_status():
    slate_counts, slate_disk_info = _slate_counts()

    slate_payload: dict | None = None
    tickets_payload: dict | None = None
    try:
        slate_payload = read_json_cached(TEMPLATES_DIR / "slate_latest.json")
    except Exception:
        pass
    try:
        tickets_payload = read_json_cached(TEMPLATES_DIR / "tickets_latest.json")
    except Exception:
        pass

    te_payload: dict | None = None
    try:
        if _template_json_available("ticket_eval_slate_latest.json"):
            te_payload = read_json_cached(TEMPLATES_DIR / "ticket_eval_slate_latest.json")
    except Exception:
        pass

    slate_js_ts, slate_js_disp = _payload_timestamp_meta(slate_payload)
    tik_js_ts, tik_js_disp = _payload_timestamp_meta(tickets_payload)
    te_js_ts, te_js_disp = _payload_timestamp_meta(te_payload)
    status_js_ts, status_js_disp = _fresher_meta(
        _fresher_meta((slate_js_ts, slate_js_disp), (tik_js_ts, tik_js_disp)),
        (te_js_ts, te_js_disp),
    )
    # One string for Slate Explorer cards: avoids COMBINED using only tickets while sports
    # used a merged fresher time, or status_js_disp None while tik_js_disp is set.
    card_disp = status_js_disp or tik_js_disp or slate_js_disp or te_js_disp
    has_web_tickets = bool(tickets_payload and tickets_payload.get("groups"))

    _pref_d = (tickets_payload or {}).get("date") or (slate_payload or {}).get("date")
    days = _slate_day_candidates(str(_pref_d)[:10] if _pref_d else None)

    nba_slate_p = _resolve_outputs_artifact(
        days, "step8_nba_direction_clean_{d}.xlsx", NBA_SLATE, NBA_DIR / "step8_nba_direction_clean.xlsx"
    )
    nba1h_slate_p = _resolve_outputs_artifact(days, "step8_nba1h_direction_clean_{d}.xlsx", NBA1H_SLATE)
    nba1q_slate_p = _resolve_outputs_artifact(days, "step8_nba1q_direction_clean_{d}.xlsx", NBA1Q_SLATE)
    cbb_slate_p = _resolve_outputs_artifact(days, "step6_ranked_cbb_{d}.xlsx", CBB_SLATE)
    wcbb_slate_p = _resolve_outputs_artifact(days, "step6_ranked_wcbb_{d}.xlsx", WCBB_SLATE)
    nhl_slate_p = _resolve_outputs_artifact(days, "step8_nhl_direction_clean_{d}.xlsx", NHL_SLATE)
    soccer_slate_p = _resolve_outputs_artifact(days, "step8_soccer_direction_clean_{d}.xlsx", SOCCER_SLATE)
    mlb_slate_p = _resolve_outputs_artifact(days, "step8_mlb_direction_clean_{d}.xlsx", MLB_SLATE)
    udq_p = _resolve_outputs_artifact(days, "upstream_data_quality_{d}.csv")

    combined_candidates: list[Path] = []
    for d in days:
        out_d = BASE_DIR / "outputs" / d
        combined_candidates.extend(BASE_DIR.glob(f"combined_slate_tickets_{d}*.xlsx"))
        combined_candidates.extend(BASE_DIR.glob(f"combined_slate_tickets_{d}*.json"))
        if out_d.is_dir():
            combined_candidates.extend(out_d.glob(f"combined_slate_tickets_{d}*.xlsx"))
            combined_candidates.extend(out_d.glob(f"combined_slate_tickets_{d}*.json"))
    combined_path = (
        max(combined_candidates, key=lambda p: p.stat().st_mtime) if combined_candidates else None
    )
    combined_slate = _file_info(combined_path) if combined_path else {"exists": False}
    if combined_slate.get("exists"):
        if has_web_tickets and card_disp:
            combined_slate = {**combined_slate, "modified": card_disp}
        elif tik_js_ts and tik_js_disp and tik_js_ts > _mtime_ts(combined_slate.get("modified")):
            combined_slate = {**combined_slate, "modified": tik_js_disp}
    elif has_web_tickets and card_disp:
        approx_kb = round(len(json.dumps(tickets_payload)) / 1024, 1)
        combined_slate = {
            "exists": True,
            "modified": card_disp,
            "size_kb": approx_kb,
        }

    return jsonify({
        "nba": {
            "run_complete_flag": NBA_FLAG.exists(),
            "slate":   _sport_slate_status(nba_slate_p, "nba", slate_counts, slate_disk_info, status_js_ts, card_disp),
            "tickets": _file_info(NBA_TICKETS),
        },
        "nba1h": {
            "slate":   _sport_slate_status(nba1h_slate_p, "nba1h", slate_counts, slate_disk_info, status_js_ts, card_disp),
            "tickets": _file_info(NBA1H_TICKETS),
        },
        "nba1q": {
            "slate":   _sport_slate_status(nba1q_slate_p, "nba1q", slate_counts, slate_disk_info, status_js_ts, card_disp),
            "tickets": _file_info(NBA1Q_TICKETS),
        },
        "cbb": {
            "slate": _sport_slate_status(cbb_slate_p, "cbb", slate_counts, slate_disk_info, status_js_ts, card_disp),
        },
        "wcbb": {
            "slate": _sport_slate_status(wcbb_slate_p, "wcbb", slate_counts, slate_disk_info, status_js_ts, card_disp),
        },
        "nhl": {
            "slate":   _sport_slate_status(nhl_slate_p, "nhl", slate_counts, slate_disk_info, status_js_ts, card_disp),
            "tickets": _file_info(NHL_TICKETS),
        },
        "soccer": {
            "slate":   _sport_slate_status(soccer_slate_p, "soccer", slate_counts, slate_disk_info, status_js_ts, card_disp),
            "tickets": _file_info(SOCCER_TICKETS),
        },
        "mlb": {
            "slate":   _sport_slate_status(mlb_slate_p, "mlb", slate_counts, slate_disk_info, status_js_ts, card_disp),
            "tickets": _file_info(MLB_TICKETS),
        },
        "combined": {
            "slate": combined_slate,
        },
        "pipeline_outputs_date": days[0] if days else None,
        "upstream_data_quality": _file_info(udq_p),
        "as_of": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "slate_json_source": "remote" if bool(_DATA_FILE_URL_MAP.get("slate_latest.json")) else "disk",
        "ticket_eval_slate_source": "remote"
        if bool(_DATA_FILE_URL_MAP.get("ticket_eval_slate_latest.json"))
        else "disk",
        "slate_sport_source": os.environ.get("SLATE_SPORT_SOURCE", "auto").strip() or "auto",
        "tickets_json_source": "remote" if bool(_DATA_FILE_URL_MAP.get("tickets_latest.json")) else "disk",
        "railway_auto_github_json": bool(_running_on_railway() and not os.environ.get("DISABLE_AUTO_GITHUB_JSON", "").strip()),
        "deploy_git_sha": (os.environ.get("RAILWAY_GIT_COMMIT_SHA") or os.environ.get("GIT_COMMIT") or "")[:40],
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
    if not _template_json_available("tickets_latest.json"):
        return jsonify({"error": "tickets_latest.json not found", "groups": []}), 404
    try:
        data = read_json_cached(json_path)
        resp = jsonify(data)
        resp.headers["Cache-Control"] = "no-store, no-cache, must-revalidate, max-age=0"
        resp.headers["Pragma"] = "no-cache"
        return resp
    except Exception as e:
        return jsonify({"error": str(e), "groups": []}), 500


@app.get("/api/slate/today-tickets")
def api_slate_today_tickets():
    """Tickets from tickets_latest.json for /payout import (pre-fills leg std/played lines)."""
    if not _template_json_available("tickets_latest.json"):
        return jsonify({"error": "tickets_latest.json not found", "tickets": []}), 404
    try:
        data = read_json_cached(TEMPLATES_DIR / "tickets_latest.json")
    except Exception as e:
        return jsonify({"error": str(e), "tickets": []}), 500
    et = _eastern_today_ymd()
    d_sl = str((data or {}).get("date") or "").strip()[:10]
    tickets_out: list[dict] = []
    for grp in (data or {}).get("groups") or []:
        sheet = str(grp.get("group_name") or "")
        for t in grp.get("tickets") or []:
            legs_in = t.get("legs") or []
            legs = []
            for lg in legs_in:
                std = lg.get("standard_line")
                played = lg.get("line")
                pt = str(lg.get("pick_type") or "Standard")
                dp = lg.get("delta_pct")
                legs.append(
                    {
                        "player": lg.get("player"),
                        "sport": lg.get("sport"),
                        "prop_type": lg.get("prop_type"),
                        "pick_type": pt,
                        "std_line": std,
                        "played_line": played,
                        "delta_pct": dp,
                        "direction": lg.get("direction"),
                        "ml_prob": lg.get("ml_prob"),
                    }
                )
            tickets_out.append(
                {
                    "ticket_id": f"{sheet}#{t.get('ticket_no')}",
                    "sheet": sheet,
                    "ticket_no": t.get("ticket_no"),
                    "n_legs": len(legs),
                    "legs": legs,
                    "est_mult": t.get("est_multiplier"),
                    "flat_mult": t.get("flat_multiplier"),
                    "combined_prob": t.get("combined_hit_prob_curve"),
                    "est_ev": t.get("est_ev"),
                }
            )
    r = jsonify(
        {
            "date": d_sl,
            "eastern_today": et,
            "tickets": tickets_out,
        }
    )
    r.headers["Cache-Control"] = "no-store, no-cache, must-revalidate, max-age=0"
    return r


@app.post("/api/payout/log-observation")
def api_payout_log_observation():
    """Append one row to data/payout_observations.csv (server-side curve learning)."""
    body = request.get_json(silent=True) or {}
    csv_path = BASE_DIR / "data" / "payout_observations.csv"
    fieldnames = [
        "date",
        "n_legs",
        "slip_type",
        "combo_label",
        "base_mult",
        "est_mult",
        "actual_mult",
        "mult_delta",
        "stake",
        "actual_payout",
        "g_exp_used",
        "d_exp_used",
        "d_scale_used",
        "leg_details_json",
    ]
    try:
        from utils.goblin_demon_multiplier import load_params as _gd_load

        prm = _gd_load()
    except Exception:
        prm = {"G_EXP": 1.0, "D_EXP": 1.5, "D_SCALE": 3.0}

    def _f(name, default=""):
        v = body.get(name)
        return default if v is None else v

    try:
        actual_mult = float(_f("actual_mult", 0) or 0)
    except (TypeError, ValueError):
        return jsonify({"saved": False, "error": "invalid actual_mult"}), 400
    if actual_mult <= 0:
        return jsonify({"saved": False, "error": "actual_mult required"}), 400

    try:
        base_mult = float(_f("base_mult", 0) or 0)
        est_mult = float(_f("est_mult", 0) or 0)
    except (TypeError, ValueError):
        base_mult, est_mult = 0.0, 0.0
    mult_delta = round(actual_mult - est_mult, 4) if est_mult else ""

    leg_json = _f("leg_details_json", "")
    if isinstance(leg_json, (list, dict)):
        leg_json = json.dumps(leg_json, ensure_ascii=False)

    row = {
        "date": str(_f("date", "")).strip(),
        "n_legs": str(_f("n_legs", "")).strip(),
        "slip_type": str(_f("slip_type", "power")).strip().lower(),
        "combo_label": str(_f("combo_label", "")).strip(),
        "base_mult": base_mult,
        "est_mult": est_mult,
        "actual_mult": actual_mult,
        "mult_delta": mult_delta,
        "stake": str(_f("stake", "")).strip(),
        "actual_payout": str(_f("actual_payout", "")).strip(),
        "g_exp_used": prm.get("G_EXP", ""),
        "d_exp_used": prm.get("D_EXP", ""),
        "d_scale_used": prm.get("D_SCALE", ""),
        "leg_details_json": leg_json if isinstance(leg_json, str) else "",
    }

    csv_path.parent.mkdir(parents=True, exist_ok=True)
    file_exists = csv_path.exists()
    with csv_path.open("a", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=fieldnames)
        if not file_exists:
            w.writeheader()
        w.writerow(row)

    n_lines = sum(1 for _ in csv_path.open("r", encoding="utf-8")) - 1
    warn = bool(est_mult and abs(float(actual_mult) - float(est_mult)) > 1.5)
    return jsonify({"saved": True, "total_obs": max(0, n_lines), "mult_delta": mult_delta, "warning_large_delta": warn})


@app.get("/api/payout/combo-table")
def api_payout_combo_table():
    """Static combo reference from outputs/combo_table_latest.json (run write_combo_table_latest.py)."""
    p = BASE_DIR / "outputs" / "combo_table_latest.json"
    if not p.is_file():
        return jsonify(
            {
                "error": "combo_table_latest.json missing — run scripts/write_combo_table_latest.py",
                "leg_counts": [],
                "combos": [],
            }
        ), 404
    try:
        data = json.loads(p.read_text(encoding="utf-8"))
    except Exception as e:
        return jsonify({"error": str(e), "leg_counts": [], "combos": []}), 500
    return jsonify(data)


def _eastern_today_ymd() -> str:
    """Calendar date in America/New_York (US slate day for NBA/CBB)."""
    return datetime.now(ZoneInfo("America/New_York")).date().strftime("%Y-%m-%d")


@app.get("/api/slate-display-date")
def api_slate_display_date():
    """
    YYYY-MM-DD for the home/nav slate chip.

    Prefer tickets_latest.json — it drives /tickets and /api/slate. Using max() across every
    JSON allowed ticket_eval or slate_latest to advertise a *later* day (e.g. 4/4) while the
    main slip was still 4/3.

    If tickets_latest is missing or has no date, use the newest candidate that is not after
    US Eastern \"today\"; if all are future (bad data), fall back to max(candidates).
    """
    candidates: list[str] = []
    tickets_date: str | None = None
    for name in ("tickets_latest.json", "slate_latest.json", "ticket_eval_slate_latest.json"):
        if not _template_json_available(name):
            continue
        try:
            data = read_json_cached(TEMPLATES_DIR / name)
            ds = str((data or {}).get("date") or "").strip()[:10]
            if len(ds) == 10 and ds[4] == "-" and ds[7] == "-":
                candidates.append(ds)
                if name == "tickets_latest.json":
                    tickets_date = ds
        except Exception:
            continue
    try:
        sp = _selected_slate_sport_payload()
        ds = str((sp or {}).get("date") or "").strip()[:10]
        if len(ds) == 10:
            candidates.append(ds)
    except Exception:
        pass

    et_today = _eastern_today_ymd()
    if tickets_date:
        best = tickets_date
    else:
        not_future = [c for c in candidates if c <= et_today]
        best = max(not_future) if not_future else (max(candidates) if candidates else None)
    r = jsonify({"date": best})
    r.headers["Cache-Control"] = "no-store, no-cache, must-revalidate, max-age=0"
    r.headers["Pragma"] = "no-cache"
    return r


# API: Slate picks - deduped unique picks from tickets_latest.json
@app.get("/api/slate")
def api_slate():
    json_path = TEMPLATES_DIR / "tickets_latest.json"
    if not _template_json_available("tickets_latest.json"):
        return jsonify({"picks": [], "generated_at": None, "date": None})

    def _build_picks():
        def _side_hit_count(raw: object, n: int) -> int | None:
            """Match UI streakHits: rate in [0,1] or integer count in last n games."""
            if raw is None:
                return None
            try:
                x = float(raw)
            except (TypeError, ValueError):
                return None
            if x != x:  # NaN
                return None
            if x <= 1.0:
                return max(0, min(n, int(round(x * n))))
            return max(0, min(n, int(round(x))))

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
                    l5_over = leg.get("l5_over")
                    l5_under = leg.get("l5_under")
                    if l5_under is None and l5_over is not None:
                        ho = _side_hit_count(l5_over, 5)
                        if ho is not None:
                            l5_under = 5 - ho
                    l10_over = leg.get("l10_over")
                    l10_under = leg.get("l10_under")
                    if l10_under is None and l10_over is not None:
                        ho = _side_hit_count(l10_over, 10)
                        if ho is not None:
                            l10_under = 10 - ho
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
                        "l5_over":    l5_over,
                        "l5_under":   l5_under,
                        "l10_over":   l10_over,
                        "l10_under":  l10_under,
                        "l5_avg":     leg.get("l5_avg"),
                        "season_avg": leg.get("season_avg"),
                    })
        picks.sort(key=lambda p: abs(p["edge"]), reverse=True)
        return {"picks": picks, "generated_at": data.get("generated_at"), "date": data.get("date")}

    try:
        return _gz_json_response("slate-picks", _build_picks, ttl=_PIPELINE_JSON_TTL)
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
    if not (
        _template_json_available("slate_latest.json")
        or _template_json_available("ticket_eval_slate_latest.json")
    ):
        return jsonify(
            {
                "error": "No slate JSON — run pipeline (slate_latest.json) or build_ticket_eval.py (ticket_eval_slate_latest.json)",
                "sports": {},
            }
        ), 404
    try:
        _selected_slate_sport_payload()
    except ValueError as e:
        return jsonify({"error": str(e), "sports": {}}), 404
    try:
        return _gz_json_response(
            "slate-sport", lambda: _selected_slate_sport_payload(), ttl=_PIPELINE_JSON_TTL
        )
    except Exception as e:
        return jsonify({"error": str(e), "sports": {}}), 500


@app.get("/api/slate-excel")
def api_slate_excel():
    """Return all sheets from the combined Excel with non-blank columns only."""
    import openpyxl

    def _build():
        path, run_date = _find_combined_excel()
        if path is None:
            return {"error": "combined slate Excel not found", "sheets": {}, "date": None}
        sheet_map = {
            "Full Slate":  "combined",
            "NBA Slate":   "nba",
            "NBA1H Slate": "nba1h",
            "NBA1Q Slate": "nba1q",
            "CBB Slate":   "cbb",
            "NHL Slate":   "nhl",
            "Soccer Slate":"soccer",
            "WCBB Slate":  "wcbb",
            "MLB Slate":   "mlb",
        }
        wb = openpyxl.load_workbook(str(path), read_only=True, data_only=True)
        result: dict[str, Any] = {}
        for sheet_name, key in sheet_map.items():
            if sheet_name not in wb.sheetnames:
                continue
            ws = wb[sheet_name]
            all_rows = list(ws.iter_rows(values_only=True))
            if not all_rows:
                continue
            headers = [str(h) if h is not None else "" for h in all_rows[0]]
            data_rows = all_rows[1:]
            # keep only columns with ≥1 non-null value in first 100 rows
            non_blank = [
                i for i, h in enumerate(headers)
                if h and any(r[i] is not None for r in data_rows[:100])
            ]
            columns = [headers[i] for i in non_blank]
            rows = []
            for r in data_rows:
                row = []
                for i in non_blank:
                    v = r[i]
                    row.append(str(v) if hasattr(v, "isoformat") else v)
                rows.append(row)
            result[key] = {"columns": columns, "rows": rows}
        wb.close()
        return {"sheets": result, "date": run_date}

    try:
        return _gz_json_response("slate-excel", _build, ttl=_PIPELINE_JSON_TTL)
    except Exception as e:
        return jsonify({"error": str(e), "sheets": {}}), 500


# ──────────────────────────────────────────────────────────────────────────────
# API: Full Slate — filterable rows from the "Full Slate" sheet of the combined
#      tickets xlsx.  Query params: sport=NBA, tier=A, pick_type=Goblin, dir=OVER
#      Returns: { date, columns, rows }  (rows are compact value arrays)
# ──────────────────────────────────────────────────────────────────────────────
@app.route("/api/full-slate")
def api_full_slate():
    # Re-use the same file-finder used by /api/slate-excel
    path, run_date = _find_combined_excel()
    if path is None:
        return jsonify({"error": "combined slate Excel not found", "date": None, "columns": [], "rows": []}), 404

    try:
        import pandas as pd
        df = pd.read_excel(str(path), sheet_name="Full Slate", dtype=object, engine="openpyxl")
    except Exception as e:
        return jsonify({"error": f"Could not read Full Slate sheet: {e}", "date": run_date, "columns": [], "rows": []}), 500

    # Normalise column names for filter matching (strip whitespace)
    df.columns = [str(c).strip() for c in df.columns]

    # Query-param filters — case-insensitive substring match on Sport/Tier/Pick Type/Direction
    sport    = request.args.get("sport", "").strip().upper()
    tier     = request.args.get("tier", "").strip().upper()
    pick_type= request.args.get("pick_type", "").strip().lower()
    direction= request.args.get("dir", "").strip().upper()

    col_map = {c.upper(): c for c in df.columns}
    def _filter(col_key: str, value: str):
        nonlocal df
        col = col_map.get(col_key)
        if col and value:
            df = df[df[col].astype(str).str.strip().str.upper() == value]

    _filter("SPORT",     sport)
    _filter("TIER",      tier)
    _filter("DIRECTION", direction)
    if pick_type:
        col = col_map.get("PICK TYPE")
        if col:
            df = df[df[col].astype(str).str.strip().str.lower() == pick_type]

    # Replace NaN with None for JSON serialisation
    df = df.where(df.notna(), other=None)

    columns = list(df.columns)
    rows = df.values.tolist()
    # Convert any remaining non-JSON-serialisable values (dates, etc.)
    import math
    def _clean(v):
        if v is None:
            return None
        if isinstance(v, float) and math.isnan(v):
            return None
        if hasattr(v, "isoformat"):
            return v.isoformat()
        return v
    rows = [[_clean(v) for v in row] for row in rows]

    resp = jsonify({"date": run_date, "columns": columns, "rows": rows})
    resp.headers["Cache-Control"] = "no-store"
    return resp


def _find_combined_excel() -> "tuple[Path | None, str | None]":
    """Return (path, date_str) for the most recent combined_slate_tickets_*.xlsx."""
    try:
        tj = read_json_cached(TEMPLATES_DIR / "tickets_latest.json")
        pref = str((tj or {}).get("date") or "")[:10]
    except Exception:
        pref = ""
    for d in _slate_day_candidates(pref if len(pref) == 10 else None):
        out_d = BASE_DIR / "outputs" / d
        candidates: list[Path] = [
            out_d / f"combined_slate_tickets_{d}.xlsx",
            COMBINED_OUT / f"combined_slate_tickets_{d}.xlsx",
        ]
        seen: set[Path] = set(candidates)
        if out_d.is_dir():
            for g in out_d.glob(f"combined_slate_tickets_{d}*.xlsx"):
                if g not in seen:
                    seen.add(g); candidates.append(g)
        for g in BASE_DIR.glob(f"combined_slate_tickets_{d}*.xlsx"):
            if g not in seen:
                seen.add(g); candidates.append(g)
        for p in candidates:
            if p.exists():
                return p, d
    return None, None


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


@app.get("/dashboard/income")
def dashboard_income():
    """
    ROI / CLV / calibration / drawdown only — see DESIGN_PRINCIPLES.md.
    Uses PROPORACLE_DB_PATH or data/cache/proporacle_income.db; ddl.sql + views.sql are applied
    automatically on first open. When bet_result is empty, demo slates are inserted unless
    PROPORACLE_INCOME_SEED_DEMO=0. Use real pipeline ingest for production metrics.
    """
    import json

    err: str | None = None
    roi_payload = {"days": [], "pnl": []}
    clv_payload = {"buckets": [], "means": []}
    cal_payload = {"pred": [], "hit": []}
    eq_payload = {"days": [], "dd": []}

    try:
        from proporacle.monitoring.dashboard_queries import (
            fetch_calibration_bins,
            fetch_clv_by_edge_bucket,
            fetch_equity_drawdown,
            fetch_roi_daily,
            load_income_db,
            maybe_seed_demo_income,
        )

        conn = load_income_db()
        try:
            maybe_seed_demo_income(conn)
            roi_rows = fetch_roi_daily(conn)
            for r in roi_rows:
                roi_payload["days"].append(r["bet_day"])
                roi_payload["pnl"].append(float(r["daily_pnl"] or 0))

            for r in fetch_clv_by_edge_bucket(conn):
                clv_payload["buckets"].append(r["ev_bucket"])
                clv_payload["means"].append(float(r["mean_clv"] or 0))

            for r in fetch_calibration_bins(conn):
                if r["pred_mean"] is not None and r["hit_rate"] is not None:
                    cal_payload["pred"].append(float(r["pred_mean"]))
                    cal_payload["hit"].append(float(r["hit_rate"]))

            br0 = float(os.environ.get("PROPORACLE_BANKROLL_0", "200"))
            for r in fetch_equity_drawdown(conn, bankroll_0=br0):
                eq_payload["days"].append(r["bet_day"])
                eq_payload["dd"].append(float(r["drawdown"]))
        finally:
            conn.close()
    except Exception as e:
        err = (
            f"{type(e).__name__}: {e}. "
            "Check PROPORACLE_DB_PATH and that proporacle/data/schema/ddl.sql and views.sql exist."
        )

    charts_empty = err is None and len(roi_payload["days"]) == 0

    return render_template(
        "dashboard_income.html",
        error=err,
        charts_empty=charts_empty,
        ui_build_id=_UI_BUILD_ID,
        roi_json=Markup(json.dumps(roi_payload)),
        clv_json=Markup(json.dumps(clv_payload)),
        cal_json=Markup(json.dumps(cal_payload)),
        equity_json=Markup(json.dumps(eq_payload)),
    )


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=8787, debug=False)
