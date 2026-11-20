"""
browser_factory.py - the ONLY module allowed to touch Playwright.

Every browser launch, context creation, viewport/locale/timezone/user-agent
setting, proxy/network setting and session (storage_state) load/save lives
here. Page modules and runners receive a ready `page` and never import
playwright themselves. To swap the engine (firefox/webkit/Chrome channel),
add driver flags, or change network routing, edit only this file.

Public API:
    open_user_session(user, fresh=False, save_on_exit=True)  -> (browser, context, page)
    save_session(context, user)
    session_path(user) / session_exists(user)
    BrowserError, BrowserTimeoutError, expect      (re-exports for page modules)
"""

from __future__ import annotations

import os
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Iterator

from playwright.sync_api import (
    Browser,
    BrowserContext,
    Error as _PlaywrightError,
    Page,
    Playwright,
    TimeoutError as _PlaywrightTimeoutError,
    expect as _expect,
    sync_playwright,
)

# Re-exported so page modules can catch errors / assert without importing playwright.
BrowserError = _PlaywrightError
BrowserTimeoutError = _PlaywrightTimeoutError
expect = _expect

BASE_DIR = Path(__file__).resolve().parent

# ---- Engine / driver settings: change these in one place ------------------ #
HEADLESS = False          # visible window so you can watch and solve CAPTCHAs
SLOW_MO_MS = 0            # >0 slows every Playwright action (debugging)
LAUNCH_ARGS: list[str] = []  # extra Chromium flags, e.g. ["--start-maximized"]


def _log(msg: str) -> None:
    print(f"  [browser] {msg}", flush=True)


# --------------------------------------------------------------------------- #
# Session file helpers
# --------------------------------------------------------------------------- #
def session_path(user: dict) -> Path:
    """Resolve the user's session_file relative to the project folder."""
    p = Path(user["session_file"])
    return p if p.is_absolute() else BASE_DIR / p


def session_exists(user: dict) -> bool:
    return session_path(user).exists()


def save_session(context: BrowserContext, user: dict) -> Path:
    """Write the context's cookies/localStorage to the user's session_file."""
    path = session_path(user)
    path.parent.mkdir(parents=True, exist_ok=True)
    context.storage_state(path=str(path))
    if os.name == "posix":  # auth tokens: owner-only permissions
        try:
            os.chmod(path, 0o600)
        except OSError:
            pass
    _log(f"session saved -> {path}")
    return path


# --------------------------------------------------------------------------- #
# Option builders (user profile -> Playwright kwargs)
# --------------------------------------------------------------------------- #
def _proxy_settings(user: dict) -> dict | None:
    """
    Accepts either a string ("http://host:port") or an object:
        {"server": "http://host:port", "username": "...", "password": "...", "bypass": "localhost"}
    Returns None when no proxy is configured.
    """
    proxy = user.get("proxy")
    if not proxy:
        return None
    if isinstance(proxy, str):
        return {"server": proxy}
    if not proxy.get("server"):
        return None
    return {k: v for k, v in proxy.items()
            if k in ("server", "username", "password", "bypass") and v}


def _context_options(user: dict, storage_state: Path | None) -> dict[str, Any]:
    opts: dict[str, Any] = {
        "viewport": {"width": int(user["viewport"]["width"]),
                     "height": int(user["viewport"]["height"])},
        "locale": user["locale"],
        "timezone_id": user["timezone_id"],
    }
    if user.get("user_agent"):
        opts["user_agent"] = user["user_agent"]
    if storage_state is not None:
        opts["storage_state"] = str(storage_state)
    return opts


def _launch_browser(pw: Playwright, user: dict) -> Browser:
    """Single place where the browser engine is chosen and launched."""
    launch_kwargs: dict[str, Any] = {"headless": HEADLESS, "slow_mo": SLOW_MO_MS,
                                     "args": LAUNCH_ARGS}
    proxy = _proxy_settings(user)
    if proxy:
        # One browser per user, so a launch-level proxy cleanly isolates its traffic.
        launch_kwargs["proxy"] = proxy
        _log(f"proxy: {proxy['server']}")
    return pw.chromium.launch(**launch_kwargs)


def _new_context(browser: Browser, user: dict, fresh: bool) -> BrowserContext:
    path = session_path(user)
    if path.exists() and not fresh:
        try:
            ctx = browser.new_context(**_context_options(user, path))
            _log(f"loaded saved session {path.name}")
            return ctx
        except _PlaywrightError as exc:
            _log(f"could not load {path.name} ({exc.message.splitlines()[0]}); starting clean")
    elif fresh and path.exists():
        _log("fresh run: ignoring saved session")
    return browser.new_context(**_context_options(user, None))


# --------------------------------------------------------------------------- #
# Public entry point
# --------------------------------------------------------------------------- #
@contextmanager
def open_user_session(user: dict, fresh: bool = False,
                      save_on_exit: bool = True) -> Iterator[tuple[Browser, BrowserContext, Page]]:
    """
    Launch an isolated browser for one user profile and yield (browser, context, page).

    - Loads storage_state from user["session_file"] if it exists (unless fresh=True).
    - On a normal exit (no exception) and save_on_exit=True, saves storage_state.
    - Always closes the context, browser and Playwright driver.
    """
    pw = sync_playwright().start()
    browser = context = None
    try:
        browser = _launch_browser(pw, user)
        context = _new_context(browser, user, fresh)
        page = context.new_page()
        yield browser, context, page
        if save_on_exit:
            try:
                save_session(context, user)
            except _PlaywrightError as exc:
                _log(f"could not save session on exit: {exc.message.splitlines()[0]}")
    finally:
        for close in (getattr(context, "close", None), getattr(browser, "close", None), pw.stop):
            if close:
                try:
                    close()
                except Exception:  # window already closed by the user, etc.
                    pass
