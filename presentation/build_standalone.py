"""Build a presentation that opens from one HTML file without a local server.

Only the public slide assets and selected written materials are embedded. The
live LLM tab is deliberately unavailable in this offline review copy.
"""

from __future__ import annotations

import base64
import html
import mimetypes
import re
from pathlib import Path


HERE = Path(__file__).resolve().parent
OUTPUT = HERE / "multiagent-workshop-standalone.html"
LOCAL_DOCUMENTS = (
    "cost-estimate.json",
    "metrics.json",
    "materials/experiment.md",
    "materials/report.md",
)


def read(relative_path: str) -> str:
    return (HERE / relative_path).read_text(encoding="utf-8")


def replace_once(source: str, old: str, new: str, description: str) -> str:
    count = source.count(old)
    if count != 1:
        raise ValueError(f"Expected one {description}, found {count}")
    return source.replace(old, new, 1)


def replace_regex_once(source: str, pattern: str, replacement: str, description: str) -> str:
    updated, count = re.subn(pattern, lambda _: replacement, source)
    if count != 1:
        raise ValueError(f"Expected one {description}, found {count}")
    return updated


def data_url(relative_path: str) -> str:
    path = HERE / relative_path
    if not path.is_file():
        raise FileNotFoundError(path)
    mime = mimetypes.guess_type(path.name)[0] or "application/octet-stream"
    return f"data:{mime};base64,{base64.b64encode(path.read_bytes()).decode('ascii')}"


def inline_recorded_demo() -> str:
    demo = read("demo.html")
    demo = replace_once(
        demo,
        '<link rel="stylesheet" href="demo-light.css?v=cases-9">',
        "<style>\n" + read("demo-light.css") + "\n</style>",
        "demo stylesheet",
    )
    demo = replace_once(
        demo,
        '<script src="demo-data.js?v=cases-9"></script>',
        "<script>\n" + read("demo-data.js") + "\n</script>",
        "recorded demo data",
    )
    demo = replace_once(demo, "<body>", '<body class="embedded">', "demo body")
    # about:srcdoc has an opaque origin; changing its history raises a
    # SecurityError in Chrome, while the tab state works without the hash.
    demo = replace_once(
        demo,
        "history.replaceState(null,'','#'+mode);",
        "",
        "demo history update",
    )
    # The back link is hidden in embedded mode; keep it from opening a missing
    # sibling file if browser styling is disabled.
    demo = replace_once(demo, 'href="index.html#demo"', 'href="about:blank"', "demo back link")
    return demo


def main() -> None:
    deck = read("index.html")
    deck = replace_regex_once(
        deck,
        r'<link rel="stylesheet" href="deck\.css(?:\?[^\"]*)?">',
        "<style>\n" + read("deck.css") + "\n</style>",
        "deck stylesheet",
    )

    def inline_asset(match: re.Match[str]) -> str:
        relative_path = match.group(1)
        return f'src="{data_url(relative_path)}"'

    deck, asset_count = re.subn(r'src="(assets/[^\"]+)"', inline_asset, deck)
    if asset_count < 7:
        raise ValueError(f"Expected speaker photo and QR images, found {asset_count} assets")

    iframe = re.search(r'<iframe class="demo-frame"[^>]*></iframe>', deck)
    if iframe is None:
        raise ValueError("Recorded demo iframe not found")
    recorded_demo = inline_recorded_demo()
    iframe_tag = (
        '<iframe class="demo-frame" id="demo-frame" '
        'title="Записанные траектории агентов" '
        f'srcdoc="{html.escape(recorded_demo, quote=True)}"></iframe>'
    )
    deck = deck[: iframe.start()] + iframe_tag + deck[iframe.end() :]

    deck = replace_once(
        deck,
        '<button data-demo="live">Живой LLM</button>',
        '<button data-demo="live" disabled title="Живой LLM доступен в полном проекте с сервером и PostgreSQL">Живой LLM · нужен сервер</button>',
        "live tab",
    )
    deck = replace_once(
        deck,
        'Отправьте свой SQL и задачу в чат. Агенты исследуют запрос, предложат изменения и проверят результат.',
        'Офлайн-запись показывает, как четыре архитектуры исследовали SQL, проверяли кандидатов и передавали результаты.',
        "demo introduction",
    )
    deck = replace_once(
        deck,
        'Записи реальных запусков: Single, Orchestrator, Handoff и Board Swarm.</div>',
        'Записи реальных запусков работают без сети. Живой LLM доступен в полном проекте с сервером.</div>',
        "demo footer",
    )

    local_docs: dict[str, str] = {}
    for index, relative_path in enumerate(LOCAL_DOCUMENTS):
        if not (HERE / relative_path).is_file():
            raise FileNotFoundError(HERE / relative_path)
        doc_id = f"doc-{index}"
        local_docs[doc_id] = read(relative_path)
        deck = deck.replace(
            f'href="{relative_path}"',
            f'href="#offline-document" data-local-doc="{doc_id}"',
        )
    document_templates = "\n".join(
        f'<template id="{doc_id}">{html.escape(body)}</template>'
        for doc_id, body in local_docs.items()
    )
    document_overlay = (
        '<div class="overlay" id="offline-document" role="dialog" aria-modal="true" '
        'aria-label="Материал презентации"><button class="close">Закрыть Esc</button>'
        '<h2 id="offline-document-title">Материал</h2>'
        '<pre id="offline-document-text" style="white-space:pre-wrap;max-width:1100px;'
        'font:17px/1.5 Consolas,monospace;color:var(--ink)"></pre></div>\n'
        + document_templates
    )
    deck = replace_once(deck, '</main>', '</main>\n' + document_overlay, "main closing tag")

    js = read("deck.js")
    start = js.index('async function selectDemo(kind){')
    end = js.index("\ndocument.querySelectorAll('[data-demo]').forEach(b=>b.addEventListener", start)
    js = (
        js[:start]
        + """function selectDemo(kind){
 const fallback=document.querySelector('#demo-unavailable');
 if(kind==='live'){
  fallback.hidden=false;
  fallback.querySelector('h3').textContent='Живой LLM требует локального сервера';
  fallback.querySelector('p').textContent='Эта копия предназначена для просмотра офлайн. Живой запуск доступен из полного проекта.';
  fallback.querySelector('code').hidden=true;
  fallback.querySelector('#retry-live').hidden=true;
  return;
 }
 fallback.hidden=true;
 document.querySelectorAll('[data-demo]').forEach(x=>x.classList.toggle('selected',x.dataset.demo==='replay'));
 demoFrame.srcdoc=demoFrame.getAttribute('srcdoc');
 demoContext.textContent='Записи реальных запусков работают без сети. Живой LLM доступен в полном проекте с сервером.';
}
"""
        + js[end:]
    )
    docs_js = """
document.addEventListener('click',e=>{
 const a=e.target.closest('a[data-local-doc]');
 if(!a)return;
 e.preventDefault();
 const template=document.getElementById(a.dataset.localDoc);
 const overlay=document.getElementById('offline-document');
 document.querySelector('#offline-document-title').textContent=a.textContent.trim();
 document.querySelector('#offline-document-text').textContent=template.content.textContent;
 document.querySelectorAll('.overlay').forEach(x=>x.classList.remove('open'));
 overlay.classList.add('open');
 overlay.querySelector('button').focus();
});
"""
    js += docs_js
    deck = replace_regex_once(
        deck,
        r'<script src="deck\.js(?:\?[^\"]*)?"></script>',
        '<script>\n' + js + '\n</script>',
        "deck script",
    )
    # Keep the existing visual language, but make the disabled live tab clear.
    deck = replace_once(
        deck,
        "</style>",
        ".demo-tabs button:disabled{opacity:.55;cursor:not-allowed}\n</style>",
        "deck style closing tag",
    )
    output = "<!-- Standalone review copy. Regenerate with python presentation/build_standalone.py. -->\n" + deck
    OUTPUT.write_text(output, encoding="utf-8")
    print(f"Built {OUTPUT} ({OUTPUT.stat().st_size / 1024 / 1024:.2f} MiB; {asset_count} images; {len(local_docs)} documents)")


if __name__ == "__main__":
    main()
