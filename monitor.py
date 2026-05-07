#!/usr/bin/env python3
"""Generic stock monitor.

Polls product URLs at a configurable interval. For Shopify storefronts it uses
/products/<handle>.js. Otherwise falls back to fetching the HTML and looking for
a configurable sold-out regex. Sends a notification via console, ntfy.sh,
Discord webhook, or generic webhook.
"""
import os
import re
import sys
import time
import json
import signal
import logging
import hashlib
import threading
from dataclasses import dataclass, field
from typing import Optional, List, Dict, Any
from urllib.parse import urlparse, urlunparse

import requests
import yaml

LOG_LEVEL = os.environ.get("LOG_LEVEL", "INFO").upper()
logging.basicConfig(level=LOG_LEVEL, format="%(asctime)s [%(levelname)s] %(name)s: %(message)s")
log = logging.getLogger("stock-monitor")

CONFIG_PATH = os.environ.get("CONFIG_PATH", "/app/config.yaml")
STATE_PATH = os.environ.get("STATE_PATH", "/app/state/state.json")
USER_AGENT = os.environ.get("USER_AGENT", "Mozilla/5.0 (compatible; StockMonitor/1.0)")

stop_event = threading.Event()


@dataclass
class Product:
    name: str
    url: str
    mode: str = "auto"
    sold_out_regex: str = r"sold\s*out"
    interval_seconds: int = 60
    rearm: bool = False
    notify: List[str] = field(default_factory=list)


@dataclass
class Notifiers:
    console: bool = True
    ntfy_topic: Optional[str] = None
    ntfy_server: str = "https://ntfy.sh"
    discord_webhook: Optional[str] = None
    generic_webhook: Optional[str] = None


def load_config(path: str):
    with open(path, "r") as f:
        cfg = yaml.safe_load(f) or {}
    n = cfg.get("notifiers", {}) or {}
    notifiers = Notifiers(
        console=n.get("console", True),
        ntfy_topic=os.environ.get("NTFY_TOPIC", n.get("ntfy_topic")),
        ntfy_server=os.environ.get("NTFY_SERVER", n.get("ntfy_server", "https://ntfy.sh")),
        discord_webhook=os.environ.get("DISCORD_WEBHOOK", n.get("discord_webhook")),
        generic_webhook=os.environ.get("GENERIC_WEBHOOK", n.get("generic_webhook")),
    )
    products = []
    for p in cfg.get("products", []):
        products.append(Product(
            name=p["name"],
            url=p["url"],
            mode=p.get("mode", "auto"),
            sold_out_regex=p.get("sold_out_regex", r"sold\s*out"),
            interval_seconds=int(p.get("interval_seconds", cfg.get("default_interval_seconds", 60))),
            rearm=bool(p.get("rearm", False)),
            notify=p.get("notify", []),
        ))
    if not products:
        log.error("No products configured in %s", path)
        sys.exit(2)
    return notifiers, products


def load_state() -> Dict[str, Any]:
    try:
        with open(STATE_PATH, "r") as f:
            return json.load(f)
    except FileNotFoundError:
        return {}
    except Exception as e:
        log.warning("Could not read state file: %s", e)
        return {}


def save_state(state: Dict[str, Any]):
    os.makedirs(os.path.dirname(STATE_PATH), exist_ok=True)
    tmp = STATE_PATH + ".tmp"
    with open(tmp, "w") as f:
        json.dump(state, f, indent=2)
    os.replace(tmp, STATE_PATH)


def product_key(p: Product) -> str:
    return hashlib.sha1(f"{p.name}|{p.url}".encode("utf-8")).hexdigest()[:12]


def to_shopify_json_url(url: str) -> Optional[str]:
    u = urlparse(url)
    parts = u.path.split("/")
    if "products" not in parts:
        return None
    i = parts.index("products")
    if i + 1 >= len(parts) or not parts[i + 1]:
        return None
    handle = parts[i + 1].split("?")[0]
    new_path = "/".join(parts[: i + 1] + [handle]) + ".js"
    return urlunparse((u.scheme, u.netloc, new_path, "", "", ""))


def check_shopify(session, url):
    json_url = to_shopify_json_url(url)
    if not json_url:
        return None, "not a shopify product url"
    r = session.get(json_url, timeout=20, headers={"User-Agent": USER_AGENT})
    if r.status_code != 200:
        return None, f"shopify json HTTP {r.status_code}"
    data = r.json()
    variants = data.get("variants", []) or []
    available = [v for v in variants if v.get("available")]
    return (len(available) > 0), f"variants={len(variants)} available={len(available)}"


def check_html(session, url, sold_out_regex):
    r = session.get(url, timeout=25, headers={"User-Agent": USER_AGENT})
    if r.status_code != 200:
        return None, f"html HTTP {r.status_code}"
    text = r.text.lower()
    has_sold_out = re.search(sold_out_regex, text, re.IGNORECASE) is not None
    return (not has_sold_out), f"htmlSoldOutMatch={has_sold_out}"


def check_product(session, p):
    if p.mode in ("auto", "shopify"):
        result = check_shopify(session, p.url)
        if result[0] is not None:
            return ("in_stock" if result[0] else "sold_out", "shopify:" + result[1])
        if p.mode == "shopify":
            return ("error", result[1])
    result = check_html(session, p.url, p.sold_out_regex)
    if result[0] is None:
        return ("error", result[1])
    return ("in_stock" if result[0] else "sold_out", "html:" + result[1])


def notify_all(notifiers, product, status, detail):
    title = f"[STOCK] {product.name}: {status.upper()}"
    body = f"{product.url}\n{detail}"
    channels = product.notify or ["all"]
    if notifiers.console and ("all" in channels or "console" in channels):
        log.warning("ALERT %s -- %s", title, body.replace("\n", " | "))
    if notifiers.ntfy_topic and ("all" in channels or "ntfy" in channels):
        try:
            requests.post(
                f"{notifiers.ntfy_server.rstrip('/')}/{notifiers.ntfy_topic}",
                data=body.encode("utf-8"),
                headers={
                    "Title": title,
                    "Priority": "urgent" if status == "in_stock" else "default",
                    "Tags": "package" if status == "in_stock" else "no_entry",
                    "Click": product.url,
                },
                timeout=15,
            )
        except Exception as e:
            log.error("ntfy notify failed: %s", e)
    if notifiers.discord_webhook and ("all" in channels or "discord" in channels):
        try:
            requests.post(notifiers.discord_webhook, json={"content": f"**{title}**\n{body}"}, timeout=15)
        except Exception as e:
            log.error("discord notify failed: %s", e)
    if notifiers.generic_webhook and ("all" in channels or "webhook" in channels):
        try:
            requests.post(notifiers.generic_webhook, json={
                "product": product.name, "url": product.url, "status": status, "detail": detail,
            }, timeout=15)
        except Exception as e:
            log.error("generic webhook notify failed: %s", e)


def run():
    notifiers, products = load_config(CONFIG_PATH)
    state = load_state()
    session = requests.Session()
    next_due = {product_key(p): 0.0 for p in products}
    log.info("Monitoring %d product(s). State file: %s", len(products), STATE_PATH)
    for p in products:
        log.info("  - %s [%s] every %ss", p.name, p.mode, p.interval_seconds)
    while not stop_event.is_set():
        now = time.time()
        for p in products:
            key = product_key(p)
            if now < next_due[key]:
                continue
            try:
                status, detail = check_product(session, p)
            except Exception as e:
                status, detail = "error", f"exception: {e}"
            log.info("[%s] %s (%s)", p.name, status, detail)
            prev = state.get(key, {})
            prev_status = prev.get("status")
            already_alerted = prev.get("alerted_in_stock", False)
            if status == "in_stock" and (not already_alerted or (p.rearm and prev_status == "sold_out")):
                notify_all(notifiers, p, status, detail)
                prev["alerted_in_stock"] = True
            if status == "sold_out" and p.rearm:
                prev["alerted_in_stock"] = False
            prev["status"] = status
            prev["detail"] = detail
            prev["last_checked"] = int(now)
            prev["name"] = p.name
            prev["url"] = p.url
            state[key] = prev
            save_state(state)
            next_due[key] = now + p.interval_seconds
        sleep_until = min(next_due.values()) if next_due else now + 5
        while not stop_event.is_set() and time.time() < sleep_until:
            time.sleep(1)


def handle_signal(signum, frame):
    log.info("Received signal %s, shutting down", signum)
    stop_event.set()


if __name__ == "__main__":
    signal.signal(signal.SIGTERM, handle_signal)
    signal.signal(signal.SIGINT, handle_signal)
    run()
