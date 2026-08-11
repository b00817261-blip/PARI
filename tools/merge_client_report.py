"""Fold the Pepco client report into the internal dashboard.

The two sites grew from the same template, so the client file's stylesheet is a
superset of the internal one's and its raw-table widget is strictly better
(sortable, filterable). This builds the merged page on the CLIENT file and adds
the internal-only pages to it, rather than the other way round.

Overlapping figures are resolved to a single home — see DROP below.

    python3 tools/merge_client_report.py --client <path to client index.html> \
                                         --internal <path to internal index.html> \
                                         --out index.html
"""
import argparse, json, re, sys

ap = argparse.ArgumentParser()
ap.add_argument('--client', required=True)
ap.add_argument('--internal', required=True)
ap.add_argument('--out', required=True)
a = ap.parse_args()

C = open(a.client, encoding='utf-8').read()
I = open(a.internal, encoding='utf-8').read()

DATA = re.compile(r'const D\s*=\s*(\{.*?\});\s*\n', re.S)
DC = json.loads(DATA.search(C).group(1))
DI = json.loads(DATA.search(I).group(1))

# Figures the client report already covers, and covers better: its delivery
# performance carries strict/+3/+7 definitions, a weekly series and a per-day
# split; its customs card has recorded causes the D&D tracker does not hold;
# its CRD reasons are windowed and broken out per supplier.
DROP = ['delivery', 'delivery_raw', 'customs', 'customs_raw',
        'crd_split', 'crd_top', 'crd_raw']
kept = {k: v for k, v in DI.items() if k not in DROP}
collisions = set(kept) & set(DC)
if collisions:
    sys.exit(f'key collision between the two datasets: {sorted(collisions)}')
D = {**DC, **kept}

# Internal raw tables move into the client's RAWS registry so they inherit its
# sortable/filterable table widget.
RAWMAP = {'queue': 'queue_raw', 'carriers_all': 'carriers_raw', 'allocation': 'allocation_raw',
          'rejections': 'rejections_raw', 'demurrage': 'demurrage_raw', 'stale': 'stale_raw',
          'docver': 'docver_raw'}
for rk, dk in RAWMAP.items():
    D['raw'][rk] = D.pop(dk)

RAWSRC_ADD = {
    'queue': 'PO Milestone Performance — open overdue queue',
    'carriers_all': 'PEPCO Carrier Scoring — every month',
    'allocation': 'Nomination TEU per Week · 2026-07-24',
    'rejections': 'Booking Rejection Analysis',
    'demurrage': 'Demurrage & Detention Tracker',
    'stale': 'Latest Delivery Data – PEPCO',
    'docver': 'Incomplete Reason – Shipping Document Verification',
}


HEADER_AND_STALE_JS = """
document.getElementById('asof').textContent = D.defs.as_of;
// Both halves normally build from the same folder on the same day; only call out
// the client date when a refresh has left the two out of step.
if (D.perf.asof === D.defs.as_of) {
  document.getElementById('asof2').closest('.stamp').innerHTML =
    'data as of <b>' + D.defs.as_of + '</b> \\u00b7 refreshed 4\\u00d7/day';
} else {
  document.getElementById('asof2').textContent = D.perf.asof;
}
// A section whose extract was not supplied keeps its previous figures. Say so on
// the card itself, so nothing stale reads as current under today's stamp.
(D.not_updated || []).forEach(function(u){
  var el = u.anchor && document.getElementById(u.anchor);
  if (!el) return;
  if (el.nextElementSibling && el.nextElementSibling.dataset.stale) return;
  el.insertAdjacentHTML('afterend',
    '<div data-stale="1" class="note" style="color:var(--rust-ink);font-weight:600">' +
    'Not refreshed \\u2014 still the ' + (u.asof || 'previous') +
    ' extract. Needs the ' + u.needs + ' export.</div>');
});
"""

FOCUS_JS = r'''
// ---- drill-down -----------------------------------------------------------
// Clicking a country has to answer "what is urgent here", not hand over a
// table. Everything below is computed live from the queue rows already in the
// page, so the focus panel can be narrowed by origin, stage, supplier or state
// without another dataset.
let focus = {origin:null, stage:null, supplier:null, state:null};
function focusRows(){
  return (RAWS.queue||[]).filter(r =>
    (!focus.origin   || r.Origin   === focus.origin) &&
    (!focus.stage    || r.Stage    === focus.stage) &&
    (!focus.supplier || r.Supplier === focus.supplier) &&
    (!focus.state    || r.State    === focus.state));
}
function tally(rows, key){
  const m = new Map();
  rows.forEach(r => { const k = r[key];
    const e = m.get(k) || {k, overdue:0, urgent:0, worst:0};
    if (r.State === 'Overdue'){ e.overdue++; e.worst = Math.max(e.worst, r['Days overdue']||0); }
    else e.urgent++;
    m.set(k, e); });
  return [...m.values()].sort((a,b) => (b.overdue+b.urgent) - (a.overdue+a.urgent));
}
function focusList(rows, key, kind, limit){
  const t = tally(rows, key).slice(0, limit), mx = Math.max(1, ...t.map(x => x.overdue + x.urgent));
  if (!t.length) return '<div class="note">Nothing open here.</div>';
  return t.map(x => `<div class="bar-row focus-go" data-kind="${kind}" data-val="${String(x.k).replace(/"/g,'&quot;')}" style="cursor:pointer">
      <div class="bar-lab" style="width:${kind==='supplier'?200:130}px;font-size:12.5px;color:var(--sea)">${x.k} →</div>
      <div class="bar-track" style="display:flex">
        <div style="width:${(x.overdue/mx*100).toFixed(1)}%;background:var(--rust)"></div>
        <div style="width:${(x.urgent/mx*100).toFixed(1)}%;background:var(--amber-bar)"></div>
      </div>
      <div class="bar-val" style="width:150px;font-size:12px;white-space:nowrap">${fmt(x.overdue)} od${x.urgent?' · '+fmt(x.urgent)+' urg':''}</div>
    </div>`).join('');
}
function crumb(label, kind){
  return `<button class="chip on focus-clear" data-kind="${kind}" style="margin-right:6px">${label} ×</button>`;
}
function renderFocus(){
  const box = document.getElementById('act-focus');
  if (!focus.origin && !focus.stage && !focus.supplier && !focus.state){ box.style.display = 'none'; return; }
  const rows = focusRows();
  const od = rows.filter(r => r.State === 'Overdue');
  const ur = rows.filter(r => r.State === 'Urgent');
  const days = od.map(r => r['Days overdue']||0).sort((a,b) => a-b);
  const med = days.length ? days[Math.floor(days.length/2)] : 0;
  const orders = new Set(rows.map(r => r.Order)).size;
  const worst = [...od].sort((a,b) => (b['Days overdue']||0) - (a['Days overdue']||0)).slice(0, 8);
  const soon  = [...ur].sort((a,b) => (a['Days to due']||0) - (b['Days to due']||0)).slice(0, 8);
  const show  = worst.length ? worst : soon;
  box.style.display = 'block';
  box.innerHTML = `
    <div style="margin-bottom:10px">
      ${focus.origin   ? crumb(focus.origin, 'origin') : ''}
      ${focus.stage    ? crumb(focus.stage, 'stage') : ''}
      ${focus.supplier ? crumb(focus.supplier, 'supplier') : ''}
      ${focus.state    ? crumb(focus.state, 'state') : ''}
      <button class="chip focus-clear" data-kind="all">clear all</button>
    </div>
    <div class="headline" style="margin-bottom:10px">
      <div class="bignum" style="color:var(--rust)">${fmt(rows.length)}</div>
      <div class="hmeta">open items across <b>${fmt(orders)}</b> orders<br>
        <span class="mono">${fmt(od.length)} overdue · ${fmt(ur.length)} urgent${od.length?` · median ${med}d late, worst ${days[days.length-1]}d`:''}</span></div>
      <div style="margin-left:auto;align-self:center">
        <button class="chip focus-state" data-val="Overdue"${focus.state==='Overdue'?' style="border-color:var(--rust);color:var(--rust)"':''}>only overdue</button>
        <button class="chip focus-state" data-val="Urgent"${focus.state==='Urgent'?' style="border-color:var(--amber);color:var(--amber)"':''}>only urgent</button>
      </div>
    </div>
    <div class="grid2">
      <div><div class="lvl" style="margin-top:6px">Where it is stuck</div>${focusList(rows,'Stage','stage',9)}</div>
      <div><div class="lvl" style="margin-top:6px">Who is behind it</div>${focusList(rows,'Supplier','supplier',8)}</div>
    </div>
    <div class="lvl" style="margin:18px 0 8px">${worst.length?'Worst right now':'Closest to breaching'}</div>
    <div class="hgrid"><table><thead><tr><th>Order</th><th>Supplier</th><th>Stage</th><th>Due</th><th class="num">${worst.length?'Days overdue':'Days to due'}</th></tr></thead><tbody>
      ${show.map(r => `<tr><td class="mono" style="font-size:12px">${r.Order}</td><td style="font-size:12.5px">${r.Supplier}</td>
        <td style="font-size:12.5px">${r.Stage}</td><td class="mono" style="font-size:12px">${r.Due}</td>
        <td class="num" style="color:${worst.length?'var(--bad)':'var(--amber)'};font-weight:600">${worst.length?r['Days overdue']+'d':r['Days to due']+'d'}</td></tr>`).join('')}
    </tbody></table></div>
    <button class="rawbtn" id="focus-all">View all ${fmt(rows.length)} rows in the raw table</button>`;

  box.querySelectorAll('.focus-go').forEach(el => el.onclick = () => {
    focus[el.dataset.kind] = el.dataset.val; renderFocus(); });
  box.querySelectorAll('.focus-clear').forEach(el => el.onclick = () => {
    if (el.dataset.kind === 'all') focus = {origin:null, stage:null, supplier:null, state:null};
    else focus[el.dataset.kind] = null;
    if (!focus.origin) { org = 'ALL'; renderStrip(); }
    renderFocus(); });
  box.querySelectorAll('.focus-state').forEach(el => el.onclick = () => {
    focus.state = focus.state === el.dataset.val ? null : el.dataset.val; renderFocus(); });
  document.getElementById('focus-all').onclick = () => {
    const p = document.getElementById('raw-queue');
    if (!p._mounted) { mountRaw(p, RAWS.queue, 'queue'); p._mounted = true; }
    p.style.display = 'block';
    [['Origin', focus.origin], ['Stage', focus.stage], ['State', focus.state]].forEach(([c, v]) => {
      const s = p.querySelector('select[data-fc="' + c + '"]');
      if (s) { s.value = v || ''; s.dispatchEvent(new Event('change')); }
    });
    p.scrollIntoView({behavior:'smooth', block:'nearest'});
  };
  box.scrollIntoView({behavior:'smooth', block:'nearest'});
}
'''


def section(html, sid):
    m = re.search(r'(<section class="page[^"]*" id="p-%s">.*?</section>)' % sid, html, re.S)
    if not m:
        sys.exit(f'section p-{sid} not found')
    return m.group(1)


def func(js, name):
    """Grab a top-level function by brace matching."""
    i = js.index('\nfunction %s(' % name)
    j = js.index('{', i)
    depth, k = 0, j
    while True:
        if js[k] == '{':
            depth += 1
        elif js[k] == '}':
            depth -= 1
            if depth == 0:
                return js[i:k + 1]
        k += 1


IJS = I.split('<script>', 1)[1]

# ---------------------------------------------------------------- page markup
pipe = section(I, 'pipe')
origin = section(I, 'origin')
carr = section(I, 'carr')
dq = section(I, 'dq')
defs = section(I, 'defs')
dest = section(I, 'dest')

# Internal "Pipeline" becomes "Milestones" — the client report already owns the
# id p-pipe for its Due-at-DC page.
pipe = pipe.replace('id="p-pipe"', 'id="p-mile"')
pipe = pipe.replace('<h1>Pipeline health</h1>',
                    '<h1>Origin milestones</h1>')
pipe = pipe.replace(
    "<p class=\"sub\">Every process stage from cargo-ready to shipping docs. Green is flow; the red column is your team's queue.</p>",
    "<p class=\"sub\">Every process stage from cargo-ready to shipping docs — the origin-side detail behind the client report. Green is flow; the red column is your team's queue.</p>")

# Supplier league (open overdue milestones) belongs with the milestones it comes
# from. Its sibling CRD-reasons card is dropped — Performance owns that.
league = re.search(r'(<div class="card">\s*<div class="lvl">Supplier league.*?</div>\s*</div>)', origin, re.S).group(1)
pipe = pipe.replace('  <div class="method">', '  ' + league + '\n  <div class="method">')

# The headline number has to lead somewhere: overdue and urgent side by side,
# split by origin, with each row clicking through to the filtered detail.
ACTION_CARD = """  <div class="card attn" id="act-card">
    <div class="lvl">Action queue \u00b7 open right now</div>
    <div class="headline" style="margin-bottom:4px">
      <div class="big" style="font-size:44px;color:var(--rust)" id="act-od"></div>
      <div class="hmeta" id="act-sub"></div>
      <button class="rawbtn solid" data-raw="queue" id="act-raw"
              style="margin:0 0 0 auto;align-self:center">View raw data</button>
    </div>
    <div class="lvl" style="margin:16px 0 8px">By origin \u2014 click a country to jump to its detail</div>
    <div id="act-origins"></div>
    <div class="lvl" style="margin:18px 0 8px">By stage</div>
    <div id="act-stages"></div>
    <div id="act-focus" style="display:none;margin-top:22px;padding-top:18px;border-top:2px solid var(--line)"></div>
    <div class="note">Overdue = past its due date and still not done. Urgent = past the urgent date but not yet due, so it is the last chance to act before it breaches. Both are chaseable today; everything else is either done or not yet due.</div>
    <div class="rawwrap" id="raw-queue"></div>
  </div>
"""
pipe = pipe.replace('  <div class="card hgrid">', ACTION_CARD + '  <div class="card hgrid">', 1)
# the strip already has its own copy of this drill-down further down the page
pipe = pipe.replace('<button class="rawbtn" data-raw="queue">View raw data \u00b7 open overdue queue</button>', '')
pipe = pipe.replace('<div class="rawwrap" id="raw-queue"></div>', '', 1)

# Demurrage is a third place cargo stalls, so it joins the client's Watch list.
dem_card = re.search(r'(<div class="card" style="border-left:4px solid var\(--rust\).*?id="raw-demurrage"></div>\s*</div>)', dest, re.S).group(1)
dem_card = dem_card.replace('style="border-left:4px solid var(--rust);border-radius:0 9px 9px 0"',
                            'style="border-left:4px solid var(--rust);border-radius:0 6px 6px 0"')

# ------------------------------------------------------------------ assemble
out = C

out = out.replace('<title>MOOV × Pepco — Delivery performance</title>',
                  '<title>MOOV Ops — Internal control</title>')
out = out.replace('<div style="font-size:13px;color:#C9DFF3">Client delivery report</div>',
                  '<div style="font-size:13px;color:#C9DFF3">Client report + internal control — all teams</div>')
out = out.replace(
    '<div class="ribbon"><b>Prototype.</b> On-time definition pending Pepco sign-off — use the definition selector to compare. All figures computed from live report extracts.</div>',
    '<div class="ribbon" style="background:#F6E3E0;border-bottom:1px solid #E5C4BE;color:#8A2A1C">'
    '<b style="color:#B3251A">Internal.</b> Pages 01&ndash;06 are the client report; 07&ndash;10 add carrier detail, '
    'rejections and data-quality queues that are <b style="color:#B3251A">not for client distribution</b>. '
    'On-time definition still pending Pepco sign-off.</div>')
out = out.replace("data as of <b id=\"asof\"></b> · refreshed 4×/day",
                  "data as of <b id=\"asof\"></b> · client views <b id=\"asof2\"></b> · refreshed 4×/day")

# extra colour tokens the internal markup references
out = out.replace('  --amber:#B97A1C; --amber-tint:#FBF3E4;\n',
                  '  --amber:#96620E; --amber-bar:#C8871B; --amber-tint:#FBF3E4;\n'
                  '  --rust-ink:#B8420F; --neutral:#6B7A8D;\n')

# nav
out = out.replace(
    '  <button data-p="trends"><span class="navnum">06</span>Monthly</button>\n',
    '  <button data-p="trends"><span class="navnum">06</span>Monthly</button>\n'
    '  <div style="margin:14px 0 6px 14px;font-family:\'IBM Plex Mono\',monospace;font-size:10px;'
    'letter-spacing:.12em;text-transform:uppercase;color:var(--ink-3)">Internal only</div>\n'
    '  <button data-p="mile"><span class="navnum">07</span>Milestones</button>\n'
    '  <button data-p="carr"><span class="navnum">08</span>Carriers</button>\n'
    '  <button data-p="dq"><span class="navnum">09</span>Data quality</button>\n'
    '  <button data-p="defs"><span class="navnum">10</span>Definitions</button>\n')

# demurrage into the watch list, ahead of its closing method note
out = out.replace(
    '  <div class="method"><b>Source.</b> Transshipment performance and customs clearance extracts.',
    '  ' + dem_card + '\n  <div class="method"><b>Source.</b> Transshipment performance and customs clearance extracts.')

# internal pages
out = out.replace('\n</main>', '\n' + '\n\n'.join([pipe, carr, dq, defs]) + '\n\n</main>')

# ---------------------------------------------------------------------- JS
# module-level state the internal renderers close over
fns = "let org='ALL';\n" + FOCUS_JS
fns += '\n'.join(func(IJS, n) for n in
                ['tableFrom', 'renderStrip', 'renderKpi', 'renderDoc', 'renderCarr', 'renderDq', 'renderDefs'])

# renderOrigin drops its CRD half; renderDest keeps only demurrage
fns += '''
function actBars(rows, key, click){
  const mx = Math.max(1, ...rows.map(r => r.overdue + r.urgent));
  return rows.map(r => {
    const tot = r.overdue + r.urgent;
    return `<div class="bar-row${click ? ' act-click' : ''}"${click ? ` data-go="${r[key]}" style="cursor:pointer"` : ''}>
      <div class="bar-lab" style="width:150px${click ? ';color:var(--sea);font-weight:600' : ''}">${r[key]}${click ? ' \u2192' : ''}</div>
      <div class="bar-track" style="display:flex">
        <div style="width:${(r.overdue / mx * 100).toFixed(1)}%;background:var(--rust)"></div>
        <div style="width:${(r.urgent / mx * 100).toFixed(1)}%;background:var(--amber-bar)"></div>
      </div>
      <div class="bar-val" style="width:212px;font-size:12px;white-space:nowrap">${fmt(r.overdue)} overdue${r.urgent ? ' \u00b7 ' + fmt(r.urgent) + ' urgent' : ''}</div>
    </div>`;
  }).join('');
}
function renderAction(){
  const A = D.action;
  document.getElementById('act-od').textContent = fmt(A.overdue);
  document.getElementById('act-sub').innerHTML =
    'milestones overdue right now<br><span class="mono">plus ' + fmt(A.urgent) +
    ' urgent \u2014 past the urgent date, not yet past due</span>';
  document.getElementById('act-raw').textContent = 'View raw data \u00b7 ' + fmt(A.overdue + A.urgent) + ' items';
  document.getElementById('act-origins').innerHTML = actBars(A.by_origin, 'origin', true);
  document.getElementById('act-stages').innerHTML = actBars(A.by_stage, 'stage', true);
  document.querySelectorAll('#act-stages .act-click').forEach(el => el.onclick = () => {
    focus = {origin: null, stage: el.dataset.go, supplier: null, state: null};
    renderFocus();
  });
  // clicking a country filters the stage strip to it, opens the drill-down
  // already filtered to that origin, and scrolls the two together
  document.querySelectorAll('#act-origins .act-click').forEach(el => el.onclick = () => {
    org = el.dataset.go; renderStrip();
    focus = {origin: el.dataset.go, stage: null, supplier: null, state: null};
    renderFocus();
  });
}
function renderLeague(){
  document.getElementById('lg-body').innerHTML=D.sup_league.map(r=>
    `<tr><td>${r.Supplier}</td><td class="num" style="color:var(--bad);font-weight:600">${r['Open overdue']}</td><td>${r['Worst stage']}</td><td class="num">${r['Max days']}d</td></tr>`).join('');
}
function renderDem(){
  document.getElementById('dm-n').textContent=fmt(D.demurrage.containers_at_risk);
  const dd=Object.entries(D.demurrage.by_dc), dmax=dd.length?dd[0][1]:1;
  document.getElementById('dm-dc').innerHTML=dd.map(([k,v])=>
    `<div class="bar-row"><div class="bar-lab" style="width:56px">${k}</div>
     <div class="bar-track"><div class="bar-fill" style="width:${(v/dmax*100).toFixed(1)}%;background:var(--rust)"></div></div>
     <div class="bar-val">${v}</div></div>`).join('');
  const dc=Object.entries(D.demurrage.by_carrier), cmx=dc.length?dc[0][1]:1;
  document.getElementById('dm-car').innerHTML=dc.map(([k,v])=>
    `<div class="bar-row"><div class="bar-lab mono" style="width:56px">${k}</div>
     <div class="bar-track"><div class="bar-fill" style="width:${(v/cmx*100).toFixed(1)}%;background:var(--sea)"></div></div>
     <div class="bar-val">${v}</div></div>`).join('');
}
'''

# the internal file's own raw-table helper is redundant here
boot_old = 'renderToday(); renderArr(); renderPipe(); renderPerfWk(); renderWatch(); renderTrends(); renderReliability(); renderTransit(); bindRaw();'
boot_new = (fns +
            "\nObject.assign(RAWSRC, " + json.dumps(RAWSRC_ADD) + ");\n" +
            boot_old.replace('bindRaw();',
                             'renderStrip(); renderKpi(); renderDoc(); renderLeague(); '
                             'renderAction(); renderCarr(); renderDem(); renderDq(); renderDefs(); bindRaw();') +
            HEADER_AND_STALE_JS)
out = out.replace(boot_old, boot_new)

# renderStrip only exists once the internal functions are in, so widen it here
out = out.replace(
    "<th class=\"num\">Open overdue</th><th class=\"num\">Med days</th>",
    "<th class=\"num\">Open overdue</th><th class=\"num\">Urgent</th><th class=\"num\">Med days</th>")
out = out.replace(
    '''<td class="num">${r.open_overdue?r.med_days+'d':'\u00b7'}</td>''',
    '''<td class="num" style="color:${r.urgent?'var(--amber)':'var(--ink-3)'};font-weight:${r.urgent?600:400}">${r.urgent||'\u00b7'}</td>
     <td class="num">${r.open_overdue?r.med_days+'d':'\u00b7'}</td>''')


# internal buttons point at the client registry keys
for old, new in [('data-raw="queue"', 'data-raw="queue"'), ('data-raw="queue2"', 'data-raw="queue"'),
                 ('data-raw="carriers"', 'data-raw="carriers_all"'), ('data-raw="customs2"', 'data-raw="customs"'),
                 ('data-raw="crd2"', 'data-raw="crd"'), ('data-raw="kpi"', 'data-raw="kpi_steps"')]:
    out = out.replace(old, new)
out = out.replace('id="raw-queue2"', 'id="raw-queue-2"')
out = out.replace('id="raw-carriers"', 'id="raw-carriers_all"')
out = out.replace('id="raw-kpi"', 'id="raw-kpi_steps"')
D['raw']['kpi_steps'] = D['kpi']
RAWSRC_ADD['kpi_steps'] = 'OHA KPI × PEPCO Weekly Volume'
out = out.replace("Object.assign(RAWSRC, " + json.dumps({k: v for k, v in RAWSRC_ADD.items() if k != 'kpi_steps'}) + ");",
                  "Object.assign(RAWSRC, " + json.dumps(RAWSRC_ADD) + ");")

# swap in the merged dataset last, so none of the surgery above can disturb it
out = DATA.sub(lambda m: 'const D = ' + json.dumps(D, separators=(',', ':'), ensure_ascii=False) + ';\n', out, count=1)

open(a.out, 'w', encoding='utf-8').write(out)
print(f'client keys {len(DC)} + internal kept {len(kept)} = {len(D)}')
print(f'dropped as duplicates: {DROP}')
print(f'raw tables: {len(D["raw"])}')
print(f'written {a.out} ({len(out)/1e6:.2f} MB)')
