"""Building page actions. Selectors live in site_map.json -> pages.building."""

from __future__ import annotations

from .common import log, open_page


def visit_building(page, site_map: dict, user: dict) -> None:
    open_page(page, site_map, user, "building")
    log(user, f"building page OK ({page.url})")
    # TODO: add building checks once selectors are extracted from your HTML snippet, e.g.
    #   page.locator(sel(site_map, "building", "some_button")).click()
    #   action_pause(page, site_map)
