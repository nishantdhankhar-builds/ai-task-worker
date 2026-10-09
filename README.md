# AI Task Worker

An AI agent that takes a plain-English office task and actually does it on a computer. It reads files, uses a web app in a real browser, deals with errors, asks for approval before changing anything, and then checks that the job really got done.

Built for the CentrAlign AI Engineering Intern assignment. The example task from the brief is the one I focused on:

> "Find the latest invoice from Acme Corp, extract the amount and due date, enter it into our internal system, and tell me once it is done."

## What it does, in one run

1. Lists the invoice files and reads every one of them.
2. Works out which Acme invoice is the latest by the date inside the document, not the file name.
3. Converts the values into what the internal system accepts (`$4,200.50` becomes `4200.50`, `18 Oct 2026` becomes `2026-10-18`).
4. Opens the internal finance portal in a real Chromium browser and fills in the form.
5. Stops and asks for human approval, showing exactly what it is about to submit.
6. Submits. The portal is rigged to fail once with a 503 error that saves nothing, so the agent has to notice and retry.
7. Reopens the records page to confirm the row exists, then reports back with a summary.
8. After that, separate harness code checks the system's data and saves a screenshot and a log. This part does not rely on the AI's word.

## Setup

You need Python 3.10+ and a free Gemini API key from https://aistudio.google.com/apikey

```powershell
python -m venv venv
venv\Scripts\Activate.ps1
pip install -r requirements.txt
playwright install chromium

$env:GEMINI_API_KEY="your-key"
$env:GEMINI_MODEL="gemini-3.5-flash"
python agent.py
```

`GEMINI_MODEL` can be any Flash model your key has access to (the default is `gemini-2.5-flash`). Free-tier quotas are per model, so switch models if one runs out.

Run a different task by passing it as text:

```powershell
python agent.py "Find the latest Beta Ltd invoice and enter it into the system"
```

Optional settings: `HEADLESS=1` hides the browser, `FLAKY=0` turns off the forced 503 error.

The mock finance portal starts automatically in the background. Type `y` when the agent asks for approval.

## How it is built

```
task -> agent.py (loop: ask Gemini for the next action, run it, feed the result back)
          |
          v
       tools.py: list_files, read_file, remember,
                 browser_goto / browser_read_page / browser_fill / browser_click,
                 ask_user, finish
          |                              |
   invoices/*.txt                Playwright -> mock_app/app.py (Flask "Finance Portal")

after finish: the harness re-reads /api/records and saves evidence/records.png + run_log.json
```

The loop is simple on purpose: goal, plan, act, observe, adapt, verify, finish. The model chooses one or more tool calls per turn, the code runs them, and the results go back to the model as the next observation.

## Design decisions and why

- **Tool errors go back to the model instead of crashing the program.** A wrong selector or a rejected form is information the agent can use to try something else.
- **The approval gate lives in the code, not only in the prompt.** `browser_click` refuses to press a submit button until a human has said yes through `ask_user`, and the approval is used up after one submit. A prompt can be ignored by the model, so it is not the only protection.
- **Failures are built into the sandbox.** The portal rejects `$` and commas in the amount, wants `YYYY-MM-DD` dates (it does not say so on the form, only in the error), and fails the first valid submit with a 503. This demonstrates recovery instead of just claiming it.
- **Verification happens twice.** The agent has to open `/records` and see the row itself. Then the harness reads `/api/records` on its own, which the agent knows nothing about. "The model said it worked" is not treated as proof.
- **Sandboxing.** File tools can only touch `invoices/`, and the browser can only open the mock app.
- **Generalization.** The loop and tools know nothing about invoices. The only task-specific part is a short ENVIRONMENT block in the system prompt, so a different task needs no code changes.
- **Free-tier friendly.** The agent is told to batch independent tool calls in one turn, waits between calls, and honours the retry delay returned on rate-limit errors.

## Results

**Test 1: the main task (Acme).** The agent read all five invoices and picked `inv_0003.txt` (INV-A-355, dated 18 Sep 2026) as the latest Acme invoice. It entered Acme Corp / 4200.50 / 2026-10-18, the human approved, and the row appeared on `/records`. The harness check of `/api/records` matched. Evidence is in `evidence/records.png` and `run_log.json`.

**Test 2: recovery.** The first submit returned a 503 and nothing was saved. The agent noticed this, retried, and reported success only after confirming the saved record.

**Test 3: a different task (Beta Ltd).** Not run. The free-tier daily request quota was used up by earlier runs. The command is in the Setup section.

## Tools and services used

- Google Gemini API (free tier) through the `google-genai` SDK, for the model and function calling
- Playwright (Chromium) for browser control
- Flask for the mock finance portal
- Built with AI coding assistance (Claude), as the brief allows

## Assumptions

Everything runs in a sandbox with fake data. One user, one browser tab, text invoices in English, approval given in the terminal.

## Known limitations

- It reads page text and form elements. It does not look at screenshots, so it cannot operate desktop apps or canvas-style interfaces yet.
- No PDF or OCR support.
- Memory lasts one run only.
- The independent check is written for this one portal's API.
- The free Gemini tier allows very few requests per day per model, so the number of test runs was small and runs can be slow because of rate limits.
- There is no automated test suite for the agent yet.

## What I would build next

1. Vision-based computer use so it can work with desktop apps.
2. An evaluation suite with many tasks and failure cases, and a pass rate.
3. Saved company memory so it remembers procedures and past outcomes between runs.
4. A proper permissions layer instead of one approval prompt.
5. A web page for approvals and a task queue for background runs.
6. PDF and OCR support.
