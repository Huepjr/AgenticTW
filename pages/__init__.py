"""
Page action modules. Every public action has the signature:

    action(page, site_map, user) -> None

They only use the `page` object they are given and never import playwright or
know how the browser / network was set up (that is browser_factory.py's job).
"""
from .building import visit_building
from .login import LoginError, login_user
from .market import visit_market

__all__ = ["LoginError", "login_user", "visit_building", "visit_market"]
