import json
import os
import re
import sys
import urllib.parse
import urllib.request
from datetime import date, datetime, timedelta
from datetime import time as ora_t
from html.parser import HTMLParser
from zoneinfo import ZoneInfo

# ===== CONTROLLO DATA E MODALITA =====
DATA_INIZIO = "2027-09-15"

EVENTO_GH = os.environ.get("GITHUB_EVENT_NAME", "")
MODALITA = os.environ.get("MODALITA", "").strip().lower()
EVENTO_TEST = os.environ.get("EVENTO_TEST", "").strip()
MANUALE = EVENTO_GH == "workflow_dispatch" and MODALITA in ("simulazione", "reale")
SIMULAZIONE = MANUALE and MODALITA == "simulazione"
REALE_MANUALE = MANUALE and MODALITA == "reale"
DATA_RAGGIUNTA = date.today() >= date.fromisoformat(DATA_INIZIO)

if SIMULAZIONE:
    STATO = {"attivo": "true", "reale": "false", "modalita": "simulazione"}
elif REALE_MANUALE:
    STATO = {"attivo": "true", "reale": "true", "modalita": "reale-manuale"}
elif DATA_RAGGIUNTA:
    STATO = {"attivo": "true", "reale": "true", "modalita": "automatica"}
else:
    STATO = {"attivo": "false", "reale": "false", "modalita": "dormiente"}

def scrivi_output(valori):
    percorso = os.environ.get("GITHUB_OUTPUT")
    if percorso:
        with open(percorso, "a") as f:
            for k, v in valori.items():
                f.write("%s=%s\n" % (k, v))

if STATO["attivo"] == "false":
    print("Dormiente fino al " + DATA_INIZIO + ". Nessuna chiamata.")
    scrivi_output(STATO)
    sys.exit(0)
if "--solo-controllo" in sys.argv:
    print("Modalità: " + STATO["modalita"])
    scrivi_output(STATO)
    sys.exit(0)

TG_TOKEN = os.environ.get("TELEGRAM_TOKEN", "")
TG_CHAT = os.environ.get("TELEGRAM_CHAT_ID", "")
MESSAGGIO = os.environ.get("MESSAGGIO", "").strip()
SERVER = "http://127.0.0.1:3000"
FILE_VINCOLI = "vincoli.json"
ROMA = ZoneInfo("Europe/Rome")

ORIZZONTE_GIORNI = 120
DATE_TRENO = 5
BUFFER_VOLO = 90
BUFFER_TRANSITO = 60

AEROPORTI_NORD = ["VCE", "TSF", "VRN"]
AEROPORTI_SUD = ["CTA", "PMO"]

TARIFFA_GIUNTABUS_CTA = None
ULTIMA_CORSA = {"CTA": "19:00", "PMO": None}

# Tariffe ufficiali fisse richieste
NAVETTE = {
    "VCE": {"prezzo": 13.00, "nome": "Busitalia Padova-VCE"},
    "BLQ": {"prezzo": 12.80, "nome": "Marconi Express"},
    "PMO": {"prezzo": 6.00, "nome": "Prestia e Comandè"},
    "TSF": {"prezzo": None, "nome": "MOM Treviso (prezzo da verificare)"}
}

URL_BUSITALIA = "https://www.fsbusitalia.it/it/turismo/collegamenti-aeroporti/collegamento-aeroporto-di-venezia.html"
BUSITALIA_PREZZO_FALLBACK = 13.00
UA_BROWSER = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0 Safari/537.36"

EVENTI = [
    ("Ognissanti", "2027-10-27", "2027-10-29", "2027-11-01", "2027-11-02"),
    ("Ponte Immacolata", "2027-12-03", "2027-12-07", "2027-12-08", "2027-12-12"),
    ("Natale e Capodanno", "2027-12-17", "2027-12-23", "2028-01-02", "2028-01-09"),
    ("Carnevale", "2028-02-23", "2028-02-26", "2028-03-01", "2028-03-05"),
    ("Pasqua", "2028-04-10", "2028-04-14", "2028-04-17", "2028-04-23"),
    ("25 aprile", "2028-04-20", "2028-04-23", "2028-04-25", "2028-04-26"),
    ("1 maggio", "2028-04-27", "2028-04-29", "2028-05-01", "2028-05-02"),
    ("2 giugno", "2028-05-31", "2028-06-01", "2028-06-04", "2028-06-05"),
    ("Esodo estivo", "2028-07-15", "2028-08-05", "2028-08-26", "2028-09-10"),
]

MESI = {"gennaio": 1, "febbraio": 2, "marzo": 3, "aprile": 4, "maggio": 5, "giugno": 6, "luglio": 7, "agosto": 8, "settembre": 9, "ottobre": 10, "novembre": 11, "dicembre": 12}
GIORNI = ["lun", "mar", "mer", "gio", "ven", "sab", "dom"]

CH_DATA_VOLO = ("departureDate", "departure_date", "departureDateTime", "departure", "outboundDate", "date", "day")
CH_PREZZO_VOLO = ("price", "amount", "totalPrice", "fare", "lowestPrice", "minPrice", "value")
CH_ORE = ("departureTimes", "departure_times", "times", "departures")
CH_DEP = ("departureTime", "departureAt", "departureDateTime", "departure", "departsAt", "departure_time", "depart")
CH_ARR = ("arrivalTime", "arrivalAt", "arrivalDateTime", "arrival", "arrivesAt", "arrival_time", "arrive")

FONTI = {}
NON_LEGGIBILI = {}
ESEMPI = set()
CACHE = {}
BUS = {}

def d(s): return date.fromisoformat(s)
def fmt(x): return "%s %d/%d" % (GIORNI[x.weekday()], x.day, x.month)
def hm(x): return x.strftime("%H:%M")
def mm(m): return "%02d:%02d" % ((m // 60) % 24, m % 60)
def f_ora(x): return hm(x) if x else "n/d"
def f_eur(p): return "n/d" if p is None else "%.2f €" % p
def f_cambi(c):
    if c is None: return "cambi n/d"
    c = int(c)
    return "diretto" if c == 0 else ("1 cambio" if c == 1 else "%d cambi" % c)
def ripulisci(testo):
    return re.sub(r"\bcirca\b\s*", "", testo, flags=re.I).replace("~", "")

def telegram(testo):
    testo = ripulisci(testo)
    blocchi, corrente = [], ""
    for riga in testo.split("\n"):
        if len(corrente) + len(riga) + 1 > 3800:
            blocchi.append(corrente); corrente = ""
        corrente += riga + "\n"
    blocchi.append(corrente)
    for b in blocchi:
        dati = urllib.parse.urlencode({"chat_id": TG_CHAT, "text": b, "disable_web_page_preview": "true"}).encode()
        urllib.request.urlopen("https://api.telegram.org/bot%s/sendMessage" % TG_TOKEN, data=dati, timeout=30)

def get_json(url):
    req = urllib.request.Request(url, headers={"User-Agent": "viaggi-bot/1.0", "Accept": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=90) as r:
            return json.load(r)
    except Exception as e:
        print("Errore GET", url, repr(e)[:200])
        return None

def conta(nome, ok):
    f = FONTI.setdefault(nome, [0, 0]); f[1] += 1
    if ok: f[0] += 1

def esempio(nome, obj):
    if nome not in ESEMPI:
        ESEMPI.add(nome); print("ESEMPIO GREZZO", nome, json.dumps(obj, default=str)[:1200])

def normalizza(x):
    def conv(o):
        if hasattr(o, "model_dump"): return o.model_dump()
        if hasattr(o, "__dict__"): return vars(o)
        return str(o)
    return json.loads(json.dumps(x, default=conv))

def primo_top(x, chiavi):
    if isinstance(x, dict):
        for c in chiavi:
            v = x.get(c)
            if v not in (None, "", [], {}): return v
    return None

def prendi_ric(x, chiavi):
    coda = [x]
    while coda:
        corrente = coda.pop(0)
        if isinstance(corrente, dict):
            for c in chiavi:
                v = corrente.get(c)
                if v not in (None, "", [], {}): return v
            coda.extend(corrente.values())
        elif isinstance(corrente, list): coda.extend(corrente)
    return None

def valore_prezzo(v):
    if v is None or isinstance(v, bool): return None
    if isinstance(v, (int, float)): p = float(v)
    elif isinstance(v, str):
        try: p = float(v.replace(",", "."))
        except ValueError: return None
    elif isinstance(v, dict): return valore_prezzo(primo_top(v, ("amount", "value", "total", "price")))
    else: return None
    return p if 0 < p < 1000 else None

def leggi_ora(v):
    if isinstance(v, dict): v = primo_top(v, ("dateTime", "datetime", "time", "at", "iso", "local"))
    if isinstance(v, str):
        try: v = datetime.fromisoformat(v.replace("Z", "+00:00"))
        except ValueError: return None
    if isinstance(v, datetime):
        if v.tzinfo is not None: v = v.astimezone(ROMA)
        return v.replace(tzinfo=None)
    return None

def scegli(lista, n):
    if len(lista) <= n: return lista
    passo = (len(lista) - 1) / (n - 1)
    return [lista[round(i * passo)] for i in range(n)]

def carica_vincoli():
    try:
        with open(FILE_VINCOLI) as f: v = json.load(f)
    except Exception: v = {}
    v.setdefault("date_bloccate", [])
    return v

def salva_vincoli(v):
    v["date_bloccate"] = sorted(set(v["date_bloccate"]))
    with open(FILE_VINCOLI, "w") as f: json.dump(v, f, indent=1)

def estrai_date(testo, oggi):
    t = testo.lower(); trovate = set()
    for m in re.finditer(r"\d{4}-\d{2}-\d{2}", t):
        try: trovate.add(date.fromisoformat(m.group()))
        except ValueError: pass
    mesi = "|".join(MESI)
    def data_da(giorno, mese, anno):
        try:
            if anno: return date(int(anno), MESI[mese], int(giorno))
            x = date(oggi.year, MESI[mese], int(giorno))
            return x if x >= oggi else date(oggi.year + 1, MESI[mese], int(giorno))
        except ValueError: return None
    for m in re.finditer(r"dal\s+(\d{1,2})\s+al\s+(\d{1,2})\s+(%s)(?:\s+(\d{4}))?" % mesi, t):
        a = data_da(m.group(1), m.group(3), m.group(4)); b = data_da(m.group(2), m.group(3), m.group(4))
        if a and b and a <= b and (b - a).days <= 31:
            for i in range((b - a).days + 1): trovate.add(a + timedelta(days=i))
    for m in re.finditer(r"(\d{1,2})\s+(%s)(?:\s+(\d{4}))?" % mesi, t):
        x = data_da(m.group(1), m.group(2), m.group(3))
        if x: trovate.add(x)
    return trovate

def applica_messaggio(v, testo, oggi):
    date_msg = sorted(estrai_date(testo, oggi))
    if not date_msg: return None
    elenco = ", ".join(fmt(x) for x in date_msg); iso = {x.isoformat() for x in date_msg}
    if "sblocca" in testo.lower():
        v["date_bloccate"] = [x for x in v["date_bloccate"] if x not in iso]
        return "Ho riabilitato %s. Ecco il nuovo calcolo." % elenco
    v["date_bloccate"] = list(set(v["date_bloccate"]) | iso)
    return "Ho escluso %s come richiesto. Ecco le alternative." % elenco

# --- Busitalia (Standard) ---
class LettoreTabelle(HTMLParser):
    def __init__(self):
        super().__init__(); self.tabelle = []; self._tab = None; self._riga = None; self._cella = None
    def handle_starttag(self, tag, attrs):
        if tag == "table": self._tab = []
        elif tag == "tr" and self._tab is not None: self._riga = []
        elif tag in ("td", "th") and self._riga is not None: self._cella = []
    def handle_data(self, data):
        if self._cella is not None: self._cella.append(data)
    def handle_endtag(self, tag):
        if tag in ("td", "th") and self._cella is not None and self._riga is not None:
            self._riga.append(" ".join("".join(self._cella).split())); self._cella = None
        elif tag == "tr" and self._riga is not None and self._tab is not None:
            self._tab.append(self._riga); self._riga = None
        elif tag == "table" and self._tab is not None: self.tabelle.append(self._tab); self._tab = None

def minuti(cella):
    m = re.search(r"(\d{1,2}):(\d{2})", cella or "")
    return int(m.group(1)) * 60 + int(m.group(2)) if m else None

def trova(righe, prefisso):
    for k, v in righe.items():
        if k.startswith(prefisso): return v
    return []

def coppie(dep, arr):
    out = []
    for a, b in zip(dep, arr):
        ta, tb = minuti(a), minuti(b)
        if ta is not None and tb is not None:
            if tb < ta: tb += 1440
            out.append((ta, tb))
    return out

def leggi_busitalia():
    try:
        req = urllib.request.Request(URL_BUSITALIA, headers={"User-Agent": UA_BROWSER})
        html = urllib.request.urlopen(req, timeout=60).read().decode("utf-8", "ignore")
        p = LettoreTabelle(); p.feed(html)
    except Exception as e: print("Errore Busitalia", repr(e)[:200]); return None
    andata, ritorno, prezzo = [], [], None
    for tab in p.tabelle:
        if not tab or not tab[0]: continue
        titolo = tab[0][0].lower(); righe = {r[0].lower(): r[1:] for r in tab if r}
        if titolo.startswith("padova - aeroporto"):
            andata += coppie(trova(righe, "padova stanga"), trova(righe, "aeroporto venezia"))
        elif titolo.startswith("aeroporto marco polo - padova"):
            ritorno += coppie(trova(righe, "aeroporto venezia"), trova(righe, "padova stanga"))
        for r in tab:
            if r and r[0].lower().startswith("da padova per venezia") and len(r) > 1:
                m = re.search(r"(\d+)[,.](\d{2})", r[1])
                if m: prezzo = float("%s.%s" % (m.group(1), m.group(2)))
    if len(andata) < 10 or len(ritorno) < 10: return None
    return {"andata": sorted(andata), "ritorno": sorted(ritorno), "prezzo": prezzo, "fonte": "orari e tariffa letti dal sito fsbusitalia.it"}

def carica_bus():
    letto = leggi_busitalia()
    if letto:
        if letto["prezzo"] is None:
            letto["prezzo"] = BUSITALIA_PREZZO_FALLBACK
            letto["fonte"] = "orari dal sito fsbusitalia.it, tariffa dalla tabella statica"
        return letto
    return {"andata": [(335 + 40 * k, 375 + 40 * k) for k in range(27)], "ritorno": [(390 + 40 * k, 430 + 40 * k) for k in range(27)], "prezzo": BUSITALIA_PREZZO_FALLBACK, "fonte": "tabella statica copiata dal sito ufficiale"}

def servizio_vce_attivo(giorno): return not (giorno.month == 12 and giorno.day == 25)

def testo_bus_vce(v, direz):
    if not servizio_vce_attivo(v["data"]): return "Busitalia Padova-VCE non attivo il 25 dicembre."
    if direz == "ANDATA":
        prima = min(p for p, a in BUS["andata"]); ultima = max(p for p, a in BUS["andata"])
        if not v["ore"]: return "Bus Padova Stanga→VCE: ogni 40 minuti, da %s a %s (orario volo n/d)." % (mm(prima), mm(ultima))
        pezzi = []
        for o in v["ore"][:2]:
            t = o.hour * 60 + o.minute
            utili = [(p, a) for p, a in BUS["andata"] if a <= t - BUFFER_VOLO]
            if utili: p, a = max(utili, key=lambda x: x[1]); pezzi.append("volo %s: bus Stanga %s→%s" % (hm(o), mm(p), mm(a)))
            else: pezzi.append("volo %s: nessun bus utile" % hm(o))
        return "Bus Padova Stanga→VCE: " + "; ".join(pezzi)
    ultimo = max(p for p, a in BUS["ritorno"])
    return "Bus VCE→Padova Stanga: ogni 40 minuti, ultima partenza %s, atterra entro le %s." % (mm(ultimo), mm(ultimo - BUFFER_TRANSITO))

# --- Voli ---
def cerca_tariffe(x, out):
    if isinstance(x, dict):
        dt, ore, ha_ora = None, [], False
        for c in CH_DATA_VOLO:
            v = x.get(c)
            if v in (None, "") or isinstance(v, list): continue
            dt = leggi_ora(v)
            if dt: ha_ora = isinstance(v, str) and ("T" in v or ":" in v); break
        prezzo = valore_prezzo(primo_top(x, CH_PREZZO_VOLO))
        if dt and prezzo is not None:
            if ha_ora: ore.append(dt)
            lista = prendi_ric(x, CH_ORE)
            if isinstance(lista, list):
                for s in lista:
                    o = leggi_ora(s)
                    if o and o.time() != ora_t.min: ore.append(o)
            out.append({"data": dt.date(), "prezzo": prezzo, "ore": sorted(set(ore))}); return
        for v in x.values(): cerca_tariffe(v, out)
    elif isinstance(x, list):
        for v in x: cerca_tariffe(v, out)

def voli(direz, nord, sud, inizio, fine, bloccate):
    o, dst = (nord, sud) if direz == "ANDATA" else (sud, nord)
    sorgenti = [("Ryanair", get_json("https://hermes.brinkhorst.dev/api/v1/routes/%s/%s/cheapest" % (o, dst)))]
    try:
        from flywizz import WizzAir, TimetableSearch
        ric = TimetableSearch(origin=o, destination=dst, date_from=datetime.combine(inizio, ora_t.min), date_to=datetime.combine(fine, ora_t.min))
        jw = normalizza(list(WizzAir().get_timetable(ric)))
    except Exception as e: print("Errore Wizz", o, dst, repr(e)[:200]); jw = None
    sorgenti.append(("Wizz Air", jw))
    out = []
    for nome, js in sorgenti:
        conta(nome, js is not None)
        if js is None: continue
        esempio(nome, js); recs = []; cerca_tariffe(js, recs)
        if not recs and js not in ([], {}): NON_LEGGIBILI.setdefault(nome, json.dumps(js, default=str)[:300])
        for r in recs:
            if inizio <= r["data"] <= fine and r["data"].isoformat() not in bloccate:
                r.update({"comp": nome, "o": o, "dst": dst}); out.append(r)
    return out

# --- Treni, FlixBus, Giuntabus ---
def nome_comp(v):
    if isinstance(v, dict): v = primo_top(v, ("name", "id", "code"))
    return str(v) if v else ""

def ora_viaggio(j, chiavi, ultimo=False):
    for c in chiavi:
        v = j.get(c)
        if v not in (None, ""):
            r = leggi_ora(v)
            if r: return r
    legs = prendi_ric(j, ("legs", "segments"))
    if isinstance(legs, list) and legs:
        leg = legs[-1] if ultimo else legs[0]
        if isinstance(leg, dict):
            for c in chiavi:
                v = leg.get(c); r = leggi_ora(v) if v not in (None, "") else None
                if r: return r
    return None

def leggi_viaggio(j):
    if not isinstance(j, dict): return None
    cambi = primo_top(j, ("changes", "transfers", "numberOfChanges", "numChanges"))
    if not isinstance(cambi, (int, float)) or isinstance(cambi, bool):
        legs = prendi_ric(j, ("legs", "segments"))
        cambi = len(legs) - 1 if isinstance(legs, list) and legs else None
    return {"dep": ora_viaggio(j, CH_DEP), "arr": ora_viaggio(j, CH_ARR, ultimo=True), "prezzo": valore_prezzo(primo_top(j, ("price", "totalPrice", "total", "fare", "amount"))), "cambi": cambi, "link": prendi_ric(j, ("bookingUrl", "booking_url", "url")), "comp": nome_comp(prendi_ric(j, ("provider", "operator", "carrier", "company")))}

def cerca_viaggi(tipo, orig, dest, giorno):
    chiave = (tipo, orig, dest, giorno)
    if chiave in CACHE: return CACHE[chiave]
    if tipo == "treno": percorso, extra, nome = "train-journeys", "", "Treni (Trenitalia/Italo)"
    elif tipo == "flixbus":
        # Usa il nuovo modulo flixbus.py
        try:
            import flixbus
            ris = flixbus.cerca(orig, dest, giorno.isoformat())
            if not ris["ok"]:
                conta("FlixBus", False); CACHE[chiave] = []; return []
            conta("FlixBus", True)
            out = []
            if ris.get("orari"):
                # Crea un finto oggetto viaggio per compatibilità
                o = leggi_ora(ris["orari"][0])
                a = leggi_ora(ris["orari"][-1]) # Assumiamo ultimo orario come arrivo
                out.append({"dep": o, "arr": a, "prezzo": ris["prezzo"], "cambi": 0, "link": "https://shop.flixbus.it/", "comp": "FlixBus"})
            CACHE[chiave] = out; return out
        except Exception as e:
            print("Errore FlixBus", repr(e)[:200]); CACHE[chiave] = []; return []
    else:
        percorso, extra, nome = "journeys", "&provider=flixbus", "FlixBus" # Legacy
    
    url = "%s/%s?origin=%s&destination=%s&date=%s&adults=1&children=0%s" % (SERVER, percorso, urllib.parse.quote(orig), urllib.parse.quote(dest), giorno.isoformat(), extra)
    js = get_json(url)
    conta(nome, js is not None)
    out = []
    if js is not None:
        lista = prendi_ric(js, ("journeys",)) or []
        if lista: esempio(nome, lista[0])
        for j in lista:
            v = leggi_viaggio(j)
            if v and v["dep"]: out.append(v)
    CACHE[chiave] = out; return out

def prezzo_padova_treviso(giorno, direz):
    if SIMULAZIONE: return None
    o, dst = ("Padova", "Treviso") if direz == "ANDATA" else ("Treviso", "Padova")
    prezzi = [t["prezzo"] for t in cerca_viaggi("treno", o, dst, giorno) if t["prezzo"] is not None]
    return min(prezzi) if prezzi else None

def somma(a, b): return None if a is None or b is None else a + b

def prezzo_giuntabus(giorno, direz):
    if SIMULAZIONE: return 5.55
    if TARIFFA_GIUNTABUS_CTA is not None: return TARIFFA_GIUNTABUS_CTA
    da, a = ("Catania Aeroporto", "Milazzo") if direz == "ANDATA" else ("Milazzo", "Catania Aeroporto")
    try:
        import giuntabus
        r = giuntabus.leggi(da, a, giorno)
        return r["prezzo"]
    except Exception: return None

def costi_volo(v, direz):
    andata = direz == "ANDATA"
    nord = v["o"] if andata else v["dst"]
    sud = v["dst"] if andata else v["o"]
    parti = [("Volo", v["prezzo"])]
    note = []
    
    # Nord (Navette)
    if nord in NAVETTE:
        parti.append((NAVETTE[nord]["nome"], NAVETTE[nord]["prezzo"]))
        if nord == "VCE": note.append(testo_bus_vce(v, direz))
        elif nord == "TSF": note.append(NAVETTE[nord]["nome"])
    else:
        parti.append(("Padova→%s" % nord if andata else "%s→Padova" % nord, None))
        
    # Sud (Giuntabus/Navette)
    if sud == "CTA":
        parti.append(("Bus CTA→Milazzo" if andata else "Bus Milazzo→CTA", prezzo_giuntabus(v["data"], direz)))
    elif sud in NAVETTE:
        parti.append((NAVETTE[sud]["nome"], NAVETTE[sud]["prezzo"]))
    else:
        parti.append(("%s→Milazzo" % sud if andata else "Milazzo→%s" % sud, None))
    return parti, note

def verifica_fattibilita(v, direz):
    andata = direz == "ANDATA"
    nord = v["o"] if andata else v["dst"]
    sud = v["dst"] if andata else v["o"]
    
    # 1. Controllo se si arriva in tempo all'aeroporto di partenza (Nord per andata, Sud per ritorno)
    if andata and nord == "VCE":
        if v["ore"]:
            t_volo = v["ore"][0].hour * 60 + v["ore"][0].minute
            utili = [a for p, a in BUS["andata"] if a <= t_volo - BUFFER_VOLO]
            if not utili: return False
    elif andata and nord == "TSF":
        # Assumiamo che il treno da Padova impieghi 1h e il bus 10 min. Se non c'è margine, scartiamo.
        if v["ore"]:
            t_volo = v["ore"][0].hour * 60 + v["ore"][0].minute
            if t_volo < 8 * 60: return False # Primo treno utile troppo tardi

    # 2. Controllo se si riesce a tornare a casa dall'aeroporto di arrivo
    if andata and sud == "CTA":
        if v["ore"]:
            # Assumiamo durata volo 2h per VCE->CTA
            t_arrivo = v["ore"][0].hour * 60 + v["ore"][0].minute + 120
            if t_arrivo > 1140 - BUFFER_TRANSITO: # Ultimo bus 19:00
                return False
    return True

# --- Simulazione ---
def simula():
    oggi = date.today(); ga = oggi + timedelta(days=30); gr = oggi + timedelta(days=37)
    BUS.update({"andata": [(335, 375)], "ritorno": [(390, 430)], "prezzo": 7.77, "fonte": "SIM"})
    def h(g, o, m): return datetime.combine(g, ora_t(o, m))
    voli_a = [{"data": ga, "o": "VCE", "dst": "CTA", "comp": "A", "prezzo": 11.11, "ore": [h(ga, 6, 15)]}]
    treni_a = [{"dep": h(ga, 7, 56), "arr": h(ga, 20, 19), "prezzo": 44.44, "cambi": 1, "link": "#", "comp": "Treno"}]
    bus_a = [{"parti": [("bus", {"dep": h(ga, 5, 40), "arr": h(ga, 14, 50), "prezzo": 33.33})], "totale": 35.55, "dep": h(ga, 5, 40)}]
    voli_r = [{"data": gr, "o": "CTA", "dst": "VCE", "comp": "A", "prezzo": 12.12, "ore": [h(gr, 20, 10)]}]
    treni_r = [{"dep": h(gr, 6, 30), "arr": h(gr, 19, 0), "prezzo": 55.55, "cambi": 1, "link": "#", "comp": "Treno"}]
    bus_r = [{"parti": [("bus", {"dep": h(gr, 7, 30), "arr": h(gr, 23, 30), "prezzo": 44.44})], "totale": 46.66, "dep": h(gr, 7, 30)}]
    righe = ["REPORT VIAGGI - SIMULAZIONE", ""]
    righe += combina_e_report(voli_a, treni_a, bus_a, voli_r, treni_r, bus_r, oggi)
    telegram("\n".join(righe))

# --- Combinazione A/R ---
def combina_e_report(voli_a, treni_a, bus_a, voli_r, treni_r, bus_r, oggi):
    opzioni_a = []; opzioni_r = []
    
    for v in voli_a:
        if not verifica_fattibilita(v, "ANDATA"): continue
        parti, _ = costi_volo(v, "ANDATA")
        if any(p is None for _, p in parti): continue
        costo = sum(p for _, p in parti)
        opzioni_a.append({"costo": costo, "desc": "Volo %s %s->%s" % (v['comp'], v['o'], v['dst']), "dett": parti})
        
    for t in treni_a:
        if t['prezzo'] is not None: opzioni_a.append({"costo": t['prezzo'], "desc": "Treno %s" % t['comp'], "dett": [("Treno", t['prezzo'])]})
        
    for b in bus_a:
        if b['totale'] is not None: opzioni_a.append({"costo": b['totale'], "desc": "Bus+Treno", "dett": [("Bus+Treno", b['totale'])]})
        
    for v in voli_r:
        if not verifica_fattibilita(v, "RITORNO"): continue
        parti, _ = costi_volo(v, "RITORNO")
        if any(p is None for _, p in parti): continue
        costo = sum(p for _, p in parti)
        opzioni_r.append({"costo": costo, "desc": "Volo %s %s->%s" % (v['comp'], v['o'], v['dst']), "dett": parti})
        
    for t in treni_r:
        if t['prezzo'] is not None: opzioni_r.append({"costo": t['prezzo'], "desc": "Treno %s" % t['comp'], "dett": [("Treno", t['prezzo'])]})
        
    for b in bus_r:
        if b['totale'] is not None: opzioni_r.append({"costo": b['totale'], "desc": "Bus+Treno", "dett": [("Bus+Treno", b['totale'])]})
        
    opzioni_a.sort(key=lambda x: x['costo']); opzioni_r.sort(key=lambda x: x['costo'])
    top_a = opzioni_a[:3]; top_r = opzioni_r[:3]
    
    combinazioni = []
    for a in top_a:
        for r in top_r:
            combinazioni.append({"totale": a['costo'] + r['costo'], "andata": a, "ritorno": r})
    combinazioni.sort(key=lambda x: x['totale'])
    
    righe = ["MIGLIORI COMBINAZIONI A/R:", ""]
    for i, c in enumerate(combinazioni[:3], 1):
        righe.append("SOLUZIONE %d - Totale: %.2f €" % (i, c['totale']))
        righe.append("  ANDATA: %s (%.2f €)" % (c['andata']['desc'], c['andata']['costo']))
        for nome, p in c['andata']['dett']: righe.append("    - %s: %s" % (nome, f_eur(p)))
        righe.append("  RITORNO: %s (%.2f €)" % (c['ritorno']['desc'], c['ritorno']['costo']))
        for nome, p in c['ritorno']['dett']: righe.append("    - %s: %s" % (nome, f_eur(p)))
        righe.append("")
    return righe

# --- Main ---
def main():
    if SIMULAZIONE: simula(); return
    if not TG_TOKEN or not TG_CHAT: print("Mancano credenziali"); sys.exit(1)
    
    oggi = date.today(); vincoli = carica_vincoli(); nota = None
    if MESSAGGIO: nota = applica_messaggio(vincoli, MESSAGGIO, oggi); salva_vincoli(vincoli)
    bloccate = set(vincoli["date_bloccate"])
    
    evento = None
    if REALE_MANUALE and EVENTO_TEST:
        date_t = re.findall(r"\d{4}-\d{2}-\d{2}", EVENTO_TEST)
        evento = ("TEST MANUALE", date_t[0], date_t[1], date_t[2], date_t[3])
    else:
        for e in EVENTI:
            if d(e[4]) > oggi and d(e[1]) <= oggi + timedelta(days=ORIZZONTE_GIORNI): evento = e; break
    if not evento: telegram("Nessuna festivita."); return

    BUS.update(carica_bus())
    nome, a_da, a_a, r_da, r_a = evento
    domani = oggi + timedelta(days=1)
    
    voli_a, treni_a, bus_a = [], [], []
    voli_r, treni_r, bus_r = [], [], []
    
    # ANDATA
    ini_a, fin_a = max(d(a_da), domani), d(a_a)
    if ini_a <= fin_a:
        giorni_a = [ini_a + timedelta(days=i) for i in range((fin_a - ini_a).days + 1) if (ini_a + timedelta(days=i)).isoformat() not in bloccate]
        for nord in AEROPORTI_NORD:
            for sud in AEROPORTI_SUD:
                voli_a += voli("ANDATA", nord, sud, ini_a, fin_a, bloccate)
        for g in scegli(giorni_a, DATE_TRENO):
            treni_a += cerca_viaggi("treno", "Padova", "Milazzo", g)
            for b in cerca_viaggi("flixbus", "Padova", "Messina", g):
                if b["arr"]:
                    tr = [t for t in cerca_viaggi("treno", "Messina", "Milazzo", b["arr"].date()) if t["dep"] >= b["arr"] + timedelta(minutes=BUFFER_TRANSITO)]
                    if tr: t = min(tr, key=lambda x: x["dep"]); bus_a.append({"parti": [("bus", b), ("treno", t)], "totale": somma(b["prezzo"], t["prezzo"]), "dep": b["dep"]})

    # RITORNO
    ini_r, fin_r = max(d(r_da), domani), d(r_a)
    if ini_r <= fin_r:
        giorni_r = [ini_r + timedelta(days=i) for i in range((fin_r - ini_r).days + 1) if (ini_r + timedelta(days=i)).isoformat() not in bloccate]
        for nord in AEROPORTI_NORD:
            for sud in AEROPORTI_SUD:
                voli_r += voli("RITORNO", nord, sud, ini_r, fin_r, bloccate)
        for g in scegli(giorni_r, DATE_TRENO):
            treni_r += cerca_viaggi("treno", "Milazzo", "Padova", g)
            for b in cerca_viaggi("flixbus", "Messina", "Padova", g):
                tr = [t for t in cerca_viaggi("treno", "Milazzo", "Messina", g) if t["arr"] and t["arr"] <= b["dep"] - timedelta(minutes=BUFFER_TRANSITO)]
                if tr: t = max(tr, key=lambda x: x["arr"]); bus_r.append({"parti": [("treno", t), ("bus", b)], "totale": somma(b["prezzo"], t["prezzo"]), "dep": t["dep"]})

    righe = ["REPORT VIAGGI - " + nome, "Aggiornato: " + fmt(oggi)]
    if nota: righe.append(nota)
    righe.append("")
    righe += combina_e_report(voli_a, treni_a, bus_a, voli_r, treni_r, bus_r, oggi)
    righe.append("Fonti: " + ", ".join("%s %d/%d" % (n, f[0], f[1]) for n, f in FONTI.items()))
    telegram("\n".join(righe))

main()
