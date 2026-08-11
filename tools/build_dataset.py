"""Rebuild the MOOV Ops dataset (const D) from the exported workbooks.

Every figure on the dashboard is derived here. Run it, then inject the JSON
into index.html. Keeping this script means the next refresh is a re-run, not
an archaeology exercise.

Usage:
    python3 tools/build_dataset.py --src <folder of .xlsx exports> [--asof YYYY-MM-DD] [--out D.json]

Exports are matched on a distinctive fragment of their filename, so the BI
system's random prefixes do not matter. Needs pandas + openpyxl.
"""
import argparse, glob, json, math, os, re, sys, warnings
import pandas as pd

warnings.filterwarnings('ignore')

ap = argparse.ArgumentParser()
ap.add_argument('--src', required=True, help='folder holding the .xlsx exports')
ap.add_argument('--asof', default='2026-08-06', help='the "data as of" date')
ap.add_argument('--out', default='D.json')
args = ap.parse_args()

SRC = args.src
TODAY = pd.Timestamp(args.asof)
OUT = args.out
CAP = 2000                      # max rows kept in any raw drill-down table


def find(fragment):
    """Locate an export by a fragment of its name, ignoring any BI-added prefix."""
    hits = [p for p in glob.glob(os.path.join(SRC, '*.xlsx'))
            if fragment.lower() in os.path.basename(p).lower()]
    if not hits:
        sys.exit(f'missing export matching "{fragment}" in {SRC}')
    return sorted(hits)[0]


F_MILE = find('PO_milestone_performance')
F_DELIV = find('latest_delivery_data')
F_DEM = find('demurrage__detention_tracker')
F_REJ = find('booking_rejection_analysis')
F_SCORE = find('pepco_carrier_scoring')
F_PERF = find('carrier_performance')
F_KPI = find('OHA_KPI')
F_VOL = find('pepco_weekly_volume')
F_DOC = find('shipping_document_verification')

# Warehouse code -> site, from the DC x FND-Booked crosstab in the D&D tracker.
DC_SITE = {'6': 'Rawa Mazowiecka', '15': 'Sosnowiec', '18': 'Gyal',
           '21': 'Bucharest', '27': 'Guadalajara', '30': 'Gdansk'}

# Dashboard stage -> (Done Segment column, Due Date column, Overdue Days column)
STAGES = [
    ('Cargo ready',       'CRD Done Segment',                        'Cargo Ready Date Due Date',            'CRD Overdue Days'),
    ('Dimensions',        'Dimensions Done Segment',                 'Dimensions Due Date',                  'Dimensions Overdue Days'),
    ('Shipper booking',   'Shipper Booking Done Segment',            'Shipper Booking Due Date',             'Shipper Booking Overdue Days'),
    ('Booking validation','Shipper Booking Validition Done Segment', 'Shipper Booking Validation Due Date',  'Shipper Booking Validition Overdue Days'),
    ('SO release',        'SO Release to Supplier Done Segment',      'SO Release to Supplier Due Date',      'SO Release to Supplier Overdue Days'),
    ('SI/VGM submit',     'SI/VGM Submit Done Segment',              'SI/VGM Submit Due Date',               'SI/VGM Submit Overdue Days'),
    ('Loading plan',      'Container Loading Plan Done Segment',     'Container Loading Plan Due Date',      'Container Loading Plan Overdue Days'),
    ('Vessel departure',  'Vessel Departure Done Segment',           'Vessel Departure Due Date',            'Vessel Departure Overdue Days'),
    ('Shipping docs',     'Upload Shipping Documents Done Segment',  'Upload Shipping Documents Due Date',   'Upload Shipping Documents Overdue Days'),
]

notes = []          # provenance / caveats surfaced back to the user


def load(path, **kw):
    """Read an export and drop the trailing 'Applied filters' / truncation footer."""
    df = pd.read_excel(path, engine='openpyxl', **kw)
    first = df.columns[0]
    junk = df[first].astype(str).str.startswith(('Applied filters', 'Exported data'))
    truncated = df[first].astype(str).str.startswith('Exported data').any()
    df = df[df[first].notna() & ~junk].copy()
    return df, truncated


def dstr(ts, fmt='%d %b'):
    return '' if pd.isna(ts) else pd.Timestamp(ts).strftime(fmt).lstrip('0')


def clean(o):
    """JSON-safe: numpy scalars -> python, NaN -> None."""
    if isinstance(o, dict):
        return {str(k): clean(v) for k, v in o.items()}
    if isinstance(o, list):
        return [clean(v) for v in o]
    if isinstance(o, (pd.Timestamp,)):
        return str(o)
    if hasattr(o, 'item'):
        o = o.item()
    if isinstance(o, float):
        if math.isnan(o) or math.isinf(o):
            return None
        return round(o, 4)
    return o


D = {}

# ---------------------------------------------------------------- milestones
mile, trunc = load(F_MILE)
if trunc:
    notes.append(f'PO milestone export truncated by the source system at {len(mile):,} rows '
                 f'(weeks {mile["ATD Week"].min()} to {mile["ATD Week"].max()}).')
mile['_origin'] = mile['Origin Location'].fillna('??')

due_cache, open_cache = {}, {}
for stage, segc, duec, ovdc in STAGES:
    due_cache[stage] = pd.to_datetime(mile[duec], errors='coerce')
    open_cache[stage] = mile[segc].isna() & (due_cache[stage] < TODAY)

origins = ['ALL'] + sorted(mile['_origin'].value_counts().loc[lambda s: s >= 50].index.tolist())
strip = {}
queue_rows = []
for org in origins:
    sel = slice(None) if org == 'ALL' else (mile['_origin'] == org)
    sub = mile if org == 'ALL' else mile[sel]
    rows = []
    for stage, segc, duec, ovdc in STAGES:
        seg = sub[segc]
        done = int(seg.notna().sum())
        ontime = round(100 * (seg == 'DONE IN POSSIBLE').sum() / done, 1) if done else 0.0
        late = round(100 * (seg == 'DONE IN OVERDUE').sum() / done, 1) if done else 0.0
        om = open_cache[stage] if org == 'ALL' else (open_cache[stage] & sel)
        openq = mile[om]
        days = (TODAY - due_cache[stage][om]).dt.days
        if len(openq):
            worst = openq['Supplier Name'].value_counts()
            wsup, wn = str(worst.index[0])[:34], int(worst.iloc[0])
        else:
            wsup, wn = '—', 0
        rows.append({'stage': stage, 'done': done, 'ontime': ontime, 'late_done': late,
                     'open_overdue': int(len(openq)),
                     'med_days': int(days.median()) if len(days) else 0,
                     'worst_sup': wsup, 'worst_n': wn})
        if org == 'ALL' and len(openq):
            q = openq.assign(_d=days, _stage=stage)
            queue_rows.append(q[['Order Number', 'Supplier Name', '_origin', '_stage', '_d']]
                              .assign(_due=due_cache[stage][om]))
    strip[org] = rows
D['strip'] = {'origins': origins, 'data': strip}

q = pd.concat(queue_rows).sort_values('_d', ascending=False).head(CAP)
D['queue_raw'] = [{'Order': r['Order Number'], 'Supplier': r['Supplier Name'],
                   'Origin': r['_origin'], 'Stage': r['_stage'],
                   'Due': dstr(r['_due']), 'Days overdue': int(r['_d'])}
                  for _, r in q.iterrows()]

# supplier league across every stage's open-overdue queue
allq = pd.concat(queue_rows)
lg = (allq.groupby('Supplier Name')
          .agg(n=('_d', 'size'), mx=('_d', 'max'))
          .sort_values('n', ascending=False).head(12))
D['sup_league'] = [{'Supplier': s, 'Open overdue': int(r.n),
                    'Worst stage': allq[allq['Supplier Name'] == s]['_stage'].value_counts().index[0],
                    'Max days': int(r.mx)} for s, r in lg.iterrows()]

# cargo-ready-date misses, by coded reason
crd = mile[mile['Cargo Ready Date Reason'].notna()].copy()
crd['_days'] = pd.to_numeric(crd['CRD Overdue Days'], errors='coerce').fillna(0)



def owner(reason):
    r = str(reason)
    if ' - ' in r:
        return r.split(' - ')[1].strip()
    return 'Other'


crd['_own'] = crd['Cargo Ready Date Reason'].map(owner)


# ------------------------------------------------------------------- KPI set
kpi_raw = pd.read_excel(F_KPI, engine='openpyxl', header=None)
vol_raw = pd.read_excel(F_VOL, engine='openpyxl', header=None)


def wide_blocks(df, nmetric):
    """Week-blocked pivot -> {(week, metric, origin): value}."""
    weeks = df.iloc[0].tolist()
    labels = df.iloc[1].tolist()
    out = {}
    for r in range(2, len(df)):
        org = df.iloc[r, 3]
        if not isinstance(org, str):
            continue
        for c in range(5 if nmetric == 13 else 4, df.shape[1]):
            wk, lab, val = weeks[c], labels[c], df.iloc[r, c]
            if isinstance(wk, str) and isinstance(lab, str) and pd.notna(val):
                out[(wk, lab, org)] = val
    return out


kpi_vals = wide_blocks(kpi_raw, 13)
vol_vals = wide_blocks(vol_raw, 3)
recent = sorted({w for w, _, _ in kpi_vals}, reverse=True)[1:9]   # 8 complete weeks
notes.append('KPI card: weighted by shipments over weeks ' + recent[-1] + ' to ' + recent[0] + '.')

steps = [l for l in kpi_raw.iloc[1].tolist()[5:18] if isinstance(l, str)]
kpi_rows = []
for step in steps:
    ship = ok = 0.0
    for wk in recent:
        for org in set(o for _, _, o in kpi_vals):
            rate = kpi_vals.get((wk, step, org))
            n = vol_vals.get((wk, 'Total Shipment', org))
            if rate is None or n is None:
                continue
            ship += float(n)
            ok += float(n) * float(rate)
    if ship:
        kpi_rows.append({'Step': step, 'Ontime': int(round(ok)),
                         'Not ontime': int(round(ship - ok)),
                         'Pct': round(100 * ok / ship, 1)})
D['kpi'] = sorted(kpi_rows, key=lambda r: r['Pct'])

# --------------------------------------------------------------- carriers
score, _ = load(F_SCORE)
score['ATD Month'] = pd.to_numeric(score['ATD Month'], errors='coerce')
score['ATD Year'] = pd.to_numeric(score['ATD Year'], errors='coerce')
# The current month is only part-run (TODAY is the 6th), so scoring it would compare a
# handful of sailings against full months. Use the last complete month instead.
months = sorted({(int(y), int(m)) for y, m in
                 score.dropna(subset=['ATD Year', 'ATD Month'])[['ATD Year', 'ATD Month']].values
                 if (int(y), int(m)) < (TODAY.year, TODAY.month)})
ly, lm = months[-1]
cur = score[(score['ATD Year'] == ly) & (score['ATD Month'] == lm)]
notes.append(f'Carrier league uses the last complete month ({ly}-{lm:02d}); '
             f'{TODAY.strftime("%Y-%m")} is only {TODAY.day} days old.')

perf, _ = load(F_PERF)
slip = perf.groupby('Carrier')['Average Booked ETD - ATD'].median()

D['carriers'] = {'month': f'{ly}-{lm:02d}',
                 'rows': [{'Carrier': r['Carrier'], 'TEU': round(float(r['TEU']), 1),
                           'Shipments': int(r['Shipments']),
                           'Ontime score': round(float(r['Ontime']), 1) if pd.notna(r['Ontime']) else None,
                           'Speed': round(float(r['Speed']), 1) if pd.notna(r['Speed']) else None,
                           'ETD slip (d)': round(float(slip.get(r['Carrier'])), 1) if pd.notna(slip.get(r['Carrier'])) else None}
                          for _, r in cur.sort_values('TEU', ascending=False).iterrows()]}
D['carriers_raw'] = clean(score.to_dict('records'))

# ------------------------------------------------------------- rejections
rej, _ = load(F_REJ)
rej['Action'] = rej['Action'].astype(str).str.replace('_', ' ', regex=False).str.upper()
rej['_cat'] = rej['Reject Reason Code'].fillna(rej['Remark']).fillna('Uncoded').astype(str).str.strip()
D['rejections'] = {'total': int(len(rej)),
                   'by_action': {k: int(v) for k, v in rej['Action'].value_counts().items()},
                   'top_cat': {k[:96]: int(v) for k, v in rej['_cat'].value_counts().head(6).items()}}
D['rejections_raw'] = [{'Order Number': str(r['Order Number'])[:60], 'Supplier Booking Ref': r['Supplier Booking Ref'],
                        'Origin Country': r['Origin Country'], 'Action': r['Action'],
                        'Category': str(r['_cat'])[:70], 'Event Time': str(r['Event Time'])}
                       for _, r in rej.sort_values('Event Time', ascending=False).head(CAP).iterrows()]

# ------------------------------------------------ demurrage & detention risk
dem, _ = load(F_DEM)
dem['DC'] = dem['DC'].astype(str)
risk = dem[pd.to_numeric(dem['DEM+DET Risk'], errors='coerce') > 0]
D['demurrage'] = {'containers_at_risk': int(len(risk)),
                  'by_dc': {k: int(v) for k, v in risk['DC'].value_counts().head(8).items()},
                  'by_carrier': {k: int(v) for k, v in risk['Carrier'].value_counts().head(6).items()},
                  'risk_total': float(pd.to_numeric(risk['DEM+DET Risk'], errors='coerce').sum())}
D['demurrage_raw'] = [{'Container #': r['Container #'], 'Carrier': r['Carrier'], 'POD': r['POD'],
                       'DC': r['DC'], 'Container Location': r['Container Location'],
                       'Delivery Status': r['Delivery Status'], 'DEM+DET risk': float(r['DEM+DET Risk'])}
                      for _, r in risk.sort_values('DEM+DET Risk', ascending=False).head(CAP).iterrows()]

# customs backlog: landed in the last 30 days and still sitting in the POD terminal.
# Containers that left the terminal but have no DC arrival are inland transit, not a hold.
_pod = pd.to_datetime(dem['POD Arrival'], errors='coerce')
held = dem[(_pod >= TODAY - pd.Timedelta(days=30)) & (_pod <= TODAY)
           & (dem['Container Location'] == 'In POD terminal')].copy()
held['_days'] = (TODAY - pd.to_datetime(held['POD Arrival'], errors='coerce')).dt.days


# ---------------------------------------------------- delivery punctuality
dl, trunc_d = load(F_DELIV)
if trunc_d:
    notes.append(f'Client delivery export truncated by the source system at {len(dl):,} rows.')
dl['_indc'] = pd.to_datetime(dl['In DC Date'], errors='coerce')
dl['_ata'] = pd.to_datetime(dl['ATA'], errors='coerce')
dl['_site'] = dl['DC'].map(lambda v: DC_SITE.get(str(v).replace('.0', ''), str(v)))
win = dl[(dl['_ata'] >= TODAY - pd.Timedelta(days=30)) & (dl['_ata'] <= TODAY)].copy()
win['_ok'] = win['_ata'] <= win['_indc']
by_site = win.groupby('_site')['_ok'].agg(['size', 'mean'])
by_site = by_site[by_site['size'] >= 20]
# Delivery punctuality, customs holds and coded CRD reasons are not emitted here:
# the client report owns those figures on pages 01-06 and states them better. The
# computations survive only where the data-quality metrics below depend on them.

# ------------------------------------------------------------- data quality
stale = dl[(dl['_indc'] < TODAY) & dl['_ata'].isna()].copy()
stale['_past'] = (TODAY - stale['_indc']).dt.days
feta = pd.to_datetime(dl['FINAL ETA'], errors='coerce')
recent_eta = int((dl['_ata'].isna() & (feta < TODAY) & (feta >= TODAY - pd.Timedelta(days=14))).sum())

late_orders = dl[dl['_ata'].notna() & (dl['_ata'] > dl['_indc'])]
miss_uncoded = int(late_orders['Miss In DC Date Reason Type'].isna().sum())
crd_overdue = mile[pd.to_numeric(mile['CRD Overdue Days'], errors='coerce') > 0]
crd_uncoded = int(crd_overdue['Cargo Ready Date Reason'].isna().sum())
doc, _ = load(F_DOC)
doc['Status'] = doc['Status'].astype(str).str.strip()
# the source carries 'V008 - Do not clear' twice, once with a hyphen and once with an en dash
doc['_grp'] = doc['Reason Group'].astype(str).str.replace('–', '-', regex=False).str.strip()
doc_inc = doc[doc['Status'].str.lower() == 'incomplete']
judged = doc[doc['Status'].str.lower().isin(['incomplete', 'complete'])]
D['docver'] = {
    'sets': int(doc['Set Number'].nunique()),
    'checks': int(len(judged)),
    'incomplete': int(len(doc_inc)),
    'pct': round(100 * len(doc_inc) / len(judged), 1) if len(judged) else 0.0,
    'top': {k[:60]: int(v) for k, v in doc_inc['_grp'].value_counts().head(6).items()},
}
D['docver_raw'] = [{'Supplier Booking Ref': r['Supplier Booking Ref'], 'Set Number': r['Set Number'],
                    'Carrier': r['Carrier'], 'Reason Group': r['_grp'],
                    'Reason': str(r['Reason'])[:80], 'Raised': str(r['Create Time(CET)'])[:16]}
                   for _, r in doc_inc.sort_values('Create Time(CET)', ascending=False).head(CAP).iterrows()]


def pc(a, b):
    return f'{round(100 * a / b)}%' if b else '—'


D['dq'] = {
    'stale_tracking': int(len(stale)),
    'stale_oldest': int(stale['_past'].max()) if len(stale) else 0,
    'stale_recent_eta': recent_eta,
    'uncoded': [
        {'What': 'Late orders with no reason code', 'Share': pc(miss_uncoded, len(late_orders)),
         'Detail': f'{miss_uncoded:,} of {len(late_orders):,} orders that missed their in-DC date'},
        {'What': 'Overdue CRDs with no coded reason', 'Share': pc(crd_uncoded, len(crd_overdue)),
         'Detail': f'{crd_uncoded:,} of {len(crd_overdue):,}'},
        {'What': 'Booking rejections with no reason code', 'Share': pc(int(rej['Reject Reason Code'].isna().sum()), len(rej)),
         'Detail': f"{int(rej['Reject Reason Code'].isna().sum()):,} of {len(rej):,} — MOOV rejects carry free-text remarks only"},
        {'What': 'Containers with no contractual free time', 'Share': pc(int(dem['Free Time Storage'].isna().sum()), len(dem)),
         'Detail': f"{int(dem['Free Time Storage'].isna().sum()):,} of {len(dem):,} — demurrage risk understated for these"},
    ],
    'open_q': ('Both the milestone and client-delivery exports come back capped at 150,000 rows with '
               '"some data may have been omitted" — every population on this page is that capped extract, '
               'not the full book. Ask IT to raise the export limit before these counts are quoted '
               'as totals.'),
}
D['stale_raw'] = [{'Order': r['Order Number'], 'DC': r['_site'],
                   'Required in DC': dstr(r['_indc'], '%d %b %y'),
                   'Final ETA': dstr(pd.to_datetime(r['FINAL ETA'], errors='coerce'), '%d %b %y'),
                   'Departure': 'Departed' if pd.notna(r['ATD']) else 'Not departed',
                   'Days past required': int(r['_past'])}
                  for _, r in stale.sort_values('_past', ascending=False).head(CAP).iterrows()]

# The page also carries the Pepco client report on pages 01-06, built from a
# different set of exports and not regenerated here. Describe those figures too,
# quoting that report's own scope notes, so the Definitions page covers the whole
# page rather than just the half this script owns.
def extend_defs_for_client(dd, client):
    dd['cards'] = [
        {'Figure': 'Delivery performance (weekly)',
         'Population': f"{client['perf']['population']:,} orders, S01 approved changes excluded "
                       f"({client['perf']['s01_excluded']:,})",
         'Counted as good': 'arrival on or before the required in-DC date (strict); +3 and +7 day '
                            'variants shown alongside',
         'Window': f"week of {client['daily']['week']['label']}, with a 13-week series behind it"},
        {'Figure': 'Needs attention / Due at DC',
         'Population': 'orders whose required in-DC date falls in the next 3 or 7 days',
         'Counted as good': 'on track = current final ETA on or before the required in-DC date',
         'Window': 'next 3 / 7 days from the client as-of date'},
        {'Figure': 'Arrivals this week',
         'Population': 'containers with an ETA or actual arrival in the current week',
         'Counted as good': 'arrived; overdue = ETA passed with no arrival recorded',
         'Window': 'current ISO week'},
        {'Figure': 'Transshipment waiting',
         'Population': 'containers at a transit port with no onward departure',
         'Counted as good': 'n/a — counts those waiting, and how long',
         'Window': 'open containers as of the extract'},
        {'Figure': 'Customs held',
         'Population': 'containers arrived without clearance finished',
         'Counted as good': 'n/a — counts held containers by recorded cause',
         'Window': 'selectable: last 7 / 30 / 90 days'},
        {'Figure': 'Monthly trends',
         'Population': 'arrived volume and on-time by month',
         'Counted as good': 'strict and +7-day definitions shown together',
         'Window': 'rolling extract; first and last months are partial'},
    ] + dd['cards']
    split = ([] if client['perf']['asof'] == dd['as_of'] else [
        '<b>This page is two extracts stitched together.</b> Pages 01\u201306 come from the client '
        f"report build ({client['perf']['asof']}) and pages 07\u201310 from the internal build "
        f"({dd['as_of']}). Figures across the two halves will not reconcile exactly."])
    dd['caveats'] = split + [
        '<b>Each figure appears once.</b> Delivery performance, customs holds and cargo-ready reasons '
        'are the client report\u2019s versions throughout \u2014 they carry definitions, windows and '
        'causes the internal extracts do not. The internal duplicates were removed rather than shown '
        'twice.',
    ] + dd['caveats']


# -------------------------------------------------- allocation (no new export)
INDEX = os.path.join(os.path.dirname(os.path.abspath(__file__)), '..', 'index.html')
old = json.loads(re.search(r'const D = (\{.*?\});\n',
                           open(INDEX, encoding='utf-8').read(), re.S).group(1))
D['allocation'] = old['allocation']
# after the client-report merge this table lives in the shared raw registry
D['allocation_raw'] = old.get('allocation_raw') or old['raw']['allocation']
notes.append('Allocation compliance carried over from the 2026-07-24 extract — no nomination/booking '
             'export was supplied in this refresh.')

# --------------------------------------------------------------- definitions
# Measured from the exports rather than asserted, so the page always states the
# window this particular refresh actually covers.
_vd_due = pd.to_datetime(mile['Vessel Departure Due Date'], errors='coerce')
stage_defs = []
for stage, segc, duec, ovdc in STAGES:
    donec = duec.replace('Due Date', 'Done Date')
    urgc = duec.replace('Due Date', 'Urgent Date')
    done = pd.to_datetime(mile[donec], errors='coerce')
    due = pd.to_datetime(mile[duec], errors='coerce')
    urg = pd.to_datetime(mile[urgc], errors='coerce')
    seg = mile[segc]
    full = done.notna() & due.notna() & urg.notna()
    predicted = pd.Series(pd.NA, index=mile.index, dtype='object')
    predicted[done <= urg] = 'DONE IN POSSIBLE'
    predicted[(done > urg) & (done <= due)] = 'DONE IN URGENT'
    predicted[done > due] = 'DONE IN OVERDUE'
    match = round(100 * (predicted[full] == seg[full]).mean(), 1) if full.any() else None
    anchor = (due - _vd_due).dt.days.median()
    stage_defs.append({
        'Stage': stage,
        'Urgent → due': f'{(due - urg).dt.days.median():.0f}d' if due.notna().any() else '—',
        'Due date set at': ('T' + format(int(anchor), '+d')) if pd.notna(anchor) else '—',
        'Has a due date': f'{round(100 * due.notna().mean())}%',
        'Dates reproduce segment': f'{match}%' if match is not None else '—',
    })

_wk = f"{mile['ATD Week'].min()} → {mile['ATD Week'].max()}"
_w0 = (TODAY - pd.Timedelta(days=30)).strftime('%d %b')
_w1 = TODAY.strftime('%d %b %Y')
_rej_t = pd.to_datetime(rej['Event Time'], errors='coerce')
_doc_t = pd.to_datetime(doc['Create Time(CET)'], errors='coerce')
_free = sorted(dem['DEM+DET Contract'].dropna().unique())

D['defs'] = {
    'as_of': TODAY.strftime('%Y-%m-%d'),
    'stage_rule': (
        'Every origin milestone carries three dates: an urgent date, a due date, and the date it was '
        'actually done. The export grades each one into a segment — done on or before the urgent date is '
        'DONE IN POSSIBLE, done between the urgent and due dates is DONE IN URGENT, done after the due '
        'date is DONE IN OVERDUE. The dashboard counts only DONE IN POSSIBLE as on time, so the urgent '
        'date — not the due date — is the real deadline.'),
    'done_rule': (
        'Done means the milestone carries a segment, whatever that segment is. Open overdue means it '
        f'carries none and its due date has already passed as of {TODAY.strftime("%d %b %Y")} — that is the '
        'actionable queue. A milestone that is not done but is not yet due counts in neither column.'),
    'stages': stage_defs,
    'cards': [
        {'Figure': 'Stage strip · on time',
         'Population': f'{len(mile):,} PO milestones, ATD weeks {_wk}',
         'Counted as good': 'segment is DONE IN POSSIBLE',
         'Window': 'whole extract, not a rolling window'},
        {'Figure': 'Open overdue queue',
         'Population': 'same milestones',
         'Counted as good': 'n/a — counts milestones with no segment and a due date in the past',
         'Window': f'as of {TODAY.strftime("%d %b %Y")}'},
        {'Figure': 'KPI bars',
         'Population': 'OHA KPI rates × PEPCO weekly shipment counts, per origin',
         'Counted as good': 'the client-reported on-time rate for that step',
         'Window': f'{recent[-1]} → {recent[0]} (8 complete ATD weeks)'},
        {'Figure': 'Doc verification',
         'Population': f"{D['docver']['checks']:,} document checks with a verdict",
         'Counted as good': "Status is Complete; Incomplete carries a V-code reason group",
         'Window': f"{_doc_t.min():%d %b %Y} → {_doc_t.max():%d %b %Y}"},
        {'Figure': 'Supplier league',
         'Population': 'suppliers appearing in the open-overdue milestone queue',
         'Counted as good': 'n/a — ranks by how many overdue milestones each supplier is sitting on',
         'Window': f'ATD weeks {_wk}'},
        {'Figure': 'Carrier league',
         'Population': 'PEPCO carrier scoring, one row per carrier per month',
         'Counted as good': 'on-time and speed are 0–10 scores set by the scoring model, not rates',
         'Window': f"{D['carriers']['month']} — the last complete month"},
        {'Figure': 'ETD slip',
         'Population': f'{len(perf):,} carrier bookings',
         'Counted as good': 'median days between booked ETD and actual departure',
         'Window': 'whole carrier-performance extract'},
        {'Figure': 'Booking rejections',
         'Population': f'{len(rej):,} rejection events',
         'Counted as good': 'n/a — every rejection is counted once',
         'Window': f'{_rej_t.min():%d %b %Y} → {_rej_t.max():%d %b %Y}'},
        {'Figure': 'DEM+DET risk',
         'Population': f'{len(dem):,} containers in the tracker',
         'Counted as good': f'at risk = storage beyond the contractual free time '
                            f'({"/".join(str(int(f)) for f in _free)} days); risk is the excess in days',
         'Window': 'open containers as of the extract'},
        {'Figure': 'Stale tracking',
         'Population': 'client delivery orders',
         'Counted as good': 'n/a — counts orders past their required in-DC date with no arrival recorded',
         'Window': f'as of {TODAY.strftime("%d %b %Y")}'},
    ],
    'caveats': [
        f'<b>Shipping docs is not its own measurement.</b> It carries its own done, due and urgent dates, '
        f'but the segment the export assigns matches the Loading plan segment on '
        f'{round(100 * (mile["Upload Shipping Documents Done Segment"].astype(str) == mile["Container Loading Plan Done Segment"].astype(str)).mean(), 1)}% of rows, '
        f'while its own dates reproduce it on almost none. That is why the two rows show identical '
        f'percentages. Treat the Shipping docs row as a copy of Loading plan until the source is fixed.',
        '<b>Later stages cannot be re-derived from the export.</b> The urgent/due/done rule reproduces the '
        'stated segment almost perfectly for Cargo ready and Dimensions, and progressively less well down '
        'the pipeline. Where the reproduction rate is low the segment is computed upstream against dates '
        'this export does not carry, so it has to be taken on trust.',
        '<b>Due dates are frequently absent.</b> SO release carries one on under 2% of orders and Dimensions '
        'on around a quarter, so their windows are set for only a fraction of the book.',
        '<b>The two order-level exports are capped at 150,000 rows</b> with a truncation warning in the '
        'footer, so every population above is that capped extract rather than the full book.',
        '<b>Allocation compliance is from the 2026-07-24 extract.</b> No nomination file came with this '
        'refresh, so it is the one card not as of the header date.',
    ],
}

# The page is two extracts stitched together: pages 01-06 come from the client
# report build and are NOT regenerated here. Merge into whatever is already in
# index.html so a refresh of the internal half leaves the client half standing.
RAWMAP = {'queue': 'queue_raw', 'carriers_all': 'carriers_raw', 'allocation': 'allocation_raw',
          'rejections': 'rejections_raw', 'demurrage': 'demurrage_raw', 'stale': 'stale_raw',
          'docver': 'docver_raw'}
raws = {rk: D.pop(dk) for rk, dk in RAWMAP.items()}
raws['kpi_steps'] = D['kpi']
D['raw'] = {**old.get('raw', {}), **raws}
if 'perf' in old:
    extend_defs_for_client(D['defs'], old)
merged = {**old, **D}
json.dump(clean(merged), open(OUT, 'w'), separators=(',', ':'), ensure_ascii=False)

print('=== rebuilt ===')
for k in sorted(set(old) | set(D)):
    o, n = old.get(k), D.get(k)
    f = lambda v: (f'{len(v)} rows' if isinstance(v, list) else
                   (json.dumps(v)[:66] if not isinstance(v, dict) else f'{len(v)} keys'))
    print(f'  {k:16s} old={f(o):26s} new={f(n)}')
print('\n=== notes ===')
for n in notes:
    print('  *', n)
print('demurrage', json.dumps({k: v for k, v in D['demurrage'].items() if k != 'by_dc'}))
print('rejections', json.dumps(D['rejections'])[:300])
print('dq counts', D['dq']['stale_tracking'], D['dq']['stale_oldest'], D['dq']['stale_recent_eta'])
print('kpi', json.dumps(D['kpi'])[:400])
print('strip ALL cargo ready', json.dumps(D['strip']['data']['ALL'][0]))
print('origins', D['strip']['origins'])
