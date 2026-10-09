"""Tools the agent can use. Every tool returns a string; errors are returned
(not raised) by the caller so the LLM can observe them and adapt."""
from pathlib import Path

from google.genai import types
from playwright.sync_api import sync_playwright

ROOT = Path(__file__).parent.resolve()
SANDBOX_DIR = ROOT / "invoices"      # the only folder file tools may touch
EVIDENCE_DIR = ROOT / "evidence"
BASE_URL = "http://127.0.0.1:5000"   # the only site the browser may visit
MAX_OUT = 3000


class Toolbox:
    def __init__(self, headless: bool = False):
        self.pw = sync_playwright().start()
        self.browser = self.pw.chromium.launch(headless=headless, slow_mo=300)
        self.page = self.browser.new_page()
        self.approved = False   # set True only by a human "yes" to an approval question
        self.memory = {}

    def close(self):
        self.browser.close()
        self.pw.stop()

    # ---------- files ----------
    def _safe(self, path: str) -> Path:
        p = (ROOT / path).resolve()
        if p != SANDBOX_DIR and SANDBOX_DIR not in p.parents:
            raise PermissionError(f"Access outside sandbox folder 'invoices/' is not allowed: {path}")
        return p

    def list_files(self, directory: str) -> str:
        p = self._safe(directory)
        return "\n".join(sorted(f.name for f in p.iterdir())) or "(empty)"

    def read_file(self, path: str) -> str:
        return self._safe(path).read_text(encoding="utf-8", errors="replace")[:MAX_OUT]

    # ---------- memory ----------
    def remember(self, key: str, value: str) -> str:
        self.memory[key] = value
        return f"Stored {key!r}."

    # ---------- browser ----------
    def _snippet(self, n: int = 1200) -> str:
        return self.page.inner_text("body")[:n]

    def browser_goto(self, url: str) -> str:
        if url.startswith("/"):
            url = BASE_URL + url
        if not url.startswith(BASE_URL):
            raise PermissionError(f"Only {BASE_URL} may be visited in this sandbox.")
        self.page.goto(url, timeout=10000)
        return f"Opened {self.page.url}\n---\n{self._snippet()}"

    def browser_read_page(self) -> str:
        elements = self.page.evaluate(
            """() => [...document.querySelectorAll('input,select,textarea,button,a')].map(e => ({
                tag: e.tagName.toLowerCase(), id: e.id, name: e.name || '', type: e.type || '',
                text: (e.innerText || '').trim().slice(0, 40), href: e.getAttribute('href') || '',
                value: e.value || ''}))"""
        )
        lines = []
        for e in elements:
            sel = f"#{e['id']}" if e["id"] else (f"[name={e['name']}]" if e["name"] else e["tag"])
            lines.append(f"- <{e['tag']} type={e['type']}> selector={sel} text={e['text']!r} "
                         f"href={e['href']!r} current_value={e['value']!r}")
        return (f"URL: {self.page.url}\nPAGE TEXT:\n{self._snippet(2000)}\n"
                f"INTERACTIVE ELEMENTS:\n" + "\n".join(lines))

    def browser_fill(self, selector: str, value: str) -> str:
        self.page.fill(selector, value, timeout=5000)
        return f"Filled {selector} with {value!r}."

    def browser_click(self, selector: str) -> str:
        loc = self.page.locator(selector).first
        is_submit = loc.evaluate(
            "e => (e.tagName==='BUTTON' && (e.type==='submit')) || (e.tagName==='INPUT' && e.type==='submit')",
            timeout=5000,
        )
        if is_submit and not self.approved:
            return ("BLOCKED: submitting changes data. You must first call ask_user with kind='approval', "
                    "stating exactly what will be submitted, and receive a 'yes'.")
        loc.click(timeout=5000)
        self.page.wait_for_load_state()
        if is_submit:
            self.approved = False   # approval is single-use
        return f"Clicked {selector}. Now at {self.page.url}\n---\n{self._snippet()}"

    # ---------- human in the loop ----------
    def ask_user(self, question: str, kind: str = "clarification") -> str:
        print(f"\n[AGENT ASKS - {kind.upper()}] {question}")
        ans = input("Your answer (type y/yes to approve): ").strip()
        if kind == "approval" and ans.lower() in ("y", "yes", "approve", "ok"):
            self.approved = True
        return f"User answered: {ans}"


# ---------- schemas shown to the LLM ----------
def _decl(name, desc, props=None):
    schema = None
    if props:
        schema = types.Schema(
            type="OBJECT",
            properties={k: types.Schema(type="STRING", description=v) for k, v in props.items()},
            required=[k for k in props if k != "kind"],
        )
    return types.FunctionDeclaration(name=name, description=desc, parameters=schema)


def declarations():
    return [types.Tool(function_declarations=[
        _decl("list_files", "List files in a folder inside the sandbox (e.g. 'invoices').",
              {"directory": "Folder path, e.g. 'invoices'"}),
        _decl("read_file", "Read a text file, e.g. 'invoices/inv_0001.txt'.",
              {"path": "File path relative to project root"}),
        _decl("remember", "Store a fact you discovered (e.g. chosen invoice, amount, due date) for later steps.",
              {"key": "Short name", "value": "The value"}),
        _decl("browser_goto", "Open a page of the internal web app. Accepts '/' paths like '/records'.",
              {"url": "URL or path"}),
        _decl("browser_read_page", "Read visible text and list form fields/buttons (with selectors) of the current page."),
        _decl("browser_fill", "Type a value into a form field.",
              {"selector": "CSS selector, e.g. '#vendor'", "value": "Text to enter"}),
        _decl("browser_click", "Click an element. Submitting a form requires prior human approval.",
              {"selector": "CSS selector"}),
        _decl("ask_user", "Ask the human a question. Use kind='approval' before submitting data; "
                          "use kind='clarification' when the task is ambiguous or you are stuck.",
              {"question": "What to ask, with all details needed to decide",
               "kind": "'approval' or 'clarification'"}),
        _decl("finish", "End the task ONLY after verifying the outcome. Give a concise summary with evidence.",
              {"summary": "What was done, the values used, and the verification evidence"}),
    ])]
