import re
from datetime import datetime, timedelta
from urllib.parse import quote


def url(da, a, giorno):
    return "https://booking.giuntabus.com/it/from/%s/%s/to/%s/?adulti=1" % (
        quote(da), giorno.strftime("%y%m%d"), quote(a))


def _ora(testo):
    m = re.fullmatch(r"(\d{1,2}):(\d{2})", testo.strip())
    return (int(m.group(1)), int(m.group(2))) if m else None


def interpreta(testo, giorno):
    """Estrae le corse dal testo della pagina. Struttura osservata, una voce per riga:
    HH:MM, citta, fermata, 'Durata', durata, HH:MM, citta, fermata, 'Prezzo totale',
    '€ 13,00', 'Posti disponibili', 'Seleziona', 'Info viaggio'."""
    if giorno.strftime("%d/%m/%Y") not in testo:
        return []
    righe = [r.strip() for r in testo.split("\n") if r.strip()]
    inizi = [i for i in range(len(righe) - 7)
             if _ora(righe[i]) and righe[i + 3].lower() == "durata" and _ora(righe[i + 5])]
    corse = []
    for n, i in enumerate(inizi):
        fine = inizi[n + 1] if n + 1 < len(inizi) else len(righe)
        blocco = righe[i:fine]
        if "seleziona" not in " ".join(blocco).lower():
            continue
        prezzo = None
        for k, r in enumerate(blocco):
            if r.lower().startswith("prezzo totale") and k + 1 < len(blocco):
                m = re.search(r"(\d+)[.,](\d{2})", blocco[k + 1])
                if m:
                    prezzo = float("%s.%s" % (m.group(1), m.group(2)))
                break
        if prezzo is None or prezzo <= 0:
            continue
        h1, m1 = _ora(righe[i])
        h2, m2 = _ora(righe[i + 5])
        dep = datetime(giorno.year, giorno.month, giorno.day, h1, m1)
        arr = datetime(giorno.year, giorno.month, giorno.day, h2, m2)
        if arr <= dep:
            arr += timedelta(days=1)
        corse.append({"dep": dep, "arr": arr,
                      "da": "%s %s" % (righe[i + 1], righe[i + 2]),
                      "a": "%s %s" % (righe[i + 6], righe[i + 7]),
                      "prezzo": prezzo})
    return corse


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
    corse = interpreta(testo, giorno)
    for c in corse:
        c["link"] = url(da, a, giorno)
    print("GIUNTABUS", da, a, giorno, "corse lette:", len(corse), "|", testo[:300].replace("\n", " | "))
    if not corse:
        print("GIUNTABUS TESTO", da, a, giorno, testo[:1500].replace("\n", " | "))
    return {"corse": corse, "ok": bool(testo.strip())}
