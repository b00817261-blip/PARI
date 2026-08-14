"""Assemble the internal dashboard from the two source reports.

Ten pages inherited from the merge are regrouped into seven, ordered by where
cargo is in the journey rather than by which report a card came from:

    01 Act now  02 Origin  03 In transit  04 Destination
    05 Carriers 06 Trends  07 Data & definitions

Cards are lifted whole out of the two source files and placed by name, so the
renderers - which address elements by id - keep working wherever a card lands.
The client file is the base: its stylesheet is a superset of the internal one's
and its raw-data tables are sortable and filterable.

This page is INTERNAL ONLY. The client-facing report is a separate deployment.

    python3 tools/assemble.py --client <client index.html> \
                              --internal <internal index.html> --out index.html
"""
import argparse, json, re, sys

ap = argparse.ArgumentParser()
ap.add_argument('--client', required=True)
ap.add_argument('--internal', required=True)
ap.add_argument('--out', required=True)
a = ap.parse_args()

C = open(a.client, encoding='utf-8').read()
I = open(a.internal, encoding='utf-8').read()
DATA = re.compile(r'const D\s*=\s*(\{.*?\});?\s*\n', re.S)
DC, DI = json.loads(DATA.search(C).group(1)), json.loads(DATA.search(I).group(1))

# Figures the client report already covers, and covers better.
DROP = ['delivery', 'delivery_raw', 'customs', 'customs_raw',
        'crd_split', 'crd_top', 'crd_raw']
kept = {k: v for k, v in DI.items() if k not in DROP}
if set(kept) & set(DC):
    sys.exit(f'key collision: {sorted(set(kept) & set(DC))}')
D = {**DC, **kept}

RAWMAP = {'queue': 'queue_raw', 'carriers_all': 'carriers_raw', 'allocation': 'allocation_raw',
          'rejections': 'rejections_raw', 'demurrage': 'demurrage_raw', 'stale': 'stale_raw',
          'docver': 'docver_raw'}
for rk, dk in RAWMAP.items():
    D['raw'][rk] = D.pop(dk)
D['raw']['kpi_steps'] = D['kpi']

RAWSRC_ADD = {
    'queue': 'PO Milestone Performance - open overdue and urgent queue',
    'carriers_all': 'PEPCO Carrier Scoring - every month',
    'allocation': 'Nomination TEU per Week - 2026-07-24',
    'rejections': 'Booking Rejection Analysis',
    'demurrage': 'Demurrage & Detention Tracker',
    'stale': 'Latest Delivery Data - PEPCO',
    'docver': 'Incomplete Reason - Shipping Document Verification',
    'kpi_steps': 'OHA KPI x PEPCO Weekly Volume',
}


def cards(html):
    """Index every top-level card block by the element ids inside it."""
    markup = html[:html.index('<script>')]
    out, i = {}, 0
    while True:
        s = markup.find('<div class="card', i)
        if s < 0:
            return out
        depth = 0
        for m in re.finditer(r'<div\b|</div>', markup[s:]):
            depth += 1 if m.group(0) != '</div>' else -1
            if depth == 0:
                block = markup[s:s + m.end()]
                for eid in re.findall(r'id="([a-z0-9\-_]+)"', block):
                    out.setdefault(eid, block)
                i = s + m.end()
                break
        else:
            return out


CC, IC = cards(C), cards(I)


def card(src, needle):
    d = CC if src == 'c' else IC
    if needle not in d:
        sys.exit(f'card for id "{needle}" not found in {src}')
    return d[needle]


def snippet(html, needle, before=0):
    """A non-card block, e.g. the flow grid, plus optional preceding lines."""
    i = html.index('id="%s"' % needle)
    s = html.rindex('<div', 0, i)
    for _ in range(before):
        s = html.rindex('<div', 0, s)
    e = html.index('</div>', i) + 6
    return html[s:e]


ACTION_CARD = """  <div class="card attn" id="act-card">
    <div class="lvl alert">Action queue \u00b7 open right now</div>
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

PAGES = [
    ('act', 'Act now',
     'Everything open right now, ordered by what happens if you ignore it today. '
     'Click any country, stage or supplier to see what is actually behind the number.',
     [ACTION_CARD, card('c', 't-risk-n'),
      '<div class="lvl" style="margin:24px 0 10px">Context - what moved today</div>',
      '<p class="sub" id="t-date" style="margin-bottom:12px"></p>',
      snippet(C, 't-flow')]),
    ('origin', 'Origin',
     'Cargo-ready to vessel departure: our own milestones, the client KPI steps we are '
     'scored on, and who is holding things up.',
     [card('i', 'strip'), card('i', 'kpi-bars'), card('i', 'lg-body'),
      card('c', 'cr-win'), card('i', 'dv-n')]),
    ('transit', 'In transit',
     'Between departure and arrival: what lands this week, how far the ETAs can be '
     'trusted, and what is sitting still at a transit port.',
     ['<p class="sub" id="a-sub" style="margin-bottom:14px"></p>',
      snippet(C, 'a-stats'), card('c', 'a-dc'), card('c', 'a-raw'),
      card('c', 'rel-bars'), card('c', 'ts-n')]),
    ('dest', 'Destination',
     'Arriving at the DC: the inbound workload, whether it landed on time, and the two '
     'places it stalls once it is in country.',
     [card('c', 'pi-toggle'),
      '<div class="lvl" style="margin:24px 0 8px">Did it land on time</div>',
      '<p class="sub" id="w-sub" style="margin-bottom:12px"></p>',
      card('c', 'w-pct'),
      '<div class="grid2">' + card('c', 'w-reasons') + card('c', 'w-sup') + '</div>',
      '<div class="lvl" style="margin:24px 0 8px">Where it stalls in country</div>',
      card('c', 'cu-win'), card('i', 'dm-n'),
      '<div class="method" id="w-method"></div>']),
    ('carr', 'Carriers',
     'Vendor management: league table, allocation compliance and rejected bookings.',
     [card('i', 'cl-body'),
      '<div class="grid2">' + card('i', 'al-body') + card('i', 'rj-n') + '</div>']),
    ('trends', 'Trends',
     'The longer view - weekly and monthly performance, volume, and how long each leg '
     'is taking.',
     [card('c', 'w-trend'), card('c', 'tr-ontime'), card('c', 'tr-teu'),
      card('c', 'tt-stats'), card('c', 'pt-stats')]),
    ('data', 'Data & definitions',
     'How much to trust everything above: the queues that are not yet measurable, and '
     'what every figure on this dashboard actually counts.',
     [card('i', 'st-n'), card('i', 'uc-body'), card('i', 'df-rule'),
      card('i', 'df-done'), card('i', 'df-cards'), card('i', 'df-caveats')]),
]

# ------------------------------------------------------------------ assemble
out = C
out = out.replace('<title>MOOV x Pepco - Delivery performance</title>',
                  '<title>MOOV Ops - Internal control</title>')
out = out.replace('<title>MOOV \u00d7 Pepco \u2014 Delivery performance</title>',
                  '<title>MOOV Ops \u2014 Internal control</title>')
out = out.replace('<div style="font-size:13px;color:#C9DFF3">Client delivery report</div>',
                  '<div style="font-size:13px;color:#C9DFF3">Internal control \u2014 all teams</div>')
out = re.sub(r'<div class="ribbon">.*?</div>',
             '<div class="ribbon" style="background:#F6E3E0;border-bottom:1px solid #E5C4BE;color:#8A2A1C">'
             '<b style="color:#B3251A">Internal.</b> Carrier scorecards, booking rejections, named '
             'suppliers and data-quality queues \u2014 <b style="color:#B3251A">not for client '
             'distribution</b>. The client report is a separate site. On-time definition still pending '
             'Pepco sign-off.</div>', out, count=1, flags=re.S)
out = out.replace('data as of <b id="asof"></b> \u00b7 refreshed 4\u00d7/day',
                  'data as of <b id="asof"></b> \u00b7 client views <b id="asof2"></b> \u00b7 refreshed 4\u00d7/day')
out = out.replace('  --amber:#B97A1C; --amber-tint:#FBF3E4;\n',
                  '  --amber:#96620E; --amber-bar:#C8871B; --amber-tint:#FBF3E4;\n'
                  '  --rust-ink:#B8420F; --neutral:#6B7A8D;\n')

# Orange had become decoration: every card label, every sub-label, the nav
# accent and the heading rules all used it, so the things that are actually
# wrong could not stand out. Restrict it to "act on this" and make everything
# structural neutral.
out = out.replace('</style>', """
:root{ --label:#5A6B80; }
.lvl{color:var(--label)}                      /* section labels: quiet by default */
.lvl.alert{color:var(--rust-ink)}             /* only where something is wrong */
nav button.on{border-left-color:var(--sea);background:linear-gradient(90deg,var(--sea-tint),transparent)}
nav button.on .navnum{color:var(--sea)}
h1::before{background:linear-gradient(90deg,var(--sea-deep) 0 58%,var(--sea-2) 58% 100%)}
</style>""")

nav = '\n'.join(
    '  <button%s data-p="%s"><span class="navnum">%02d</span>%s</button>'
    % (' class="on"' if n == 0 else '', pid, n + 1, title)
    for n, (pid, title, _, _) in enumerate(PAGES))
out = re.sub(r'<nav>.*?</nav>', '<nav>\n' + nav + '\n</nav>', out, count=1, flags=re.S)

sections = []
for n, (pid, title, sub, blocks) in enumerate(PAGES):
    sections.append('<section class="page%s" id="p-%s">\n  <h1>%s</h1>\n  <p class="sub">%s</p>\n%s\n</section>'
                    % (' on' if n == 0 else '', pid, title, sub, '\n'.join('  ' + b for b in blocks)))
out = re.sub(r'<main>.*?</main>', '<main>\n\n' + '\n\n'.join(sections) + '\n\n</main>',
             out, count=1, flags=re.S)

# ---------------------------------------------------------------------- JS
IJS = I.split('<script>', 1)[1]


def func(js, name):
    i = js.index('\nfunction %s(' % name)
    depth, k = 0, js.index('{', i)
    while True:
        if js[k] == '{':
            depth += 1
        elif js[k] == '}':
            depth -= 1
            if depth == 0:
                return js[i:k + 1]
        k += 1


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

fns = "let org='ALL';\n" + FOCUS_JS
fns += '\n'.join(func(IJS, n) for n in
                 ['tableFrom', 'renderStrip', 'renderKpi', 'renderDoc', 'renderCarr',
                  'renderDq', 'renderDefs'])
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
  // the action queue is produced by the internal data build, which runs after
  // this page is assembled; hide the card rather than throw if it is not there
  if (!A) { const c = document.getElementById('act-card'); if (c) c.style.display = 'none'; return; }
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

boot_old = ('renderToday(); renderArr(); renderPipe(); renderPerfWk(); renderWatch(); '
            'renderTrends(); renderReliability(); renderTransit(); bindRaw();')
boot_new = (fns + "\nObject.assign(RAWSRC, " + json.dumps(RAWSRC_ADD) + ");\n" +
            boot_old.replace('bindRaw();',
                             'renderStrip(); renderKpi(); renderDoc(); renderLeague(); '
                             'renderAction(); renderCarr(); renderDem(); renderDq(); '
                             'renderDefs(); bindRaw();') +
            HEADER_AND_STALE_JS)
out = out.replace(boot_old, boot_new)

# renderStrip only exists once the internal functions are in, so widen it here
out = out.replace(
    '<th class="num">Open overdue</th><th class="num">Med days</th>',
    '<th class="num">Open overdue</th><th class="num">Urgent</th><th class="num">Med days</th>')
out = out.replace(
    '''<td class="num">${r.open_overdue?r.med_days+\'d\':\'\u00b7\'}</td>''',
    '''<td class="num" style="color:${r.urgent?\'var(--amber)\':\'var(--ink-3)\'};font-weight:${r.urgent?600:400}">${r.urgent||\'\u00b7\'}</td>
     <td class="num">${r.open_overdue?r.med_days+\'d\':\'\u00b7\'}</td>''')

# internal raw buttons point at the shared registry keys
for old, new in [('data-raw="queue2"', 'data-raw="queue"'),
                 ('data-raw="carriers"', 'data-raw="carriers_all"'),
                 ('data-raw="kpi"', 'data-raw="kpi_steps"')]:
    out = out.replace(old, new)
out = out.replace('id="raw-queue2"', 'id="raw-queue-2"')
out = out.replace('id="raw-carriers"', 'id="raw-carriers_all"')
out = out.replace('id="raw-kpi"', 'id="raw-kpi_steps"')

# The client source card still says "Today page" \u2014 that page is now "Act now".
out = out.replace("Same population as the Today page\'s needs-attention list",
                  "Same population as the Act now page\'s needs-attention list")

out = out.replace('<div class="lvl">Needs attention</div>',
                  '<div class="lvl alert">Needs attention</div>')

out = DATA.sub(lambda m: 'const D = ' + json.dumps(D, separators=(',', ':'), ensure_ascii=False) + ';\n',
               out, count=1)
open(a.out, 'w', encoding='utf-8').write(out)
print(f'{len(PAGES)} pages, {len(D)} data keys, {len(D["raw"])} raw tables -> {a.out} ({len(out)/1e6:.2f} MB)')
