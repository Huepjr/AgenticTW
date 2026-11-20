# AgenticTW: Tent World E2E QA Runner

Local multi-user end-to-end tests (Playwright, Python) for login, session
persistence, localization and page checks on Tent World.

## Layout

| File | Role |
|---|---|
| `site_map.json` | All URLs + CSS selectors, grouped by page. **Local only** (gitignored). Template: `site_map.example.json` |
| `users.json` | Test accounts + viewport / locale / timezone / user agent / proxy / session file. **Local only**. Template: `users.example.json` |
| `browser_factory.py` | The **only** module that imports Playwright, launches browsers, creates contexts, sets network/proxy and loads/saves sessions |
| `pages/` | One module per page. Every action is `action(page, site_map, user)` |
| `test_runner.py` | Loads config, selects profiles, VPN pause, runs `SCENARIO` |
| `sessions/` | Saved logins (cookies). **Local only**, treat like passwords |

### Rules
1. Never `import playwright` outside `browser_factory.py`. Page modules that need
   `expect`, `BrowserError` or `BrowserTimeoutError` import them from `browser_factory`.
2. Never hardcode a URL or selector in Python: add it to `site_map.json` under
   `pages.<page>.url` / `pages.<page>.selectors.<name>` and read it with
   `pages.common.sel(site_map, "<page>", "<name>")`.
3. To change engine, launch flags or network routing, edit only `browser_factory.py`
   (`HEADLESS`, `SLOW_MO_MS`, `LAUNCH_ARGS`, `_launch_browser`).

## First run
```bash
cp site_map.example.json site_map.json   # then fill in your URLs/selectors
cp users.example.json users.json         # then fill in your accounts
python test_runner.py
```

## site_map.json
- `settings`: `captcha_timeout_ms`, `action_delay_ms` (pause between steps),
  optional `session_check_timeout_ms`, `page_timeout_ms`.
- Required pages: `login` (`username_input`, `password_input`, `submit_button`) and
  `home` (the page after login, with `logged_in_indicator`).
- Any other page may define `page_ready`: a selector that must be visible for the page to count as loaded.

## users.json extras
- `"password": ""` makes the runner prompt for it in the terminal.
- `"proxy"`: `null`, `"http://host:port"`, or
  `{"server": "http://host:port", "username": "...", "password": "...", "bypass": "localhost"}`.

## Adding a page
1. Add `pages.<name>` with `url` + `selectors` to `site_map.json` (and the example file).
2. Create `pages/<name>.py` with `def visit_<name>(page, site_map, user)`.
3. Export it in `pages/__init__.py` and append it to `SCENARIO` in `test_runner.py`.

## CLI
```
python test_runner.py [--all | --profile NAME ... | --list] [--fresh] [--no-pause]
                      [--site-map PATH] [--users PATH]
```
