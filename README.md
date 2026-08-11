# MOOV Ops — Internal control

A single-page dashboard that carries **both** Pepco reports: the client delivery
report and the internal operational detail behind it.

| | Pages | Audience | Built by |
| --- | --- | --- | --- |
| Client report | 01 Today · 02 Arrivals · 03 Due at DC · 04 Performance · 05 Watch list · 06 Monthly | shared with Pepco | `tools/build_client_data.py` |
| Internal only | 07 Milestones · 08 Carriers · 09 Data quality · 10 Definitions | MOOV teams | `tools/build_dataset.py` here |

Every figure appears exactly once. Where both reports measured the same thing —
delivery performance, customs holds, coded cargo-ready reasons — the client
report's version is kept, because it carries definitions, windows and recorded
causes the internal extracts do not. The internal duplicates were removed.

Everything is baked into `index.html`: markup, styles and the full dataset as an
inline `const D = {...}` object. There is no build step, no backend and no
runtime data fetching. The only external request is the Google Fonts stylesheet.

## ⚠️ Before you share the link

This page carries internal operational data — named suppliers with their overdue
milestone counts, carrier scorecards, booking rejections, order IDs and container
numbers — and its own banner reads *"not for client distribution."* Any host below
serves it to anyone who has the URL, with no login.

That matters more since the merge: pages 01–06 are the report Pepco already sees,
so the link looks shareable. It is not — pages 07–10 are not for the client. If
Pepco needs the client report, send them the `PEPCO-` deployment, not this one.

`robots.txt` and the `X-Robots-Tag` header ask search engines not to index it.
That keeps it out of Google; it does **not** make the link private. Delete both
if you want it indexed.

## Deploying

### Vercel

The repo is ready to import as-is — `vercel.json` marks it a static site.

1. <https://vercel.com/new> → **Import Git Repository** → pick this repo.
2. Framework preset: **Other**. Leave build command and output directory empty.
3. **Deploy**.

You get `<project>.vercel.app` in about a minute. Every later push to the
production branch redeploys automatically.

For a custom domain: Project → **Settings** → **Domains** → add it and point the
DNS record Vercel shows you at it.

### GitHub Pages

`.github/workflows/deploy-pages.yml` deploys on every push to `main` or
`claude/website-deployment-7u69nj`. Pages has to be switched on once by hand
first — the workflow's own token is not allowed to create the site:

1. **Settings → Pages → Build and deployment → Source: GitHub Actions**.
2. **Actions → Deploy to GitHub Pages → Run workflow** (or just push again).

The live URL — `https://b00817261-blip.github.io/PARI/` — then appears in the
workflow summary and under Settings → Pages.

Note that GitHub Pages is only free on public repos. If you make this repo
private, Pages needs a paid plan; Vercel serves private repos on the free tier.

## Updating the data

The dataset is the `const D = {...}` literal in the `<script>` block at the
bottom of `index.html`. It holds both halves, so which script you run depends on
which half went stale.

**Refreshing the internal half (pages 07–10).** `tools/build_dataset.py` reads
the current `index.html`, rebuilds only the internal keys and merges them back
over the client half, which it leaves untouched:

```sh
pip install pandas openpyxl
python3 tools/build_dataset.py --src ~/exports --asof 2026-08-06 --out D.json
```

Drop all nine exports in one folder — the script finds each by a fragment of its
filename, so the random prefix the BI tool adds does not matter:

| Export | Feeds |
| --- | --- |
| `PO_milestone_performance` | stage strip, open-overdue queue, supplier league |
| `latest_delivery_data__pepco` | stale tracking records, data-quality counts |
| `demurrage__detention_tracker` | DEM+DET risk on the Watch list |
| `booking_rejection_analysis` | booking rejections |
| `pepco_carrier_scoring` | carrier league |
| `carrier_performance` | ETD slip column |
| `OHA_KPI` + `pepco_weekly_volume` | KPI bars (rates weighted by shipments) |
| `shipping_document_verification` | doc-verification card |

Then paste the contents of `D.json` over the `const D = {...}` literal. The
`data as of` stamp updates itself from `--asof`. Both hosts redeploy on push, and
`index.html` is served `must-revalidate` so viewers pick up the new numbers
without a hard refresh.

The script also measures the definitions it applied — window widths, due-date
coverage, and how well each stage's own dates reproduce the segment the export
assigns — and writes them into `D.defs`, which renders as the **Definitions**
page. That page is generated, not hand-written, so it always describes the
refresh currently deployed.

**Refreshing the client half (pages 01–06).** `tools/build_client_data.py` builds
those pages from eleven exports of its own, two of which it shares with the
internal build. Drop everything in the same folder and run both scripts:

```sh
python3 tools/build_client_data.py ~/exports --write        # pages 01-06
python3 tools/build_dataset.py --src ~/exports --out D.json # pages 07-10, then re-inject
```

Run them in that order. The internal build owns the Definitions page and fills in
the rows describing both halves, so it needs to see the client figures already in
place.

| Client export | Feeds |
| --- | --- |
| `latest_delivery_da…` | performance, Due at DC, monthly on-time, transit *(shared)* |
| `PO_milestone_performance` | coded cargo-ready reasons *(shared)* |
| `supplier_performance_2` | supplier names on Performance and Needs attention |
| `AHOD_reason_code_2` | miss reasons, FM events, the S01 exclusion |
| `destination_milestone` | Arrivals this week, port arrivals on Today |
| `transport_carrier` | DC deliveries on Today |
| `pepco_tranship_port_performance` | transshipment waiting |
| `Monthly_Summary` | monthly TEU volume |
| `destination_lead_time_analysis` | port-to-DC final leg |
| `customs_clearance_finished` | customs held card *(optional)* |
| `predictive_eta` | ETA reliability bars *(optional)* |

The last two are optional. If they are absent the build keeps those cards'
previous figures, prints them under **NOT UPDATED**, and the page labels each
affected card with the extract date it is still showing — so nothing stale reads
as current. Everything else is required and the build stops if it is missing.

The as-of date is taken from the data (`max ATA`), not passed in. When both halves
build from the same folder they land on the same date and the header shows one;
if a refresh leaves them out of step the header shows both, and the Definitions
page adds a caveat saying the two will not reconcile.

**Re-running the structural merge.** `tools/merge_client_report.py` is what
combined the two sites in the first place. You only need it again if the client
report gains or loses a page:

```sh
python3 tools/merge_client_report.py \
  --client ../pepco-/index.html --internal <an internal-only index.html> --out index.html
```

It always builds on the client file, because that stylesheet is a superset of the
internal one's and its raw-data tables are sortable and filterable. It also owns
the header and stale-label behaviour, so re-run the two data builds afterwards.

Two things the script cannot do for you:

- **Allocation compliance** has no export in the current set, so the script
  carries the previous values forward and the card is labelled with their own
  older date. Supply a nomination/booked TEU export to make it live.
- **The row cap.** The milestone and delivery exports come out of BI capped at
  150,000 rows with *"some data may have been omitted"* in the footer. The script
  reads that footer and surfaces it on the Data quality page. Raise the export
  limit before anyone quotes these counts as totals.
