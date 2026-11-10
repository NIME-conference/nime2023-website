# NIME 2023 website archive

Static, self-contained copy of <https://www.nime2023.org> (originally a Google Sites site), the
website of the 2023 International Conference on New Interfaces for Musical Expression, Mexico City.

- Browse it: <https://nime-conference.github.io/nime2023-website/>
- The ready-to-serve site is in [`site/`](site/); it is published to GitHub Pages on every push
  to `main` and can be copied as-is to `nime.org/web_archive/2023/`.

## Proceedings papers

The paper PDFs are not kept in this repository. Each paper on the Papers page links to its entry
in the NIME proceedings (`https://nime.org/proc/<key>/`), where the PDF and DOI are available.
[`papers.json`](papers.json) maps each PDF of the original site to its
[NIME-bibliography](https://github.com/NIME-conference/NIME-bibliography) key. One paper listed
on the original site was not presented or published in the proceedings and is marked as such.

## Rebuilding

    python3 build.py            # uses ./cache, writes ./site
    python3 build.py --refresh  # re-fetch the pages (images are re-downloaded too)
    cd site && python3 -m http.server 8765   # preview at http://127.0.0.1:8765/

- `build.py` — fetches the 24 pages, strips Google's scripts, stores CSS/fonts/images locally,
  downloads linked Drive files (`site/files/`), converts embedded Google Sheets to static HTML
  plus `.xlsx` (`site/embeds/sheets/`), and rewrites links. Standard library only.
- `overlay/archive.css`, `overlay/archive.js` — small additions replacing Google's JS
  (menus, collapsible sections, mobile drawer, click-to-enlarge images).
- `build-report.json` — what was downloaded and what could not be recovered.
- `cache/` (not committed) — downloaded originals.
- Page file names match the previous archive so existing URLs keep working.

Known gaps: the 15 online-concert videos embedded from Google Drive were already deleted at the
source (placeholder notes are shown); the YouTube video on the LATAM page and the Flickr album
remain external; Google Forms links are left as they were.
