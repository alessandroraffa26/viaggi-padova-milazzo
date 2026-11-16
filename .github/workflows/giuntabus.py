import re
from urllib.parse import quote


def url(da, a, giorno):
    return "https://booking.giuntabus.com/it/from/%s/%s/to/%s/?adulti=1" % (
        quote(da), giorno.strftime("%y%m%d"), quote(a))


def leggi(da, a, giorno):
    from playwright.sync_api import sync_playwright
    testo = ""
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        try:
            page = browser.new_page()
            page.goto(url(da, a, giorno), wait_until="networkidle", timeout=30000)
            page.wait_for_timeout(5000)
            testo = page.inner_text("body")
        finally:
            browser.close()
    print("GIUNTABUS TESTO", da, a, giorno, testo[:1500].replace("\n", " | "))
    importi = set()
    for m in re.finditer(r"€\s*(\d{1,3}[.,]\d{2})|(\d{1,3}[.,]\d{2})\s*€", testo):
        importi.add(float((m.group(1) or m.group(2)).replace(",", ".")))
    orari = sorted(set(re.findall(r"\b(?:[01]?\d|2[0-3]):[0-5]\d\b", testo)))
    prezzo = next(iter(importi)) if len(importi) == 1 else None
    return {"prezzo": prezzo, "importi": sorted(importi), "orari": orari,
            "ok": bool(testo.strip())}
