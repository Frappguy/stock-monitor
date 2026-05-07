"""Polls product pages on a schedule and pushes notifications when stock returns.
Config (/app/config.yaml) is re-read at the top of every cycle so UI edits are picked up
without a container restart. Last-seen status per product is written to
/app/state/status.json so the web UI can render badges."""
import json, logging, os, time, hashlib
from pathlib import Path
import requests, yaml

CONFIG_PATH = Path(os.environ.get("CONFIG_PATH", "/app/config.yaml"))
STATE_DIR   = Path(os.environ.get("STATE_DIR",   "/app/state"))
STATE_FILE  = STATE_DIR / "state.json"
STATUS_FILE = STATE_DIR / "status.json"
LOG_LEVEL   = os.environ.get("LOG_LEVEL", "INFO").upper()

NTFY_TOPIC  = os.environ.get("NTFY_TOPIC")
NTFY_SERVER = os.environ.get("NTFY_SERVER", "https://ntfy.sh").rstrip("/")
DISCORD_WEBHOOK = os.environ.get("DISCORD_WEBHOOK")
GENERIC_WEBHOOK = os.environ.get("GENERIC_WEBHOOK")

logging.basicConfig(level=LOG_LEVEL, format="%(asctime)s [%(levelname)s] %(name)s: %(message)s")
log = logging.getLogger("stock-monitor")


def load_config():
    if not CONFIG_PATH.exists():
        return {"products": []}
    with CONFIG_PATH.open() as f:
        return yaml.safe_load(f) or {"products": []}


def load_json(path, default):
    try:
        return json.loads(path.read_text())
    except Exception:
        return default


def save_json_atomic(path, obj):
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(obj, indent=2))
    tmp.replace(path)


def check_shopify(url, session):
    j = session.get(url.rstrip("/") + ".js", timeout=20).json()
    variants = j.get("variants", []) or []
    available = sum(1 for v in variants if v.get("available"))
    in_stock = available > 0
    return in_stock, f"shopify:variants={len(variants)} available={available}"


def check_html(url, sold_out_text, case_sensitive, session):
    body = session.get(url, timeout=20).text
    needle = sold_out_text if case_sensitive else sold_out_text.lower()
    haystack = body if case_sensitive else body.lower()
    sold_out = needle in haystack
    return (not sold_out), f"html:sold_out_match={sold_out}"


def notify(title, message, click_url, priority="default"):
    if NTFY_TOPIC:
        try:
            requests.post(
                f"{NTFY_SERVER}/{NTFY_TOPIC}",
                data=message.encode("utf-8"),
                headers={
                    "Title": title,
                    "Click": click_url,
                    "Tags": "package,rotating_light",
                    "Priority": priority,
                },
                timeout=20,
            )
        except Exception as e:
            log.warning("ntfy send failed: %s", e)
    if DISCORD_WEBHOOK:
        try:
            requests.post(DISCORD_WEBHOOK, json={"content": f"**{title}**\n{message}\n{click_url}"}, timeout=20)
        except Exception as e:
            log.warning("discord send failed: %s", e)
    if GENERIC_WEBHOOK:
        try:
            requests.post(GENERIC_WEBHOOK, json={"title": title, "message": message, "url": click_url}, timeout=20)
        except Exception as e:
            log.warning("generic webhook failed: %s", e)


def check_one(p, session):
    try:
        if p.get("mode") == "shopify":
            return check_shopify(p["url"], session)
        return check_html(p["url"], p.get("sold_out_text", "Sold out"), p.get("case_sensitive", False), session)
    except Exception as e:
        return None, f"error:{e!s}"


def run():
    STATE_DIR.mkdir(parents=True, exist_ok=True)
    state  = load_json(STATE_FILE,  {})
    status = load_json(STATUS_FILE, {})
    session = requests.Session()
    session.headers.update({"User-Agent": "stock-monitor/2.0"})

    while True:
        cfg = load_config()
        products = cfg.get("products", []) or []
        log.info("Tick: %d product(s)", len(products))

        for p in products:
            pid = p.get("id") or hashlib.md5(p["url"].encode()).hexdigest()[:10]
            name = p.get("name", pid)
            in_stock, detail = check_one(p, session)
            now = int(time.time())
            label = "in_stock" if in_stock else ("error" if in_stock is None else "sold_out")
            log.info("[%s] %s (%s)", name, label, detail)

            status[pid] = {
                "name": name,
                "in_stock": in_stock,
                "detail": detail,
                "checked_at": now,
                "url": p.get("url"),
                "mode": p.get("mode", "html"),
            }

            if in_stock and not state.get(pid, {}).get("notified"):
                title = f"BACK IN STOCK: {name}"
                msg   = f"{name} is available again.\n{detail}"
                notify(title, msg, p.get("url", ""), p.get("priority", "high"))
                state[pid] = {"notified": True, "at": now}
            elif in_stock is False and state.get(pid, {}).get("notified"):
                state[pid] = {"notified": False, "at": now}

        save_json_atomic(STATE_FILE,  state)
        save_json_atomic(STATUS_FILE, status)

        intervals = [int(p.get("interval_sec", 60)) for p in products] or [60]
        time.sleep(max(15, min(intervals)))


if __name__ == "__main__":
    run()
