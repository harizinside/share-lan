import html
import json
import os

from .mounts import Denied, Missing, can_upload_here, resolve
from .state import ST
from .thumb import HAVE_PDF, HAVE_PIL

CSS = r"""
*{box-sizing:border-box}
:root{
  --bg:#f4f6fb; --blob1:#c9c2ff; --blob2:#9ad9ff;
  --card:rgba(255,255,255,.72); --card2:rgba(255,255,255,.55);
  --line:rgba(16,20,45,.10); --line2:rgba(16,20,45,.06);
  --text:#12142099; --ink:#121420; --muted:#5b6076;
  --accent:#5a48f5; --accent2:#9b4bff; --ok:#16a34a; --warn:#c2740a;
  --shadow:0 1px 2px rgba(16,20,45,.06),0 8px 24px rgba(16,20,45,.08);
  --r:14px;
}
@media (prefers-color-scheme:dark){
  :root:not([data-theme=light]){
    --bg:#0a0c12; --blob1:#3a2f7a; --blob2:#123a52;
    --card:rgba(255,255,255,.045); --card2:rgba(255,255,255,.03);
    --line:rgba(255,255,255,.10); --line2:rgba(255,255,255,.06);
    --ink:#e9ebf5; --muted:#8f95ad;
    --accent:#8b7bff; --accent2:#c07bff; --ok:#4ade80; --warn:#fbbf24;
    --shadow:0 1px 2px rgba(0,0,0,.4),0 12px 32px rgba(0,0,0,.35);
  }
}
:root[data-theme=dark]{
  --bg:#0a0c12; --blob1:#3a2f7a; --blob2:#123a52;
  --card:rgba(255,255,255,.045); --card2:rgba(255,255,255,.03);
  --line:rgba(255,255,255,.10); --line2:rgba(255,255,255,.06);
  --ink:#e9ebf5; --muted:#8f95ad;
  --accent:#8b7bff; --accent2:#c07bff; --ok:#4ade80; --warn:#fbbf24;
  --shadow:0 1px 2px rgba(0,0,0,.4),0 12px 32px rgba(0,0,0,.35);
}
html,body{margin:0;height:100%}
body{
  background:var(--bg); color:var(--ink); font:14px/1.5 -apple-system,BlinkMacSystemFont,
  "Segoe UI",Inter,Roboto,sans-serif; -webkit-font-smoothing:antialiased;
  padding-bottom:96px;
}
.bgfx{position:fixed;inset:0;z-index:-1;pointer-events:none;
  background:
    radial-gradient(60vw 50vh at 8% -8%, var(--blob1) 0%, transparent 62%),
    radial-gradient(52vw 44vh at 96% 6%, var(--blob2) 0%, transparent 60%);
  opacity:.5; filter:blur(8px)}
button,input{font:inherit;color:inherit}
button{cursor:pointer;border:0;background:none}
a{color:inherit;text-decoration:none}
.mono{font-variant-numeric:tabular-nums}

/* header */
.top{position:sticky;top:0;z-index:30;display:flex;align-items:center;gap:10px;
  padding:12px 18px;backdrop-filter:blur(14px);
  background:linear-gradient(var(--bg),color-mix(in srgb,var(--bg) 70%,transparent));
  border-bottom:1px solid var(--line2)}
.brand{display:flex;align-items:center;gap:9px;font-weight:650;letter-spacing:-.02em}
.brand svg{width:26px;height:26px}
.brand b{color:var(--accent)}
.spacer{flex:1}
.chip{display:inline-flex;align-items:center;gap:8px;height:36px;padding:0 12px;
  border-radius:999px;border:1px solid var(--line);background:var(--card);
  font-weight:600;letter-spacing:.14em;box-shadow:var(--shadow)}
.chip small{letter-spacing:0;font-weight:500;color:var(--muted)}
.iconbtn{display:grid;place-items:center;width:36px;height:36px;border-radius:11px;
  border:1px solid var(--line);background:var(--card);box-shadow:var(--shadow);
  transition:transform .15s,border-color .15s}
.iconbtn:hover{transform:translateY(-1px);border-color:var(--accent)}
.iconbtn:active{transform:translateY(0) scale(.96)}
.iconbtn svg{width:17px;height:17px;stroke:currentColor;fill:none;stroke-width:1.7;
  stroke-linecap:round;stroke-linejoin:round}

main{max-width:1080px;margin:0 auto;padding:20px 18px 0}

/* toolbar */
.bar{display:flex;align-items:center;gap:12px;flex-wrap:wrap;margin-bottom:14px}
.crumbs{display:flex;align-items:center;gap:4px;flex:1;min-width:0;flex-wrap:wrap}
.crumbs button{padding:5px 9px;border-radius:9px;color:var(--muted);font-weight:550}
.crumbs button:hover{background:var(--card);color:var(--ink)}
.crumbs .cur{color:var(--ink);font-weight:650;font-size:16px;letter-spacing:-.01em}
.crumbs .sep{color:var(--muted);opacity:.5}
.search{display:flex;align-items:center;gap:7px;height:36px;padding:0 12px;
  border-radius:11px;border:1px solid var(--line);background:var(--card);min-width:180px}
.search svg{width:15px;height:15px;stroke:var(--muted);fill:none;stroke-width:1.8}
.search input{border:0;background:none;outline:none;width:100%}
.tools{display:flex;gap:8px}

/* aksi folder */
.actions{display:flex;gap:8px;flex-wrap:wrap;margin-bottom:14px}
.btn{display:inline-flex;align-items:center;gap:7px;height:36px;padding:0 14px;
  border-radius:11px;border:1px solid var(--line);background:var(--card);
  font-weight:600;box-shadow:var(--shadow);transition:transform .15s,border-color .15s}
.btn:hover{transform:translateY(-1px);border-color:var(--accent)}
.btn:active{transform:translateY(0) scale(.98)}
.btn svg{width:15px;height:15px;stroke:currentColor;fill:none;stroke-width:1.8;
  stroke-linecap:round;stroke-linejoin:round}
.btn.primary{background:linear-gradient(135deg,var(--accent),var(--accent2));
  color:#fff;border-color:transparent}
.note{display:inline-flex;align-items:center;gap:6px;padding:0 10px;height:36px;
  border-radius:11px;color:var(--warn);font-size:12.5px;font-weight:550;
  background:color-mix(in srgb,var(--warn) 12%,transparent)}

/* daftar */
.list{display:flex;flex-direction:column;gap:6px}
.row{display:flex;align-items:center;gap:12px;padding:9px 12px;border-radius:var(--r);
  border:1px solid transparent;transition:background .15s,border-color .15s,transform .15s;
  animation:pop .32s backwards}
.row:hover{background:var(--card);border-color:var(--line2)}
.row .thumb{width:42px;height:42px;border-radius:10px;flex:none;overflow:hidden;
  display:grid;place-items:center;background:var(--card2);border:1px solid var(--line2);
  position:relative}
.tb{position:absolute;inset:0;width:100%;height:100%;object-fit:cover;
  opacity:0;transition:opacity .25s}
.tb.on{opacity:1}
.tb.pdf{object-position:top;background:#fff}
/* video preview pas hover (kayak YouTube) */
.vp{position:absolute;inset:0;width:100%;height:100%;object-fit:cover;
  opacity:0;transition:opacity .25s;pointer-events:none;background:#000}
.ph:hover .vp,.thumb:hover .vp{opacity:1}
.ph:hover .tb,.thumb:hover .tb{opacity:0}
.vp.on{opacity:1}
@media (hover:none){.vp{display:none}}
.row .name{flex:1;min-width:0;font-weight:550;overflow:hidden;text-overflow:ellipsis;
  white-space:nowrap}
.row .meta{color:var(--muted);font-size:12.5px;flex:none}
.row .kind{color:var(--accent);font-size:11.5px;font-weight:600;
  background:color-mix(in srgb,var(--accent) 12%,transparent);
  padding:2px 8px;border-radius:999px}
.row .act{display:flex;gap:4px;opacity:0;transition:opacity .15s}
.row:hover .act,.row:focus-within .act{opacity:1}
@media (hover:none){.row .act{opacity:1}}
.ic{width:22px;height:22px;stroke-width:1.6;fill:none;stroke-linecap:round;stroke-linejoin:round}

/* checklist */
.ck{flex:none;width:20px;height:20px;margin:0;accent-color:var(--accent);cursor:pointer}
.row.checked,.card.checked{border-color:var(--accent);
  background:color-mix(in srgb,var(--accent) 9%,transparent)}

/* grid */
.grid{display:grid;grid-template-columns:repeat(auto-fill,minmax(148px,1fr));gap:12px}
.card{border-radius:var(--r);overflow:hidden;border:1px solid var(--line2);
  background:var(--card);box-shadow:var(--shadow);animation:pop .32s backwards;
  transition:transform .18s}
.card:hover{transform:translateY(-3px)}
.card .ph{aspect-ratio:4/3;display:grid;place-items:center;background:var(--card2);
  overflow:hidden;position:relative}
.card .cap{padding:8px 10px;display:flex;align-items:center;gap:6px}
.card .cap span{flex:1;min-width:0;overflow:hidden;text-overflow:ellipsis;
  white-space:nowrap;font-size:12.5px;font-weight:550}
.card .cap .kind{font-size:10px;color:var(--accent)}
.card .qr{position:absolute;top:6px;right:6px;width:28px;height:28px;border-radius:8px;
  background:rgba(0,0,0,.45);color:#fff;display:grid;place-items:center;opacity:0;
  transition:opacity .15s;backdrop-filter:blur(4px)}
.card:hover .qr{opacity:1}
@media (hover:none){.card .qr{opacity:1}}

/* bilah pilihan */
.selbar{position:sticky;top:66px;z-index:20;display:flex;align-items:center;gap:10px;
  flex-wrap:wrap;margin-bottom:14px;padding:9px 12px;border-radius:var(--r);
  border:1px solid var(--accent);background:color-mix(in srgb,var(--accent) 10%,var(--card));
  box-shadow:var(--shadow)}
.selbar .cnt{font-weight:650;flex:1;min-width:120px}
.selbar .cnt small{color:var(--muted);font-weight:500;display:block}
.selbar .btn{padding:0 12px;height:34px;font-size:13px}

/* kosong & skeleton */
.empty{text-align:center;padding:64px 20px;color:var(--muted)}
.empty svg{width:44px;height:44px;stroke:currentColor;fill:none;stroke-width:1.2;
  opacity:.5;margin-bottom:10px}
.sk{height:60px;border-radius:var(--r);background:linear-gradient(90deg,
  var(--card2) 25%,var(--card) 37%,var(--card2) 63%);
  background-size:400% 100%;animation:sh 1.3s infinite;margin-bottom:6px}

/* upload */
.ub{position:fixed;left:0;right:0;bottom:0;z-index:25;padding:12px 18px;
  border-top:1px solid var(--line2);backdrop-filter:blur(14px);
  background:color-mix(in srgb,var(--bg) 82%,transparent)}
.ub .inner{max-width:1080px;margin:0 auto;display:flex;align-items:center;gap:12px;
  flex-wrap:wrap}
.ub .hint{color:var(--muted);font-size:13px;flex:1;min-width:140px}
.jobs{max-width:1080px;margin:0 auto 8px;display:flex;flex-direction:column;gap:6px}
.job{display:flex;align-items:center;gap:10px;font-size:12.5px}
.job .nm{flex:1;min-width:0;overflow:hidden;text-overflow:ellipsis;white-space:nowrap}
.job .pb{width:130px;height:6px;border-radius:99px;background:var(--line);overflow:hidden}
.job .pb i{display:block;height:100%;border-radius:99px;
  background:linear-gradient(90deg,var(--accent),var(--accent2));transition:width .2s}
.job .st{color:var(--muted);flex:none;min-width:112px;text-align:right}

/* drag overlay */
.drop{position:fixed;inset:0;z-index:60;display:none;place-items:center;
  background:color-mix(in srgb,var(--bg) 70%,transparent);backdrop-filter:blur(6px)}
.drop.on{display:grid}
.drop div{padding:52px 64px;border-radius:22px;border:2px dashed var(--accent);
  font-size:17px;font-weight:650;background:var(--card);animation:pulse 1.6s infinite}

/* modal QR */
.modal{position:fixed;inset:0;z-index:70;display:none;place-items:center;padding:20px;
  background:rgba(4,6,14,.62);backdrop-filter:blur(10px)}
.modal.on{display:grid;animation:fade .18s}
.sheet{background:#fff;color:#121420;border-radius:22px;padding:22px;max-width:390px;
  width:100%;text-align:center;box-shadow:0 24px 70px rgba(0,0,0,.5);animation:pop .24s}
.sheet h3{margin:0 0 4px;font-size:15px;color:#101220;overflow:hidden;
  text-overflow:ellipsis;white-space:nowrap}
.sheet p{margin:0 0 14px;font-size:12.5px;color:#6b7080}
.sheet .qrbox{background:#fff;border-radius:14px;padding:8px;border:1px solid #eceef5}
.sheet .qrbox svg,.sheet .qrbox img{width:100%;height:auto;display:block}
.sheet .url{margin-top:12px;font-size:11.5px;color:#6b7080;word-break:break-all;
  background:#f5f6fa;border-radius:10px;padding:9px}
.sheet .row2{display:flex;gap:8px;margin-top:12px}
.sheet .row2 button{flex:1;height:38px;border-radius:11px;font-weight:600;
  border:1px solid #e6e8f0;background:#fff;color:#101220}
.sheet .row2 button.primary{background:linear-gradient(135deg,var(--accent),var(--accent2));
  color:#fff;border-color:transparent}

/* toast */
.toasts{position:fixed;left:50%;transform:translateX(-50%);bottom:104px;z-index:80;
  display:flex;flex-direction:column;gap:8px;align-items:center}
.toast{padding:9px 15px;border-radius:11px;background:var(--card);
  border:1px solid var(--line);box-shadow:var(--shadow);backdrop-filter:blur(12px);
  font-size:13px;font-weight:550;animation:pop .2s}
.toast.bad{color:var(--warn)}

@keyframes pop{from{opacity:0;transform:translateY(6px) scale(.99)}}
@keyframes fade{from{opacity:0}}
@keyframes sh{from{background-position:100% 0}to{background-position:0 0}}
@keyframes pulse{50%{transform:scale(1.015)}}
@media (prefers-reduced-motion:reduce){*{animation:none!important;transition:none!important}}
@media (max-width:560px){
  main{padding:16px 12px 0}
  .top{padding:10px 12px}
  .bar{flex-direction:column;align-items:stretch;gap:10px}
  .crumbs{width:100%}
  .tools{width:100%}
  .search{flex:1}
  .row .meta{display:none}
  .chip{letter-spacing:.1em;padding:0 10px}
  .job .pb{width:74px}
}
"""

JS = r"""
const CFG = __CFG__;
const $ = s => document.querySelector(s);
const enc = encodeURIComponent;
const deep = new URLSearchParams(location.search).get("p");
let S = {path: deep !== null ? deep : CFG.initial, entries: [], total: 0,
         filter: "", view: "auto", loading: false, sel: new Set()};

/* ---------- ikon ---------- */
const P = {
  dir:  '<path d="M3 7a2 2 0 0 1 2-2h4l2 2h8a2 2 0 0 1 2 2v8a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2z"/>',
  image:'<rect x="3" y="4" width="18" height="16" rx="2"/><circle cx="9" cy="10" r="1.6"/><path d="m4 17 5-5 4 4 3-2 4 4"/>',
  video:'<rect x="3" y="5" width="18" height="14" rx="2"/><path d="m10 9 5 3-5 3z"/>',
  audio:'<path d="M9 18V6l10-2v12"/><circle cx="6.5" cy="18" r="2.5"/><circle cx="16.5" cy="16" r="2.5"/>',
  archive:'<rect x="3" y="4" width="18" height="16" rx="2"/><path d="M3 9h18M12 4v5m-1.5 3h3m-3 3h3"/>',
  doc:  '<path d="M14 3H7a2 2 0 0 0-2 2v14a2 2 0 0 0 2 2h10a2 2 0 0 0 2-2V8z"/><path d="M14 3v5h5M9 13h6M9 17h4"/>',
  xls:  '<path d="M14 3H7a2 2 0 0 0-2 2v14a2 2 0 0 0 2 2h10a2 2 0 0 0 2-2V8z"/><path d="M14 3v5h5M8 13h8M8 17h8M12 13v4"/>',
  ppt:  '<path d="M14 3H7a2 2 0 0 0-2 2v14a2 2 0 0 0 2 2h10a2 2 0 0 0 2-2V8z"/><path d="M14 3v5h5"/><path d="M8.5 16.5v-3m3 3v-5m3 5V9.5"/>',
  sql:  '<ellipse cx="12" cy="5" rx="7" ry="3"/><path d="M5 5v14c0 1.66 3.13 3 7 3s7-1.34 7-3V5"/><path d="M5 12c0 1.66 3.13 3 7 3s7-1.34 7-3"/>',
  code: '<path d="m9 8-5 4 5 4M15 8l5 4-5 4"/>',
  file: '<path d="M14 3H7a2 2 0 0 0-2 2v14a2 2 0 0 0 2 2h10a2 2 0 0 0 2-2V8z"/><path d="M14 3v5h5"/>',
};
const TINT = {dir:"var(--accent)", image:"#e8735a", video:"#c07bff", audio:"#3aa6a0",
              archive:"#d99a2b", doc:"#4a8ef0", xls:"#27a844", ppt:"#f47a3c", sql:"#3aa6d6",
              code:"#5bbf6a", file:"var(--muted)"};
const ico = (k, cls) => `<svg class="${cls||'ic'}" viewBox="0 0 24 24" stroke="${TINT[k]||TINT.file}">${P[k]||P.file}</svg>`;
const KIND = {dir:"Folder", image:"Gambar", video:"Video", audio:"Audio",
              archive:"Arsip", doc:"Dokumen", xls:"Spreadsheet", ppt:"Slide",
              sql:"Database", code:"Kode", file:"File"};
const kindTag = e => `<span class="kind">${e.dir?"Folder":KIND[e.kind]||"File"}</span>`;
const UI = {
  qr:'<svg viewBox="0 0 24 24"><rect x="3" y="3" width="7" height="7" rx="1"/><rect x="14" y="3" width="7" height="7" rx="1"/><rect x="3" y="14" width="7" height="7" rx="1"/><path d="M14 14h3v3h-3zM19 19h2v2h-2M14 21h1"/></svg>',
  dl:'<svg viewBox="0 0 24 24"><path d="M12 3v12m0 0 4-4m-4 4-4-4M4 19h16"/></svg>',
  zip:'<svg viewBox="0 0 24 24"><path d="M20 7 12 3 4 7v10l8 4 8-4z"/><path d="M12 12v9M4 7l8 5 8-5"/></svg>',
  link:'<svg viewBox="0 0 24 24"><path d="M10 13a5 5 0 0 0 7 0l2-2a5 5 0 0 0-7-7l-1 1"/><path d="M14 11a5 5 0 0 0-7 0l-2 2a5 5 0 0 0 7 7l1-1"/></svg>',
  sun:'<svg viewBox="0 0 24 24"><circle cx="12" cy="12" r="4"/><path d="M12 2v2m0 16v2M2 12h2m16 0h2M4.9 4.9l1.4 1.4m11.4 11.4 1.4 1.4M19.1 4.9l-1.4 1.4M6.3 17.7l-1.4 1.4"/></svg>',
  moon:'<svg viewBox="0 0 24 24"><path d="M21 13A9 9 0 1 1 11 3a7 7 0 0 0 10 10z"/></svg>',
  grid:'<svg viewBox="0 0 24 24"><rect x="3" y="3" width="7" height="7" rx="1.5"/><rect x="14" y="3" width="7" height="7" rx="1.5"/><rect x="3" y="14" width="7" height="7" rx="1.5"/><rect x="14" y="14" width="7" height="7" rx="1.5"/></svg>',
  list:'<svg viewBox="0 0 24 24"><path d="M4 6h16M4 12h16M4 18h16"/></svg>',
  up:'<svg viewBox="0 0 24 24"><path d="M12 20V8m0 0-4 4m4-4 4 4M4 4h16"/></svg>',
  hash:'<svg viewBox="0 0 24 24"><path d="M10 3 8 21M16 3l-2 18M3.5 8.5h17M3 15.5h17"/></svg>',
  box:'<svg viewBox="0 0 24 24"><path d="M20 7 12 3 4 7v10l8 4 8-4z"/><path d="M4 7l8 5 8-5M12 12v9"/></svg>',
};

/* ---------- util ---------- */
function human(n){ if(n===null||n===undefined) return "";
  const u=["B","KB","MB","GB","TB"]; let i=0; n=Number(n);
  while(n>=1024&&i<u.length-1){n/=1024;i++}
  return (i?n.toFixed(1):n.toFixed(0))+" "+u[i]; }
function when(ts){ const d=new Date(ts*1000), n=new Date();
  const same=d.toDateString()===n.toDateString();
  return same ? d.toLocaleTimeString([], {hour:"2-digit",minute:"2-digit"})
              : d.toLocaleDateString([], {day:"numeric",month:"short",
                  year: d.getFullYear()===n.getFullYear()?undefined:"numeric"}); }
function toast(msg, bad){ const t=document.createElement("div");
  t.className="toast"+(bad?" bad":""); t.textContent=msg; $("#toasts").append(t);
  setTimeout(()=>{t.style.opacity=0; setTimeout(()=>t.remove(),300)}, 2400); }
async function copy(text, msg){ try{ await navigator.clipboard.writeText(text); toast(msg||"Disalin"); }
  catch(e){ const a=document.createElement("textarea"); a.value=text; document.body.append(a);
    a.select(); document.execCommand("copy"); a.remove(); toast(msg||"Disalin"); } }
const dlUrl = p => `/dl?p=${enc(p)}&dl=1`;
const abs = u => location.origin + u + (u.includes("?")?"&":"?") + "code=" + CFG.code;

/* ---------- tema & tampilan ---------- */
function applyTheme(){ const t=localStorage.getItem("ls-theme");
  if(t) document.documentElement.dataset.theme=t; else delete document.documentElement.dataset.theme;
  const dark = t ? t==="dark" : matchMedia("(prefers-color-scheme:dark)").matches;
  $("#themeBtn").innerHTML = dark ? UI.sun : UI.moon; }
function toggleTheme(){ const cur=document.documentElement.dataset.theme
    || (matchMedia("(prefers-color-scheme:dark)").matches?"dark":"light");
  localStorage.setItem("ls-theme", cur==="dark"?"light":"dark"); applyTheme(); }
function viewMode(){ const v=localStorage.getItem("ls-view");
  if(v==="grid"||v==="list") return v;
  const vis=filtered(); const media=vis.filter(e=>e.kind==="image"||e.kind==="video").length;
  return (vis.length && media/vis.length>=0.5) ? "grid" : "list"; }

/* ---------- data ---------- */
async function load(path){
  S.path=path; S.loading=true; S.sel.clear(); render();
  const r=await fetch(`/api/list?p=${enc(path)}`, {headers:{"Accept":"application/json"}});
  if(!r.ok){ S.loading=false;
    if(path){ toast("Folder itu udah nggak dibagikan", true); return load(""); }
    toast("Gagal memuat", true); render(); return; }
  const d=await r.json();
  S.entries=d.entries; S.total=d.total; S.parent=d.parent; S.next=d.next_cursor;
  S.rev=d.rev; S.shared=d.shared_count; S.loading=false;
  CFG.canUpload=d.can_upload; CFG.singleRoot=d.single_root;
  syncUpload();
  history.replaceState(null,"", path?`/?p=${enc(path)}`:"/");
  render(); zipInfo(path);
}
async function zipInfo(path){
  $("#zipnote").innerHTML="";
  if(!S.entries.some(e=>e.dir) && S.entries.length===0) return;
  try{
    const r=await fetch(`/api/zipinfo?p=${enc(path)}`); if(!r.ok) return;
    const d=await r.json(); if(path!==S.path) return;
    if(!d.resumable) $("#zipnote").innerHTML =
      `<span class="note">⚠ ${human(d.size)}${d.truncated?"+":""} — ZIP nggak bisa dilanjut kalau putus, mending per file</span>`;
  }catch(e){}
}
async function loadMore(){
  if(S.next===null||S.next===undefined) return;
  const btn=$("#more"); if(btn) btn.textContent="Memuat…";
  const r=await fetch(`/api/list?p=${enc(S.path)}&cursor=${S.next}`);
  if(!r.ok){ toast("Gagal memuat sisanya", true); return; }
  const d=await r.json();
  S.entries=S.entries.concat(d.entries); S.next=d.next_cursor; render();
}
const filtered = () => { const q=S.filter.trim().toLowerCase();
  return q ? S.entries.filter(e=>e.name.toLowerCase().includes(q)) : S.entries; };

/* ---------- render ---------- */
function render(){
  const parts = S.path ? S.path.split("/") : [];
  let cr = "";
  if(!CFG.singleRoot) cr += `<button data-go="">Dibagikan</button>`;
  parts.forEach((seg,i)=>{
    const target = parts.slice(0,i+1).join("/");
    const last = i===parts.length-1;
    if(i||!CFG.singleRoot) cr += `<span class="sep">/</span>`;
    cr += last ? `<span class="cur">${esc(seg)}</span>`
               : `<button data-go="${escA(target)}">${esc(seg)}</button>`;
  });
  if(!parts.length) cr = `<span class="cur">Dibagikan</span>`;
  $("#crumbs").innerHTML = cr;

  const acts=[];
  if(S.parent!==null && S.parent!==undefined)
    acts.push(`<button class="btn" data-go="${escA(S.parent)}">${UI.up}Naik</button>`);
  if(S.entries.length){
    acts.push(`<button class="btn primary" data-zip="1">${UI.zip}Download semua (ZIP)</button>`);
    acts.push(`<button class="btn" data-urls="1">${UI.link}Salin daftar URL</button>`);
    acts.push(`<button class="btn" data-qrfolder="1">${UI.qr}QR folder</button>`);
  }
  $("#folderActions").innerHTML = acts.join("") + `<span id="zipnote"></span>`;

  const selEl=$("#selbar");
  if(S.sel.size){
    const selNames = S.entries.filter(e=>S.sel.has(e.path)).map(e=>e.name);
    selEl.innerHTML = `<div class="selbar">
      <span class="cnt">${S.sel.size} dipilih
        <small>${selNames.slice(0,3).join(", ")}${selNames.length>3?"…":""}</small></span>
      <button class="btn primary" data-selzip="1">${UI.zip}Download pilihan</button>
      <button class="btn" data-selclear="1">Batal</button>
    </div>`;
  } else selEl.innerHTML="";

  const box=$("#content");
  if(S.loading){ box.innerHTML=`<div class="sk"></div><div class="sk"></div><div class="sk"></div>`; return; }
  const list=filtered();
  if(!list.length){
    const kosong = !S.path && !S.shared;
    box.innerHTML = `<div class="empty">${UI.box}<div>${
      S.filter ? "Nggak ada yang cocok"
      : kosong ? "Belum ada yang dibagikan"
      : "Folder ini kosong"}</div>${
      kosong ? `<div style="margin-top:6px;font-size:12.5px;opacity:.8">
        Yang bagiin lagi nyiapin filenya — halaman ini update sendiri.</div>` : ""}</div>`;
    return; }

  const mode=viewMode();
  $("#viewBtn").innerHTML = mode==="grid" ? UI.list : UI.grid;
  box.className = mode;
  box.innerHTML = list.map((e,i)=>{
    const d=Math.min(i,22)*18, tb = thumbTag(e);
    if(mode==="grid") return `<div class="card" style="animation-delay:${d}ms">
        <div class="ph" data-open="${escA(e.path)}" data-dir="${e.dir?1:0}">${ico(e.kind,"ic")}${tb}
          <input type="checkbox" class="ck" style="position:absolute;top:8px;left:8px;z-index:2"
                 data-check="${escA(e.path)}" ${S.sel.has(e.path)?"checked":""}>
          <button class="qr" data-qr="${escA(e.path)}" data-isdir="${e.dir?1:0}" title="QR">${UI.qr}</button>
        </div>
        <div class="cap">${ico(e.kind,"ic")}<span title="${escA(e.name)}">${esc(e.name)}</span>${kindTag(e)}</div>
      </div>`;
    const chk = `<input type="checkbox" class="ck" data-check="${escA(e.path)}"
        ${S.sel.has(e.path)?"checked":""} aria-label="Pilih ${escA(e.name)}">`;
    return `<div class="row" style="animation-delay:${d}ms">
        ${chk}
        <div class="thumb">${ico(e.kind,"ic")}${tb}</div>
        <div class="name" data-open="${escA(e.path)}" data-dir="${e.dir?1:0}"
             title="${escA(e.name)}">${esc(e.name)}</div>
        <div class="meta">${kindTag(e)}</div>
        <div class="meta mono">${e.dir?"—":human(e.size)}</div>
        <div class="meta mono">${when(e.mtime)}</div>
        <div class="act">
          <button class="iconbtn" data-qr="${escA(e.path)}" data-isdir="${e.dir?1:0}" title="QR">${UI.qr}</button>
          ${e.dir?"":`<button class="iconbtn" data-hash="${escA(e.path)}" title="Salin SHA-256">${UI.hash}</button>`}
          <a class="iconbtn" href="${e.dir?`/zip?p=${enc(e.path)}`:dlUrl(e.path)}" title="Download">${UI.dl}</a>
        </div>
      </div>`;
  }).join("");
  if(S.total>S.entries.length) box.insertAdjacentHTML("beforeend",
    `<div class="empty" style="padding:18px 0">
       <button class="btn" id="more">Muat ${Math.min(2000,S.total-S.entries.length)} item lagi</button>
       <div style="margin-top:8px;font-size:12.5px">Nampilin ${S.entries.length} dari ${S.total}</div>
     </div>`);
  const more=$("#more"); if(more) more.onclick=loadMore;
}
const esc = s => String(s).replace(/[&<>]/g, c=>({"&":"&amp;","<":"&lt;",">":"&gt;"}[c]));
const escA = s => esc(s).replace(/"/g,"&quot;");

/* Lazy-load bawaan browser: kalau thumbnail gagal, <img> dibuang dan ikonnya
   yang keliatan lagi - jadi nggak ada state kosong. */
const isPdf = e => !e.dir && /\.pdf$/i.test(e.name);
const isVideo = e => !e.dir && e.kind === "video";
const thumbTag = e => {
  const img = (CFG.thumbs && (e.kind === "image" || (CFG.pdfThumbs && isPdf(e))))
    ? `<img class="tb${isPdf(e)?' pdf':''}" loading="lazy" decoding="async" alt="" src="/thumb?p=${enc(e.path)}"
         onload="this.classList.add('on')" onerror="this.remove()">` : "";
  const v = isVideo(e)
    ? `<video class="vp" preload="metadata" muted loop playsinline data-src="/dl?p=${enc(e.path)}"></video>`
    : "";
  return img + v;
};

/* Video preview ala YouTube: play muted selagi di-hover, berhenti pas ditinggal.
   Delegated biar element yang baru di-render langsung kena. */
let preview = null;
function startPreview(host){
  const v = host.querySelector("video.vp");
  if(!v || !v.dataset.src || preview === v) return;
  stopPreview();
  preview = v;
  if(!v.src) v.src = v.dataset.src;
  v.currentTime = 0.5;
  v.play().catch(()=>{});
}
function stopPreview(){
  if(!preview) return;
  const v = preview; preview = null;
  try{ v.pause(); }catch(e){}
}
document.addEventListener("mouseover", ev=>{
  const host = ev.target.closest(".ph,.thumb");
  if(host) startPreview(host);
});
document.addEventListener("mouseout", ev=>{
  const host = ev.target.closest(".ph,.thumb");
  if(!host){ stopPreview(); return; }
  if(!host.contains(ev.relatedTarget)) stopPreview();
});

function syncUpload(){
  const ub=$("#ub"); if(!ub) return;
  ub.hidden = !CFG.canUpload;
  document.body.style.paddingBottom = CFG.canUpload ? "96px" : "24px";
}

/* ---------- polling: daftar bagikan bisa berubah kapan aja dari terminal ---------- */
async function checkRev(){
  if(S.loading) return;
  try{
    const r=await fetch("/api/rev"); if(!r.ok) return;
    const d=await r.json();
    if(S.rev!==undefined && d.rev!==S.rev) load(S.path);
  }catch(e){}
}
setInterval(()=>{ if(!document.hidden) checkRev(); }, 3000);
// HP yang baru dibuka lagi jangan nunggu tick berikutnya
addEventListener("visibilitychange", ()=>{ if(!document.hidden) checkRev(); });

/* ---------- QR ---------- */
function showQR(url, title, sub){
  $("#sheetTitle").textContent=title;
  $("#sheetSub").textContent=sub||"";
  $("#sheetUrl").textContent=url;
  $("#qrbox").innerHTML=`<img alt="QR" src="/qr?u=${enc(url)}">`;
  $("#modal").classList.add("on");
  $("#sheetCopy").onclick=()=>copy(url,"Link disalin");
  $("#sheetOpen").onclick=()=>{ location.href=url; };
}

/* ---------- upload ---------- */
function upload(files){
  [...files].forEach(f=>{
    const el=document.createElement("div"); el.className="job";
    el.innerHTML=`<span class="nm">${esc(f.name)}</span>
      <span class="pb"><i style="width:0%"></i></span><span class="st">0%</span>`;
    $("#jobs").append(el);
    const bar=el.querySelector("i"), st=el.querySelector(".st");
    const xhr=new XMLHttpRequest(); const t0=Date.now();
    xhr.open("PUT", `/up?name=${enc(f.name)}&p=${enc(S.path)}`);
    xhr.upload.onprogress=ev=>{
      if(!ev.lengthComputable) return;
      const pct=ev.loaded/ev.total, sec=(Date.now()-t0)/1000;
      const sp=ev.loaded/Math.max(sec,.001), left=(ev.total-ev.loaded)/Math.max(sp,1);
      bar.style.width=(pct*100).toFixed(1)+"%";
      st.textContent=`${human(sp)}/s · ${left>90?Math.round(left/60)+"m":Math.round(left)+"s"}`;
    };
    xhr.onload=()=>{ if(xhr.status<300){ bar.style.width="100%"; st.textContent="selesai ✓";
        setTimeout(()=>el.remove(),1800); load(S.path); }
      else { st.textContent="gagal"; el.style.color="var(--warn)";
        try{toast(JSON.parse(xhr.responseText).error,true)}catch(e){toast("Upload gagal",true)} } };
    xhr.onerror=()=>{ st.textContent="gagal"; toast("Upload gagal",true); };
    xhr.send(f);
  });
}

/* ---------- event ---------- */
document.addEventListener("click", ev=>{
  const t=ev.target.closest("[data-go],[data-open],[data-qr],[data-hash],[data-zip],[data-urls],[data-qrfolder],[data-selzip],[data-selclear]");
  if(!t) return;
  if(t.dataset.go!==undefined){ load(t.dataset.go); return; }
  if(t.dataset.open!==undefined){
    if(t.dataset.dir==="1") load(t.dataset.open);
    else location.href=`/dl?p=${enc(t.dataset.open)}`;
    return; }
  if(t.dataset.qr!==undefined){ ev.preventDefault();
    const p=t.dataset.qr, dir=t.dataset.isdir==="1";
    const u=abs(dir?`/zip?p=${enc(p)}`:dlUrl(p));
    showQR(u, p.split("/").pop(), dir?"Scan buat download folder (ZIP)":"Scan buat download file ini");
    return; }
  if(t.dataset.qrfolder!==undefined){
    showQR(abs(S.path?`/?p=${enc(S.path)}`:"/"), S.path.split("/").pop()||"Semua file",
           "Scan buat buka daftar ini"); return; }
  if(t.dataset.zip!==undefined){ location.href=`/zip?p=${enc(S.path)}`; return; }
  if(t.dataset.urls!==undefined){
    fetch(`/urls?p=${enc(S.path)}`).then(r=>r.text()).then(txt=>{
      const n=txt.trim().split("\n").length;
      copy(txt, `${n} URL disalin — pakai: aria2c -i list.txt`); }); return; }
  if(t.dataset.hash!==undefined){ ev.preventDefault(); toast("Ngitung SHA-256...");
    fetch(`/api/hash?p=${enc(t.dataset.hash)}`).then(r=>r.json())
      .then(d=>d.sha256?copy(d.sha256,"SHA-256 disalin"):toast("Gagal",true)); return; }
  if(t.dataset.selzip!==undefined){
    if(!S.sel.size) return;
    const q=[...S.sel].map(x=>"p="+enc(x)).join("&");
    location.href="/zip?"+q; return; }
  if(t.dataset.selclear!==undefined){ S.sel.clear(); render(); return; }
});
document.addEventListener("change", ev=>{
  const cb=ev.target.closest("input.ck");
  if(!cb) return;
  if(cb.checked) S.sel.add(cb.dataset.check); else S.sel.delete(cb.dataset.check);
  render();
});
$("#modal").onclick = e => { if(e.target.id==="modal") $("#modal").classList.remove("on"); };
addEventListener("keydown", e=>{ if(e.key==="Escape") $("#modal").classList.remove("on");
  if(e.key==="/" && document.activeElement!==$("#q")){ e.preventDefault(); $("#q").focus(); } });
$("#q").oninput = e => { S.filter=e.target.value; render(); };
$("#themeBtn").onclick = toggleTheme;
$("#viewBtn").onclick = () => { localStorage.setItem("ls-view", viewMode()==="grid"?"list":"grid"); render(); };
$("#codeChip").onclick = () => showQR(abs("/"), "Kode akses "+CFG.code, "Scan buat masuk tanpa ngetik");
$("#qrTop").onclick = () => showQR(abs(S.path?`/?p=${enc(S.path)}`:"/"), "Halaman ini", "Scan buat buka di HP");

$("#pick").onchange = e => { upload(e.target.files); e.target.value=""; };
let depth=0;
addEventListener("dragenter", e=>{ if(!CFG.canUpload) return; e.preventDefault();
  if(++depth===1) $("#drop").classList.add("on"); });
addEventListener("dragover", e=>{ if(CFG.canUpload) e.preventDefault(); });
addEventListener("dragleave", e=>{ if(!CFG.canUpload) return;
  if(--depth<=0){ depth=0; $("#drop").classList.remove("on"); } });
addEventListener("drop", e=>{ if(!CFG.canUpload) return; e.preventDefault(); depth=0;
  $("#drop").classList.remove("on");
  if(e.dataTransfer.files.length) upload(e.dataTransfer.files); });
$("#qrTop").innerHTML = UI.qr;
syncUpload();
applyTheme();
load(S.path);
"""

SHELL = r"""<!doctype html>
<html lang="id"><head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1,viewport-fit=cover">
<meta name="color-scheme" content="light dark">
<title>__TITLE__</title>
<style>__CSS__</style>
</head><body>
<div class="bgfx"></div>
<header class="top">
  <div class="brand">
    <svg viewBox="0 0 24 24" fill="none" stroke="url(#g)" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round">
      <defs><linearGradient id="g" x1="0" y1="0" x2="1" y2="1">
        <stop offset="0" stop-color="var(--accent)"/><stop offset="1" stop-color="var(--accent2)"/>
      </linearGradient></defs>
      <circle cx="6" cy="12" r="2.6"/><circle cx="18" cy="6" r="2.6"/><circle cx="18" cy="18" r="2.6"/>
      <path d="m8.4 10.7 7.2-3.4M8.4 13.3l7.2 3.4"/>
    </svg>
    <span>share<b>·</b>lan</span>
  </div>
  <div class="spacer"></div>
  <button class="chip" id="codeChip" title="Kode akses — klik buat QR">__CODE__ <small>kode</small></button>
  <button class="iconbtn" id="qrTop" title="QR halaman ini"></button>
  <button class="iconbtn" id="themeBtn" title="Ganti tema"></button>
</header>
<main>
  <div class="bar">
    <nav class="crumbs" id="crumbs"></nav>
    <div class="tools">
      <label class="search">
        <svg viewBox="0 0 24 24"><circle cx="11" cy="11" r="7"/><path d="m20 20-3.5-3.5"/></svg>
        <input id="q" type="search" placeholder="Cari di folder ini…" aria-label="Cari">
      </label>
      <button class="iconbtn" id="viewBtn" title="Ganti tampilan"></button>
    </div>
  </div>
  <div class="actions" id="folderActions"></div>
  <div id="selbar"></div>
  <div id="content" class="list"></div>
</main>
__UPLOAD__
<div class="drop" id="drop"><div>Lepas di sini buat dikirim</div></div>
<div class="modal" id="modal"><div class="sheet">
  <h3 id="sheetTitle"></h3><p id="sheetSub"></p>
  <div class="qrbox" id="qrbox"></div>
  <div class="url" id="sheetUrl"></div>
  <div class="row2">
    <button id="sheetCopy">Salin link</button>
    <button id="sheetOpen" class="primary">Buka</button>
  </div>
</div></div>
<div class="toasts" id="toasts"></div>
<script>__JS__</script>
</body></html>"""

UPLOAD_BAR = r"""<div class="ub" id="ub" hidden>
  <div class="jobs" id="jobs"></div>
  <div class="inner">
    <label class="btn primary" for="pick">
      <svg viewBox="0 0 24 24"><path d="M12 20V8m0 0-4 4m4-4 4 4M4 4h16"/></svg>Kirim file
    </label>
    <input id="pick" type="file" multiple hidden>
    <div class="hint">Atau seret file ke mana aja di halaman ini</div>
  </div>
</div>"""

ERROR_PAGE = r"""<!doctype html><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>__CODE__</title>
<style>body{margin:0;min-height:100vh;display:grid;place-items:center;background:#0a0c12;
color:#e9ebf5;font:15px/1.6 -apple-system,BlinkMacSystemFont,"Segoe UI",sans-serif}
@media(prefers-color-scheme:light){body{background:#f4f6fb;color:#121420}}
div{text-align:center;padding:30px}b{font-size:44px;display:block;opacity:.25;letter-spacing:-.03em}
a{color:#8b7bff}</style>
<div><b>__CODE__</b>__MSG__<p><a href="/">← balik ke daftar file</a></p></div>"""

LOGIN_PAGE = r"""<!doctype html><html lang="id"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1,viewport-fit=cover">
<meta name="color-scheme" content="light dark"><title>Masukin kode — share·lan</title>
<style>__CSS__
.wrap{min-height:100vh;display:grid;place-items:center;padding:24px}
.box{width:100%;max-width:340px;text-align:center;background:var(--card);
  border:1px solid var(--line);border-radius:22px;padding:30px 26px;box-shadow:var(--shadow);
  backdrop-filter:blur(14px);animation:pop .3s}
.box h1{margin:14px 0 4px;font-size:19px;letter-spacing:-.02em}
.box p{margin:0 0 20px;color:var(--muted);font-size:13px}
.box input{width:100%;height:58px;text-align:center;font-size:27px;font-weight:650;
  letter-spacing:.42em;text-indent:.42em;border-radius:14px;border:1px solid var(--line);
  background:var(--card2);outline:none;transition:border-color .15s}
.box input:focus{border-color:var(--accent)}
.box button{width:100%;height:46px;margin-top:12px;border-radius:14px;font-weight:650;
  background:linear-gradient(135deg,var(--accent),var(--accent2));color:#fff}
.err{color:var(--warn);font-size:13px;margin-top:14px;font-weight:550}
.logo{width:44px;height:44px}</style></head><body>
<div class="bgfx"></div>
<div class="wrap"><form class="box" method="post" action="/login">
  <svg class="logo" viewBox="0 0 24 24" fill="none" stroke="var(--accent)" stroke-width="1.7"
       stroke-linecap="round" stroke-linejoin="round">
    <circle cx="6" cy="12" r="2.6"/><circle cx="18" cy="6" r="2.6"/><circle cx="18" cy="18" r="2.6"/>
    <path d="m8.4 10.7 7.2-3.4M8.4 13.3l7.2 3.4"/></svg>
  <h1>Masukin kode akses</h1>
  <p>Minta kodenya ke yang lagi bagiin file</p>
  <input name="code" inputmode="numeric" pattern="[0-9]*" autocomplete="off"
         maxlength="12" autofocus aria-label="Kode akses">
  <input type="hidden" name="next" value="__NEXT__">
  <button type="submit">Masuk</button>
  __ERR__
</form></div></body></html>"""


def login_page(err="", nxt="/"):
    return (
        LOGIN_PAGE.replace("__CSS__", CSS)
        .replace("__NEXT__", html.escape(nxt, quote=True))
        .replace("__ERR__", f'<div class="err">{html.escape(err)}</div>' if err else "")
    )


def page_html():
    try:
        initial_target = resolve(ST.initial_path)
    except (Missing, Denied):
        initial_target = None
    cfg = {
        "initial": ST.initial_path,
        "code": ST.code,
        "canUpload": can_upload_here(initial_target),
        "thumbs": HAVE_PIL,
        "pdfThumbs": HAVE_PDF,
        "singleRoot": ST.single_root,
    }
    title = os.path.basename(next(iter(ST.mounts))) if len(ST.mounts) == 1 else "share·lan"
    return (
        SHELL.replace("__CSS__", CSS)
        .replace("__JS__", JS.replace("__CFG__", json.dumps(cfg)))
        .replace("__TITLE__", html.escape(title))
        .replace("__CODE__", html.escape(ST.code))
        .replace("__UPLOAD__", UPLOAD_BAR)  # selalu ada, disembunyiin lewat JS
    )
