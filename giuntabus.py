import re
from urllib.parse import quote

def url(da, a, giorno):
    return "https://booking.giuntabus.com/it/from/%s/%s/to/%s/?adulti=1" % (
        quote(da), giorno.strftime("%y%m%d"), quote(a))

def leggi(da, a, giorno):
    from playwright.sync_api import sync_playwright
    testo = ""
    try:
        with sync_playwright() as p:
            browser = p.chromium.launch(headless=True)
            page = browser.new_page()
            page.goto(url(da, a, giorno), wait_until="networkidle", timeout=30000)
            page.wait_for_timeout(5000)
            testo = page.inner_text("body")
            browser.close()
    except Exception as e:
        print("Errore Giuntabus:", repr(e)[:200])
        return {"prezzo": None, "orari": [], "ok": False}
        
    print("GIUNTABUS TESTO", da, a, giorno, testo[:500].replace("\n", " | "))
    
    # Estrai prezzi
    importi = set()
    for m in re.finditer(r"€\s*(\d{1,3}[.,]\d{2})", testo):
        importi.add(float(m.group(1).replace(",", ".")))
    prezzo = min(importi) if importi else None
    
    # Estrai orari (cerca pattern HH:MM seguiti da nomi di città)
    orari = []
    # Euristica: cerca tutti gli orari e filtra
    tutti_orari = re.findall(r"\b(\d{2}:\d{2})\b", testo)
    # Filtra per escludere ore notturne non valide (es. 00:00) e durate
    orari = sorted(list(set([o for o in tutti_orari if o not in ("00:00", "02:00")])))
    
    return {"prezzo": prezzo, "orari": orari, "ok": bool(testo.strip())}
