/* ============================================================
   Eigenes Wörterbuch (WOERTERBUCH in server.py)
   ============================================================
   Gemeinsam für die volle Oberfläche und /verlauf: Zugriff auf
   /woerterbuch und das Feld, das nach dem Markieren eines Worts
   erscheint. Das Feld ist vorbelegt, aber änderbar — wer ein falsch
   gehörtes Wort markiert („Schmid“), trägt die richtige Schreibweise
   („Schmitt“) ein, statt den Fehler zu übernehmen. */
window.Woerterbuch = (() => {
  const MAX_LEN = 40;   // wie _DICT_MAX_LEN in server.py

  async function call(method, url, body) {
    const r = await fetch(url, {
      method,
      headers: body ? { "Content-Type": "application/json" } : {},
      body: body ? JSON.stringify(body) : undefined,
    });
    const data = await r.json().catch(() => ({}));
    if (!r.ok) throw new Error(data.detail || `Fehler ${r.status}`);
    return data;
  }

  const list = () => call("GET", "/woerterbuch");
  const add = (word) => call("POST", "/woerterbuch", { wort: word });
  const remove = (word) => call("DELETE", `/woerterbuch?wort=${encodeURIComponent(word)}`);

  // Satzzeichen am Rand einer Markierung weg — der Server tut dasselbe.
  function clean(text) {
    return text.replace(/\s+/g, " ").replace(/^[\s.,;:!?"'„“”‚‘’»«()[\]{}<>]+|[\s.,;:!?"'„“”‚‘’»«()[\]{}<>]+$/g, "");
  }

  let pop = null;

  function injectStyle() {
    const style = document.createElement("style");
    style.textContent = `
      .wb-pop { position: absolute; z-index: 300; display: flex; flex-wrap: wrap; gap: 6px;
        align-items: center; max-width: calc(100vw - 32px); padding: 6px; background: #1f2937;
        color: #f9fafb; border: 1px solid #4b5563; border-radius: 8px;
        box-shadow: 0 6px 18px rgba(0,0,0,.35); font: 13px/1.3 system-ui, "Segoe UI", sans-serif; }
      .wb-pop input { width: 16ch; max-width: 100%; padding: 4px 6px; font: inherit; color: #f9fafb;
        background: #111827; border: 1px solid #4b5563; border-radius: 5px; }
      .wb-pop button { font: inherit; padding: 4px 10px; border: 0; border-radius: 5px;
        cursor: pointer; color: #fff; background: #10b981; }
      .wb-pop button:hover { background: #059669; }
      .wb-pop button.wb-close { background: none; color: #9ca3af; padding: 4px 6px; }
      .wb-pop button.wb-close:hover { color: #f9fafb; background: #374151; }
      .wb-pop .wb-msg { flex-basis: 100%; color: #9ca3af; }
      .wb-pop .wb-msg.err { color: #f87171; }
      .wb-pop .wb-msg:empty { display: none; }`;
    document.head.appendChild(style);
  }

  function build() {
    injectStyle();
    pop = document.createElement("div");
    pop.className = "wb-pop";
    pop.hidden = true;
    pop.innerHTML = `
      <input type="text" maxlength="${MAX_LEN}" aria-label="Wort fürs Wörterbuch" autocomplete="off" spellcheck="false">
      <button type="button" class="wb-add">In Wörterbuch übernehmen</button>
      <button type="button" class="wb-close" title="Schließen (Esc)">✕</button>
      <div class="wb-msg" role="status"></div>`;
    document.body.appendChild(pop);
    const input = pop.querySelector("input");
    const msg = pop.querySelector(".wb-msg");

    async function submit() {
      const word = clean(input.value);
      if (!word) return;
      msg.className = "wb-msg";
      msg.textContent = "…";
      try {
        const data = await add(word);
        msg.textContent = data.added ? `✓ „${data.word}“ übernommen` : `„${data.word}“ steht schon drin`;
        document.dispatchEvent(new CustomEvent("woerterbuch-changed", { detail: data }));
        setTimeout(hide, 1400);
      } catch (e) {
        msg.className = "wb-msg err";
        msg.textContent = e.message;
      }
    }
    pop.querySelector(".wb-add").addEventListener("click", submit);
    pop.querySelector(".wb-close").addEventListener("click", hide);
    input.addEventListener("keydown", (e) => {
      if (e.key === "Enter") { e.preventDefault(); submit(); }
    });
  }

  function hide() {
    if (pop) pop.hidden = true;
  }

  function show(word, rect) {
    pop.querySelector("input").value = word;
    pop.querySelector(".wb-msg").textContent = "";
    pop.hidden = false;
    const width = pop.offsetWidth;
    const left = Math.max(16, Math.min(rect.left, document.documentElement.clientWidth - width - 16));
    pop.style.left = `${left + window.scrollX}px`;
    pop.style.top = `${rect.bottom + window.scrollY + 6}px`;
  }

  // Nach dem Markieren in einem der Bereiche das Feld zeigen. Nur kurze
  // Markierungen ohne Umbruch: wer einen Absatz zum Kopieren markiert,
  // soll kein Feld vor die Nase bekommen.
  function attach(roots) {
    if (!pop) build();
    document.addEventListener("mouseup", (e) => {
      if (pop.contains(e.target)) return;
      setTimeout(() => {
        const sel = window.getSelection();
        if (!sel || sel.isCollapsed || !sel.rangeCount) return hide();
        const range = sel.getRangeAt(0);
        const inside = roots.some((r) => r && r.contains(range.commonAncestorContainer));
        const raw = sel.toString();
        const word = clean(raw);
        if (!inside || !word || raw.includes("\n") || word.length > MAX_LEN) return hide();
        show(word, range.getBoundingClientRect());
      }, 0);
    });
    document.addEventListener("mousedown", (e) => {
      if (!pop.hidden && !pop.contains(e.target)) hide();
    });
    document.addEventListener("keydown", (e) => {
      if (e.key === "Escape") hide();
    });
  }

  // Für Seiten, die sich selbst neu zeichnen (/verlauf): nicht, solange das
  // Feld offen ist oder in `root` etwas markiert ist.
  function busy(root) {
    const sel = window.getSelection();
    return Boolean((pop && !pop.hidden) ||
      (sel && !sel.isCollapsed && sel.rangeCount && root.contains(sel.getRangeAt(0).commonAncestorContainer)));
  }

  return { list, add, remove, attach, busy };
})();
