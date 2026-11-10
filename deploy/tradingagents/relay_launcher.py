r"""Cross-platform launcher for TradingAgents behind the streaming relay.

Why this exists (same four reasons as the Windows-only original):

1. ``openai_compatible`` builds a non-streaming client, but the relay
   (api.cingsi.xyz) rejects non-streaming chat completions with HTTP 400.
   The launcher swaps that one provider's registry entry for a subclass that
   defaults ``streaming=True``.
2. yfinance goes through curl_cffi (libcurl), which honours HTTP(S)_PROXY but
   ignores the Windows WinINET registry. On Windows this exports the system
   proxy; on Linux it simply leaves the environment alone (set HTTP(S)_PROXY
   yourself if the host needs one).
3. yfinance writes its SQLite caches under the user cache dir; redirect it into
   ``TA_DATA/yf-cache`` so a sandboxed or read-only home does not break fetches.
4. chdir to the repo so ``.env`` is found regardless of the caller's cwd.

Paths come from the environment so the same file runs on Windows and Linux:

    TA_REPO   default: ~/TradingAgents
    TA_DATA   default: ~/.runtime

Usage:

    TA_REPO=/home/workspace/TradingAgents \
    TA_DATA=/home/workspace/.runtime \
    .venv/bin/python relay_launcher.py \
        --ticker 600009.SS --date 2026-11-10 \
        --analysts market,news,fundamentals \
        --no-checkpoint --save --no-show --no-html
"""

from __future__ import annotations

import os
import pathlib
import sys


def _env_path(name: str, default: pathlib.Path) -> pathlib.Path:
    raw = os.environ.get(name)
    if raw:
        return pathlib.Path(raw).expanduser().resolve()
    return default.expanduser().resolve()


REPO = _env_path("TA_REPO", pathlib.Path.home() / "TradingAgents")
DATA = _env_path("TA_DATA", pathlib.Path.home() / ".runtime")


def _apply_system_proxy() -> None:
    """Export the Windows system proxy; a no-op on other platforms."""
    if os.environ.get("HTTPS_PROXY") or os.environ.get("https_proxy"):
        return

    if sys.platform != "win32":
        return

    import winreg

    try:
        with winreg.OpenKey(
            winreg.HKEY_CURRENT_USER,
            r"Software\Microsoft\Windows\CurrentVersion\Internet Settings",
        ) as key:
            enabled, _ = winreg.QueryValueEx(key, "ProxyEnable")
            server, _ = winreg.QueryValueEx(key, "ProxyServer")
    except OSError:
        return

    if not enabled or not server:
        return

    # ProxyServer is "host:port" or per-scheme, e.g. "http=h:p;https=h:p".
    if "=" in server:
        parts = dict(
            entry.split("=", 1) for entry in server.split(";") if "=" in entry
        )
        server = parts.get("https") or parts.get("http") or ""
    if not server:
        return

    url = server if "://" in server else f"http://{server}"
    os.environ["HTTP_PROXY"] = url
    os.environ["HTTPS_PROXY"] = url
    print(f"Using system proxy {url}")


def _redirect_yfinance_cache() -> None:
    """Keep yfinance's SQLite caches inside TA_DATA."""
    import yfinance

    target = DATA / "yf-cache"
    target.mkdir(parents=True, exist_ok=True)
    yfinance.set_tz_cache_location(str(target))


def _apply_streaming_shim() -> None:
    """Make the `openai_compatible` provider ask its gateway to stream."""
    from dataclasses import replace

    from tradingagents.llm_clients import openai_client as oc

    class StreamingLocalCompatibleChatOpenAI(oc.LocalCompatibleChatOpenAI):
        def __init__(self, **kwargs):
            kwargs.setdefault("streaming", True)
            super().__init__(**kwargs)

    spec = oc.OPENAI_COMPATIBLE_PROVIDERS["openai_compatible"]
    oc.OPENAI_COMPATIBLE_PROVIDERS["openai_compatible"] = replace(
        spec, chat_class=StreamingLocalCompatibleChatOpenAI
    )


def main() -> None:
    # tradingagents/__init__.py loads .env at import time, so chdir first.
    os.chdir(REPO)
    sys.path.insert(0, str(REPO))

    DATA.mkdir(parents=True, exist_ok=True)
    print(f"TA_REPO={REPO}")
    print(f"TA_DATA={DATA}")
    print(f"python={sys.version.split()[0]} platform={sys.platform}")

    _apply_system_proxy()
    _redirect_yfinance_cache()
    _apply_streaming_shim()

    from cli.main import app

    app()


if __name__ == "__main__":
    main()