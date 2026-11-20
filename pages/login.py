"""Login page actions: reuse a saved session or log in with a manual CAPTCHA pause."""

from __future__ import annotations

import getpass
import time

from .common import action_pause, goto, is_visible_now, log, sel, setting, wait_visible

POLL_MS = 500


class LoginError(Exception):
    """Login could not be confirmed."""


def is_logged_in(page, site_map: dict, user: dict) -> bool:
    """Open the 'home' page; a valid session shows home.selectors.logged_in_indicator."""
    goto(page, site_map, "home")
    timeout = setting(site_map, "session_check_timeout_ms", 10000)
    return wait_visible(page, sel(site_map, "home", "logged_in_indicator"), timeout)


def _password(user: dict) -> str:
    return user.get("password") or getpass.getpass(
        f"Password for '{user['profile_name']}' ({user['username']}): ")


def _wait_for_manual_verification(page, site_map: dict, user: dict) -> bool:
    indicator = sel(site_map, "home", "logged_in_indicator")
    timeout_ms = setting(site_map, "captcha_timeout_ms", 120000)
    print("\n" + "=" * 70)
    print(f"  ACTION REQUIRED [{user['profile_name']}]: complete the CAPTCHA / manual")
    print(f"  verification in the browser window (timeout {timeout_ms // 1000}s)")
    print("=" * 70, flush=True)
    deadline = time.monotonic() + timeout_ms / 1000
    while time.monotonic() < deadline:
        if page.is_closed():
            raise LoginError("browser window was closed before login completed")
        if is_visible_now(page, indicator):
            return True
        page.wait_for_timeout(POLL_MS)
    return is_visible_now(page, indicator)


def login_user(page, site_map: dict, user: dict) -> None:
    """
    Ensure the user is authenticated. Skips the form if the saved session is
    still valid; otherwise fills the login form and waits for you to finish
    the CAPTCHA. Raises LoginError on failure.
    """
    if is_logged_in(page, site_map, user):
        log(user, "saved session valid - login form skipped")
        return

    log(user, "not logged in - using login form")
    page.context.clear_cookies()  # drop any stale/expired auth cookies
    goto(page, site_map, "login")
    page.locator(sel(site_map, "login", "username_input")).first.fill(user["username"])
    page.locator(sel(site_map, "login", "password_input")).first.fill(_password(user))
    action_pause(page, site_map)
    page.locator(sel(site_map, "login", "submit_button")).first.click()
    log(user, "credentials submitted")

    if not _wait_for_manual_verification(page, site_map, user):
        raise LoginError("logged_in_indicator never appeared before captcha_timeout_ms")
    log(user, "login confirmed")
