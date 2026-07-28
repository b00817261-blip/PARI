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
`claude/website-deployment-7u69nj`. It enables Pages itself on the first run, so
there is nothing to switch on beforehand — the live URL appears in the workflow
summary and under **Settings → Pages**.

Note that GitHub Pages is only free on public repos. If you make this repo
private, Pages needs a paid plan; Vercel serves private repos on the free tier.

## Updating the data

The dataset is the `const D = {...}` literal in the `<script>` block at the
bottom of `index.html`. Replace that object with a freshly exported one, keeping
the same shape, and push. Both hosts redeploy on push, and `index.html` is served
`must-revalidate` so viewers pick up the new numbers without a hard refresh.
