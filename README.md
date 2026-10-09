# AI Task Worker (CentrAlign AI - AI Engineering Intern assignment)

An autonomous agent that takes a natural-language office task, works out the steps itself, operates a
real browser against a sandbox "finance portal", recovers from failures, asks for human approval before
changing data, and verifies the outcome before reporting back.

Example task: *"Find the latest invoice from Acme Corp, extract the amount and due date, enter it into our
internal system, and tell me once it is done."*

## Setup (Windows PowerShell)
```powershell
python -m venv venv
venv\Scripts\Activate.ps1
pip install -r requirements.txt
playwright install chromium
$env:GEMINI_API_KEY="your-free-key-from-aistudio.google.com/apikey"
python agent.py
```
Custom task: `python agent.py "Find the Beta Ltd invoice and enter it into the system"`
Optional: `$env:GEMINI_MODEL="<model name>"`, `$env:HEADLESS="1"`, `$env:FLAKY="0"` (disable the forced 503).

## Architecture
```
 user task -> agent.py (LLM loop, Gemini function calling)
                 |  picks one tool per step, reads the result, decides next step
                 v
            tools.py: list_files, read_file, remember, browser_goto/read_page/fill/click,
                      ask_user (clarification / approval), finish
                 |                         |
        invoices/*.txt (sandbox)     Playwright -> mock_app/app.py (Flask "Finance Portal")
 after finish: harness re-reads system state via /api/records + screenshot -> evidence/, run_log.json
```
Loop: Goal -> Understand -> Plan -> Execute -> Observe -> Adapt -> Verify -> Complete.

## Key design decisions
- **Errors are observations, not crashes.** Every tool exception is returned to the LLM so it can adapt.
- **Hard approval gate in code, not just in the prompt.** Clicking a submit button is blocked until the human
  has approved via `ask_user(kind="approval")`. Approval is single-use.
- **Deliberate failures in the mock app** to prove recovery: amount must be plain digits, date must be
  YYYY-MM-DD (not stated on the form), and the first valid submit returns a 503 that saves nothing.
- **Two-layer verification:** the LLM must re-open /records and confirm the row; then the harness
  independently reads /api/records and takes a screenshot. The agent is not told about the API.
- **Sandboxing:** file tools only reach `invoices/`; the browser only visits the mock app.
- **Generalization:** the loop and tools are task-agnostic; only the ENVIRONMENT block in the system prompt
  describes this sandbox. Different tasks need no code changes.
- Step limit (25), rate-limit backoff and a short delay between calls for the free API tier.

## Models / tools used
Gemini API (free tier, `google-genai` SDK), Playwright (Chromium), Flask. AI coding assistance (Claude) was used
to write parts of the code; I can explain and modify every part.

## Assumptions
Sandbox only, fake data, single user, text invoices, English, one browser tab, terminal approval.

## Known limitations
- No PDF/OCR or screenshot-based (vision) computer use; the agent reads page text and DOM selectors.
- Memory lasts one run only. Approval is a terminal prompt. No automated eval suite yet.
- The independent check is written for this app's /api/records.

## What I would build next
Vision-based computer use for desktop apps, persistent company memory, a policy/permissions layer,
an evaluation harness with many tasks, a web UI for approvals, parallel/background task queue, PDF/OCR support.

## Test results (fill in after your runs)
1. Happy path (Acme): ...
2. Recovery (bad date/amount format, 503): ...
3. Different task (Beta Ltd): ...
