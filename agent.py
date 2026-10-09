"""Autonomous task worker: Goal -> Plan -> Execute -> Observe -> Adapt -> Verify -> Complete.

Usage:  python agent.py "Find the latest invoice from Acme Corp, extract the amount and due date,
                         enter it into the internal system, and tell me once it is done."
"""
import json
import os
import re
import subprocess
import sys
import time
import urllib.request

from google import genai
from google.genai import types

from tools import BASE_URL, EVIDENCE_DIR, ROOT, Toolbox, declarations

MODEL = os.environ.get("GEMINI_MODEL", "gemini-2.5-flash")
MAX_STEPS = 25
STEP_DELAY = 8  # seconds between LLM calls, to stay under free-tier rate limits
HEADLESS = os.environ.get("HEADLESS", "0") == "1"

DEFAULT_GOAL = ("Find the latest invoice from Acme Corp, extract the amount and due date, "
                "enter it into our internal system, and tell me once it is done.")

# Only this block is task/environment specific. Swap it to point the agent at a new environment.
ENVIRONMENT = f"""ENVIRONMENT
- Invoice files (text) are in the folder 'invoices/'.
- The internal finance web app is at {BASE_URL} (page '/' adds a payable, '/records' lists saved payables).
- You can only use the provided tools. You cannot see the screen, only text from browser_read_page."""

SYSTEM_PROMPT = f"""You are an autonomous AI worker completing office tasks on a computer.
Work out the required steps yourself; never ask the user for steps you can discover.

{ENVIRONMENT}

OPERATING RULES
1. Plan briefly, then act. After EVERY tool result, read it carefully and decide the next action.
2. Do not guess. Open/read the real data (e.g. read every candidate file) before deciding which is correct.
   'Latest' means the most recent invoice DATE inside the document, not the file name.
3. Use 'remember' to store key facts (source file, vendor, amount, due date) as soon as you find them.
4. Convert values to what the target system requires. If the system rejects input, read the error,
   fix the input and retry. If the same problem persists after 2 fixes, ask the user for clarification.
5. If a server error says nothing was saved, retry; never assume success.
6. BEFORE submitting any form, call ask_user with kind='approval' stating exactly what will be submitted.
   If the user declines, do not submit; ask what to change.
7. AFTER submitting, VERIFY independently: open '/records' and confirm a row with the exact vendor, amount
   and due date exists. If it is missing, fix it and try again. Only then call finish.
8. If the task is ambiguous (e.g. two vendors match) or you cannot proceed safely, call ask_user.
9. finish(summary) must include: the source file used, the values entered, and the evidence seen on '/records'.
10. You have a small request budget, so be efficient. Call SEVERAL independent tools in the SAME turn
    (e.g. read all invoice files at once, store all facts with remember at once, fill all form fields at once).
    Do not narrate. Ask for approval in one turn and click submit in a LATER turn."""


def ensure_app():
    def up():
        try:
            urllib.request.urlopen(BASE_URL + "/api/records", timeout=1)
            return True
        except Exception:
            return False

    if up():
        print("[harness] mock app already running")
        return None
    proc = subprocess.Popen([sys.executable, str(ROOT / "mock_app" / "app.py")],
                            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    for _ in range(30):
        if up():
            print("[harness] mock app started")
            return proc
        time.sleep(0.5)
    raise RuntimeError("Mock app failed to start")


def call_llm(client, contents, config):
    for attempt in range(8):
        try:
            return client.models.generate_content(model=MODEL, contents=contents, config=config)
        except Exception as e:  # rate limits / transient server errors -> back off and retry
            msg = str(e)
            if any(s in msg for s in ("429", "RESOURCE_EXHAUSTED", "503", "UNAVAILABLE", "500")):
                if "PerDay" in msg or "per day" in msg.lower():
                    raise RuntimeError("Daily free quota for this model is used up. Try another model "
                                       "(set GEMINI_MODEL) or a key from a new project.\n" + msg[:400])
                m = re.search(r"retry in ([\d.]+)s", msg) or re.search(r"'retryDelay': '([\d.]+)s'", msg)
                wait = int(float(m.group(1))) + 3 if m else 20 * (attempt + 1)
                print(f"[harness] LLM busy/rate-limited ({msg[:120].replace(chr(10), ' ')}) "
                      f"-> retrying in {wait}s ...")
                time.sleep(wait)
                continue
            raise
    raise RuntimeError("LLM unavailable after retries")


def main():
    goal = " ".join(sys.argv[1:]) or DEFAULT_GOAL
    client = genai.Client(api_key=os.environ.get("GEMINI_API_KEY"))
    config = types.GenerateContentConfig(
        system_instruction=SYSTEM_PROMPT,
        tools=declarations(),
        temperature=0.2,
        automatic_function_calling=types.AutomaticFunctionCallingConfig(disable=True),
    )
    app_proc = ensure_app()
    tb = Toolbox(headless=HEADLESS)
    log, final_summary, nudges = [], None, 0
    contents = [types.Content(role="user", parts=[types.Part(text=f"TASK: {goal}")])]
    print(f"\n=== GOAL ===\n{goal}\n(model: {MODEL})\n")

    try:
        for step in range(1, MAX_STEPS + 1):
            resp = call_llm(client, contents, config)
            cand = resp.candidates[0] if resp.candidates else None
            if not cand or not cand.content or not cand.content.parts:
                contents.append(types.Content(role="user", parts=[types.Part(text="Continue. Use a tool.")]))
                continue
            content = cand.content
            contents.append(content)  # keep the model turn intact (preserves any reasoning signatures)

            for p in content.parts:
                if p.text and not getattr(p, "thought", False):
                    print(f"[step {step}] agent: {p.text.strip()}")
            calls = [p.function_call for p in content.parts if p.function_call]

            if not calls:
                nudges += 1
                if nudges > 3:
                    final_summary = "Stopped: agent did not take an action."
                    break
                contents.append(types.Content(role="user", parts=[types.Part(
                    text="Act using tools. If everything is verified, call finish.")]))
                continue

            results = []
            for c in calls:
                args = dict(c.args or {})
                if c.name == "finish":
                    final_summary = args.get("summary", "")
                    log.append({"step": step, "tool": "finish", "input": args})
                    break
                try:
                    out = getattr(tb, c.name)(**args)
                except Exception as e:   # never crash: the error is an observation for the LLM
                    out = f"ERROR: {type(e).__name__}: {e}"
                out = str(out)[:3000]
                print(f"[step {step}] {c.name}({json.dumps(args)[:150]}) -> {out[:160].replace(chr(10), ' | ')}")
                log.append({"step": step, "tool": c.name, "input": args, "output": out})
                results.append(types.Part.from_function_response(name=c.name, response={"result": out}))

            if final_summary is not None:
                break
            contents.append(types.Content(role="user", parts=results))
            time.sleep(STEP_DELAY)
        else:
            final_summary = "Stopped: reached max steps without finishing."

        # ---------- independent verification + evidence (outside the LLM) ----------
        try:
            records = json.loads(urllib.request.urlopen(BASE_URL + "/api/records", timeout=3).read())
        except Exception as e:
            records = f"could not read: {e}"
        EVIDENCE_DIR.mkdir(exist_ok=True)
        try:
            tb.page.goto(BASE_URL + "/records")
            tb.page.screenshot(path=str(EVIDENCE_DIR / "records.png"), full_page=True)
        except Exception as e:
            print(f"[harness] screenshot failed: {e}")
        (ROOT / "run_log.json").write_text(json.dumps(
            {"goal": goal, "model": MODEL, "steps": log, "memory": tb.memory,
             "final_summary": final_summary, "system_records": records}, indent=2))

        print("\n=== AGENT SUMMARY ===\n" + str(final_summary))
        print("\n=== INDEPENDENT CHECK (system records, read by harness) ===")
        print(json.dumps(records, indent=2))
        print("\nEvidence saved: evidence/records.png, run_log.json")
    finally:
        tb.close()
        if app_proc:
            app_proc.terminate()


if __name__ == "__main__":
    main()