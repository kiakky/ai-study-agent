"""Explicit foreground launch commands; no secret inspection or automatic exposure."""
import argparse
import json
from pathlib import Path
import subprocess
import ssl
import sys
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen
import certifi

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "backend"))
from deployment import load_deployment


def command_for(service, deployment):
    if service == "api":
        return [sys.executable, "-m", "uvicorn", "api:app", "--app-dir", "backend",
                "--host", "127.0.0.1", "--port", "8000", "--no-access-log", "--no-server-header"]
    if service == "bot":
        if not deployment.mini_app_url:
            raise ValueError("Set mini_app_url in config/deployment.json before starting the integrated bot.")
        return [sys.executable, "backend/bot.py"]
    if service == "tunnel":
        if not deployment.mini_app_url:
            raise ValueError("Set your assigned public HTTPS URL in config/deployment.json first.")
        return [deployment.ngrok_executable, "http", "http://127.0.0.1:8000",
                "--url", deployment.mini_app_url, "--inspect=false"]
    raise ValueError("Unknown service")


def probe(base_url):
    """Read-only public checks. Never send Telegram auth or print response bodies."""
    tls_context = ssl.create_default_context(cafile=certifi.where())
    checks = [
        ("/", 200, "html"),
        ("/static/app.js", 200, "javascript"),
        ("/static/styles.css", 200, "css"),
        ("/static/icon.svg", 200, "svg"),
        ("/health", 200, "health"),
        ("/api/me", 401, "auth"),
        ("/api/quizzes", 401, "auth"),
        ("/api/grades", 401, "auth"),
        ("/api/attempts/1", 401, "auth"),
        ("/docs", 404, None),
        ("/redoc", 404, None),
        ("/openapi.json", 404, None),
        ("/__preview__/telegram.js", 404, None),
    ]
    good = True
    for path, expected, kind in checks:
        request = Request(base_url + path, headers={
            "User-Agent": "Mozilla/5.0 (Linux; Android 14) AppleWebKit/537.36 Chrome/130.0 Mobile Safari/537.36",
            "Accept": "text/html" if kind == "html" else "*/*",
            "ngrok-skip-browser-warning": "true",
        })
        try:
            try:
                response = urlopen(request, timeout=15, context=tls_context)
            except HTTPError as error:
                response = error
            with response:
                status = response.status
                content_type = response.headers.get("Content-Type", "")
                body = response.read(65536).decode("utf-8", errors="replace") if kind else ""
            passed = status == expected
            if passed and kind == "html":
                passed = '<title>AI Study Agent</title>' in body
                if not passed:
                    print("WARNING: expected Mini App HTML was replaced, possibly by a provider interstitial. Telegram first-launch testing is required.")
            elif passed and kind in ("javascript", "css", "svg"):
                passed = kind in content_type
            elif passed and kind in ("health", "auth"):
                try:
                    data = json.loads(body)
                    passed = data.get("service") == "AI Study Agent Mini App API" if kind == "health" else data.get("detail") == "Open the Mini App inside Telegram."
                except (ValueError, AttributeError):
                    passed = False
            good = good and passed
            print(f"{'PASS' if passed else 'FAIL'} {path}: HTTP {status}")
        except (URLError, TimeoutError, OSError):
            good = False
            print(f"FAIL {path}: connection or TLS failure; check the running API and tunnel.")
            break
    print("Read-only reachability checks passed; real Telegram acceptance is still required." if good else "Reachability checks incomplete or failed; do not mark the tunnel ready.")
    return good


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("service", choices=["api", "bot", "tunnel", "check"])
    parser.add_argument("--local", action="store_true", help="Check localhost instead of the configured HTTPS origin")
    args = parser.parse_args()
    try:
        deployment = load_deployment()
        if args.service == "check":
            url = "http://127.0.0.1:8000" if args.local else deployment.mini_app_url
            if not url:
                raise ValueError("No public URL configured; use check --local or set config/deployment.json.")
            return 0 if probe(url) else 1
        if args.local:
            parser.error("--local is only valid with check")
        command = command_for(args.service, deployment)
        # No shell interpolation, background services, PATH edits or saved secrets.
        return subprocess.call(command, cwd=ROOT)
    except (ValueError, FileNotFoundError) as error:
        if isinstance(error, FileNotFoundError):
            print("Executable not found. Install it manually or set its absolute path; see README.md.")
        else:
            print(str(error))
        return 1
    except KeyboardInterrupt:
        return 0


if __name__ == "__main__":
    raise SystemExit(main())
