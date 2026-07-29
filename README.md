# Topps DB & W — Website

A static, one-page website for Topps DB & W, a pressure washing company. Plain HTML/CSS/JS — no build step required.

## Files

- `index.html` — page content and structure
- `styles.css` — styling (mobile-first, responsive)
- `script.js` — footer year, estimate form submission handling
- `netlify.toml` — Netlify deployment config
- `robots.txt`, `sitemap.xml` — basic SEO

## Local preview

Open `index.html` directly in a browser, or serve it locally:

```bash
python3 -m http.server 8000
```

Then visit `http://localhost:8000`.

## Deploy to Netlify

1. Push this repo/branch to GitHub.
2. In Netlify, click **Add new site → Import an existing project** and select this repo.
3. Build settings: no build command needed; publish directory is `.` (already set in `netlify.toml`).
4. Deploy.

## Notes

- Update `https://toppsdbw.com/` placeholders (canonical URL, Open Graph, sitemap) once a real domain is assigned.
- The Before/After Gallery section currently uses placeholders — replace with real project photos when available.
- The "Get a Free Estimate" buttons link out to the company's Google Form for lead capture (opens in a new tab).
