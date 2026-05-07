"""Flask web UI for managing /app/config.yaml. Reads /app/state/status.json for live status."""
import json, os, hashlib, logging, tempfile, shutil
from pathlib import Path
from flask import Flask, render_template, request, redirect, url_for, jsonify, flash
import requests, yaml

CONFIG_PATH = Path(os.environ.get("CONFIG_PATH", "/app/config.yaml"))
STATUS_FILE = Path(os.environ.get("STATE_DIR", "/app/state")) / "status.json"

app = Flask(__name__)
app.secret_key = os.environ.get("FLASK_SECRET", "stock-monitor-dev-key")
log = logging.getLogger("webui")

def load_yaml():
    if not CONFIG_PATH.exists():
        return {"products": []}
    with CONFIG_PATH.open() as f:
        return yaml.safe_load(f) or {"products": []}

def save_yaml_atomic(data):
    # Write to /tmp (always writable inside container) then move into place.
    # shutil.move handles cross-filesystem moves and overwrites the bind-mounted target.
    fd, tmp_path = tempfile.mkstemp(prefix="config-", suffix=".yaml", dir="/tmp")
    try:
        with os.fdopen(fd, "w") as f:
            yaml.safe_dump(data, f, sort_keys=False, default_flow_style=False)
        shutil.move(tmp_path, str(CONFIG_PATH))
    except Exception:
        try:
            os.unlink(tmp_path)
        except OSError:
            pass
        raise

def load_status():
    try:
        return json.loads(STATUS_FILE.read_text())
    except Exception:
        return {}

def make_id(url):
    return hashlib.md5(url.encode()).hexdigest()[:10]

@app.route("/")
def index():
    cfg = load_yaml()
    status = load_status()
    products = cfg.get("products", []) or []
    for p in products:
        p["_id"] = p.get("id") or make_id(p["url"])
        p["_status"] = status.get(p["_id"], {})
    return render_template("index.html",
        products=products,
        topic=os.environ.get("NTFY_TOPIC", "(unset)"),
        server=os.environ.get("NTFY_SERVER", "https://ntfy.sh"))

def parse_form_to_product(form):
    mode = form.get("mode", "shopify")
    p = {
        "id": form.get("id") or make_id(form["url"]),
        "name": form["name"].strip(),
        "url": form["url"].strip(),
        "mode": mode,
        "interval_sec": max(15, int(form.get("interval_sec") or 60)),
        "priority": form.get("priority", "default"),
    }
    if mode == "html":
        p["sold_out_text"] = (form.get("sold_out_text") or "Sold out").strip()
        p["case_sensitive"] = form.get("case_sensitive") == "on"
    return p

@app.post("/add")
def add():
    cfg = load_yaml(); products = cfg.get("products", []) or []
    p = parse_form_to_product(request.form)
    if any(x.get("id") == p["id"] for x in products):
        flash(f"Product with id {p['id']} already exists", "error")
    else:
        products.append(p); cfg["products"] = products; save_yaml_atomic(cfg)
        flash(f'Added "{p["name"]}"', "success")
    return redirect(url_for("index"))

@app.post("/edit/<pid>")
def edit(pid):
    cfg = load_yaml(); products = cfg.get("products", []) or []
    new = parse_form_to_product(request.form)
    new["id"] = pid
    cfg["products"] = [new if (x.get("id") == pid or make_id(x["url"]) == pid) else x for x in products]
    save_yaml_atomic(cfg)
    flash(f'Updated "{new["name"]}"', "success")
    return redirect(url_for("index"))

@app.post("/delete/<pid>")
def delete(pid):
    cfg = load_yaml(); products = cfg.get("products", []) or []
    cfg["products"] = [x for x in products if x.get("id") != pid and make_id(x["url"]) != pid]
    save_yaml_atomic(cfg)
    flash("Product removed", "success")
    return redirect(url_for("index"))

@app.post("/test/<pid>")
def test(pid):
    """Run a one-shot check against the product RIGHT NOW and return JSON for the UI."""
    cfg = load_yaml()
    products = cfg.get("products", []) or []
    target = next((x for x in products if (x.get("id") == pid or make_id(x["url"]) == pid)), None)
    if not target:
        return jsonify(ok=False, error="not found"), 404
    s = requests.Session(); s.headers.update({"User-Agent": "stock-monitor-ui/2.0"})
    try:
        if target.get("mode") == "shopify":
            j = s.get(target["url"].rstrip("/") + ".js", timeout=20).json()
            variants = j.get("variants", []) or []
            avail = sum(1 for v in variants if v.get("available"))
            return jsonify(ok=True, in_stock=avail > 0, detail=f"variants={len(variants)} available={avail}")
        else:
            body = s.get(target["url"], timeout=20).text
            needle = target.get("sold_out_text", "Sold out")
            cs = target.get("case_sensitive", False)
            sold_out = (needle if cs else needle.lower()) in (body if cs else body.lower())
            return jsonify(ok=True, in_stock=not sold_out, detail=f"sold_out_match={sold_out}")
    except Exception as e:
        return jsonify(ok=False, error=str(e)), 200

@app.post("/test-notification")
def test_notification():
    topic = os.environ.get("NTFY_TOPIC")
    server = os.environ.get("NTFY_SERVER", "https://ntfy.sh").rstrip("/")
    if not topic:
        return jsonify(ok=False, error="NTFY_TOPIC not set"), 400
    try:
        r = requests.post(f"{server}/{topic}", data=b"This is a test from your Stock Monitor UI.",
            headers={"Title": "Stock Monitor — UI test", "Tags": "white_check_mark", "Priority": "default"},
            timeout=15)
        return jsonify(ok=r.ok, status=r.status_code)
    except Exception as e:
        return jsonify(ok=False, error=str(e)), 200

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=8088, debug=False)
