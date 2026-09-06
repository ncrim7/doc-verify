"""
Demo shell — the smallest thing that shows the workflow instead of describing it.

    python scripts/demo_server.py            # http://localhost:8000

Fatura yükle, istersen sipariş yükle, ANALİZ ET. That is the whole surface.

Its job is NOT to sell anything. It is so that when a bookkeeper asks "bunu
gerçek hayatta nasıl kullanacağım?", the answer is a screen rather than a
terminal, and the conversation stays on their workflow instead of drifting into
a technology demo.

Deliberately built on the standard library. A demo that needs `pip install`
before it runs is a demo that fails on someone else's laptop, in an office with
no wifi, ten minutes before the meeting.
"""
import html
import json
import sys
import tempfile
from email.parser import BytesParser
from email.policy import default as email_policy
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from src.analyze import DocumentAnalyzer                # noqa: E402
from src.reporting.humanizer import humanize_decision   # noqa: E402

ANALYZER = DocumentAnalyzer()
ACCEPT = {".pdf", ".png", ".jpg", ".jpeg", ".webp"}


# ---------------------------------------------------------------------------
# multipart, without cgi.FieldStorage (deprecated, gone in 3.13)
# ---------------------------------------------------------------------------
def parse_multipart(body: bytes, content_type: str) -> dict:
    """Return {field_name: (filename, bytes)} for file parts, str for the rest."""
    header = f"Content-Type: {content_type}\r\nMIME-Version: 1.0\r\n\r\n".encode()
    msg = BytesParser(policy=email_policy).parsebytes(header + body)
    out: dict = {}
    for part in msg.iter_parts():
        name = part.get_param("name", header="content-disposition")
        if not name:
            continue
        filename = part.get_filename()
        payload = part.get_payload(decode=True) or b""
        out[name] = (filename, payload) if filename else payload.decode("utf-8", "replace")
    return out


class Handler(BaseHTTPRequestHandler):
    def log_message(self, fmt, *args):          # quiet; the console is the demo
        pass

    def _send(self, code: int, body: bytes, ctype: str) -> None:
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):
        if self.path in ("/", "/index.html"):
            self._send(200, PAGE.encode("utf-8"), "text/html; charset=utf-8")
        else:
            self._send(404, b"yok", "text/plain; charset=utf-8")

    def do_POST(self):
        if self.path != "/analyze":
            self._send(404, b"yok", "text/plain; charset=utf-8")
            return
        try:
            length = int(self.headers.get("Content-Length", 0))
            fields = parse_multipart(self.rfile.read(length),
                                     self.headers.get("Content-Type", ""))
            payload = self._analyze(fields)
            code = 200
        except Exception as exc:                # noqa: BLE001 - demo surface
            payload = {"error": f"{type(exc).__name__}: {exc}"}
            code = 500
        self._send(code, json.dumps(payload, ensure_ascii=False).encode("utf-8"),
                   "application/json; charset=utf-8")

    def _analyze(self, fields: dict) -> dict:
        doc = fields.get("document")
        if not doc or not doc[0]:
            return {"error": "Fatura dosyası seçilmedi."}
        filename, blob = doc
        suffix = Path(filename).suffix.lower()
        if suffix not in ACCEPT:
            return {"error": f"Desteklenmeyen dosya türü: {suffix or 'bilinmiyor'}. "
                             f"PDF veya görüntü yükleyin."}

        po_data = None
        po = fields.get("po")
        if po and po[0] and po[1]:
            try:
                po_data = json.loads(po[1].decode("utf-8"))
            except (UnicodeDecodeError, json.JSONDecodeError):
                return {"error": "Sipariş dosyası JSON olarak okunamadı."}

        with tempfile.NamedTemporaryFile(suffix=suffix, delete=False) as tmp:
            tmp.write(blob)
            tmp_path = Path(tmp.name)
        try:
            result = ANALYZER.analyze(tmp_path,
                                      fields.get("doc_type", "invoice"),
                                      po_data=po_data)
        finally:
            tmp_path.unlink(missing_ok=True)

        h = humanize_decision(result.decision)
        d = result.decision
        return {
            "verdict": h["verdict"],
            "headline": h["headline"],
            "financial_impact_text": h["financial_impact_text"],
            "internal_discrepancy_text": h["internal_discrepancy_text"],
            "problems": h["problems"],
            "blind_spots": h["blind_spots"],
            "checked_fields": d.checked_fields,
            "coverage": d.confidence,
            "po_matched": result.po_matched,
            "seconds": result.timings.get("total_sec", 0),
            "filename": filename,
            "extraction": result.extraction.data or {},
        }


PAGE = r"""<!doctype html>
<html lang="tr"><head>
<meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>Fatura Kontrol</title>
<link rel="preconnect" href="https://fonts.googleapis.com">
<link rel="preconnect" href="https://fonts.gstatic.com" crossorigin>
<link rel="stylesheet" href="https://fonts.googleapis.com/css2?family=Newsreader:wght@400;600&family=Source+Sans+3:wght@400;600&family=JetBrains+Mono:wght@400;600&display=swap">
<style>
:root{
  --ground:#f6f7f9;--surface:#fff;--surface-2:#eef1f5;--ink:#14181e;--ink-2:#3b424d;
  --muted:#5b6470;--faint:#8b939e;--rule:#dde1e7;--rule-strong:#c3cad3;
  --accent:#1f4d68;--ok:#1a7a56;--hold:#b03028;--review:#a2681c;
  --serif:"Newsreader",Georgia,serif;--sans:"Source Sans 3",-apple-system,"Segoe UI",sans-serif;
  --mono:"JetBrains Mono",ui-monospace,Consolas,monospace;
}
@media (prefers-color-scheme:dark){:root{
  --ground:#11141a;--surface:#171b22;--surface-2:#1e232c;--ink:#e4e8ee;--ink-2:#c2c9d3;
  --muted:#98a1af;--faint:#6c7684;--rule:#262c36;--rule-strong:#39414d;
  --accent:#7fb3cd;--ok:#5cc99b;--hold:#e88a80;--review:#dbab63;}}
*{box-sizing:border-box}
body{margin:0;background:var(--ground);color:var(--ink);font-family:var(--sans);
  font-size:16px;line-height:1.6;-webkit-font-smoothing:antialiased}
.wrap{max-width:760px;margin:0 auto;padding:48px 24px 80px}
h1{font-family:var(--serif);font-weight:600;font-size:34px;margin:0 0 6px;letter-spacing:-.01em}
.sub{color:var(--muted);margin:0 0 32px;font-size:15px}
.card{background:var(--surface);border:1px solid var(--rule-strong);border-radius:5px;padding:26px}
label{display:block;font-size:14px;font-weight:600;margin:0 0 6px}
label span{font-weight:400;color:var(--faint)}
input[type=file]{width:100%;font-family:var(--sans);font-size:14px;padding:11px;
  border:1px dashed var(--rule-strong);border-radius:4px;background:var(--surface-2);color:var(--ink)}
input[type=file]::file-selector-button{font-family:var(--sans);font-size:13px;padding:6px 12px;
  margin-right:12px;border:1px solid var(--rule-strong);border-radius:3px;
  background:var(--surface);color:var(--ink);cursor:pointer}
.field{margin-bottom:20px}
button{width:100%;padding:14px;font-family:var(--sans);font-size:15px;font-weight:600;
  letter-spacing:.03em;background:var(--accent);color:#fff;border:none;border-radius:4px;cursor:pointer}
button:disabled{opacity:.55;cursor:progress}
button:focus-visible,input:focus-visible{outline:2px solid var(--accent);outline-offset:2px}
@media (prefers-color-scheme:dark){button{color:#0d1319}}
#out{margin-top:26px}
.res{background:var(--surface);border:1px solid var(--rule-strong);border-left-width:4px;
  border-radius:5px;padding:24px 26px}
.res.PAY{border-left-color:var(--ok)} .res.HOLD{border-left-color:var(--hold)}
.res.REVIEW{border-left-color:var(--review)}
.res h2{font-family:var(--sans);font-size:19px;font-weight:600;margin:0 0 4px;letter-spacing:.01em}
.res.PAY h2{color:var(--ok)} .res.HOLD h2{color:var(--hold)} .res.REVIEW h2{color:var(--review)}
.res .st{color:var(--muted);font-size:14.5px;margin:0 0 18px}
.money{font-family:var(--mono);font-size:26px;font-weight:600;font-variant-numeric:tabular-nums;
  margin:0 0 4px;letter-spacing:-.01em}
.money-lbl{font-size:12.5px;color:var(--muted);text-transform:uppercase;letter-spacing:.09em;
  font-family:var(--mono);margin:0 0 18px}
.p{border-top:1px solid var(--rule);padding:14px 0 2px}
.p-h{display:flex;justify-content:space-between;gap:14px;align-items:baseline}
.p-h b{font-weight:600;font-size:15px}
.p-h .amt{font-family:var(--mono);font-variant-numeric:tabular-nums;font-size:14px;white-space:nowrap}
.p .claim{font-size:12.5px;color:var(--faint);font-family:var(--mono);margin:2px 0 8px}
.p .cmp{display:flex;gap:26px;font-family:var(--mono);font-size:13px;margin:0 0 8px}
.p .cmp i{font-style:normal;color:var(--faint)}
.p .act{font-size:14px;color:var(--accent);margin:0}
.blind{margin-top:20px;padding-top:16px;border-top:1px solid var(--rule);
  font-size:13px;color:var(--muted);line-height:1.55}
.blind code{font-family:var(--mono);font-size:12px}
.foot{margin-top:16px;font-family:var(--mono);font-size:11.5px;color:var(--faint)}
.err{background:var(--surface);border:1px solid var(--hold);border-radius:5px;
  padding:18px 22px;color:var(--hold);font-size:14.5px}
.spin{text-align:center;color:var(--muted);padding:34px;font-size:14.5px}
</style></head><body>
<div class="wrap">
  <h1>Fatura Kontrol</h1>
  <p class="sub">Faturayı yükleyin. Siparişi de yüklerseniz ikisi karşılaştırılır.</p>

  <form class="card" id="f">
    <div class="field">
      <label for="d">Fatura <span>— PDF veya fotoğraf</span></label>
      <input type="file" id="d" name="document" accept=".pdf,.png,.jpg,.jpeg,.webp" required>
    </div>
    <div class="field">
      <label for="p">Sipariş <span>— opsiyonel, JSON</span></label>
      <input type="file" id="p" name="po" accept=".json">
    </div>
    <button type="submit" id="b">ANALİZ ET</button>
  </form>

  <div id="out"></div>
</div>
<script>
const f=document.getElementById('f'),b=document.getElementById('b'),out=document.getElementById('out');
const esc=s=>String(s??'').replace(/[&<>"]/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;'}[c]));
f.addEventListener('submit',async e=>{
  e.preventDefault();
  b.disabled=true; b.textContent='OKUNUYOR…';
  out.innerHTML='<div class="spin">Belge okunuyor, bu 10–40 saniye sürebilir…</div>';
  try{
    const r=await fetch('/analyze',{method:'POST',body:new FormData(f)});
    const j=await r.json();
    out.innerHTML = j.error ? `<div class="err">${esc(j.error)}</div>` : render(j);
  }catch(err){
    out.innerHTML=`<div class="err">${esc(err.message)}</div>`;
  }finally{ b.disabled=false; b.textContent='ANALİZ ET'; }
});
function render(j){
  let m='';
  if(j.financial_impact_text)
    m=`<p class="money">${esc(j.financial_impact_text)}</p><p class="money-lbl">Fazla faturalanan</p>`;
  else if(j.internal_discrepancy_text)
    m=`<p class="money">${esc(j.internal_discrepancy_text)}</p><p class="money-lbl">Belge içi tutarsızlık — kaynağı belirsiz</p>`;
  const probs=(j.problems||[]).map(p=>{
    const amt=p.amount_text?`<span class="amt">${esc(p.amount_text)}</span>`:'';
    const cmp=(p.expected!=null&&p.actual!=null)
      ? `<div class="cmp"><span><i>beklenen</i> ${esc(p.expected)}</span><span><i>belgede</i> ${esc(p.actual)}</span></div>` : '';
    const det=(!cmp&&p.detail)?`<div class="cmp"><span>${esc(p.detail)}</span></div>`:'';
    return `<div class="p"><div class="p-h"><b>${esc(p.label)}</b>${amt}</div>
      <div class="claim">${esc(p.claim||'')}</div>${cmp}${det}
      ${p.action?`<p class="act">→ ${esc(p.action)}</p>`:''}</div>`;
  }).join('');
  const blind=(j.blind_spots||[]).length
    ? `<div class="blind"><b>Denetlenemeyen alanlar (${j.blind_spots.length}):</b>
       <code>${j.blind_spots.map(esc).join(', ')}</code><br>
       Bu alanları karşılaştıracak ikinci bir kaynak yok; doğruluklarına dair bir iddiada bulunulmuyor.</div>` : '';
  return `<div class="res ${esc(j.verdict)}">
    <h2>${esc(j.headline.icon)} ${esc(j.headline.title)}</h2>
    <p class="st">${esc(j.headline.subtitle)}</p>
    ${m}${probs}${blind}
    <p class="foot">${esc(j.filename)} · ${j.checked_fields} alan denetlendi ·
      kapsam %${Math.round((j.coverage||0)*100)} · ${j.seconds}s${j.po_matched?' · sipariş ile karşılaştırıldı':''}</p>
  </div>`;
}
</script></body></html>
"""


def main() -> int:
    port = int(sys.argv[1]) if len(sys.argv) > 1 else 8000
    srv = ThreadingHTTPServer(("127.0.0.1", port), Handler)
    print(f"\n  Fatura Kontrol  ->  http://localhost:{port}\n"
          f"  durdurmak icin Ctrl+C\n")
    try:
        srv.serve_forever()
    except KeyboardInterrupt:
        print("  kapatildi")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
