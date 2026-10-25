"""Check what the SmallTV will see from CLIProxyAPI, using the values in secrets.env.

Lists the Claude/Codex accounts, then fetches each account's 5-hour and weekly
usage the same way the device will: GET /auth-files, then POST /api-call with
the provider's usage URL. Prints usage only, never the management key or tokens.

CLIProxyAPI bans an IP for 30 minutes after 5 failed key attempts, so this stops
at the first auth error instead of retrying.

    python3 -I tools/cpa_probe.py
"""
import json
import sys
import urllib.error
import urllib.request
from datetime import datetime, timezone
from pathlib import Path

SECRETS = Path(__file__).resolve().parent.parent / "secrets.env"

USAGE = {
    "claude": ("https://api.anthropic.com/api/oauth/usage", {
        "Authorization": "Bearer $TOKEN$",
        "anthropic-beta": "oauth-2025-04-20",
        "User-Agent": "claude-cli/2.1.280 (external, cli)",
    }),
    "codex": ("https://chatgpt.com/backend-api/wham/usage", {
        "Authorization": "Bearer $TOKEN$",
        "User-Agent": "codex-tui/0.149.1",
    }),
}


def load_secrets():
    if not SECRETS.exists():
        sys.exit(f"missing {SECRETS} (copy secrets.example.env and fill it in)")
    vals = {}
    for line in SECRETS.read_text().splitlines():
        line = line.strip()
        if line and not line.startswith("#") and "=" in line:
            k, v = line.split("=", 1)
            vals[k.strip()] = v.strip()
    for k in ("CPA_URL", "CPA_KEY"):
        if not vals.get(k):
            sys.exit(f"{k} is empty in {SECRETS}")
    return vals["CPA_URL"].rstrip("/"), vals["CPA_KEY"]


def call(base, key, method, path, body=None):
    req = urllib.request.Request(base + "/v0/management" + path, method=method,
                                 data=json.dumps(body).encode() if body else None)
    req.add_header("Authorization", "Bearer " + key)
    req.add_header("Content-Type", "application/json")
    # Cloudflare's browser check rejects Python's default User-Agent (error 1010);
    # the firmware sends this same one.
    req.add_header("User-Agent", "smalltv-cpa/0.1")
    try:
        with urllib.request.urlopen(req, timeout=20) as r:
            raw = r.read()
            return r.status, raw
    except urllib.error.HTTPError as e:
        raw = e.read()
        if raw.startswith(b"error code:"):    # Cloudflare block page; never reached CLIProxyAPI
            sys.exit(f"blocked by Cloudflare on {path}: HTTP {e.code} {raw[:60].decode(errors='replace')}")
        if e.code in (401, 403):
            sys.exit(f"auth failed on {path}: HTTP {e.code} {raw[:200].decode(errors='replace')}\n"
                     "Stopping so the IP isn't banned. Check CPA_KEY and remote-management.allow-remote.")
        return e.code, raw


def reset_in(value):
    """Human countdown from an ISO timestamp, epoch seconds, or seconds-from-now."""
    if value is None:
        return "?"
    now = datetime.now(timezone.utc)
    if isinstance(value, str):
        try:
            secs = (datetime.fromisoformat(value.replace("Z", "+00:00")) - now).total_seconds()
        except ValueError:
            return value
    elif value > 1e9:
        secs = value - now.timestamp()
    else:
        secs = value
    secs = max(0, int(secs))
    d, h, m = secs // 86400, secs % 86400 // 3600, secs % 3600 // 60
    return f"{d}D{h:02d}H" if d else f"{h}H{m:02d}M" if h else f"{m}M"


def summarize(provider, payload):
    if provider == "claude":
        out = []
        for k, name in (("five_hour", "5H"), ("seven_day", "WEEK")):
            w = payload.get(k) or {}
            out.append(f"{name} {w.get('utilization', '?')}% reset {reset_in(w.get('resets_at'))}")
        return ", ".join(out)
    rl = payload.get("rate_limit") or {}
    out = []
    for k in ("primary_window", "secondary_window"):
        w = rl.get(k) or {}
        secs = w.get("limit_window_seconds")
        name = {18000: "5H", 604800: "WEEK"}.get(secs, f"{k}({secs}s)")
        reset = w.get("reset_at") if w.get("reset_at") is not None else w.get("reset_after_seconds")
        out.append(f"{name} {w.get('used_percent', '?')}% reset {reset_in(reset)}")
    return ", ".join(out)


def main():
    base, key = load_secrets()
    status, raw = call(base, key, "GET", "/auth-files")
    print(f"/auth-files: HTTP {status}, {len(raw)} bytes")
    data = json.loads(raw)
    files = data.get("files", data) if isinstance(data, dict) else data
    if files:
        print("fields per account:", ", ".join(sorted(files[0].keys())))
    for f in files:
        provider = str(f.get("provider") or f.get("type") or "").lower()
        label = f.get("label") or f.get("email") or f.get("name") or ""
        idx = f.get("auth_index") or f.get("authIndex")
        off = " (disabled)" if f.get("disabled") else ""
        line = f"- {provider:10} {label}{off}"
        if provider in USAGE and idx and not f.get("disabled"):
            url, header = USAGE[provider]
            header = dict(header)
            acct = f.get("chatgpt_account_id") or (f.get("metadata") or {}).get("chatgpt_account_id")
            if provider == "codex" and acct:
                header["Chatgpt-Account-Id"] = acct
            s, r = call(base, key, "POST", "/api-call",
                        {"auth_index": idx, "method": "GET", "url": url, "header": header})
            res = json.loads(r) if s == 200 else {}
            code = res.get("status_code", res.get("statusCode", s))
            body = res.get("body", res.get("bodyText"))
            if isinstance(body, str):
                try:
                    body = json.loads(body)
                except ValueError:
                    pass
            line += f"\n    api-call HTTP {s}, upstream {code}, {len(r)} bytes"
            if isinstance(body, dict) and 200 <= int(code or 0) < 300:
                line += "\n    " + summarize(provider, body)
            else:
                line += f"\n    body: {str(body)[:160]}"
        print(line)


if __name__ == "__main__":
    main()
