"""Exercise dashboard rendering with a DOM harness and headless Chrome."""

import json
import re
import shutil
import subprocess
from html.parser import HTMLParser

import pytest

from avo.web_pages import _get_dashboard_html


def test_dashboard_xss_in_chrome(tmp_path):
    chrome = shutil.which("google-chrome") or shutil.which("chromium")
    if not chrome:
        pytest.skip("Chrome is unavailable")
    html = _get_dashboard_html()
    # Keep the actual dashboard DOM and inline code, with no network dependencies.
    html = re.sub(r"<script\b[^>]*\bsrc=[^>]*>.*?</script>", "", html, flags=re.S)
    html = re.sub(r"<link\b[^>]*>", "", html)
    harness = r"""<script>
window.onload = async () => {
  const result = document.createElement('output');
  result.id = 'security-result';
  document.body.appendChild(result);
  const check = (ok, message) => {if (!ok) throw new Error(message);};
  try {
    const payload = `quote');globalThis.xss=1;//" attack="<img src=x onerror=globalThis.xss=1>`;
    state.workspaceTree = {tree: [{type: 'file', path: payload, name: payload, size: 4}]};
    let opened;
    openWorkspaceFile = value => {opened = value;};
    renderWorkspaceTree();
    const tree = document.getElementById('workspaceTreeContainer');
    const file = tree.querySelector('.tree-file-item');
    check(file.dataset.filePath === payload, 'filename attribute changed');
    check(!tree.querySelector('img'), 'filename injected HTML');
    file.click();
    check(opened === payload, 'click changed filename');
    for (const trace of [{text: payload}, {payload}]) {
      fetchJson = async () => trace;
      await inspectRun('test');
      const body = document.getElementById('modalBody');
      check(body.textContent === (trace.text || JSON.stringify(trace, null, 2)), 'trace changed');
      check(!body.querySelector('img'), 'trace injected HTML');
    }
    await new Promise(resolve => setTimeout(resolve, 50));
    check(!globalThis.xss, 'payload executed');
    result.textContent = 'PASS';
  } catch (error) {
    result.textContent = 'FAIL: ' + error.message;
  }
};
</script>"""
    page = tmp_path / "dashboard.html"
    page.write_text(html.replace("</body>", harness + "</body>"), encoding="utf-8")
    result = subprocess.run(
        [
            chrome,
            "--headless=new",
            "--no-sandbox",
            "--disable-gpu",
            "--disable-dev-shm-usage",
            "--no-first-run",
            f"--user-data-dir={tmp_path / 'chrome'}",
            "--virtual-time-budget=3000",
            "--dump-dom",
            page.as_uri(),
        ],
        capture_output=True,
        text=True,
        timeout=30,
    )
    assert result.returncode == 0, result.stderr
    assert '<output id="security-result">PASS</output>' in result.stdout, result.stdout


class ParsedHTML(HTMLParser):
    def __init__(self, html):
        super().__init__()
        self.elements = []
        self.text = []
        self.feed(html)

    def handle_starttag(self, tag, attrs):
        self.elements.append((tag, dict(attrs)))

    def handle_data(self, data):
        self.text.append(data)


@pytest.mark.parametrize("case", ["filename", "trace", "permissions"])
def test_dashboard_untrusted_content(case):
    html = _get_dashboard_html()
    if case == "permissions":
        selects = re.findall(
            r'<select id="(?:overviewPermSelect|chatPermSelect)".*?</select>', html, re.S
        )
        assert len(selects) == 2
        for select in selects:
            values = {
                attrs["value"] for tag, attrs in ParsedHTML(select).elements if tag == "option"
            }
            assert values == {"default", "accept_edits", "plan", "bypass_permissions"}
        return
    node = shutil.which("node")
    if not node:
        pytest.skip("Node is needed to execute dashboard JavaScript")
    scripts = re.findall(r"<script>(.*?)</script>", html, re.S)
    assert scripts
    harness = r"""
const assert = require('node:assert/strict');
const vm = require('node:vm');
const escape = text => String(text).replaceAll('&', '&amp;')
  .replaceAll('<', '&lt;').replaceAll('>', '&gt;');
const payload = `quote');globalThis.xss=1;//" attack="<img src=x onerror=globalThis.xss=1>`;
// Browser text serialization does not escape quotes. Simulate only the DOM
// boundary; run the complete emitted script and independently parse its HTML.
class Element {
  constructor() {
    this.html = ''; this.children = []; this.classList = {remove() {}}; this.listeners = {};
  }
  set textContent(value) {this.text = String(value); this.html = escape(value); this.children = [];}
  get textContent() {
    return this.children.length ? this.children.map(c => c.textContent).join('') : this.text;
  }
  set innerText(value) {this.textContent = value;}
  set innerHTML(value) {this.html = value; this.text = undefined; this.children = [];}
  get innerHTML() {return this.html;}
  replaceChildren(...children) {
    this.children = children; this.html = children.map(c => c.innerHTML).join('');
  }
  appendChild(child) {this.children.push(child); return child;}
  addEventListener(event, callback) {this.listeners[event] = callback;}
  querySelectorAll() {return [fileItem];}
}
const elements = new Map();
const fileItem = new Element();
fileItem.dataset = {filePath: payload};
global.window = global;
global.document = {
  createElement: () => new Element(),
  getElementById: id => {
    if (!elements.has(id)) elements.set(id, new Element()); return elements.get(id);
  }
};
vm.runInThisContext(SCRIPT);
(async () => {
  if (CASE === 'filename') {
    const tree = [{type: 'file', path: payload, name: payload, size: 4}];
    vm.runInThisContext('state.workspaceTree = ' + JSON.stringify({tree}));
    let opened;
    openWorkspaceFile = value => {opened = value;};
    renderWorkspaceTree();
    const rendered = elements.get('workspaceTreeContainer').innerHTML;
    if (fileItem.listeners.click) fileItem.listeners.click();
    console.log(JSON.stringify({rendered, payload, opened}));
  } else {
    fetchJson = async () => ({text: payload});
    await inspectRun('test');
    const body = elements.get('modalBody');
    assert.equal(body.textContent, payload, 'trace must be inserted as literal text');
    assert.ok(!body.innerHTML.includes('<img'), 'trace created HTML');
    fetchJson = async () => ({payload});
    await inspectRun('test');
    assert.equal(body.textContent, JSON.stringify({payload}, null, 2), 'literal JSON fallback');
    assert.ok(!body.innerHTML.includes('<img'));
    console.log('{}');
  }
})().catch(error => {console.error(error); process.exitCode = 1;});
"""
    result = subprocess.run(
        [
            node,
            "-e",
            "const SCRIPT = "
            + json.dumps(scripts[-1])
            + "; const CASE = "
            + json.dumps(case)
            + ";\n"
            + harness,
        ],
        capture_output=True,
        text=True,
        timeout=10,
    )
    assert result.returncode == 0, result.stderr
    if case == "filename":
        rendered = json.loads(result.stdout)
        parsed = ParsedHTML(rendered["rendered"])
        files = [attrs for _, attrs in parsed.elements if "data-file-path" in attrs]
        assert len(files) == 1
        assert files[0]["data-file-path"] == rendered["payload"]
        assert not any(key.startswith("on") for key in files[0])
        assert not any(tag == "img" for tag, _ in parsed.elements)
        assert rendered["payload"] in "".join(parsed.text)
        assert rendered.get("opened") == rendered["payload"]
