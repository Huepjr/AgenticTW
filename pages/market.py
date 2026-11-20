"""Market page actions. Selectors live in site_map.json -> pages.market."""

from __future__ import annotations

from .common import log, open_page


def visit_market(page, site_map: dict, user: dict) -> None:
    open_page(page, site_map, user, "market")
    log(user, f"market page OK ({page.url})")
    # TODO: add market checks once selectors are extracted from your HTML snippet.
