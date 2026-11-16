import re
from urllib.parse import quote

def cerca(origine, destinazione, data_iso):
    """
    Cerca su FlixBus usando Playwright.
    data_iso: formato YYYY-MM-DD
    """
    from playwright.sync_api import sync_playwright
    from playwright_stealth import stealth_sync
    
    url = "https://shop.flixbus.it/"
    risultati = []
    
    try:
        with sync_playwright() as p:
            browser = p.chromium.launch(headless=True)
            context = browser.new_context(
                user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
                viewport={"width": 1920, "height": 1080}
            )
            page = context.new_page()
            stealth_sync(page)
            
            page.goto(url, wait_until="networkidle", timeout=45000)
            
            # Accetta cookie se presente
            try:
                page.click("button:has-text('Accetta tutti')", timeout=3000)
            except:
                pass
                
            # Compila campi
            page.fill("input[data-e2e='search-form-origin']", origine)
            page.wait_for_timeout(1000)
            page.keyboard.press("Enter")
            
            page.fill("input[data-e2e='search-form-destination']", destinazione)
            page.wait_for_timeout(1000)
            page.keyboard.press("Enter")
            
            # Inserisci data (formato richiesto dal sito: GG.MM.AAAA)
            giorno, mese, anno = data_iso.split("-")
            data_it = f"{giorno}.{mese}.{anno}"
            try:
                page.fill("input[data-e2e='search-form-date']", data_it)
                page.keyboard.press("Enter")
            except:
                pass
                
            # Clicca Cerca
            try:
                page.click("button[data-e2e='search-form-submit']", timeout=5000)
            except:
                pass
                
            page.wait_for_timeout(8000)
            testo = page.inner_text("body")
            browser.close()
            
            if "Nessun risultato" in testo or "Nessun viaggio" in testo:
                return {"ok": True, "corse": [], "msg": "Nessuna corsa"}
            if "Access Denied" in testo or "Just a moment" in testo:
                return {"ok": False, "errore": "Blocco anti-bot"}
                
            # Estrai orari e prezzi con euristiche
            orari = sorted(set(re.findall(r"\b(?:[01]?\d|2[0-3]):[0-5]\d\b", testo)))
            prezzi = re.findall(r"(\d+,\d{2})\s*€", testo)
            
            if not orari or not prezzi:
                return {"ok": False, "errore": "Impossibile leggere orari/prezzi"}
                
            # Assumiamo che il primo prezzo sia il più economico
            prezzo_min = min([float(p.replace(",", ".")) for p in prezzi]) if prezzi else None
            
            return {"ok": True, "orari": orari, "prezzo": prezzo_min}
            
    except Exception as e:
        print("Errore FlixBus:", repr(e)[:200])
        return {"ok": False, "errore": "Timeout o blocco"}
