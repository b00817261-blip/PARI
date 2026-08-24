#!/usr/bin/env python3
"""
Rebuild the `const D` blob in index.html from the smartMOOV BI extracts.

There was no generation script in the repo, so this reconstructs the pipeline
from the shape of the existing D and the report's own RAWSRC source map.
Assumptions that are judgement calls are marked ASSUMPTION and echoed in the
run summary so they can be checked against the report.

Ported into the PARI repo so both halves of the merged page build from one
folder of extracts. Extracts are matched on a filename fragment, and the two
optional ones (customs clearance, predictive ETA) may be absent - those sections
then keep their previous values and are listed under NOT UPDATED.

Usage:  python3 tools/build_client_data.py <extract-dir> [--write]
Without --write it prints the summary and leaves index.html untouched.
"""
import argparse, json, re, sys, warnings
from pathlib import Path
import pandas as pd
import numpy as np

warnings.filterwarnings("ignore")

_ap = argparse.ArgumentParser(description=__doc__)
_ap.add_argument("src", help="folder holding the .xlsx exports")
_ap.add_argument("--index", default=str(Path(__file__).resolve().parent.parent / "index.html"),
                 help="index.html to read the current dataset from and write back to")
_ap.add_argument("--write", action="store_true", help="apply the result to index.html")
_args = _ap.parse_args()

SRC = Path(_args.src)
WRITE = _args.write
HTML = Path(_args.index)

# Match each extract on a distinctive fragment of its filename so the random
# prefix the BI tool prepends does not have to be hard-coded.
FRAGMENTS = {
    "ldd":  "latest_delivery_da",          # ..._date_... and ..._data_... are the same report
    "sup":  "supplier_performance",
    "ahod": "AHOD_reason_code",
    "dest": "destination_milestone",
    "tc":   "transport_carrier",
    "tran": "tranship_port_performance",
    "cust": "customs_clearance_finished",
    "po":   "PO_milestone_performance",
    "peta": "predictive_eta",
    "msum": "Monthly_Summary",
    "dlt":  "destination_lead_time",
}
# The previous dataset, so a section whose extract is absent can keep its
# existing figures instead of collapsing to zero.
PREV = {}
try:
    _m = re.search(r"^const D = (.*?);?$", Path(_args.index).read_text(encoding="utf-8"), re.M)
    PREV = json.loads(_m.group(1).rstrip(";")) if _m else {}
except OSError:
    pass

FILES, HAVE = {}, {}
for _k, _frag in FRAGMENTS.items():
    _hits = sorted(p for p in SRC.glob("*.xlsx") if _frag.lower() in p.name.lower())
    HAVE[_k] = bool(_hits)
    if _hits:
        FILES[_k] = _hits[0].name

# Sections that need extracts we do not have. Left exactly as-is and reported.
MISSING_SOURCES = {}

notes, assumptions, not_updated = [], [], []


def skipped(export, what):
    """An optional extract is absent: keep the old figures and say so."""
    not_updated.append((export, what))
    print(f"  SKIP {what} - no {export} export", flush=True)

def note(s): notes.append(s)
def assume(s): assumptions.append(s)


REQUIRED = ["ldd", "sup", "ahod", "dest", "tc", "tran", "dlt"]
# "po" and "msum" were required until the 2026-08-23 refresh arrived without
# them. They are now optional: their sections keep the previous figures and are
# labelled with the extract date they are still showing.
_absent = [k for k in REQUIRED if not HAVE[k]]
if _absent:
    sys.exit("missing required extracts: " +
             ", ".join(FRAGMENTS[k] for k in _absent))


def load(key, **kw):
    return pd.read_excel(SRC / FILES[key], **kw)


def dt(s):
    return pd.to_datetime(s, errors="coerce")


def dstr(s):
    """Render a date the way the existing raw tables do: '24 Jul 26'."""
    return "" if pd.isna(s) else pd.Timestamp(s).strftime("%d %b %y")


def clean(v):
    """JSON-safe scalar, matching how the old blob encodes blanks."""
    if v is None or (isinstance(v, float) and np.isnan(v)):
        return None
    if isinstance(v, (pd.Timestamp, np.datetime64)):
        return dstr(v)
    if isinstance(v, (np.integer,)):
        return int(v)
    if isinstance(v, (np.floating,)):
        return round(float(v), 2)
    return str(v).strip()


def dcfmt(v):
    """DC codes are 4-digit strings ('0021'). LDD stores them as numbers."""
    if v is None or (isinstance(v, float) and np.isnan(v)):
        return None
    try:
        return f"{int(float(v)):04d}"
    except (TypeError, ValueError):
        return str(v).strip()


def rows(df, colmap):
    """Project a frame into the list-of-dicts shape the raw tables use."""
    out = []
    for _, r in df.iterrows():
        out.append({k: clean(r[v]) if v in r else None for k, v in colmap.items()})
    return out


# ---------------------------------------------------------------- load ------
print("loading extracts ...", flush=True)
ldd = load("ldd")
for c in ["In DC Date", "ATD", "ATA", "FINAL ETA", "Carrier Booking ETA",
          "Carrier Booking ETD", "ETA at Departure"]:
    if c in ldd.columns:
        ldd[c] = dt(ldd[c])

ahod = load("ahod", usecols=["Order Number", "AHOD Reason code",
                             "AHOD Reason Description", "Origin Location",
                             "AHOD Create Time", "Remark"])
sup = load("sup", usecols=["Order Number", "Supplier Name", "Supplier Code"])

ASOF = ldd["ATA"].max().normalize()
note(f"as-of date taken from max ATA = {ASOF:%Y-%m-%d}")

# ---------------------------------------------------- supplier + AHOD -------
sup_names = sup.drop_duplicates("Order Number").set_index("Order Number")["Supplier Name"]
ldd["Supplier Name"] = ldd["Order Number"].map(sup_names)
note(f"supplier name matched on {ldd['Supplier Name'].notna().mean()*100:.1f}% of orders")

ahod["code"] = ahod["AHOD Reason code"].astype(str).str.strip().str.upper()
s01_orders = set(ahod.loc[ahod["code"] == "S01", "Order Number"])
# ASSUMPTION: S01 = "Goods will ship on approved schedule (by Pepco approval)".
# The report footnote says "S01 approved changes excluded".
assume("on-time population excludes orders with AHOD reason code S01 "
       f"({len(s01_orders):,} orders)")

# ------------------------------------------------------------- perf ---------
print("perf ...", flush=True)
base = ldd.dropna(subset=["ATA", "In DC Date"]).copy()
excluded = base["Order Number"].isin(s01_orders)
s01_excluded = int(excluded.sum())
base = base[~excluded]
base["gap"] = (base["ATA"] - base["In DC Date"]).dt.days
ldd["DC"] = ldd["DC"].map(dcfmt)
base["DC"] = base["DC"].map(dcfmt)

# ASSUMPTION: on-time = ATA on/before required In DC Date; +3d / +7d windows.
assume("on-time = ATA <= In DC Date (strict), +3 days, +7 days")

# Minimum order count for a supplier to appear in the worst-performers ranking.
# The old blob listed 77 suppliers but the cutoff it used is not recorded; this
# is a stated threshold rather than a reverse-engineered match to that number.
MIN_SUPPLIER_ORDERS = 150
assume(f"supplier ranking limited to suppliers with >= {MIN_SUPPLIER_ORDERS} orders "
       "(the live report's cutoff is unknown, so this is a stated choice)")

population = len(base)


def band(thr):
    ot = int((base["gap"] <= thr).sum())
    return {"ontime": ot, "pct": round(ot / population * 100, 1)}


strict, w3, w7 = band(0), band(3), band(7)

# late reasons -- from the real source column, NOT the old hand-made buckets.
REASON_COL = "Miss In DC Date Reason Content"
TYPE_COL = "Miss In DC Date Reason Type"


def reason_counts(frame, top=6):
    r = frame[REASON_COL].fillna("No reason recorded").replace("", "No reason recorded")
    vc = r.value_counts()
    out = {}
    if "No reason recorded" in vc.index:
        out["No reason recorded"] = int(vc.pop("No reason recorded"))
    for k, v in vc.head(top - len(out)).items():
        out[str(k)] = int(v)
    return out


assume(f"late reasons now read straight from '{REASON_COL}' "
       "(the old 5-bucket mapping is not in the repo and could not be reproduced)")


def band_detail(thr):
    ot = int((base["gap"] <= thr).sum())
    late = base[base["gap"] > thr]
    sup_g = (late.groupby("Supplier Name").size().rename("late").to_frame()
             .join(base.groupby("Supplier Name").size().rename("orders"))
             .dropna())
    sup_g = sup_g[sup_g["orders"] >= MIN_SUPPLIER_ORDERS]
    sup_g["ontime_pct"] = ((sup_g["orders"] - sup_g["late"]) / sup_g["orders"] * 100).round(1)
    sup_g = sup_g.sort_values("ontime_pct").head(8).reset_index()
    # The 13-week trend chart plots this on a percentage axis, so it must hold
    # the on-time rate for this definition — filling it with late counts sent
    # values like 359 off a 0-100 scale and produced a spiked, unreadable line.
    wk = {w: (round(float((base.loc[base["week"] == w, "gap"] <= thr).mean()) * 100, 1)
              if (base["week"] == w).any() else None)
          for w in weeks}
    return {
        "ontime": ot, "pct": round(ot / population * 100, 1), "late": int(len(late)),
        "median_days_late": float(late["gap"].median()) if len(late) else 0.0,
        "reasons": reason_counts(late),
        "suppliers": [{"Supplier Name": r["Supplier Name"], "orders": int(r["orders"]),
                       "late": int(r["late"]), "ontime_pct": float(r["ontime_pct"])}
                      for _, r in sup_g.iterrows()],
        "weekly": wk,
    }


base["week"] = base["ATA"].dt.strftime("%G-W%V")
weeks = sorted(base["week"].dropna().unique())[-13:]
weekly = []
for w in weeks:
    sl = base[base["week"] == w]
    weekly.append({
        "week": w, "n": int(len(sl)),
        "strict": round((sl["gap"] <= 0).mean() * 100, 1),
        "w3": round((sl["gap"] <= 3).mean() * 100, 1),
        "w7": round((sl["gap"] <= 7).mean() * 100, 1),
    })

late_all = base[base["gap"] > 0]
sup_all = (base.groupby("Supplier Name").size().rename("orders").to_frame()
           .join(late_all.groupby("Supplier Name").size().rename("late")))
sup_all["late"] = sup_all["late"].fillna(0)
sup_all = sup_all[sup_all["orders"] >= MIN_SUPPLIER_ORDERS]
sup_all["ontime_pct"] = ((sup_all["orders"] - sup_all["late"]) / sup_all["orders"] * 100).round(1)
worst = sup_all.sort_values("ontime_pct").head(10).reset_index()

perf = {
    "asof": f"{ASOF:%Y-%m-%d}",
    "population": population,
    "s01_excluded": s01_excluded,
    "strict": strict, "w3": w3, "w7": w7,
    "weekly": weekly,
    "late_total": int(len(late_all)),
    "reasons": reason_counts(late_all),
    "late_gap_median": float(late_all["gap"].median()),
    "suppliers_worst": [{"Supplier Name": r["Supplier Name"], "orders": int(r["orders"]),
                         "late": int(r["late"]), "ontime_pct": float(r["ontime_pct"])}
                        for _, r in worst.iterrows()],
    "supplier_count": int(sup_all.shape[0]),
    "defs": {"strict": band_detail(0), "w3": band_detail(3), "w7": band_detail(7)},
    "weeks_axis": weeks,
    "supplier_match_pct": round(ldd["Supplier Name"].notna().mean() * 100, 1),
}

# --------------------------------------------------------- trends ----------
print("trends ...", flush=True)
base["month"] = base["ATA"].dt.strftime("%Y-%m")
months = sorted(base["month"].dropna().unique())[-11:]
monthly_ontime = [{
    "month": m,
    "n": int((base["month"] == m).sum()),
    "strict": round((base.loc[base["month"] == m, "gap"] <= 0).mean() * 100, 1),
    "w7": round((base.loc[base["month"] == m, "gap"] <= 7).mean() * 100, 1),
} for m in months]

# transit time: ATD -> ATA
tr = ldd.dropna(subset=["ATD", "ATA"]).copy()
tr["days"] = (tr["ATA"] - tr["ATD"]).dt.days
tr = tr[(tr["days"] >= 0) & (tr["days"] < 200)]
tr["month"] = tr["ATA"].dt.strftime("%Y-%m")
transit_months = [{"month": m, "days": float(tr.loc[tr["month"] == m, "days"].median())}
                  for m in months if (tr["month"] == m).any()]

# ------------------------------------------------------- reliability -------
print("reliability ...", flush=True)
if HAVE["peta"]:
    peta = load("peta")
    peta_rows = []
    for _, r in peta.iterrows():
        peta_rows.append({c: clean(r[c]) for c in peta.columns})

    HORIZONS = [
        ("At booking", "Predicted ETA at time of booking"),
        ("At departure", "Predicted ETA at time of departure"),
        ("4 weeks out", "Predicted ETA , after departure, 4 weeks before ATA"),
        ("2 weeks out", "Predicted ETA , after departure, 2 weeks before ATA"),
        ("1 week out", "Predicted ETA , after departure, 1 weeks before ATA"),
    ]
    pa = dt(peta["ATA"])
    horizons = []
    for label, col in HORIZONS:
        if col not in peta.columns:
            continue
        d = (dt(peta[col]) - pa).dt.days.dropna()
        if not len(d):
            continue
        horizons.append({
            "horizon": label, "n": int(len(d)),
            "within2": round((d.abs() <= 2).mean() * 100, 1),
            "within5": round((d.abs() <= 5).mean() * 100, 1),
            "med_abs": float(d.abs().median()),
            "bias": float(d.median()),
        })

else:
    skipped("Pepco Predictive ETA", "ETA reliability bars on In transit")
    peta_rows, horizons, pa = None, None, None

# ------------------------------------------------------------ customs ------
print("customs ...", flush=True)
if HAVE["cust"]:
    cust = load("cust")
    for c in ["ATA", "Customs Clearance Finish Date", "Customs Clearance Start Date"]:
        cust[c] = dt(cust[c])
    held = cust[cust["ATA"].notna() & cust["Customs Clearance Finish Date"].isna()].copy()
    held["days"] = (ASOF - held["ATA"]).dt.days
    held = held[held["days"] >= 0]
    # ASSUMPTION: "arrived but held in customs" = ATA present, no clearance finish date.
    assume("customs card = containers with an ATA but no Customs Clearance Finish Date")

    CUST_COLS = {"Container": "Container No", "Booking": "Carrier Booking Ref",
                 "MBL": "MBL", "DC": "DC", "Arrived": "ATA",
                 "Days held": "days", "Reason": "Reason Code"}
    customs_win = {"windows": ["d7", "d30", "d90"],
                   "labels": {"d7": "Last 7 days", "d30": "Last 30 days", "d90": "Last 90 days"},
                   "data": {}}
    raw_customs_win = {}
    for key, win in [("d7", 7), ("d30", 30), ("d90", 90)]:
        sl = held[held["days"] <= win]
        rc = sl["Reason Code"].fillna("Not yet coded").replace("", "Not yet coded")
        customs_win["data"][key] = {
            "total": int(len(sl)),
            "by_dc": {str(k): int(v) for k, v in sl["DC"].value_counts().head(6).items()},
            "reasons": {str(k): int(v) for k, v in rc.value_counts().head(6).items()},
        }
        raw_customs_win[key] = rows(sl.sort_values("days", ascending=False).head(600), CUST_COLS)

else:
    skipped("Incomplete Reason - Customs Clearance Finished",
            "customs held card on Destination")
    customs_win = None
    raw_customs_win = {"d7": [], "d30": [], "d90": []}

# --------------------------------------------------------------- CRD -------
print("CRD reasons ...", flush=True)
if HAVE["po"]:
    po = load("po", usecols=["Order Number", "Supplier Name", "Origin Location",
                             "Cargo Ready Date Reason", "Cargo Ready Date Due Date",
                             "CRD Overdue Days", "CRD Done Segment"])
    po["Cargo Ready Date Due Date"] = dt(po["Cargo Ready Date Due Date"])
    # The card asks "why suppliers MISS their cargo-ready date", so it must count
    # missed CRDs, not every order that happens to carry a reason code. Use the BI
    # tool's own classification rather than a hand-picked overdue-days threshold.
    coded = po[po["Cargo Ready Date Reason"].notna() &
               (po["Cargo Ready Date Reason"].astype(str).str.strip() != "") &
               (po["CRD Done Segment"].astype(str).str.strip().str.upper()
                == "DONE IN OVERDUE")].copy()
    assume("CRD card counts orders where CRD Done Segment = 'DONE IN OVERDUE' and a "
           "Cargo Ready Date Reason is recorded")


    def crd_group(code):
        s = str(code)
        if s.startswith("B"):
            return "Buyer"
        if s.startswith("S"):
            return "Supplier"
        if s.startswith("N"):
            return "Natural Factors"
        return "Other"


    coded["grp"] = coded["Cargo Ready Date Reason"].map(crd_group)
    CRD_COLS = {"Order": "Order Number", "Supplier": "Supplier Name",
                "Origin": "Origin Location", "Due": "Cargo Ready Date Due Date",
                "Reason": "Cargo Ready Date Reason"}
    sup_reasons_win = {"windows": ["m3", "m6", "all"],
                       "labels": {"m3": "Past 3 months", "m6": "Past 6 months", "all": "All time"},
                       "data": {}}
    raw_crd_win = {}
    for key, mo in [("m3", 3), ("m6", 6), ("all", None)]:
        sl = coded if mo is None else coded[coded["Cargo Ready Date Due Date"] >=
                                            ASOF - pd.DateOffset(months=mo)]
        sup_reasons_win["data"][key] = {
            "total": int(len(sl)),
            "split": {k: int(v) for k, v in sl["grp"].value_counts().items()},
            "top": {str(k): int(v) for k, v in
                    sl["Cargo Ready Date Reason"].value_counts().head(8).items()},
        }
        raw_crd_win[key] = rows(sl.sort_values("Cargo Ready Date Due Date",
                                               ascending=False).head(900), CRD_COLS)
else:
    # No PO_milestone_performance export in this refresh. The cargo-ready reason
    # card is the only thing in this file that depends on it, so keep its previous
    # figures verbatim and let the page label them with the date they came from.
    sup_reasons_win = PREV.get("sup_reasons_win")
    raw_crd_win = PREV.get("raw", {}).get("crd_win", {})
    skipped("PO_milestone_performance", "coded cargo-ready reasons on Origin")

# ---------------------------------------------------------- tranship -------
print("tranship ...", flush=True)
tran = load("tran")
# The 2026-08-23 refresh delivers this export row-level (one row per booking,
# raw ATA/ATD in the transit port) where it used to arrive pre-aggregated. Fold
# the row-level shape into the aggregate columns the rest of this file expects,
# so one booking = one container with its own dwell time.
if "Count of Container" not in tran.columns:
    _dw = pd.to_numeric(tran["ATA - ATD (Transit Port)"], errors="coerce")
    tran = tran.assign(**{
        "ATD Month": dt(tran["ATD at POL"]).dt.strftime("%Y-%m"),
        "Count of Container": 1,
        "Average of Expected Dwell time": _dw,
        "Max of Expected Dwell time": _dw,
    })
    tran = tran[_dw.notna()]
    note("tranship export arrived row-level and carries no container count, so "
         "the card now counts BOOKINGS waiting, not containers - its labels were "
         "changed to match. Dwell is measured per booking from ATA/ATD in the "
         "transit port instead of read from a pre-aggregated column.")
# The export carries artifact rows ("No filters applied", blanks) in ATD Month,
# and a plain string max() picks those over any real YYYY-MM value.
_months = sorted(m for m in tran["ATD Month"].dropna().unique()
                 if re.fullmatch(r"\d{4}-\d{2}", str(m).strip()))
_keep = _months[-2:]
tran = tran[tran["ATD Month"].isin(_keep)]
assume(f"tranship figures cover the two most recent ATD months ({', '.join(_keep)}); "
       "the extract spans all history, so it must be windowed")
TRAN_COLS = {"Booking": "Booking Number", "Carrier": "Carrier", "Transit port": "Transit Port",
             "POL": "POL", "POD": "POD",
             "Days waiting": "Average of Expected Dwell time",
             "Est. total wait": "Max of Expected Dwell time"}
tran_ports = (tran.groupby("Transit Port")
              .agg(n=("Count of Container", "sum"),
                   max_wait=("Max of Expected Dwell time", "max"))
              .sort_values("n", ascending=False).head(6).reset_index())

# ------------------------------------------------------------ daily --------
print("daily ...", flush=True)
dest = load("dest")
for c in ["ETA", "ATA"]:
    dest[c] = dt(dest[c])
tc = load("tc")
for c in ["ATA", "Delivery Schedule Date", "Arrival at DC Date"]:
    tc[c] = dt(tc[c])

# ASSUMPTION: "today" on the report is the as-of date (latest ATA in the data).
assume(f"'today' on the Today tab = the as-of date, {ASOF:%d %b %Y}")

dep = ldd[ldd["ATD"].dt.normalize() == ASOF]
DEP_COLS = {"Order": "Order Number", "Booking": "Carrier Booking Ref",
            "POL": "Carrier Booking POL", "DC": "DC",
            "ETA at destination": "FINAL ETA"}

port_today = dest[dest["ETA"].dt.normalize() == ASOF].copy()
port_today["Status"] = np.where(port_today["ATA"].notna(), "Arrived", "Due")
PORT_COLS = {"Container": "Container No.", "Booking": "Carrier Booking Ref",
             "POD": "POD", "DC": "DC", "Vessel": "Arrival Vessel",
             "ETA": "ETA", "ATA": "ATA", "Status": "Status"}

dc_today = tc[tc["Delivery Schedule Date"].dt.normalize() == ASOF].copy()
DC_COLS = {"Container": "Container No", "Booking": "Carrier Booking Ref",
           "DC": "DC Name", "MOT": "MOT", "Scheduled": "Delivery Schedule Date",
           "Arrived": "Arrival at DC Date"}

# orders still in transit whose FINAL ETA lands after the required In DC Date
pend = ldd[ldd["ATA"].isna() & ldd["In DC Date"].notna() & ldd["FINAL ETA"].notna()].copy()
pend["days_late"] = (pend["FINAL ETA"] - pend["In DC Date"]).dt.days
pend["due_in"] = (pend["In DC Date"] - ASOF).dt.days
ahod_reason = (ahod.dropna(subset=["AHOD Reason Description"])
               .drop_duplicates("Order Number")
               .set_index("Order Number")["AHOD Reason Description"])
pend["Reason"] = pend["Order Number"].map(ahod_reason).fillna("No reason recorded")
assume("'at risk' = order not yet arrived whose FINAL ETA falls after its "
       "required In DC Date, with In DC Date inside the window")

RISK_COLS = {"Order": "Order Number", "Booking": "Carrier Booking Ref", "DC": "DC",
             "Supplier": "Supplier Name", "Required": "In DC Date",
             "ETA": "FINAL ETA", "Days late": "days_late", "Reason": "Reason"}


def window(days):
    due = pend[(pend["due_in"] >= 0) & (pend["due_in"] <= days)]
    risk = due[due["days_late"] > 0]
    by_dc = (risk.groupby("DC")
             .agg(n=("Order Number", "size"), max_days=("days_late", "max"))
             .sort_values("n", ascending=False).head(5).reset_index())
    top = {}
    for d in by_dc["DC"]:
        rr = risk.loc[risk["DC"] == d, "Reason"]
        top[d] = rr.value_counts().index[0] if len(rr) else "No reason recorded"
    return due, risk, by_dc, top


due7, risk7, by_dc7, top7 = window(7)
daily_risk = {
    "total": int(len(risk7)), "due_total": int(len(due7)),
    "by_reason": {str(k): int(v) for k, v in risk7["Reason"].value_counts().head(4).items()},
    "by_dc": [{"DC": r["DC"], "n": int(r["n"]), "max_days": int(r["max_days"]),
               "top_reason": top7[r["DC"]]} for _, r in by_dc7.iterrows()],
    "suppliers": {str(k): int(v) for k, v in
                  risk7.loc[risk7["Reason"].str.contains("Supplier", case=False, na=False),
                            "Supplier Name"].value_counts().head(3).items()},
    "median_days": int(risk7["days_late"].median()) if len(risk7) else 0,
    "max_days": int(risk7["days_late"].max()) if len(risk7) else 0,
}

wk_start = ASOF - pd.Timedelta(days=int(ASOF.dayofweek))
wk = base[base["ATA"] >= wk_start]
daily_week = {
    "n": int(len(wk)),
    "pct": round((wk["gap"] <= 0).mean() * 100, 1) if len(wk) else 0.0,
    "days": [{"day": f"{d:%a}", "n": int((wk["ATA"].dt.normalize() == d).sum()),
              "pct": round((wk.loc[wk["ATA"].dt.normalize() == d, "gap"] <= 0).mean() * 100, 1)}
             for d in pd.date_range(wk_start, ASOF)
             if (wk["ATA"].dt.normalize() == d).any()],
    "label": f"{wk_start:%d %b} – today",
    "late": int((wk["gap"] > 0).sum()),
    "late_reasons": reason_counts(wk[wk["gap"] > 0], top=3),
}

daily = {
    "risk": daily_risk,
    "departed": {"orders": int(len(dep)),
                 "bookings": int(dep["Carrier Booking Ref"].nunique())},
    "port_arrivals": {
        "total": int(len(port_today)),
        "arrived": int(port_today["ATA"].notna().sum()),
        "by_dc": {str(k): int(v) for k, v in port_today["DC"].value_counts().head(5).items()}},
    "dc_deliveries": {
        "total": int(len(dc_today)),
        "arrived": int(dc_today["Arrival at DC Date"].notna().sum()),
        "by_dc": {str(k): int(v) for k, v in dc_today["DC Name"].value_counts().head(5).items()}},
    "week": daily_week,
    "today_str": f"{ASOF:%A %-d %B %Y}",
}

# --------------------------------------------------------- pipeline --------
pipeline = {}
for key, days in [("d3", 3), ("d7", 7)]:
    due, risk, by_dc, _ = window(days)
    due = due.copy()
    due["Status"] = np.where(due["days_late"] > 0, "At risk", "On track")
    pipeline[key] = {
        "total": int(len(due)), "atrisk": int(len(risk)),
        "by_dc": [{"DC": r["DC"], "n": int((due["DC"] == r["DC"]).sum()),
                   "atrisk": int(r["n"])} for _, r in by_dc.iterrows()],
        "raw": rows(due.sort_values("In DC Date").head(2500),
                    {"Order": "Order Number", "DC": "DC", "Supplier": "Supplier Name",
                     "Required": "In DC Date", "ETA": "FINAL ETA", "Status": "Status"}),
    }

# --------------------------------------------------------- arrivals --------
print("arrivals ...", flush=True)
dest["wk"] = dest["ETA"].dt.to_period("W").dt.start_time
awks = sorted([w for w in dest["wk"].dropna().unique() if w <= ASOF + pd.Timedelta(days=21)])[-6:]
adcs = []
for d in dest["DC"].dropna().unique():
    sl = dest[dest["DC"] == d]
    cells = [int(((sl["wk"] == w)).sum()) for w in awks]
    if sum(cells) == 0:
        continue
    adcs.append({"dc": str(d), "cells": cells,
                 "later": int((sl["wk"] > awks[-1]).sum())})
adcs = sorted(adcs, key=lambda x: -sum(x["cells"]))[:7]
preplan_col = "Delivery Pre-plan"
arrivals = {
    "weeks": [f"{w:%Y-%m-%d}" for w in awks],
    "dcs": adcs,
    "in_transit_total": int(dest["ATA"].isna().sum()),
    "preplan": {str(k): int(v) for k, v in
                dest[preplan_col].value_counts().head(4).items()} if preplan_col in dest else {},
}

# ------------------------------------------------------ weekly pages -------
print("weekly pages ...", flush=True)
wk_dest = dest[(dest["ETA"] >= wk_start) & (dest["ETA"] <= wk_start + pd.Timedelta(days=6))].copy()
wk_dest["status"] = np.where(wk_dest["ATA"].notna(), "Arrived",
                             np.where(wk_dest["ETA"] < ASOF, "Overdue", "Due"))
wp_arrivals = {
    "week_label": f"{wk_start:%d}–{wk_start + pd.Timedelta(days=6):%d %b}",
    "total": int(len(wk_dest)),
    "status": {k: int((wk_dest["status"] == k).sum()) for k in ["Arrived", "Due", "Overdue"]},
    "by_dc": [{"DC": str(d),
               **{s: int(((wk_dest["DC"] == d) & (wk_dest["status"] == s)).sum())
                  for s in ["Arrived", "Due", "Overdue"]}}
              for d in wk_dest["DC"].value_counts().head(7).index],
    "raw": rows(wk_dest, {"Container No.": "Container No.", "Booking": "Carrier Booking Ref",
                          "POD": "POD", "DC": "DC", "ETA": "ETA", "ATA": "ATA",
                          "status": "status", "Arrival Vessel": "Arrival Vessel"}),
}

wk_perf = base[base["ATA"] >= wk_start].copy()
wk_late = wk_perf[wk_perf["gap"] > 0]
wsup = (wk_perf.groupby("Supplier Name").size().rename("orders").to_frame()
        .join(wk_late.groupby("Supplier Name").size().rename("late")))
wsup["late"] = wsup["late"].fillna(0)
# Ranking a weekly accountability table by on-time % with no volume floor puts
# suppliers with a single order at the top on 0%, which is noise and unfair to
# name. Require a few orders, and rank by how many misses they actually caused.
MIN_WEEK_SUPPLIER_ORDERS = 3
assume(f"weekly supplier table needs >= {MIN_WEEK_SUPPLIER_ORDERS} orders due that "
       "week and is ranked by late count, not by on-time %")
wsup = wsup[(wsup["late"] > 0) &
            (wsup["orders"] >= MIN_WEEK_SUPPLIER_ORDERS)].copy()
wsup["ontime_pct"] = ((wsup["orders"] - wsup["late"]) / wsup["orders"] * 100).round(1)
wsup = wsup.sort_values(["late", "ontime_pct"],
                        ascending=[False, True]).head(8).reset_index()
wk_perf["Status"] = np.where(wk_perf["gap"] <= 0, "On time", "Late")
wp_perf = {
    "n": int(len(wk_perf)),
    "pct": round((wk_perf["gap"] <= 0).mean() * 100, 1) if len(wk_perf) else 0.0,
    "late": int(len(wk_late)),
    "reasons": reason_counts(wk_late, top=3),
    "suppliers": [{"Supplier Name": r["Supplier Name"], "orders": int(r["orders"]),
                   "late": int(r["late"]), "ontime_pct": float(r["ontime_pct"])}
                  for _, r in wsup.iterrows()],
    "median_late": float(wk_late["gap"].median()) if len(wk_late) else 0.0,
    # Surfaced so the card's footnote states the real threshold instead of a
    # hardcoded one — the markup previously claimed >=10 while the code used a
    # different value, and the table listed suppliers below the stated cutoff.
    "min_orders": MIN_WEEK_SUPPLIER_ORDERS,
}

# Week-on-week and month-to-date context for the weekly card. A partial week can
# swing hard, so the headline number is close to meaningless without the
# previous week beside it and the month's dominant driver underneath.
prev_wk = weekly[-2] if len(weekly) > 1 else None
cur_month = f"{ASOF:%Y-%m}"
prev_month = f"{(ASOF.replace(day=1) - pd.Timedelta(days=1)):%Y-%m}"
mth = base[base["month"] == cur_month]
mth_prev = base[base["month"] == prev_month]
mth_late = mth[mth["gap"] > 0]
# "biggest issue" means the largest *attributed* cause; "No reason recorded" is
# a data-capture gap, so it is reported separately rather than as the top driver.
mth_coded = mth_late[mth_late[REASON_COL].notna() &
                     (mth_late[REASON_COL].astype(str).str.strip() != "")]
top_reason = mth_coded[REASON_COL].value_counts()
wp_perf["compare"] = {
    "week": weekly[-1]["week"], "pct": weekly[-1]["strict"],
    "prev_week": prev_wk["week"] if prev_wk else None,
    "prev_pct": prev_wk["strict"] if prev_wk else None,
    "delta": round(weekly[-1]["strict"] - prev_wk["strict"], 1) if prev_wk else None,
    "partial_week": True,
    "month": cur_month,
    "month_n": int(len(mth)),
    "month_pct": round((mth["gap"] <= 0).mean() * 100, 1) if len(mth) else None,
    "month_late": int(len(mth_late)),
    "prev_month": prev_month,
    "prev_month_pct": round((mth_prev["gap"] <= 0).mean() * 100, 1) if len(mth_prev) else None,
    "month_delta": (round((mth["gap"] <= 0).mean() * 100 - (mth_prev["gap"] <= 0).mean() * 100, 1)
                    if len(mth) and len(mth_prev) else None),
    "month_top_reason": str(top_reason.index[0]) if len(top_reason) else None,
    "month_top_n": int(top_reason.iloc[0]) if len(top_reason) else 0,
    "month_uncoded": int(len(mth_late) - len(mth_coded)),
}

tran_dwell = tran["Average of Expected Dwell time"]
wp_watch = {
    "tranship_total": int(tran["Count of Container"].sum()),
    "tranship_over7": int(tran.loc[tran_dwell > 7, "Count of Container"].sum()),
    "tranship_over14": int(tran.loc[tran_dwell > 14, "Count of Container"].sum()),
    "tranship_ports": [{"Transit Port": r["Transit Port"], "n": int(r["n"]),
                        "max_wait": float(r["max_wait"])}
                       for _, r in tran_ports.iterrows()],
    "tranship_median": round(float(tran_dwell.median()), 1),
    **({"customs_total": customs_win["data"]["d30"]["total"],
        "customs_by_dc": customs_win["data"]["d30"]["by_dc"],
        "customs_reasons": customs_win["data"]["d30"]["reasons"]}
       if HAVE["cust"] else
       {k: PREV.get("weekly_pages", {}).get("watch", {}).get(k)
        for k in ("customs_total", "customs_by_dc", "customs_reasons")}),
}

# ------------------------------------------------------------ risks --------
intransit = ldd[ldd["ATA"].isna()]
fm = ahod[ahod["code"].str.startswith("FM", na=False)].copy()
fm["AHOD Create Time"] = dt(fm["AHOD Create Time"])
fm_recent = fm[fm["AHOD Create Time"] >= ASOF - pd.Timedelta(days=30)]
risks = {
    "exposure": [{"Carrier Booking POL Region": str(k), "orders": int(v),
                  "containers": int(intransit.loc[intransit["Carrier Booking POL Region"] == k,
                                                  "Container No"].nunique())}
                 for k, v in intransit["Carrier Booking POL Region"].value_counts().head(8).items()],
    "in_transit_orders": int(len(intransit)),
    "fm_events": [{"AHOD Reason Description": str(k[0]), "Origin Location": str(k[1]),
                   "n": int(v)}
                  for k, v in fm_recent.groupby(
                      ["AHOD Reason Description", "Origin Location"]).size()
                  .sort_values(ascending=False).head(6).items()],
    "fm_total": int(len(fm_recent)),
}

# ---------------------------------------------------------- compare --------
cmonths = sorted(base["month"].dropna().unique())[-13:]
compare_monthly = [{"month": m, "orders": int((base["month"] == m).sum()),
                    "ontime": round((base.loc[base["month"] == m, "gap"] <= 0).mean() * 100, 1)}
                   for m in cmonths]
cur_m, prev_m = cmonths[-1], cmonths[-2]
compare_comps = [{
    "label": "Month to date vs previous month",
    "cur_month": cur_m, "prev_month": prev_m,
    "ontime_cur": compare_monthly[-1]["ontime"], "ontime_prev": compare_monthly[-2]["ontime"],
    "ontime_delta": round(compare_monthly[-1]["ontime"] - compare_monthly[-2]["ontime"], 1),
    "orders_cur": compare_monthly[-1]["orders"], "orders_prev": compare_monthly[-2]["orders"],
}]
compare_arrival = {
    "due_total": int(len(ldd.dropna(subset=["In DC Date"]))),
    "arrived": int(ldd["ATA"].notna().sum()),
    "not_arrived": int(ldd["ATA"].isna().sum()),
    "arrived_pct": round(ldd["ATA"].notna().mean() * 100, 1),
    "in_transit": int(len(intransit)),
}

# --------------------------------------------------------- raw tables ------
raw_new = {
    "departed": rows(dep.head(1500), DEP_COLS),
    "risk": rows(risk7.sort_values("days_late", ascending=False).head(1500), RISK_COLS),
    "port_today": rows(port_today.head(1500), PORT_COLS),
    "dc_today": rows(dc_today.head(1500), DC_COLS),
    "perf_week": rows(wk_perf.sort_values("ATA", ascending=False).head(2000),
                      {"Order": "Order Number", "Booking": "Carrier Booking Ref", "DC": "DC",
                       "Supplier": "Supplier Name", "Required": "In DC Date",
                       "ETA": "FINAL ETA", "Days +/-": "gap", "Status": "Status",
                       "Reason": REASON_COL}),
    "tranship": rows(tran.sort_values("Average of Expected Dwell time",
                                      ascending=False).head(600), TRAN_COLS),
    "customs": raw_customs_win["d30"],
    "reliability": peta_rows,
    "crd_win": raw_crd_win,
    "customs_win": raw_customs_win,
    # These three panels rendered "No rows." in the live report: the aggregates
    # behind the charts existed but the raw tables under them were never built.
    "weekly13": [{"Week": w["week"], "Orders": w["n"], "On time %": w["strict"],
                  "Within +3d %": w["w3"], "Within +7d %": w["w7"],
                  "Late": int(round(w["n"] * (100 - w["strict"]) / 100))}
                 for w in weekly],
    "monthly_ontime": [{"Month": m["month"], "Orders": m["n"],
                        "On time %": m["strict"], "Within +7d %": m["w7"],
                        "Late": int(round(m["n"] * (100 - m["strict"]) / 100))}
                       for m in monthly_ontime],
    "transit": rows(tr.sort_values("ATA", ascending=False).head(2000),
                    {"Order": "Order Number", "Booking": "Carrier Booking Ref",
                     "POL": "Carrier Booking POL", "POD": "Carrier Booking POD",
                     "DC": "DC", "Departed": "ATD", "Arrived": "ATA",
                     "Transit days": "days"}),
}

# ------------------------------------------------------------- output ------
D_new_parts = {
    "_daily": daily, "_pipeline": pipeline, "_arrivals": arrivals,
    "_weekly_pages": {"arrivals": wp_arrivals, "perf": wp_perf, "watch": wp_watch},
    "_risks": risks,
    "_compare": {"monthly": compare_monthly, "comps": compare_comps,
                 "arrival": compare_arrival},
    "_raw": raw_new,
    "perf": perf,
    "trends_monthly_ontime": monthly_ontime,
    "trends_transit_months": transit_months,
    "trends_reliability": ({"horizons": horizons, "note_n": int(pa.notna().sum())}
                           if HAVE["peta"] else None),
    "raw_reliability": peta_rows,
    "customs_win": customs_win,
    "raw_customs_win": raw_customs_win,
    "sup_reasons_win": sup_reasons_win,
    "raw_crd_win": raw_crd_win,
    "tranship_ports": [{"Transit Port": r["Transit Port"], "n": int(r["n"]),
                        "max_wait": float(r["max_wait"])}
                       for _, r in tran_ports.iterrows()],
}

# ------------------------------------------------------ volume / TEU -------
print("volume ...", flush=True)
if HAVE["msum"]:
    msum = load("msum")
    msum["Total TEU"] = pd.to_numeric(msum["Total TEU"], errors="coerce")
    mg = msum.groupby("ATA Month").agg(teu=("Total TEU", "sum"),
                                       orders=("Total Order", "sum"),
                                       cont=("Total Container", "sum")).reset_index()
    mg = mg[mg["ATA Month"].astype(str).str.match(r"\d{4}-\d{2}")].sort_values("ATA Month")
    volume_months = [{"ATA Month": r["ATA Month"], "teu": float(r["teu"]),
                      "orders": int(r["orders"]), "cont": int(r["cont"])}
                     for _, r in mg.tail(11).iterrows()]
    monthly_teu = {r["ATA Month"]: float(r["teu"]) for _, r in mg.iterrows()}

    last3 = list(mg["ATA Month"])[-3:]
    ports_3mo = (msum[msum["ATA Month"].isin(last3)]
                 .groupby("Destination Port")["Total TEU"].sum()
                 .sort_values(ascending=False))
    volume = {"months": volume_months,
              "ports_3mo": {str(k): float(v) for k, v in ports_3mo.head(5).items()}}

    # The extract is a rolling window, so BOTH ends are partial months: it starts
    # mid-July 2025 (322 TEU against 8,430 that September) and ends five days into
    # August 2026. Comparing either against a full month produces a headline that is
    # an artifact of the calendar - the live report shows +1713% YoY for exactly
    # this reason. Compare complete months only.
    _mk = list(mg["ATA Month"])
    _partial = {_mk[0], _mk[-1]}
    _complete = [m for m in _mk if m not in _partial]
    cur_v, prev_v = _complete[-1], _complete[-2]
    yoy = f"{int(cur_v[:4]) - 1}-{cur_v[5:]}"
    note(f"volume comparisons use the last complete month ({cur_v}); "
         f"{', '.join(sorted(_partial))} are partial window edges and are excluded")


    def _pct(a, b):
        return round((a - b) / b * 100, 1) if b else None


    volume_mom = {"cur": cur_v, "teu_cur": int(monthly_teu[cur_v]), "prev": prev_v,
                  "teu_prev": int(monthly_teu[prev_v]),
                  "delta_pct": _pct(monthly_teu[cur_v], monthly_teu[prev_v])}
    volume_yoy = ({"cur": cur_v, "teu_cur": int(monthly_teu[cur_v]), "prev": yoy,
                   "teu_prev": int(monthly_teu[yoy]),
                   "delta_pct": _pct(monthly_teu[cur_v], monthly_teu[yoy])}
                  if yoy in monthly_teu and yoy not in _partial else None)
    if volume_yoy is None:
        note(f"year-on-year suppressed: {yoy} is a partial window edge, so the "
             "comparison would be meaningless")
    note(f"TEU months {_mk[0]} .. {_mk[-1]}; latest month is partial")

    MSUM_COLS = {"Month": "ATA Month", "Week": "ATA Week",
                 "Destination port": "Destination Port", "Containers": "Total Container",
                 "Orders": "Total Order", "TEU": "Total TEU"}
    raw_monthly_teu = rows(msum[msum["ATA Month"].astype(str).str.match(r"\d{4}-\d{2}")]
                           .sort_values(["ATA Month", "ATA Week"], ascending=False), MSUM_COLS)
else:
    # No Monthly_Summary export in this refresh. The volume card plots TEU,
    # containers AND orders per month; the booking-level exports that did arrive
    # carry no order count, so a substitute would silently redefine one of the
    # three series. Keep the whole card on its previous figures instead.
    volume = PREV.get("volume")
    monthly_teu = PREV.get("trends", {}).get("monthly_teu", {})
    volume_mom = PREV.get("compare", {}).get("volume_mom")
    volume_yoy = PREV.get("compare", {}).get("volume_yoy")
    raw_monthly_teu = PREV.get("raw", {}).get("monthly_teu", [])
    skipped("Monthly_Summary", "monthly TEU and volume on Trends")

# ------------------------------------------------ destination lead time ----
print("lead time ...", flush=True)
dlt = load("dlt")
for c in ["ATA", "Arrival DC Date", "Customs Clearance Finish Date", "Discharge Date"]:
    dlt[c] = dt(dlt[c])
A2DC, D2DC, C2DC = ("ATA TO Arrival in the DC",
                    "Actual discharge date TO Arrival in the DC",
                    "Customs TO Arrival in the DC")
for c in (A2DC, D2DC, C2DC):
    dlt[c] = pd.to_numeric(dlt[c], errors="coerce")
dl = dlt[dlt["ATA"].notna()].copy()
dl["month"] = dl["ATA"].dt.strftime("%Y-%m")
port_months = [{"month": m, "days": float(dl.loc[dl["month"] == m, A2DC].median())}
               for m in months if (dl["month"] == m).any()]

LEADTIME_COLS = {"Booking": "Carrier Booking Ref", "Container": "Container No",
                 "DC": "DC Name", "MOT": "MOT", "Arrived port": "ATA",
                 "Discharged": "Discharge Date",
                 "Customs finished": "Customs Clearance Finish Date",
                 "Arrived DC": "Arrival DC Date", "Port to DC days": A2DC,
                 "Customs to DC days": C2DC, "T1": "T1 Status"}
raw_port = rows(dl.sort_values("ATA", ascending=False).head(2500), LEADTIME_COLS)

# ------------------------------------------------- merge into the old D ----
# Load the D currently in index.html and overwrite only the sections rebuilt
# here. Anything driven by an extract we were not given keeps its old value and
# is listed under NOT UPDATED rather than being silently passed off as current.
html = HTML.read_text(encoding="utf-8")
m = re.search(r"^const D = (.*?);?$", html, re.M)
D = json.loads(m.group(1).rstrip(";"))

D["perf"] = perf
D["daily"] = daily
D["pipeline"] = pipeline
D["arrivals"] = arrivals
D["weekly_pages"] = {"arrivals": wp_arrivals, "perf": wp_perf, "watch": wp_watch}
D["risks"] = risks
if HAVE["cust"]:
    D["customs_win"] = customs_win
D["sup_reasons_win"] = sup_reasons_win
# Sections whose extract was absent keep the previous figures. Record that in
# the dataset so the affected cards can say so on the page, rather than showing
# stale numbers under the current as-of date.
_prev_stale = {u.get("section"): u.get("asof") for u in PREV.get("not_updated", [])}
D["not_updated"] = [{"section": what, "needs": export,
                     # a section that stayed behind keeps the date it was last
                     # actually built, not the date of the run that skipped it
                     "asof": _prev_stale.get(what) or PREV.get("perf", {}).get("asof"),
                     "anchor": {"customs held card on Destination": "cu-note",
                                "ETA reliability bars on In transit": "rel-note",
                                "coded cargo-ready reasons on Origin": "cr-note",
                                "monthly TEU and volume on Trends": "tr-teu"}.get(what)}
                    for export, what in not_updated]

if not HAVE["peta"]:
    raw_new.pop("reliability", None)
if not HAVE["cust"]:
    raw_new.pop("customs", None)
    raw_new.pop("customs_win", None)
D["raw"] = {**D.get("raw", {}), **raw_new}
D["trends"]["monthly_ontime"] = monthly_ontime
D["trends"]["transit"]["transit_months"] = transit_months
D["trends"]["transit"]["transit_median"] = round(float(tr["days"].median()), 1)
D["trends"]["monthly_teu"] = monthly_teu
D["trends"]["transit"]["port_months"] = port_months
D["trends"]["transit"]["port_median"] = round(float(dl[A2DC].median()), 1)
D["trends"]["transit"]["port_customs_median"] = round(float(dl[C2DC].median()), 1)
D["trends"]["transit"]["port_disch_median"] = round(float(dl[D2DC].median()), 1)
D["volume"] = volume
D["raw"]["monthly_teu"] = raw_monthly_teu
D["raw"]["port"] = raw_port
D["compare"]["volume_mom"] = volume_mom
# Assign unconditionally: when YoY is suppressed this must clear the previous
# value, not leave the old one sitting in the blob.
D["compare"]["volume_yoy"] = volume_yoy
if HAVE["peta"]:
    D["trends"]["reliability"] = {"horizons": horizons, "note_n": int(pa.notna().sum())}
for k, v in {"monthly": compare_monthly, "comps": compare_comps,
             "arrival": compare_arrival}.items():
    D["compare"][k] = v
D["compare"]["july_partial"] = True

out = Path(__file__).resolve().parent.parent / "build_out.json"
out.write_text(json.dumps(D, ensure_ascii=False))

if WRITE:
    new_line = "const D = " + json.dumps(D, ensure_ascii=False) + ";"
    html = re.sub(r"^const D = .*?;?$", lambda _: new_line, html, count=1, flags=re.M)
    HTML.write_text(html, encoding="utf-8")
    print(f"index.html updated ({len(new_line):,} bytes of data)")
else:
    print("dry run - index.html untouched (pass --write to apply)")

print("\n" + "=" * 70)
print(f"as of {perf['asof']}   population {population:,}   (S01 excluded {s01_excluded:,})")
print(f"  strict {strict['pct']}%   +3d {w3['pct']}%   +7d {w7['pct']}%")
print(f"  late {perf['late_total']:,}   median {perf['late_gap_median']} days")
print(f"  suppliers ranked {perf['supplier_count']}   name match {perf['supplier_match_pct']}%")
print(f"  weeks {weeks[0]} .. {weeks[-1]}")
print(f"  monthly on-time points: {len(monthly_ontime)}   transit points: {len(transit_months)}")
if HAVE["peta"]:
    print(f"  reliability horizons: {len(horizons)}   raw rows: {len(peta_rows)}")
if HAVE["cust"]:
    print(f"  customs d7/d30/d90: " +
          "/".join(str(customs_win['data'][k]['total']) for k in ['d7', 'd30', 'd90']))
print(f"  CRD m3/m6/all: " +
      "/".join(str(sup_reasons_win['data'][k]['total']) for k in ['m3', 'm6', 'all']))
print(f"  CRD all-time split: {sup_reasons_win['data']['all']['split']}")
D_port_median = round(float(dl[A2DC].median()), 1)
print("=" * 70)
print("ASSUMPTIONS")
for a in assumptions:
    print("  -", a)
print("NOTES")
for n in notes:
    print("  -", n)
if not_updated:
    print("\nNOT UPDATED - no export supplied, previous figures kept:")
    for _exp, _what in not_updated:
        print(f"  - {_what}  (needs {_exp})")

if HAVE["msum"]:
    print(f"  TEU {cur_v} {volume_mom['teu_cur']:,} ({volume_mom['delta_pct']:+}% MoM)"
          + (f", {volume_yoy['delta_pct']:+}% YoY" if volume_yoy else ", YoY suppressed"))
print(f"  port->DC median {D_port_median} days   raw.port {len(raw_port):,} rows")
print(f"\nwrote {out.name}")
