"""Assemble le guide (parts/*.html) et l'imprime en PDF avec Microsoft Edge (Playwright).

Usage : python docs/guide/build_guide.py   ->  docs/MovieShazam_Guide_Zero_to_Hero.pdf
"""

from __future__ import annotations

from pathlib import Path

from playwright.sync_api import sync_playwright

HERE = Path(__file__).resolve().parent
OUT_HTML = HERE / "guide.html"
OUT_PDF = HERE.parent / "MovieShazam_Guide_Zero_to_Hero.pdf"

FOOTER = """<div style="font-size:8px;width:100%;padding:0 16mm;color:#5b6676;font-family:Segoe UI,Arial;
display:flex;justify-content:space-between"><span>MovieShazam — Zero to Hero guide</span>
<span><span class="pageNumber"></span> / <span class="totalPages"></span></span></div>"""


def main() -> None:
    parts = sorted((HERE / "parts").glob("*.html"))
    OUT_HTML.write_text("\n".join(p.read_text(encoding="utf-8") for p in parts), encoding="utf-8")
    with sync_playwright() as p:
        try:
            browser = p.chromium.launch(channel="msedge")
        except Exception:  # noqa: BLE001
            browser = p.chromium.launch()
        page = browser.new_page()
        page.goto(OUT_HTML.as_uri(), wait_until="networkidle")
        page.pdf(path=str(OUT_PDF), format="A4", print_background=True, prefer_css_page_size=True,
                 display_header_footer=True, header_template="<div></div>", footer_template=FOOTER,
                 margin={"top": "18mm", "bottom": "20mm", "left": "16mm", "right": "16mm"})
        browser.close()
    print(f"PDF : {OUT_PDF} ({OUT_PDF.stat().st_size / 1024:.0f} Ko)")


if __name__ == "__main__":
    main()
