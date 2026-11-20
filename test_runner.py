#!/usr/bin/env python3
"""
Portable Playwright E2E multi-user QA test runner.

Workflow per selected user profile:
  1. Pause so you can switch network / VPN.
  2. Launch a visible Chromium and an isolated context with the profile's
     viewport, locale, timezone and (optional) user agent.
  3. Reuse the saved session (storage_state) if it exists and is still valid.
  4. Otherwise log in, wait for YOU to finish the CAPTCHA / manual verification,
     then save the authenticated session to disk.
  5. Run run_post_login_actions(page, user) - your own checks go there.

Usage:
  python test_runner.py                      # interactive menu
  python test_runner.py --all                # every profile in users.json
  python test_runner.py --profile user1_desktop_es [--profile user2_laptop_de]
  python test_runner.py --all --fresh        # ignore saved sessions, force login
  python test_runner.py --list               # show profiles and exit
"""

from __future__ import annotations

import argparse
import getpass
import json
import os
import sys
import time
from pathlib import Path
from typing import Any

from playwright.sync_api import (
    Browser,
    BrowserContext,
    Error as PlaywrightError,
    Page,
    Playwright,
    sync_playwright,
)

# All paths are resolved relative to this script, so the folder is portable
# and the runner works no matter which directory you launch it from.
BASE_DIR = Path(__file__).resolve().parent
DEFAULT_CONFIG = BASE_DIR / "config.json"
DEFAULT_USERS = BASE_DIR / "users.json"

REQUIRED_CONFIG_KEYS = ["login_url", "post_login_url_or_selector", "selectors"]
REQUIRED_SELECTOR_KEYS = [
    "username_input",
    "password_input",
    "submit_button",
    "post_login_indicator",
]
REQUIRED_USER_KEYS = [
    "profile_name",
    "username",
    "viewport",
    "locale",
    "timezone_id",
    "session_file",
]

POLL_INTERVAL_MS = 500  # how often we check whether login has succeeded


# --------------------------------------------------------------------------- #
# Console helpers
# --------------------------------------------------------------------------- #
def log(msg: str) -> None:
    print(f"[{time.strftime('%H:%M:%S')}] {msg}", flush=True)


def banner(msg: str) -> None:
    line = "=" * max(60, len(msg) + 4)
    print(f"\n{line}\n  {msg}\n{line}", flush=True)


# --------------------------------------------------------------------------- #
# Configuration loading & validation
# --------------------------------------------------------------------------- #
class ConfigError(Exception):
    """Raised when config.json or users.json is missing or malformed."""


def load_json(path: Path) -> Any:
    if not path.exists():
        hint = ""
        example = path.with_name(path.stem + ".example.json")
        if example.exists():
            hint = f"\n  Hint: copy {example.name} to {path.name} and fill in your values."
        raise ConfigError(f"File not found: {path}{hint}")
    try:
        with path.open("r", encoding="utf-8") as fh:
            return json.load(fh)
    except json.JSONDecodeError as exc:
        raise ConfigError(f"Invalid JSON in {path.name}: {exc}") from exc


def load_config(path: Path) -> dict:
    cfg = load_json(path)
    if not isinstance(cfg, dict):
        raise ConfigError(f"{path.name} must contain a JSON object.")
    missing = [k for k in REQUIRED_CONFIG_KEYS if k not in cfg]
    if missing:
        raise ConfigError(f"{path.name} is missing keys: {', '.join(missing)}")
    missing_sel = [k for k in REQUIRED_SELECTOR_KEYS if not cfg["selectors"].get(k)]
    if missing_sel:
        raise ConfigError(f"{path.name} 'selectors' is missing: {', '.join(missing_sel)}")

    # Defaults for optional keys.
    cfg.setdefault("captcha_timeout_ms", 120_000)
    cfg.setdefault("session_check_timeout_ms", 10_000)
    cfg.setdefault("slow_mo_ms", 0)
    return cfg


def load_users(path: Path) -> list[dict]:
    users = load_json(path)
    if not isinstance(users, list) or not users:
        raise ConfigError(f"{path.name} must contain a non-empty JSON list of users.")

    seen: set[str] = set()
    for i, user in enumerate(users):
        missing = [k for k in REQUIRED_USER_KEYS if k not in user]
        if missing:
            raise ConfigError(f"User #{i + 1} in {path.name} is missing: {', '.join(missing)}")
        vp = user["viewport"]
        if not (isinstance(vp, dict) and "width" in vp and "height" in vp):
            raise ConfigError(
                f"User '{user['profile_name']}': viewport must be {{\"width\": .., \"height\": ..}}"
            )
        name = user["profile_name"]
        if name in seen:
            raise ConfigError(f"Duplicate profile_name in {path.name}: '{name}'")
        seen.add(name)
    return users


def is_url(value: str) -> bool:
    return value.strip().lower().startswith(("http://", "https://"))


def resolve_session_path(user: dict) -> Path:
    """Session paths in users.json are relative to the project folder."""
    p = Path(user["session_file"])
    return p if p.is_absolute() else BASE_DIR / p


# --------------------------------------------------------------------------- #
# Profile selection
# --------------------------------------------------------------------------- #
def select_users_interactively(users: list[dict]) -> list[dict]:
    banner("Available test profiles")
    for i, u in enumerate(users, start=1):
        session = "session saved" if resolve_session_path(u).exists() else "no session"
        vp = u["viewport"]
        print(
            f"  [{i}] {u['profile_name']:<24} {vp['width']}x{vp['height']:<6} "
            f"{u['locale']:<7} {u['timezone_id']:<22} ({session})"
        )
    print("  [a] Run ALL profiles")
    print("  [q] Quit")

    while True:
        choice = input("\nSelect profile number(s) (e.g. 1 or 1,3), 'a' or 'q': ").strip().lower()
        if choice == "q":
            return []
        if choice == "a":
            return users
        try:
            idxs = [int(x) for x in choice.replace(" ", "").split(",") if x]
            if idxs and all(1 <= n <= len(users) for n in idxs):
                return [users[n - 1] for n in idxs]
        except ValueError:
            pass
        print("  Invalid selection, try again.")


def select_users_from_args(users: list[dict], names: list[str]) -> list[dict]:
    by_name = {u["profile_name"]: u for u in users}
    unknown = [n for n in names if n not in by_name]
    if unknown:
        raise ConfigError(
            f"Unknown profile(s): {', '.join(unknown)}. "
            f"Available: {', '.join(by_name)}"
        )
    return [by_name[n] for n in names]


# --------------------------------------------------------------------------- #
# Browser / context setup
# --------------------------------------------------------------------------- #
def build_context_options(user: dict, session_path: Path | None) -> dict:
    """Translate a user profile into browser.new_context() keyword arguments."""
    opts: dict[str, Any] = {
        "viewport": {
            "width": int(user["viewport"]["width"]),
            "height": int(user["viewport"]["height"]),
        },
        "locale": user["locale"],
        "timezone_id": user["timezone_id"],
    }
    # Only override the UA when one is actually provided.
    if user.get("user_agent"):
        opts["user_agent"] = user["user_agent"]
    # Reuse a saved authenticated session if available.
    if session_path is not None:
        opts["storage_state"] = str(session_path)
    return opts


def launch_browser(pw: Playwright, cfg: dict) -> Browser:
    # headless=False: visible window so you can watch and solve the CAPTCHA.
    return pw.chromium.launch(headless=False, slow_mo=int(cfg.get("slow_mo_ms", 0)))


# --------------------------------------------------------------------------- #
# Login state detection
# --------------------------------------------------------------------------- #
def login_succeeded(page: Page, cfg: dict) -> bool:
    """
    True if the page looks authenticated:
      - post_login_indicator is visible, OR
      - post_login_url_or_selector is a URL and the current URL starts with it, OR
      - post_login_url_or_selector is a selector and it is visible.
    Safe to call mid-navigation (errors are treated as 'not yet').
    """
    target = cfg["post_login_url_or_selector"]
    try:
        if page.locator(cfg["selectors"]["post_login_indicator"]).first.is_visible():
            return True
        if is_url(target):
            return page.url.rstrip("/").startswith(target.rstrip("/"))
        return page.locator(target).first.is_visible()
    except PlaywrightError:
        # Page is navigating or the execution context was destroyed; retry later.
        return False


def wait_for_login(page: Page, cfg: dict, timeout_ms: int) -> bool:
    """Poll for login success until timeout. Returns True on success."""
    deadline = time.monotonic() + timeout_ms / 1000
    while time.monotonic() < deadline:
        if page.is_closed():
            raise RuntimeError("Browser page was closed before login completed.")
        if login_succeeded(page, cfg):
            return True
        page.wait_for_timeout(POLL_INTERVAL_MS)
    return login_succeeded(page, cfg)


def session_is_valid(page: Page, cfg: dict) -> bool:
    """
    Check whether the loaded storage_state still grants access.
    - If post_login_url_or_selector is a URL: open that protected page; an expired
      session normally redirects to the login page.
    - Otherwise: open login_url; a valid session normally redirects away from it
      and/or shows the post-login indicator.
    """
    target = cfg["post_login_url_or_selector"]
    check_url = target if is_url(target) else cfg["login_url"]
    log(f"Verifying saved session at {check_url} ...")
    try:
        page.goto(check_url, wait_until="domcontentloaded")
    except PlaywrightError as exc:
        log(f"Navigation during session check failed: {exc}")
        return False

    # Bounced back to the login page -> session expired.
    login_url = cfg["login_url"].rstrip("/")
    if is_url(target) and page.url.rstrip("/").startswith(login_url):
        return False

    # Give the app a moment to render the authenticated UI.
    try:
        page.locator(cfg["selectors"]["post_login_indicator"]).first.wait_for(
            state="visible", timeout=int(cfg["session_check_timeout_ms"])
        )
        return True
    except PlaywrightError:
        return login_succeeded(page, cfg)


# --------------------------------------------------------------------------- #
# Login flow (human-in-the-loop)
# --------------------------------------------------------------------------- #
def get_password(user: dict) -> str:
    """Use the password from users.json, or prompt securely if it is empty."""
    pwd = user.get("password") or ""
    if not pwd:
        pwd = getpass.getpass(f"Password for '{user['profile_name']}' ({user['username']}): ")
    return pwd


def perform_login(page: Page, context: BrowserContext, user: dict, cfg: dict,
                  session_path: Path) -> bool:
    sel = cfg["selectors"]
    timeout_ms = int(cfg["captcha_timeout_ms"])

    log(f"Opening login page: {cfg['login_url']}")
    page.goto(cfg["login_url"], wait_until="domcontentloaded")

    page.locator(sel["username_input"]).first.fill(user["username"])
    page.locator(sel["password_input"]).first.fill(get_password(user))
    page.locator(sel["submit_button"]).first.click()
    log("Credentials submitted.")

    banner(
        f"ACTION REQUIRED [{user['profile_name']}]: complete the CAPTCHA / manual "
        f"verification in the browser window (timeout {timeout_ms // 1000}s)"
    )

    if not wait_for_login(page, cfg, timeout_ms):
        log(f"Login NOT confirmed within {timeout_ms // 1000}s "
            f"(post_login_indicator '{sel['post_login_indicator']}' never appeared).")
        return False

    save_session(context, session_path)
    return True


def save_session(context: BrowserContext, session_path: Path) -> None:
    session_path.parent.mkdir(parents=True, exist_ok=True)
    context.storage_state(path=str(session_path))
    # Session files hold auth cookies/tokens: restrict to owner on POSIX systems.
    if os.name == "posix":
        try:
            os.chmod(session_path, 0o600)
        except OSError:
            pass
    log(f"Authenticated session saved -> {session_path}")


# --------------------------------------------------------------------------- #
# YOUR post-login checks go here
# --------------------------------------------------------------------------- #
def run_post_login_actions(page: Page, user: dict) -> None:
    """
    Placeholder for your own E2E checks once the user is authenticated.
    Raise an exception (e.g. AssertionError) to mark the profile as FAILED.

    Examples:
        from playwright.sync_api import expect
        page.goto("https://example.com/settings")
        expect(page.locator("#language")).to_have_value(user["locale"])
        page.locator("#nav-profile").click()
        page.screenshot(path=f"screenshots/{user['profile_name']}.png", full_page=True)

        # Verify the browser really reports the profile's timezone/locale:
        tz = page.evaluate("Intl.DateTimeFormat().resolvedOptions().timeZone")
        assert tz == user["timezone_id"], f"timezone {tz} != {user['timezone_id']}"
    """
    log(f"[{user['profile_name']}] Logged in. Current URL: {page.url}")
    tz = page.evaluate("Intl.DateTimeFormat().resolvedOptions().timeZone")
    lang = page.evaluate("navigator.language")
    vp = page.viewport_size or {"width": "?", "height": "?"}
    log(f"[{user['profile_name']}] Browser reports timezone={tz}, "
        f"language={lang}, viewport={vp['width']}x{vp['height']}")
    # TODO: add your own clicks / assertions here.


# --------------------------------------------------------------------------- #
# Per-profile runner
# --------------------------------------------------------------------------- #
def run_profile(pw: Playwright, user: dict, cfg: dict, fresh: bool, skip_pause: bool) -> str:
    """Run the full flow for one profile. Returns 'PASS', 'FAIL' or 'SKIPPED'."""
    name = user["profile_name"]
    session_path = resolve_session_path(user)

    banner(f"Profile: {name}")
    if not skip_pause:
        try:
            input(f"Switch network/VPN if needed, then press Enter to launch [{name}]... ")
        except EOFError:
            pass

    browser = launch_browser(pw, cfg)
    context: BrowserContext | None = None
    try:
        use_saved = session_path.exists() and not fresh
        if use_saved:
            log(f"Found saved session: {session_path.name}")
        elif fresh and session_path.exists():
            log("--fresh given: ignoring saved session.")

        try:
            context = browser.new_context(
                **build_context_options(user, session_path if use_saved else None)
            )
        except PlaywrightError as exc:
            # Corrupted / unreadable session file: fall back to a clean context.
            log(f"Could not load session file ({exc}); starting without it.")
            use_saved = False
            context = browser.new_context(**build_context_options(user, None))
        page = context.new_page()

        authenticated = False
        if use_saved:
            authenticated = session_is_valid(page, cfg)
            if authenticated:
                log("Saved session is valid - login form skipped.")
            else:
                log("Saved session expired or invalid - logging in again.")
                context.clear_cookies()

        if not authenticated:
            authenticated = perform_login(page, context, user, cfg, session_path)

        if not authenticated:
            return "FAIL"

        run_post_login_actions(page, user)
        # Refresh the saved state in case the app rotated tokens during the run.
        save_session(context, session_path)
        log(f"[{name}] PASS")
        return "PASS"

    except AssertionError as exc:
        log(f"[{name}] Assertion failed: {exc}")
        return "FAIL"
    except (PlaywrightError, RuntimeError) as exc:
        log(f"[{name}] Error: {exc}")
        return "FAIL"
    finally:
        # Close quietly even if the user already closed the window.
        for closer in (context.close if context else None, browser.close):
            if closer:
                try:
                    closer()
                except PlaywrightError:
                    pass


# --------------------------------------------------------------------------- #
# Entry point
# --------------------------------------------------------------------------- #
def parse_args() -> argparse.Namespace:
    ap = argparse.ArgumentParser(description="Playwright multi-user E2E QA runner")
    group = ap.add_mutually_exclusive_group()
    group.add_argument("--all", action="store_true", help="run every profile in users.json")
    group.add_argument("--profile", action="append", metavar="NAME",
                       help="run a specific profile (repeatable)")
    group.add_argument("--list", action="store_true", help="list profiles and exit")
    ap.add_argument("--fresh", action="store_true",
                    help="ignore saved sessions and force a new login")
    ap.add_argument("--no-pause", action="store_true",
                    help="skip the network/VPN pause before each profile")
    ap.add_argument("--config", type=Path, default=DEFAULT_CONFIG, help="path to config.json")
    ap.add_argument("--users", type=Path, default=DEFAULT_USERS, help="path to users.json")
    return ap.parse_args()


def main() -> int:
    args = parse_args()
    try:
        cfg = load_config(args.config)
        users = load_users(args.users)

        if args.list:
            for u in users:
                print(u["profile_name"])
            return 0
        if args.all:
            selected = users
        elif args.profile:
            selected = select_users_from_args(users, args.profile)
        else:
            selected = select_users_interactively(users)
    except ConfigError as exc:
        print(f"Configuration error: {exc}", file=sys.stderr)
        return 2

    if not selected:
        print("No profiles selected. Bye.")
        return 0

    results: dict[str, str] = {}
    try:
        with sync_playwright() as pw:
            for user in selected:
                results[user["profile_name"]] = run_profile(
                    pw, user, cfg, fresh=args.fresh, skip_pause=args.no_pause
                )
    except KeyboardInterrupt:
        print("\nInterrupted by user.")

    banner("Summary")
    for name in (u["profile_name"] for u in selected):
        print(f"  {results.get(name, 'SKIPPED'):<8} {name}")
    return 0 if results and all(r == "PASS" for r in results.values()) \
        and len(results) == len(selected) else 1


if __name__ == "__main__":
    sys.exit(main())
