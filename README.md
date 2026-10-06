# Ayakaleaf Pro Docs

Source for the Ayakaleaf Pro documentation site, built with [Mintlify](https://mintlify.com).

The site has one top tab per GitBook section:

| Tab | Path | Content |
| --- | --- | --- |
| Home | `/` | Landing page |
| On-Premises | `/on-premises` | Installation, configuration, and maintenance |
| Blog | `/blog` | Release notes and articles |
| TeXLive | `/texlive` | TeX Live images and LaTeX skills |
| Developer | `/dev` | Development environment and data model |

## Migration from GitBook

During the transition period, the content source of truth is the GitBook Git Sync repository [ayaka-notes/ayakaleaf-docs](https://github.com/ayaka-notes/ayakaleaf-docs). Only the English content is migrated.

`scripts/gitbook-to-mintlify/convert.py` regenerates the Mintlify pages from that repository:

```bash
git clone https://github.com/ayaka-notes/ayakaleaf-docs ../ayakaleaf-docs
pip install pyyaml
python3 scripts/gitbook-to-mintlify/convert.py --src ../ayakaleaf-docs --strict
python3 scripts/gitbook-to-mintlify/check_leftovers.py
npx mint validate
npx mint broken-links
```

The script overwrites these generated files on every run, so don't edit them by hand while GitBook is still the source:

- `index.mdx` and the `on-premises/`, `blog/`, `texlive/`, `latex/`, and `dev/` folders
- `images/<section>/` (GitBook assets, including files mirrored from the GitBook CDN)
- `navigation` and `redirects` in `docs.json`

`HIDDEN_GROUPS` in the script lists sidebar groups to leave out, such as On-Premises **Getting started**. Their pages are still generated, so links to them keep working.

All other `docs.json` settings, such as theme, colors, logo, and navbar, are kept.

Sections listed in `UNPUBLISHED` in the script are indexed but not generated. The LaTeX knowledge base (`latex/en`, about 500 pages) is unpublished for now. Links that point into it are rendered as plain text. To publish it, remove `"latex"` from `UNPUBLISHED` and run the script again.

Videos larger than 15 MB are re-encoded to 1280px H.264 because Mintlify does not deploy large static files. This needs `ffmpeg` on `PATH` or in `$FFMPEG`. `derived-assets.json` records the source of each re-encoded video and each file downloaded from the GitBook CDN. While the source is unchanged, the committed file is kept as is, so runs on other machines or in CI don't rewrite it with different bytes. Brand-only Font Awesome icons, such as `claude`, are resolved against Mintlify's icon CDN and cached in `icon-types.json`.

The script converts:

- `SUMMARY.md` into tabs and groups. Entries with children become groups with a `root` page.
- `{% hint %}` into `<Info>`, `<Check>`, `<Warning>`, and `<Danger>`.
- `{% stepper %}` into `<Steps>`, `{% tabs %}` into `<Tabs>`, and `{% columns %}` into `<Columns>`.
- `{% content-ref %}` and `{% file %}` into `<Card>`, and `{% embed %}` into a YouTube iframe, `<video>`, or `<Card>`.
- `{% code %}` options (title, wrap, expandable) into code block meta.
- GitBook HTML into Mintlify components or JSX: card tables, `<figure>`, `<details>`, buttons, `<i class="fa-*">` icons, and `<pre>` code blocks.
- Page `icon`, `description`, and sidebar titles from the frontmatter.
- Internal links, including `app.gitbook.com/s/<space>` links and `"mention"` links.

`--strict` exits with a non-zero code when there are warnings, so CI can catch content that needs attention.

## Development

Install the [Mintlify CLI](https://www.npmjs.com/package/mint) and run it from the repository root:

```bash
npm i -g mint
mint dev
```

View your local preview at `http://localhost:3000`.

## Publishing changes

The Mintlify GitHub app deploys the default branch to production automatically after each push.
