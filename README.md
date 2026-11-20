# E2E QA Runner (Playwright, multi-user)

## Setup

**Windows PowerShell**
```powershell
cd path\to\e2e-qa-runner
python -m venv .venv
# If activation is blocked: Set-ExecutionPolicy -Scope CurrentUser RemoteSigned
.\.venv\Scripts\Activate.ps1
python -m pip install --upgrade pip
python -m pip install -r requirements.txt
python -m playwright install chromium
Copy-Item users.example.json users.json
```

**macOS / Linux (bash/zsh)**
```bash
cd path/to/e2e-qa-runner
python3 -m venv .venv
source .venv/bin/activate
python -m pip install --upgrade pip
python -m pip install -r requirements.txt
python -m playwright install chromium
# Linux only, if Chromium complains about missing system libraries:
#   sudo .venv/bin/python -m playwright install-deps chromium
cp users.example.json users.json
```

Then edit `config.json` (URL + selectors) and `users.json` (accounts).

## Run (with .venv activated)
```
python test_runner.py                         # interactive menu
python test_runner.py --all                   # all profiles
python test_runner.py --profile user1_desktop_es
python test_runner.py --all --fresh           # ignore saved sessions
python test_runner.py --all --no-pause        # skip the VPN pause
python test_runner.py --list
```

## Notes
- `post_login_url_or_selector`: a URL (`https://...`) is opened to check a saved
  session; a CSS selector is checked on `login_url` instead.
- Leave `password` as `""` in `users.json` to be prompted securely at runtime.
- `sessions/*.json` contain live auth cookies, so treat them like passwords.
- Moving to another machine: copy the folder **without** `.venv/` and rerun setup.
