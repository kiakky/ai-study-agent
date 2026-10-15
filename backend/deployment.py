"""Non-secret deployment settings shared by the API, bot and launch scripts."""
from dataclasses import dataclass
import ipaddress
import json
from pathlib import Path
import re
from urllib.parse import urlsplit

ROOT = Path(__file__).resolve().parent.parent
CONFIG_PATH = ROOT / "config" / "deployment.json"


def validate_public_url(value: str) -> str:
    """Require a public HTTPS origin, never a credential-bearing or local URL."""
    if not isinstance(value, str) or not value or any(c.isspace() for c in value):
        raise ValueError("mini_app_url must be a public HTTPS origin.")
    try:
        parsed = urlsplit(value)
        host = parsed.hostname or ""
        port = parsed.port
    except ValueError:
        raise ValueError("mini_app_url must be a public HTTPS origin.") from None
    if (parsed.scheme != "https" or parsed.username is not None or parsed.password is not None
            or port not in (None, 443) or parsed.path not in ("", "/")
            or parsed.query or parsed.fragment or not re.fullmatch(
                r"[a-z0-9](?:[a-z0-9-]*[a-z0-9])?(?:\.[a-z0-9](?:[a-z0-9-]*[a-z0-9])?)+", host
            ) or host.endswith((".localhost", ".local", ".internal"))):
        raise ValueError("mini_app_url must use HTTPS and a public DNS hostname, with no path, query or fragment.")
    try:
        ipaddress.ip_address(host)
    except ValueError:
        return f"https://{host}"
    raise ValueError("mini_app_url must use a public DNS hostname, not an IP address.")


@dataclass(frozen=True)
class Deployment:
    mini_app_url: str = ""
    ngrok_executable: str = "ngrok"

    @property
    def allowed_hosts(self):
        hosts = ["127.0.0.1", "localhost", "[::1]"]
        if self.mini_app_url:
            hosts.append(urlsplit(self.mini_app_url).hostname)
        return hosts


def load_deployment(path: Path | None = None) -> Deployment:
    path = path or CONFIG_PATH
    if not path.exists():
        return Deployment()
    try:
        values = json.loads(path.read_text(encoding="utf-8-sig"))
        if not isinstance(values, dict) or set(values) - {"mini_app_url", "ngrok_executable"}:
            raise ValueError("Only mini_app_url and ngrok_executable belong in deployment.json; never add secrets.")
        url = values.get("mini_app_url", "")
        executable = values.get("ngrok_executable", "ngrok")
        if not isinstance(executable, str) or not executable.strip():
            raise ValueError("ngrok_executable must be an executable name or path.")
        if url != "":
            url = validate_public_url(url)
        return Deployment(url, executable)
    except (OSError, json.JSONDecodeError):
        raise ValueError("Cannot read config/deployment.json. Use the non-secret example format.") from None
