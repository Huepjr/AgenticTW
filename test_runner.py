#!/usr/bin/env python3
"""
Multi-user E2E QA runner for Tent World.

Architecture (strict separation of concerns):
  site_map.json        all URLs + CSS selectors (local, gitignored)
  users.json           test accounts + viewport/locale/timezone/UA/proxy (local, gitignored)
  browser_factory.py   the ONLY module that imports playwright / launches browsers
  pages/*.py           page actions with signature action(page, site_map, user)
  test_runner.py       loads config, picks profiles, runs the SCENARIO (this file)

Usage:
  python test_runner.py                       # interactive menu
  python test_runner.py --all                 # every profile
  python test_runner.py --profile NAME [--profile NAME2]
  python test_runner.py --all --fresh         # ignore saved sessions
  python test_runner.py --all --no-pause      # skip the network/VPN pause
  python test_runner.py --list
"""

from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path
from typing import Any, Callable

from browser_factory import BrowserError, open_user_session, save_session, session_exists
from pages import LoginError, login_user, visit_building, visit_market
from pages.common import SiteMapError

BASE_DIR = Path(__file__).resolve().parent
DEFAULT_SITE_MAP = BASE_DIR / "site_map.json"
DEFAULT_USERS = BASE_DIR / "users.json"

# Actions run in order after login. Add/reorder page actions here.
# Each one must have the signature: action(page, site_map, user) -> None
SCENARIO: list[Callable[[Any, dict, dict], None]] = [
    visit_building,
    visit_market,
]

REQUIRED_PAGES = {"login": ["username_input", "password_input", "submit_button"],
                  "home": ["logged_in_indicator"]}
REQUIRED_USER_KEYS = ["profile_name", "username", "viewport", "locale",
                      "timezone_id", "session_file"]


class ConfigError(Exception):
    pass


# --------------------------------------------------------------------------- #
# Config loading
# --------------------------------------------------------------------------- #
def load_json(path: Path) -> Any:
    if not path.exists():
        example = path.with_name(path.stem + ".example.json")
        hint = (f"\n  Hint: copy {example.name} to {path.name} and fill in your values."
                if example.exists() else "")
        raise ConfigError(f"File not found: {path}{hint}")
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise ConfigError(f"Invalid JSON in {path.name}: {exc}") from exc


def load_site_map(path: Path) -> dict:
    sm = load_json(path)
    if not isinstance(sm, dict) or not isinstance(sm.get("pages"), dict):
        raise ConfigError(f"{path.name} must be an object with a 'pages' object.")
    sm.setdefault("settings", {})
    for name, page in sm["pages"].items():
        if not isinstance(page, dict) or "url" not in page:
            raise ConfigError(f"{path.name}: pages.{name} needs a 'url'.")
        page.setdefault("selectors", {})
    for name, keys in REQUIRED_PAGES.items():
        if name not in sm["pages"]:
            raise ConfigError(f"{path.name}: missing required page '{name}'.")
        missing = [k for k in keys if not sm["pages"][name]["selectors"].get(k)]
        if missing:
            raise ConfigError(f"{path.name}: pages.{name}.selectors missing {', '.join(missing)}")
    return sm


def load_users(path: Path) -> list[dict]:
    users = load_json(path)
    if not isinstance(users, list) or not users:
        raise ConfigError(f"{path.name} must be a non-empty JSON list.")
    seen: set[str] = set()
    for i, u in enumerate(users, 1):
        missing = [k for k in REQUIRED_USER_KEYS if k not in u]
        if missing:
            raise ConfigError(f"User #{i} in {path.name} missing: {', '.join(missing)}")
        if not {"width", "height"} <= set(u["viewport"]):
            raise ConfigError(f"User '{u['profile_name']}': viewport needs width and height.")
        if u["profile_name"] in seen:
            raise ConfigError(f"Duplicate profile_name '{u['profile_name']}'.")
        seen.add(u["profile_name"])
    return users


# --------------------------------------------------------------------------- #
# Profile selection
# --------------------------------------------------------------------------- #
def choose_interactively(users: list[dict]) -> list[dict]:
    print("\nAvailable test profiles:")
    for i, u in enumerate(users, 1):
        vp = u["viewport"]
        state = "session saved" if session_exists(u) else "no session"
        proxy = " +proxy" if u.get("proxy") else ""
        print(f"  [{i}] {u['profile_name']:<22} {vp['width']}x{vp['height']:<5} "
              f"{u['locale']:<6} {u['timezone_id']:<20} ({state}){proxy}")
    print("  [a] all    [q] quit")
    while True:
        choice = input("Select number(s) e.g. 1 or 1,3: ").strip().lower()
        if choice == "q":
            return []
        if choice == "a":
            return users
        try:
            idx = [int(x) for x in choice.replace(" ", "").split(",") if x]
            if idx and all(1 <= n <= len(users) for n in idx):
                return [users[n - 1] for n in idx]
        except ValueError:
            pass
        print("  Invalid selection.")


def choose_by_name(users: list[dict], names: list[str]) -> list[dict]:
    by_name = {u["profile_name"]: u for u in users}
    unknown = [n for n in names if n not in by_name]
    if unknown:
        raise ConfigError(f"Unknown profile(s): {', '.join(unknown)}. "
                          f"Available: {', '.join(by_name)}")
    return [by_name[n] for n in names]


# --------------------------------------------------------------------------- #
# Running one profile
# --------------------------------------------------------------------------- #
def run_profile(user: dict, site_map: dict, fresh: bool, pause: bool) -> str:
    name = user["profile_name"]
    print(f"\n{'=' * 60}\n  Profile: {name}\n{'=' * 60}", flush=True)
    if pause:
        try:
            input(f"Switch network/VPN if needed, then press Enter to launch [{name}]... ")
        except EOFError:
            pass
    try:
        # Browser, context, network and session loading are all hidden in the factory.
        with open_user_session(user, fresh=fresh) as (_browser, context, page):
            login_user(page, site_map, user)
            save_session(context, user)          # persist right after login
            for action in SCENARIO:
                action(page, site_map, user)
            # session is saved again automatically on a clean exit
        print(f"[{time.strftime('%H:%M:%S')}] [{name}] PASS", flush=True)
        return "PASS"
    except (LoginError, AssertionError, SiteMapError, BrowserError) as exc:
        msg = getattr(exc, "message", None) or str(exc)
        print(f"[{time.strftime('%H:%M:%S')}] [{name}] FAIL: {type(exc).__name__}: "
              f"{msg.splitlines()[0] if msg else ''}", flush=True)
        return "FAIL"


# --------------------------------------------------------------------------- #
# Entry point
# --------------------------------------------------------------------------- #
def parse_args() -> argparse.Namespace:
    ap = argparse.ArgumentParser(description="Tent World multi-user E2E QA runner")
    g = ap.add_mutually_exclusive_group()
    g.add_argument("--all", action="store_true", help="run every profile")
    g.add_argument("--profile", action="append", metavar="NAME", help="run one profile (repeatable)")
    g.add_argument("--list", action="store_true", help="list profiles and exit")
    ap.add_argument("--fresh", action="store_true", help="ignore saved sessions")
    ap.add_argument("--no-pause", action="store_true", help="skip the network/VPN pause")
    ap.add_argument("--site-map", type=Path, default=DEFAULT_SITE_MAP)
    ap.add_argument("--users", type=Path, default=DEFAULT_USERS)
    return ap.parse_args()


def main() -> int:
    args = parse_args()
    try:
        site_map = load_site_map(args.site_map)
        users = load_users(args.users)
        if args.list:
            print("\n".join(u["profile_name"] for u in users))
            return 0
        selected = (users if args.all else
                    choose_by_name(users, args.profile) if args.profile else
                    choose_interactively(users))
    except ConfigError as exc:
        print(f"Configuration error: {exc}", file=sys.stderr)
        return 2
    if not selected:
        print("No profiles selected.")
        return 0

    results: dict[str, str] = {}
    try:
        for user in selected:
            results[user["profile_name"]] = run_profile(
                user, site_map, fresh=args.fresh, pause=not args.no_pause)
    except KeyboardInterrupt:
        print("\nInterrupted.")

    print(f"\n{'=' * 60}\n  Summary\n{'=' * 60}")
    for u in selected:
        print(f"  {results.get(u['profile_name'], 'SKIPPED'):<8} {u['profile_name']}")
    ok = len(results) == len(selected) and all(r == "PASS" for r in results.values())
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
