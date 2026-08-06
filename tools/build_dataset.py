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
D['crd_top'] = {k: int(v) for k, v in crd['Cargo Ready Date Reason'].value_counts().head(8).items()}


def owner(reason):
    r = str(reason)
    if ' - ' in r:
        return r.split(' - ')[1].strip()
    return 'Other'


crd['_own'] = crd['Cargo Ready Date Reason'].map(owner)
D['crd_split'] = {k: int(v) for k, v in crd['_own'].value_counts().items() if k != 'Other'}
D['crd_raw'] = [{'Order': r['Order Number'], 'Supplier': r['Supplier Name'],
                 'Coded reason': r['Cargo Ready Date Reason'], 'Days overdue': int(r['_days'])}
                for _, r in crd.sort_values('_days', ascending=False).head(CAP).iterrows()]

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
D['customs'] = {'total': int(len(held)),
                'by_dc': {k: int(v) for k, v in held['DC'].value_counts().head(6).items()}}
D['customs_raw'] = [{'Container': r['Container #'], 'MBL': r['MBL'], 'DC': r['DC'],
                     'Arrived': dstr(r['POD Arrival']), 'Days held': int(r['_days']),
                     'Reason': 'Not yet coded'}
                    for _, r in held.sort_values('_days', ascending=False).head(CAP).iterrows()]

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
D['delivery'] = {'pct_30d': round(100 * win['_ok'].mean(), 1), 'n_30d': int(len(win)),
                 'by_dc': {k: round(100 * r['mean'], 1) for k, r in by_site.iterrows()}}
D['delivery_raw'] = [{'Order': r['Order Number'], 'DC': r['_site'],
                      'MOT': r['Shipping Type'], 'Required in DC': dstr(r['_indc']),
                      'Arrived': dstr(r['_ata']),
                      'Status': 'On schedule' if r['_ok'] else 'Late'}
                     for _, r in win.sort_values('_ok').head(CAP).iterrows()]

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

# -------------------------------------------------- allocation (no new export)
INDEX = os.path.join(os.path.dirname(os.path.abspath(__file__)), '..', 'index.html')
old = json.loads(re.search(r'const D = (\{.*?\});\n',
                           open(INDEX, encoding='utf-8').read(), re.S).group(1))
D['allocation'] = old['allocation']
D['allocation_raw'] = old['allocation_raw']
notes.append('Allocation compliance carried over from the 2026-07-24 extract — no nomination/booking '
             'export was supplied in this refresh.')

json.dump(clean(D), open(OUT, 'w'), separators=(',', ':'), ensure_ascii=False)

print('=== rebuilt ===')
for k in old:
    o, n = old[k], D.get(k)
    f = lambda v: (f'{len(v)} rows' if isinstance(v, list) else
                   (json.dumps(v)[:66] if not isinstance(v, dict) else f'{len(v)} keys'))
    print(f'  {k:16s} old={f(o):26s} new={f(n)}')
print('\n=== notes ===')
for n in notes:
    print('  *', n)
print('\ndelivery', json.dumps(D['delivery']))
print('demurrage', json.dumps({k: v for k, v in D['demurrage'].items() if k != 'by_dc'}))
print('rejections', json.dumps(D['rejections'])[:300])
print('dq counts', D['dq']['stale_tracking'], D['dq']['stale_oldest'], D['dq']['stale_recent_eta'])
print('kpi', json.dumps(D['kpi'])[:400])
print('strip ALL cargo ready', json.dumps(D['strip']['data']['ALL'][0]))
print('origins', D['strip']['origins'])
