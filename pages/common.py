"""Shared helpers for page modules: site_map lookups, navigation, delays."""

from __future__ import annotations

import time

from browser_factory import BrowserError, BrowserTimeoutError


class SiteMapError(Exception):
    """A page or selector the code needs is missing from site_map.json."""


def log(user: dict, msg: str) -> None:
    print(f"[{time.strftime('%H:%M:%S')}] [{user['profile_name']}] {msg}", flush=True)


def setting(site_map: dict, key: str, default: int) -> int:
    return int(site_map.get("settings", {}).get(key, default))


def page_url(site_map: dict, page_name: str) -> str:
    try:
        return site_map["pages"][page_name]["url"]
    except KeyError:
        raise SiteMapError(f"site_map.json: missing pages.{page_name}.url") from None


def sel(site_map: dict, page_name: str, key: str) -> str:
    """Return the CSS selector pages.<page_name>.selectors.<key>."""
    try:
        return site_map["pages"][page_name]["selectors"][key]
    except KeyError:
        raise SiteMapError(
            f"site_map.json: missing pages.{page_name}.selectors.{key}") from None


def action_pause(page, site_map: dict) -> None:
    """Wait settings.action_delay_ms between steps so you can follow along."""
    page.wait_for_timeout(setting(site_map, "action_delay_ms", 1500))


def goto(page, site_map: dict, page_name: str) -> None:
    """Navigate to a named page from site_map.json."""
    page.goto(page_url(site_map, page_name), wait_until="domcontentloaded")


def wait_visible(page, selector: str, timeout_ms: int) -> bool:
    """True if selector becomes visible within timeout_ms, else False."""
    try:
        page.locator(selector).first.wait_for(state="visible", timeout=timeout_ms)
        return True
    except BrowserTimeoutError:
        return False


def is_visible_now(page, selector: str) -> bool:
    """Non-waiting visibility check that is safe while the page is navigating."""
    try:
        return page.locator(selector).first.is_visible()
    except BrowserError:
        return False


def open_page(page, site_map: dict, user: dict, page_name: str) -> None:
    """Go to a page and wait for its 'page_ready' selector (if defined)."""
    log(user, f"opening '{page_name}' page")
    goto(page, site_map, page_name)
    ready = site_map["pages"][page_name].get("selectors", {}).get("page_ready")
    if ready:
        timeout = setting(site_map, "page_timeout_ms", 15000)
        if not wait_visible(page, ready, timeout):
            raise AssertionError(
                f"'{page_name}' page did not show page_ready '{ready}' within {timeout} ms "
                f"(current URL: {page.url})")
    action_pause(page, site_map)
