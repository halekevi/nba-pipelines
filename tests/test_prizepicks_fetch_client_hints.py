"""Regression tests: PrizePicks direct API must keep TLS (curl_cffi) and User-Agent aligned."""
from __future__ import annotations

import importlib.util
import os
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]


def _load_pp_api(*, env_impersonate: str | None) -> object:
    """Fresh module load so _CURL_IMPERSONATE is read from env at import time."""
    if env_impersonate is None:
        os.environ.pop("PROPORACLE_CURL_IMPERSONATE", None)
    else:
        os.environ["PROPORACLE_CURL_IMPERSONATE"] = env_impersonate
    name = f"pp_api_test_{env_impersonate or 'default'}"
    path = REPO_ROOT / "NBA" / "scripts" / "step1_fetch_prizepicks_api.py"
    spec = importlib.util.spec_from_file_location(name, path)
    assert spec and spec.loader
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def test_tls_major_parses_chrome_variants():
    mod = _load_pp_api(env_impersonate="chrome131")
    assert mod._tls_chrome_major_from_impersonate() == 131
    mod2 = _load_pp_api(env_impersonate="Chrome_120")
    assert mod2._tls_chrome_major_from_impersonate() == 120


def test_profiles_filtered_when_curl_cffi_enabled():
    mod = _load_pp_api(env_impersonate="chrome120")
    mod._CURL_CFFI_AVAILABLE = True
    pool = mod._profiles_for_current_transport()
    assert pool
    for p in pool:
        ua = p["User-Agent"]
        assert "Chrome/120." in ua or "Edg/120." in ua


def test_profiles_full_pool_when_requests_only():
    mod = _load_pp_api(env_impersonate="chrome120")
    mod._CURL_CFFI_AVAILABLE = False
    pool = mod._profiles_for_current_transport()
    assert len(pool) == len(mod._BROWSER_PROFILES)


def test_random_headers_pass_tls_guard_when_curl_and_chrome120():
    mod = _load_pp_api(env_impersonate="chrome120")
    mod._CURL_CFFI_AVAILABLE = True
    for _ in range(5):
        h = mod._random_browser_headers()
        assert "Chrome/120." in h["User-Agent"] or "Edg/120." in h["User-Agent"]


def test_missing_profile_for_impersonate_raises():
    mod = _load_pp_api(env_impersonate="chrome999")
    mod._CURL_CFFI_AVAILABLE = True
    with pytest.raises(RuntimeError, match="No User-Agent profile"):
        mod._profiles_for_current_transport()
