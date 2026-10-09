"""Mock 'internal finance system' used as the sandbox for the AI worker.

Deliberate failure modes (so the agent's recovery can be demonstrated):
  1. Amount must be a plain number (no $ or commas).
  2. Due date must be YYYY-MM-DD (the form does NOT say so; only the error does).
  3. The first otherwise-valid submission fails with a 503 "temporary error".
     Disable with FLAKY=0.
"""
import os
import re
from datetime import datetime

from flask import Flask, jsonify, redirect, render_template_string, request

app = Flask(__name__)
RECORDS = []
STATE = {"flaky_pending": os.environ.get("FLAKY", "1") == "1"}

LAYOUT = """
<!doctype html>
<html><head><meta charset="utf-8"><title>Finance Portal</title>
<style>
 body{font-family:Arial,sans-serif;max-width:640px;margin:30px auto;padding:0 16px}
 nav a{margin-right:14px} label{display:block;margin-top:12px;font-weight:bold}
 input{padding:6px;width:100%;box-sizing:border-box}
 button{margin-top:16px;padding:8px 16px}
 .err{background:#fde8e8;border:1px solid #e57373;padding:10px;margin-top:12px}
 .ok{background:#e8f5e9;border:1px solid #81c784;padding:10px;margin-top:12px}
 table{border-collapse:collapse;width:100%} td,th{border:1px solid #ccc;padding:6px}
</style></head><body>
<h2>Finance Portal</h2>
<nav><a href="/">Add Payable</a><a href="/records">View Records</a></nav><hr>
{{ body|safe }}
</body></html>
"""

FORM = """
<h3>Add Payable</h3>
{% if error %}<div class="err" id="error">{{ error }}</div>{% endif %}
<form method="post" action="/">
  <label for="vendor">Vendor</label>
  <input id="vendor" name="vendor" value="{{ v.get('vendor','') }}">
  <label for="amount">Amount (USD)</label>
  <input id="amount" name="amount" value="{{ v.get('amount','') }}">
  <label for="due_date">Due date</label>
  <input id="due_date" name="due_date" value="{{ v.get('due_date','') }}">
  <button type="submit" id="save">Save payable</button>
</form>
"""

RECORDS_TMPL = """
<h3>Payable Records</h3>
{% if added %}<div class="ok" id="notice">Record #{{ added }} saved.</div>{% endif %}
{% if not records %}<p>No records yet.</p>{% else %}
<table id="records"><tr><th>ID</th><th>Vendor</th><th>Amount (USD)</th><th>Due date</th></tr>
{% for r in records %}
<tr><td>{{ r.id }}</td><td>{{ r.vendor }}</td><td>{{ r.amount }}</td><td>{{ r.due_date }}</td></tr>
{% endfor %}</table>{% endif %}
"""


def page(body_template, **ctx):
    body = render_template_string(body_template, **ctx)
    return render_template_string(LAYOUT, body=body)


@app.route("/", methods=["GET", "POST"])
def add_payable():
    if request.method == "GET":
        return page(FORM, error=None, v={})

    v = {k: request.form.get(k, "").strip() for k in ("vendor", "amount", "due_date")}
    error = None
    if not v["vendor"]:
        error = "Vendor is required."
    elif not re.fullmatch(r"\d+(\.\d{1,2})?", v["amount"]):
        error = "Invalid amount: use digits with an optional decimal point only (no currency symbols or commas)."
    else:
        try:
            if not re.fullmatch(r"\d{4}-\d{2}-\d{2}", v["due_date"]):
                raise ValueError
            datetime.strptime(v["due_date"], "%Y-%m-%d")
        except ValueError:
            error = "Invalid due date: expected format YYYY-MM-DD."
    if error:
        return page(FORM, error=error, v=v), 400

    if STATE["flaky_pending"]:
        STATE["flaky_pending"] = False
        return page(FORM, error="503 Service temporarily unavailable. Nothing was saved. Please try again.", v=v), 503

    rec = {"id": len(RECORDS) + 1, "vendor": v["vendor"], "amount": v["amount"], "due_date": v["due_date"]}
    RECORDS.append(rec)
    return redirect(f"/records?added={rec['id']}")


@app.route("/records")
def records():
    return page(RECORDS_TMPL, records=RECORDS, added=request.args.get("added"))


@app.route("/api/records")
def api_records():
    """Used by the harness for independent verification (the agent is not told about it)."""
    return jsonify(RECORDS)


if __name__ == "__main__":
    app.run(host="127.0.0.1", port=5000, debug=False)
