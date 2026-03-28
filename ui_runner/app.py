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
from datetime import date, datetime, timedelta, timezone

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
WCBB_SLATE    = CBB_DIR / "step6_ranked_wcbb.xlsx"
# Same paths as scripts/run_pipeline.ps1 (sport root, not sport/outputs/)
NHL_SLATE     = NHL_DIR / "step8_nhl_direction_clean.xlsx"
NHL_TICKETS   = NHL_DIR / "nhl_best_tickets.xlsx"
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
_UI_BUILD_ID = "2026-03-28-railway-gunicorn-sh-1"

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
    Prefer slate_latest.json generated_at over baked-in Excel mtimes when JSON is newer
    (Docker/Railway images ship stale .xlsx from last deploy).
    """
    key = sport_key.lower()
    cnt = int(counts.get(key, 0))
    if key == "cbb":
        cnt += int(counts.get("wcbb", 0))

    direct = _file_info(path)
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
        if tik_js_ts and tik_js_disp and tik_js_ts > _mtime_ts(combined_slate.get("modified")):
            combined_slate = {**combined_slate, "modified": tik_js_disp}
    elif tickets_payload and (tickets_payload.get("groups") or []):
        if tik_js_disp:
            approx_kb = round(len(json.dumps(tickets_payload)) / 1024, 1)
            combined_slate = {
                "exists": True,
                "modified": tik_js_disp,
                "size_kb": approx_kb,
            }

    return jsonify({
        "nba": {
            "run_complete_flag": NBA_FLAG.exists(),
            "slate":   _sport_slate_status(nba_slate_p, "nba", slate_counts, slate_disk_info, status_js_ts, status_js_disp),
            "tickets": _file_info(NBA_TICKETS),
        },
        "nba1h": {
            "slate":   _sport_slate_status(nba1h_slate_p, "nba1h", slate_counts, slate_disk_info, status_js_ts, status_js_disp),
            "tickets": _file_info(NBA1H_TICKETS),
        },
        "nba1q": {
            "slate":   _sport_slate_status(nba1q_slate_p, "nba1q", slate_counts, slate_disk_info, status_js_ts, status_js_disp),
            "tickets": _file_info(NBA1Q_TICKETS),
        },
        "cbb": {
            "slate": _sport_slate_status(cbb_slate_p, "cbb", slate_counts, slate_disk_info, status_js_ts, status_js_disp),
        },
        "wcbb": {
            "slate": _sport_slate_status(wcbb_slate_p, "wcbb", slate_counts, slate_disk_info, status_js_ts, status_js_disp),
        },
        "nhl": {
            "slate":   _sport_slate_status(nhl_slate_p, "nhl", slate_counts, slate_disk_info, status_js_ts, status_js_disp),
            "tickets": _file_info(NHL_TICKETS),
        },
        "soccer": {
            "slate":   _sport_slate_status(soccer_slate_p, "soccer", slate_counts, slate_disk_info, status_js_ts, status_js_disp),
            "tickets": _file_info(SOCCER_TICKETS),
        },
        "mlb": {
            "slate":   _sport_slate_status(mlb_slate_p, "mlb", slate_counts, slate_disk_info, status_js_ts, status_js_disp),
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


@app.get("/api/slate-display-date")
def api_slate_display_date():
    """
    Max YYYY-MM-DD among tickets_latest, slate_latest, ticket_eval_slate_latest, and the
    active /api/slate-sport payload — avoids hero pill stuck on 3/27 when another file is 3/28.
    """
    candidates: list[str] = []
    for name in ("tickets_latest.json", "slate_latest.json", "ticket_eval_slate_latest.json"):
        if not _template_json_available(name):
            continue
        try:
            data = read_json_cached(TEMPLATES_DIR / name)
            ds = str((data or {}).get("date") or "").strip()[:10]
            if len(ds) == 10 and ds[4] == "-" and ds[7] == "-":
                candidates.append(ds)
        except Exception:
            continue
    try:
        sp = _selected_slate_sport_payload()
        ds = str((sp or {}).get("date") or "").strip()[:10]
        if len(ds) == 10:
            candidates.append(ds)
    except Exception:
        pass
    best = max(candidates) if candidates else None
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

    def _find_excel():
        try:
            tj = read_json_cached(TEMPLATES_DIR / "tickets_latest.json")
            pref = str((tj or {}).get("date") or "")[:10]
        except Exception:
            pref = ""
        for d in _slate_day_candidates(pref if len(pref) == 10 else None):
            out_d = BASE_DIR / "outputs" / d
            candidates = [
                out_d / f"combined_slate_tickets_{d}.xlsx",
                COMBINED_OUT / f"combined_slate_tickets_{d}.xlsx",
            ]
            seen: set[Path] = set(candidates)
            if out_d.is_dir():
                for g in out_d.glob(f"combined_slate_tickets_{d}*.xlsx"):
                    if g not in seen:
                        seen.add(g)
                        candidates.append(g)
            for g in BASE_DIR.glob(f"combined_slate_tickets_{d}*.xlsx"):
                if g not in seen:
                    seen.add(g)
                    candidates.append(g)
            for p in candidates:
                if p.exists():
                    return p, d
        return None, None

    def _build():
        path, run_date = _find_excel()
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
