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
DATA_INIZIO = "2027-09-15"   # data ufficiale di avvio: i test manuali non la modificano

EVENTO_GH = os.environ.get("GITHUB_EVENT_NAME", "")
MODALITA = os.environ.get("MODALITA", "").strip().lower()
EVENTO_TEST = os.environ.get("EVENTO_TEST", "").strip()
# Solo l'avvio manuale (workflow_dispatch) con modalita valida salta il blocco data.
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

if SIMULAZIONE:
    # Blocco di sicurezza: in simulazione e permessa solo la rete verso Telegram.
    _urlopen_originale = urllib.request.urlopen

    def _solo_telegram(richiesta, *args, **kwargs):
        url = richiesta.full_url if hasattr(richiesta, "full_url") else str(richiesta)
        if "api.telegram.org" not in url:
            raise RuntimeError("Simulazione: chiamata bloccata a " + url)
        return _urlopen_originale(richiesta, *args, **kwargs)

    urllib.request.urlopen = _solo_telegram

TG_TOKEN = os.environ.get("TELEGRAM_TOKEN", "")
TG_CHAT = os.environ.get("TELEGRAM_CHAT_ID", "")
MESSAGGIO = os.environ.get("MESSAGGIO", "").strip()
SERVER = "http://127.0.0.1:3000"
FILE_VINCOLI = "vincoli.json"
ROMA = ZoneInfo("Europe/Rome")

ORIZZONTE_GIORNI = 120   # quanto in anticipo guardare le festivita
DATE_TRENO = 5           # date campionate per finestra per treni e bus
BUFFER_VOLO = 90         # minuti prima del volo
BUFFER_TRANSITO = 60     # minuti tra arrivo e coincidenza successiva

AEROPORTI_NORD = ["VCE", "TSF", "VRN"]
NOTA_VRN = "VRN: treno + navetta, 1h50 in più rispetto a VCE/TSF (dato tuo); prezzi di terra n/d"
AEROPORTI_SUD = ["CTA", "PMO"]

# Prezzo Giuntabus aeroporto Catania <-> Milazzo in euro. Se lo imposti, vince sulla ricerca.
# Lascia None per lasciarlo leggere dalla pagina (altrimenti n/d).
TARIFFA_GIUNTABUS_CTA = None
# Ultima partenza bus aeroporto -> Milazzo, "HH:MM". Valori impostati da te, non verificati.
ULTIMA_CORSA = {"CTA": "19:00", "PMO": None}

URL_BUSITALIA = ("https://www.fsbusitalia.it/it/turismo/collegamenti-aeroporti/"
                 "collegamento-aeroporto-di-venezia.html")
BUSITALIA_PREZZO_FALLBACK = 13.00
UA_BROWSER = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
              "(KHTML, like Gecko) Chrome/124.0 Safari/537.36")

# (nome, andata_da, andata_a, ritorno_da, ritorno_a)  - in ordine di data
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

MESI = {"gennaio": 1, "febbraio": 2, "marzo": 3, "aprile": 4, "maggio": 5,
        "giugno": 6, "luglio": 7, "agosto": 8, "settembre": 9, "ottobre": 10,
        "novembre": 11, "dicembre": 12}
GIORNI = ["lun", "mar", "mer", "gio", "ven", "sab", "dom"]

CH_DATA_VOLO = ("departureDate", "departure_date", "departureDateTime", "departure",
                "outboundDate", "date", "day")
CH_PREZZO_VOLO = ("price", "amount", "totalPrice", "fare", "lowestPrice", "minPrice", "value")
CH_ORE = ("departureTimes", "departure_times", "times", "departures")
CH_DEP = ("departureTime", "departureAt", "departureDateTime", "departure",
          "departsAt", "departure_time", "depart")
CH_ARR = ("arrivalTime", "arrivalAt", "arrivalDateTime", "arrival",
          "arrivesAt", "arrival_time", "arrive")

FONTI = {}
NON_LEGGIBILI = {}
ESEMPI = set()
CACHE = {}
BUS = {}


# ---------- utilita ----------
def d(s):
    return date.fromisoformat(s)


def fmt(x):
    return "%s %d/%d" % (GIORNI[x.weekday()], x.day, x.month)


def hm(x):
    return x.strftime("%H:%M")


def mm(m):
    return "%02d:%02d" % ((m // 60) % 24, m % 60)


def f_ora(x):
    return hm(x) if x else "n/d"


def f_eur(p):
    return "n/d" if p is None else "%.2f €" % p


def f_cambi(c):
    if c is None:
        return "cambi n/d"
    c = int(c)
    if c == 0:
        return "diretto"
    return "1 cambio" if c == 1 else "%d cambi" % c


def ripulisci(testo):
    testo = re.sub(r"\bcirca\b\s*", "", testo, flags=re.I)
    return testo.replace("~", "")


def telegram(testo):
    testo = ripulisci(testo)
    blocchi, corrente = [], ""
    for riga in testo.split("\n"):
        if len(corrente) + len(riga) + 1 > 3800:
            blocchi.append(corrente)
            corrente = ""
        corrente += riga + "\n"
    blocchi.append(corrente)
    for b in blocchi:
        dati = urllib.parse.urlencode({
            "chat_id": TG_CHAT, "text": b, "disable_web_page_preview": "true",
        }).encode()
        urllib.request.urlopen(
            "https://api.telegram.org/bot%s/sendMessage" % TG_TOKEN,
            data=dati, timeout=30)


def get_json(url):
    req = urllib.request.Request(url, headers={
        "User-Agent": "viaggi-bot/1.0", "Accept": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=90) as r:
            return json.load(r)
    except Exception as e:
        print("Errore GET", url, repr(e)[:200])
        return None


def conta(nome, ok):
    f = FONTI.setdefault(nome, [0, 0])
    f[1] += 1
    if ok:
        f[0] += 1


def esempio(nome, obj):
    if nome not in ESEMPI:
        ESEMPI.add(nome)
        print("ESEMPIO GREZZO", nome, json.dumps(obj, default=str)[:1200])


def normalizza(x):
    def conv(o):
        if hasattr(o, "model_dump"):
            return o.model_dump()
        if hasattr(o, "__dict__"):
            return vars(o)
        return str(o)
    return json.loads(json.dumps(x, default=conv))


def primo_top(x, chiavi):
    if isinstance(x, dict):
        for c in chiavi:
            v = x.get(c)
            if v not in (None, "", [], {}):
                return v
    return None


def prendi_ric(x, chiavi):
    coda = [x]
    while coda:
        corrente = coda.pop(0)
        if isinstance(corrente, dict):
            for c in chiavi:
                v = corrente.get(c)
                if v not in (None, "", [], {}):
                    return v
            coda.extend(corrente.values())
        elif isinstance(corrente, list):
            coda.extend(corrente)
    return None


def valore_prezzo(v):
    if v is None or isinstance(v, bool):
        return None
    if isinstance(v, (int, float)):
        p = float(v)
    elif isinstance(v, str):
        try:
            p = float(v.replace(",", "."))
        except ValueError:
            return None
    elif isinstance(v, dict):
        return valore_prezzo(primo_top(v, ("amount", "value", "total", "price")))
    else:
        return None
    return p if 0 < p < 1000 else None


def leggi_ora(v):
    if isinstance(v, dict):
        v = primo_top(v, ("dateTime", "datetime", "time", "at", "iso", "local"))
    if isinstance(v, str):
        try:
            v = datetime.fromisoformat(v.replace("Z", "+00:00"))
        except ValueError:
            return None
    if isinstance(v, datetime):
        if v.tzinfo is not None:
            v = v.astimezone(ROMA)
        return v.replace(tzinfo=None)
    return None


def scegli(lista, n):
    if len(lista) <= n:
        return lista
    passo = (len(lista) - 1) / (n - 1)
    return [lista[round(i * passo)] for i in range(n)]


# ---------- vincoli ----------
def carica_vincoli():
    try:
        with open(FILE_VINCOLI) as f:
            v = json.load(f)
    except Exception:
        v = {}
    v.setdefault("date_bloccate", [])
    return v


def salva_vincoli(v):
    v["date_bloccate"] = sorted(set(v["date_bloccate"]))
    with open(FILE_VINCOLI, "w") as f:
        json.dump(v, f, indent=1)


def estrai_date(testo, oggi):
    t = testo.lower()
    trovate = set()
    for m in re.finditer(r"\d{4}-\d{2}-\d{2}", t):
        try:
            trovate.add(date.fromisoformat(m.group()))
        except ValueError:
            pass
    mesi = "|".join(MESI)

    def data_da(giorno, mese, anno):
        try:
            if anno:
                return date(int(anno), MESI[mese], int(giorno))
            x = date(oggi.year, MESI[mese], int(giorno))
            if x < oggi:
                x = date(oggi.year + 1, MESI[mese], int(giorno))
            return x
        except ValueError:
            return None

    for m in re.finditer(r"dal\s+(\d{1,2})\s+al\s+(\d{1,2})\s+(%s)(?:\s+(\d{4}))?" % mesi, t):
        a = data_da(m.group(1), m.group(3), m.group(4))
        b = data_da(m.group(2), m.group(3), m.group(4))
        if a and b and a <= b and (b - a).days <= 31:
            for i in range((b - a).days + 1):
                trovate.add(a + timedelta(days=i))
    for m in re.finditer(r"(\d{1,2})\s+(%s)(?:\s+(\d{4}))?" % mesi, t):
        x = data_da(m.group(1), m.group(2), m.group(3))
        if x:
            trovate.add(x)
    return trovate


def applica_messaggio(v, testo, oggi):
    date_msg = sorted(estrai_date(testo, oggi))
    if not date_msg:
        return None
    elenco = ", ".join(fmt(x) for x in date_msg)
    iso = {x.isoformat() for x in date_msg}
    if "sblocca" in testo.lower():
        v["date_bloccate"] = [x for x in v["date_bloccate"] if x not in iso]
        return "Ho riabilitato %s. Ecco il nuovo calcolo." % elenco
    v["date_bloccate"] = list(set(v["date_bloccate"]) | iso)
    return "Ho escluso %s come richiesto. Ecco le alternative." % elenco


# ---------- Busitalia Padova Stanga <-> VCE ----------
class LettoreTabelle(HTMLParser):
    def __init__(self):
        super().__init__()
        self.tabelle = []
        self._tab = None
        self._riga = None
        self._cella = None

    def handle_starttag(self, tag, attrs):
        if tag == "table":
            self._tab = []
        elif tag == "tr" and self._tab is not None:
            self._riga = []
        elif tag in ("td", "th") and self._riga is not None:
            self._cella = []

    def handle_data(self, data):
        if self._cella is not None:
            self._cella.append(data)

    def handle_endtag(self, tag):
        if tag in ("td", "th") and self._cella is not None and self._riga is not None:
            self._riga.append(" ".join("".join(self._cella).split()))
            self._cella = None
        elif tag == "tr" and self._riga is not None and self._tab is not None:
            self._tab.append(self._riga)
            self._riga = None
        elif tag == "table" and self._tab is not None:
            self.tabelle.append(self._tab)
            self._tab = None


def minuti(cella):
    m = re.search(r"(\d{1,2}):(\d{2})", cella or "")
    return int(m.group(1)) * 60 + int(m.group(2)) if m else None


def trova(righe, prefisso):
    for k, v in righe.items():
        if k.startswith(prefisso):
            return v
    return []


def coppie(dep, arr):
    out = []
    for a, b in zip(dep, arr):
        ta, tb = minuti(a), minuti(b)
        if ta is not None and tb is not None:
            if tb < ta:
                tb += 1440
            out.append((ta, tb))
    return out


def leggi_busitalia():
    try:
        req = urllib.request.Request(URL_BUSITALIA, headers={"User-Agent": UA_BROWSER})
        html = urllib.request.urlopen(req, timeout=60).read().decode("utf-8", "ignore")
        p = LettoreTabelle()
        p.feed(html)
    except Exception as e:
        print("Errore Busitalia", repr(e)[:200])
        return None
    andata, ritorno, prezzo = [], [], None
    for tab in p.tabelle:
        if not tab or not tab[0]:
            continue
        titolo = tab[0][0].lower()
        righe = {r[0].lower(): r[1:] for r in tab if r}
        if titolo.startswith("padova - aeroporto"):
            andata += coppie(trova(righe, "padova stanga"), trova(righe, "aeroporto venezia"))
        elif titolo.startswith("aeroporto marco polo - padova"):
            ritorno += coppie(trova(righe, "aeroporto venezia"), trova(righe, "padova stanga"))
        for r in tab:
            if r and r[0].lower().startswith("da padova per venezia") and len(r) > 1:
                m = re.search(r"(\d+)[,.](\d{2})", r[1])
                if m:
                    prezzo = float("%s.%s" % (m.group(1), m.group(2)))
    if len(andata) < 10 or len(ritorno) < 10:
        return None
    return {"andata": sorted(andata), "ritorno": sorted(ritorno), "prezzo": prezzo,
            "fonte": "orari e tariffa letti dal sito fsbusitalia.it"}


def carica_bus():
    letto = leggi_busitalia()
    if letto:
        if letto["prezzo"] is None:
            letto["prezzo"] = BUSITALIA_PREZZO_FALLBACK
            letto["fonte"] = "orari dal sito fsbusitalia.it, tariffa dalla tabella statica"
        return letto
    # Tabella statica copiata dalla pagina ufficiale (verificata il 15/11/2026):
    # Stanga 05:35-22:55 ogni 40 minuti, arrivo VCE dopo 40 minuti;
    # VCE 06:30-23:50 ogni 40 minuti, arrivo Stanga dopo 40 minuti.
    return {"andata": [(335 + 40 * k, 375 + 40 * k) for k in range(27)],
            "ritorno": [(390 + 40 * k, 430 + 40 * k) for k in range(27)],
            "prezzo": BUSITALIA_PREZZO_FALLBACK,
            "fonte": "tabella statica copiata dal sito ufficiale (verificata il 15/11/2026)"}


def servizio_vce_attivo(giorno):
    return not (giorno.month == 12 and giorno.day == 25)


def testo_bus_vce(v, direz):
    if not servizio_vce_attivo(v["data"]):
        return "Busitalia Padova-VCE non attivo il 25 dicembre."
    if direz == "ANDATA":
        prima = min(p for p, a in BUS["andata"])
        ultima = max(p for p, a in BUS["andata"])
        if not v["ore"]:
            return "Bus Padova Stanga→VCE: ogni 40 minuti, da %s a %s (orario volo n/d)." % (
                mm(prima), mm(ultima))
        pezzi = []
        for o in v["ore"][:2]:
            t = o.hour * 60 + o.minute
            utili = [(p, a) for p, a in BUS["andata"] if a <= t - BUFFER_VOLO]
            if utili:
                p, a = max(utili, key=lambda x: x[1])
                pezzi.append("volo %s: bus Stanga %s→%s" % (hm(o), mm(p), mm(a)))
            else:
                pezzi.append("volo %s: nessun bus utile" % hm(o))
        return "Bus Padova Stanga→VCE: " + "; ".join(pezzi)
    ultimo = max(p for p, a in BUS["ritorno"])
    return "Bus VCE→Padova Stanga: ogni 40 minuti, ultima partenza %s, atterra entro le %s." % (
        mm(ultimo), mm(ultimo - BUFFER_TRANSITO))


# ---------- voli ----------
def cerca_tariffe(x, out):
    if isinstance(x, dict):
        dt, ore, ha_ora = None, [], False
        for c in CH_DATA_VOLO:
            v = x.get(c)
            if v in (None, "") or isinstance(v, list):
                continue
            dt = leggi_ora(v)
            if dt:
                ha_ora = isinstance(v, str) and ("T" in v or ":" in v)
                break
        prezzo = valore_prezzo(primo_top(x, CH_PREZZO_VOLO))
        if dt and prezzo is not None:
            if ha_ora:
                ore.append(dt)
            lista = prendi_ric(x, CH_ORE)
            if isinstance(lista, list):
                for s in lista:
                    o = leggi_ora(s)
                    if o and o.time() != ora_t.min:
                        ore.append(o)
            out.append({"data": dt.date(), "prezzo": prezzo, "ore": sorted(set(ore))})
            return
        for v in x.values():
            cerca_tariffe(v, out)
    elif isinstance(x, list):
        for v in x:
            cerca_tariffe(v, out)


def voli(direz, nord, sud, inizio, fine, bloccate):
    o, dst = (nord, sud) if direz == "ANDATA" else (sud, nord)
    sorgenti = []
    sorgenti.append(("Ryanair", get_json(
        "https://hermes.brinkhorst.dev/api/v1/routes/%s/%s/cheapest" % (o, dst))))
    try:
        from flywizz import WizzAir, TimetableSearch
        ric = TimetableSearch(origin=o, destination=dst,
                              date_from=datetime.combine(inizio, ora_t.min),
                              date_to=datetime.combine(fine, ora_t.min))
        jw = normalizza(list(WizzAir().get_timetable(ric)))
    except Exception as e:
        print("Errore Wizz", o, dst, repr(e)[:200])
        jw = None
    sorgenti.append(("Wizz Air", jw))

    out = []
    for nome, js in sorgenti:
        conta(nome, js is not None)
        if js is None:
            continue
        esempio(nome, js)
        recs = []
        cerca_tariffe(js, recs)
        if not recs and js not in ([], {}):
            NON_LEGGIBILI.setdefault(nome, json.dumps(js, default=str)[:300])
        for r in recs:
            if inizio <= r["data"] <= fine and r["data"].isoformat() not in bloccate:
                r.update({"comp": nome, "o": o, "dst": dst})
                out.append(r)
    return out


# ---------- treni e bus ----------
def nome_comp(v):
    if isinstance(v, dict):
        v = primo_top(v, ("name", "id", "code"))
    return str(v) if v else ""


def ora_viaggio(j, chiavi, ultimo=False):
    for c in chiavi:
        v = j.get(c)
        if v not in (None, ""):
            r = leggi_ora(v)
            if r:
                return r
    legs = prendi_ric(j, ("legs", "segments"))
    if isinstance(legs, list) and legs:
        leg = legs[-1] if ultimo else legs[0]
        if isinstance(leg, dict):
            for c in chiavi:
                v = leg.get(c)
                r = leggi_ora(v) if v not in (None, "") else None
                if r:
                    return r
    return None


def leggi_viaggio(j):
    if not isinstance(j, dict):
        return None
    cambi = primo_top(j, ("changes", "transfers", "numberOfChanges", "numChanges"))
    if not isinstance(cambi, (int, float)) or isinstance(cambi, bool):
        legs = prendi_ric(j, ("legs", "segments"))
        cambi = len(legs) - 1 if isinstance(legs, list) and legs else None
    return {
        "dep": ora_viaggio(j, CH_DEP),
        "arr": ora_viaggio(j, CH_ARR, ultimo=True),
        "prezzo": valore_prezzo(primo_top(j, ("price", "totalPrice", "total", "fare", "amount"))),
        "cambi": cambi,
        "link": prendi_ric(j, ("bookingUrl", "booking_url", "url")),
        "comp": nome_comp(prendi_ric(j, ("provider", "operator", "carrier", "company"))),
    }


def cerca_viaggi(tipo, orig, dest, giorno):
    chiave = (tipo, orig, dest, giorno)
    if chiave in CACHE:
        return CACHE[chiave]
    if tipo == "treno":
        percorso, extra, nome = "train-journeys", "", "Treni (Trenitalia/Italo)"
    else:
        percorso, extra, nome = "journeys", "&provider=flixbus", "FlixBus"
    url = "%s/%s?origin=%s&destination=%s&date=%s&adults=1&children=0%s" % (
        SERVER, percorso, urllib.parse.quote(orig), urllib.parse.quote(dest),
        giorno.isoformat(), extra)
    js = get_json(url)
    conta(nome, js is not None)
    out = []
    if js is not None:
        lista = prendi_ric(js, ("journeys",)) or []
        if lista:
            esempio(nome, lista[0])
        for j in lista:
            v = leggi_viaggio(j)
            if v and v["dep"]:
                out.append(v)
        if lista and not out:
            NON_LEGGIBILI.setdefault(nome, json.dumps(lista[0], default=str)[:300])
    CACHE[chiave] = out
    return out


def prezzo_padova_treviso(giorno, direz):
    if SIMULAZIONE:
        return None
    o, dst = ("Padova", "Treviso") if direz == "ANDATA" else ("Treviso", "Padova")
    prezzi = [t["prezzo"] for t in cerca_viaggi("treno", o, dst, giorno)
              if t["prezzo"] is not None]
    return min(prezzi) if prezzi else None


def somma(a, b):
    return None if a is None or b is None else a + b


def bus_andata(giorni):
    ris = []
    for g in giorni:
        for b in cerca_viaggi("bus", "Padova", "Messina", g):
            if not b["arr"]:
                continue
            trenini = cerca_viaggi("treno", "Messina", "Milazzo", b["arr"].date())
            utili = [t for t in trenini
                     if t["dep"] >= b["arr"] + timedelta(minutes=BUFFER_TRANSITO)]
            if not utili:
                continue  # arrivo troppo tardi: pernottamento, scartata
            t = min(utili, key=lambda x: x["dep"])
            ris.append({"parti": [("bus", b), ("treno", t)],
                        "totale": somma(b["prezzo"], t["prezzo"]), "dep": b["dep"]})
    return ris


def bus_ritorno(giorni):
    ris = []
    for g in giorni:
        for b in cerca_viaggi("bus", "Messina", "Padova", g):
            trenini = cerca_viaggi("treno", "Milazzo", "Messina", g)
            utili = [t for t in trenini if t["arr"] and
                     t["arr"] <= b["dep"] - timedelta(minutes=BUFFER_TRANSITO)]
            if not utili:
                continue  # bus troppo presto: servirebbe partire il giorno prima
            t = max(utili, key=lambda x: x["arr"])
            ris.append({"parti": [("treno", t), ("bus", b)],
                        "totale": somma(b["prezzo"], t["prezzo"]), "dep": t["dep"]})
    return ris


# ---------- report ----------
def chiave_prezzo(p):
    return (p is None, p if p is not None else 0)


def limite_atterraggio(apt):
    u = ULTIMA_CORSA.get(apt)
    if not u:
        return "Ultimo bus %s-Milazzo non impostato (ULTIMA_CORSA): vincolo non verificato." % apt
    h, m = map(int, u.split(":"))
    lim = datetime.combine(date.today(), ora_t(h, m)) - timedelta(minutes=BUFFER_TRANSITO)
    return ("Atterra entro le %s: ultimo bus %s-Milazzo %s "
            "(valore impostato da te, non verificato)." % (hm(lim), apt, u))


GIUNTA = {}


def prezzo_giuntabus(giorno, direz):
    if SIMULAZIONE:
        return 5.55
    if TARIFFA_GIUNTABUS_CTA is not None:
        return TARIFFA_GIUNTABUS_CTA
    da, a = ("Catania Aeroporto", "Milazzo") if direz == "ANDATA" else ("Milazzo", "Catania Aeroporto")
    chiave = (da, giorno)
    if chiave not in GIUNTA:
        try:
            import giuntabus
            GIUNTA[chiave] = giuntabus.leggi(da, a, giorno)
        except Exception as e:
            print("Errore Giuntabus", repr(e)[:200])
            GIUNTA[chiave] = {"prezzo": None, "importi": [], "orari": [], "ok": False}
    return GIUNTA[chiave]["prezzo"]


def costi_volo(v, direz):
    andata = direz == "ANDATA"
    nord = v["o"] if andata else v["dst"]
    sud = v["dst"] if andata else v["o"]
    parti = [("Volo", v["prezzo"])]
    note = []
    if nord == "VCE":
        et = "Busitalia Padova→VCE" if andata else "Busitalia VCE→Padova"
        parti.append((et, BUS["prezzo"] if servizio_vce_attivo(v["data"]) else None))
        note.append(testo_bus_vce(v, direz))
    elif nord == "TSF":
        p = prezzo_padova_treviso(v["data"], direz)
        parti.append(("Padova→Treviso (treno)" if andata else "Treviso→Padova (treno)", p))
        parti.append(("Treviso→TSF" if andata else "TSF→Treviso", None))
        if p is None:
            note.append("Bus da Padova Stanga, prezzo da verificare")
    else:
        parti.append(("Padova→%s" % nord if andata else "%s→Padova" % nord, None))
        note.append(NOTA_VRN)
    if sud == "CTA":
        parti.append(("Bus CTA→Milazzo" if andata else "Bus Milazzo→CTA", prezzo_giuntabus(v["data"], direz)))
    else:
        parti.append(("%s→Milazzo" % sud if andata else "Milazzo→%s" % sud, None))
    return parti, note


def sezione(direz, titolo, voli_l, treni_l, bus_l):
    righe = [direz + ": " + titolo]
    if not (voli_l or treni_l or bus_l):
        righe += ["Nessuna soluzione senza pernottamento trovata per queste date.", ""]
        return righe
    migliori = []
    if voli_l:
        righe.append("VOLI (totale = somma dei prezzi reali, n/d se manca un pezzo)")
        top = sorted(voli_l, key=lambda v: v["prezzo"])[:3]
        for i, v in enumerate(top, 1):
            ore = ", ".join(hm(x) for x in v["ore"]) if v["ore"] else "orario n/d"
            righe.append("%d) %s %s→%s %s | partenze: %s" % (
                i, fmt(v["data"]), v["o"], v["dst"], v["comp"], ore))
            parti, note = costi_volo(v, direz)
            tot = None if any(p is None for _, p in parti) else sum(p for _, p in parti)
            righe.append("   " + " + ".join("%s %s" % (et, f_eur(p)) for et, p in parti)
                         + " = Totale " + f_eur(tot))
            for n in note:
                righe.append("   " + n)
            if tot is not None:
                migliori.append((tot, "volo " + fmt(v["data"])))
        if direz == "ANDATA":
            for apt in sorted(set(v["dst"] for v in top)):
                righe.append(limite_atterraggio(apt))
    if treni_l:
        righe.append("TRENO (Trenitalia/Italo)")
        top = sorted(treni_l, key=lambda t: chiave_prezzo(t["prezzo"]))[:3]
        for i, t in enumerate(top, 1):
            righe.append("%d) %s %s→%s, %s, %s %s %s" % (
                i, fmt(t["dep"]), hm(t["dep"]), f_ora(t["arr"]), f_cambi(t["cambi"]),
                f_eur(t["prezzo"]), t["comp"], t["link"] or ""))
            if t["prezzo"] is not None:
                migliori.append((t["prezzo"], "treno " + fmt(t["dep"])))
    if bus_l:
        righe.append("BUS FlixBus + treno regionale Messina-Milazzo (prezzi reali)")
        top = sorted(bus_l, key=lambda x: chiave_prezzo(x["totale"]))[:3]
        for i, x in enumerate(top, 1):
            testo = " + ".join("%s %s→%s %s" % (
                nome, hm(p["dep"]), f_ora(p["arr"]), f_eur(p["prezzo"])) for nome, p in x["parti"])
            link = x["parti"][0][1]["link"] if x["parti"][0][0] == "bus" else x["parti"][1][1]["link"]
            righe.append("%d) %s %s = totale %s %s" % (
                i, fmt(x["dep"]), testo, f_eur(x["totale"]), link or ""))
            if x["totale"] is not None:
                migliori.append((x["totale"], "FlixBus " + fmt(x["dep"])))
    if migliori:
        p, che = min(migliori)
        righe.append("Migliore totale porta-a-porta confermato: %.2f € (%s)" % (p, che))
    else:
        righe.append("Totale porta-a-porta: n/d (manca almeno un prezzo reale per ogni opzione).")
    righe.append("")
    return righe


# ---------- simulazione (dati inventati, nessuna chiamata) ----------
def bus_simulato():
    return {"andata": [(335 + 40 * k, 375 + 40 * k) for k in range(27)],
            "ritorno": [(390 + 40 * k, 430 + 40 * k) for k in range(27)],
            "prezzo": 7.77, "fonte": "SIMULAZIONE, dati fittizi"}


def simula():
    oggi = date.today()
    ga = oggi + timedelta(days=30)
    gr = oggi + timedelta(days=37)
    BUS.update(bus_simulato())

    def h(g, ore, minuti):
        return datetime.combine(g, ora_t(ore, minuti))

    def volo(g, o, dst, comp, prezzo, ore):
        return {"data": g, "o": o, "dst": dst, "comp": comp, "prezzo": prezzo, "ore": ore}

    def treno(dep, arr, prezzo, cambi):
        return {"dep": dep, "arr": arr, "prezzo": prezzo, "cambi": cambi,
                "link": "https://esempio.invalid/treno-fittizio", "comp": "Treno Fittizio"}

    def bus(dep, arr, prezzo):
        return {"dep": dep, "arr": arr, "prezzo": prezzo,
                "link": "https://esempio.invalid/bus-fittizio", "comp": "Bus Fittizio"}

    voli_a = [volo(ga, "VCE", "CTA", "Compagnia Fittizia A", 11.11, [h(ga, 6, 15), h(ga, 18, 40)]),
              volo(ga, "TSF", "CTA", "Compagnia Fittizia B", 22.22, []),
              volo(ga, "VRN", "PMO", "Compagnia Fittizia A", 33.33, [h(ga, 10, 30)])]
    treni_a = [treno(h(ga, 7, 56), h(ga, 20, 19), 44.44, 1),
               treno(h(ga, 13, 10), h(ga, 23, 50), None, 2)]
    bus_a = [{"parti": [("bus", bus(h(ga, 5, 40), h(ga, 14, 50), 33.33)),
                        ("treno", treno(h(ga, 16, 5), h(ga, 16, 30), 2.22, 0))],
              "totale": 35.55, "dep": h(ga, 5, 40)}]
    voli_r = [volo(gr, "CTA", "VCE", "Compagnia Fittizia A", 12.12, [h(gr, 20, 10)]),
              volo(gr, "CTA", "TSF", "Compagnia Fittizia B", 23.23, []),
              volo(gr, "PMO", "VRN", "Compagnia Fittizia A", 34.34, [])]
    treni_r = [treno(h(gr, 6, 30), h(gr, 19, 0), 55.55, 1)]
    bus_r = [{"parti": [("treno", treno(h(gr, 6, 0), h(gr, 6, 25), 2.22, 0)),
                        ("bus", bus(h(gr, 7, 30), h(gr, 23, 30), 44.44))],
              "totale": 46.66, "dep": h(gr, 6, 0)}]

    corpo = ["REPORT VIAGGI - SIMULAZIONE", "Aggiornato: " + fmt(oggi),
             "Ho escluso %s come richiesto. Ecco le alternative. (esempio di messaggio)" % fmt(
                 ga + timedelta(days=2)),
             ""]
    corpo += sezione("ANDATA", "Padova → Milazzo", voli_a, treni_a, bus_a)
    corpo += sezione("RITORNO", "Milazzo → Padova", voli_r, treni_r, bus_r)
    corpo += ["Fonti: nessuna, dati inventati.",
              "Busitalia Padova-VCE: SIMULAZIONE, dati fittizi.",
              "Giuntabus CTA-Milazzo: SIMULAZIONE, dati fittizi."]

    righe = ["TEST – DATI NON REALI",
             "Simulazione: nessuna chiamata a API o siti di trasporto.", ""]
    righe += ["[FITTIZIO] " + r if r.strip() else r for r in corpo]
    righe += ["TEST – DATI NON REALI: date, compagnie, orari, importi e link sono inventati.",
              "Serve solo a verificare la formattazione del report."]
    testo = "\n".join(righe)
    print(testo)
    if TG_TOKEN and TG_CHAT:
        telegram(testo)
    else:
        print("Telegram non configurato: report solo nel log.")


# ---------- esecuzione reale ----------
def main():
    if SIMULAZIONE:
        simula()
        return
    if not TG_TOKEN or not TG_CHAT:
        print("Mancano TELEGRAM_TOKEN o TELEGRAM_CHAT_ID")
        sys.exit(1)
    oggi = date.today()

    vincoli = carica_vincoli()
    nota = None
    if MESSAGGIO:
        nota = applica_messaggio(vincoli, MESSAGGIO, oggi)
        salva_vincoli(vincoli)
    bloccate = set(vincoli["date_bloccate"])

    evento = None
    if REALE_MANUALE and EVENTO_TEST:
        date_t = re.findall(r"\d{4}-\d{2}-\d{2}", EVENTO_TEST)
        try:
            if len(date_t) != 4:
                raise ValueError("servono 4 date")
            for x in date_t:
                d(x)
        except ValueError:
            telegram("Campo evento_test non valido: servono 4 date AAAA-MM-GG separate da virgola "
                     "(andata da, andata a, ritorno da, ritorno a).")
            return
        evento = ("TEST MANUALE", date_t[0], date_t[1], date_t[2], date_t[3])
    else:
        for e in EVENTI:
            if d(e[4]) > oggi and d(e[1]) <= oggi + timedelta(days=ORIZZONTE_GIORNI):
                evento = e
                break
    if not evento:
        if REALE_MANUALE:
            telegram("Esecuzione manuale reale: nessuna festivita nei prossimi %d giorni. "
                     "Compila il campo evento_test con 4 date AAAA-MM-GG." % ORIZZONTE_GIORNI)
        else:
            telegram("Nessuna festivita nei prossimi %d giorni nella lista EVENTI: "
                     "aggiorna lo script." % ORIZZONTE_GIORNI)
        return

    BUS.update(carica_bus())

    nome, a_da, a_a, r_da, r_a = evento
    domani = oggi + timedelta(days=1)
    finestre = (("ANDATA", "Padova → Milazzo", max(d(a_da), domani), d(a_a)),
                ("RITORNO", "Milazzo → Padova", max(d(r_da), domani), d(r_a)))

    righe = ["REPORT VIAGGI - " + nome, "Aggiornato: " + fmt(oggi)]
    if REALE_MANUALE:
        righe.append("Esecuzione manuale in modalità reale: solo dati ottenuti dalle fonti, n/d dove mancano.")
    if nota:
        righe.append(nota)
    if bloccate:
        righe.append("Date escluse: " + ", ".join(fmt(d(x)) for x in sorted(bloccate)))
    righe.append("")

    for direz, titolo, ini, fin in finestre:
        voli_l, treni_l, bus_l = [], [], []
        if ini <= fin:
            giorni = [ini + timedelta(days=i) for i in range((fin - ini).days + 1)]
            giorni = [g for g in giorni if g.isoformat() not in bloccate]
            for nord in AEROPORTI_NORD:
                for sud in AEROPORTI_SUD:
                    voli_l += voli(direz, nord, sud, ini, fin, bloccate)
            scelti = scegli(giorni, DATE_TRENO)
            for g in scelti:
                o, dst = ("Padova", "Milazzo") if direz == "ANDATA" else ("Milazzo", "Padova")
                for t in cerca_viaggi("treno", o, dst, g):
                    treni_l.append(t)
            bus_l = bus_andata(scelti) if direz == "ANDATA" else bus_ritorno(scelti)
        righe += sezione(direz, titolo, voli_l, treni_l, bus_l)

    righe.append("Fonti (ricerche riuscite): " + ", ".join(
        "%s %d/%d" % (n, f[0], f[1]) for n, f in FONTI.items()))
    righe.append("Busitalia Padova-VCE: %s, tariffa corsa semplice %s." % (
        BUS["fonte"], f_eur(BUS["prezzo"])))
    if TARIFFA_GIUNTABUS_CTA is not None:
        righe.append("Giuntabus CTA-Milazzo: tariffa impostata da te, %s." % f_eur(TARIFFA_GIUNTABUS_CTA))
    elif GIUNTA:
        for (da, g), r in sorted(GIUNTA.items(), key=lambda x: (x[0][0], x[0][1])):
            if not r["ok"]:
                righe.append("Giuntabus %s %s: pagina vuota o non raggiungibile, prezzo n/d." % (da, fmt(g)))
            else:
                righe.append("Giuntabus %s %s: importi nella pagina: %s; orari nella pagina: %s (partenze e arrivi non distinti)." % (
                    da, fmt(g),
                    ", ".join("%.2f" % x for x in r["importi"]) or "nessuno",
                    ", ".join(r["orari"][:12]) or "nessuno"))
    else:
        righe.append("Giuntabus CTA-Milazzo: nessuna ricerca eseguita.")
    for n, s in NON_LEGGIBILI.items():
        righe.append("Dati non leggibili da %s, esempio: %s" % (n, s))
    righe.append("Itabus escluso (inaffidabile). Italo incluso solo se restituisce corse.")
    telegram("\n".join(righe))


main()
