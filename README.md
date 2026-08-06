# MOOV Ops — Internal control

A single-page operations dashboard covering pipeline health, origin/supplier
accountability, carrier performance, destination delivery and data quality.

Everything is baked into `index.html`: markup, styles and the full dataset as an
inline `const D = {...}` object. There is no build step, no backend and no
runtime data fetching. The only external request is the Google Fonts stylesheet.

## ⚠️ Before you share the link

This page carries internal operational data — named suppliers with their overdue
milestone counts, carrier scorecards, order IDs and container numbers — and its
own banner reads *"not for client distribution."* Any host below serves it to
anyone who has the URL, with no login.

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
bottom of `index.html`. `tools/build_dataset.py` rebuilds that object from the
smartMOOV BI exports, so a refresh is a re-run rather than hand-editing JSON:

```sh
pip install pandas openpyxl
python3 tools/build_dataset.py --src ~/exports --asof 2026-08-06 --out D.json
```

Drop all nine exports in one folder — the script finds each by a fragment of its
filename, so the random prefix the BI tool adds does not matter:

| Export | Feeds |
| --- | --- |
| `PO_milestone_performance` | stage strip, open-overdue queue, supplier league, CRD reasons |
| `latest_delivery_data__pepco` | delivery punctuality, stale tracking records |
| `demurrage__detention_tracker` | DEM+DET risk, customs backlog |
| `booking_rejection_analysis` | booking rejections |
| `pepco_carrier_scoring` | carrier league |
| `carrier_performance` | ETD slip column |
| `OHA_KPI` + `pepco_weekly_volume` | KPI bars (rates weighted by shipments) |
| `shipping_document_verification` | doc-verification card |

Then paste the contents of `D.json` over the `const D = {...}` literal and update
the `data as of` stamp in the header. Both hosts redeploy on push, and
`index.html` is served `must-revalidate` so viewers pick up the new numbers
without a hard refresh.

Two things the script cannot do for you:

- **Allocation compliance** has no export in the current set, so the script
  carries the previous values forward and the card is labelled with their own
  older date. Supply a nomination/booked TEU export to make it live.
- **The row cap.** The milestone and delivery exports come out of BI capped at
  150,000 rows with *"some data may have been omitted"* in the footer. The script
  reads that footer and surfaces it on the Data quality page. Raise the export
  limit before anyone quotes these counts as totals.
