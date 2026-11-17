import json
import os
import re
import sys
import urllib.error
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

# Nome con cui Trenitalia riconosce la stazione (con "Messina" da solo da errore).
STAZIONE_MESSINA = "Messina Centrale"

# FlixBus: se True scarta le corse senza nome/numero ufficiale della linea.
# Oggi la fonte non lo fornisce: con True nessuna corsa FlixBus compare nel report.
RICHIEDI_LINEA_BUS = True

AEROPORTI_NORD = ["VCE", "TSF", "VRN"]
AEROPORTI_SUD = ["CTA", "PMO"]

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
CH_NUM_VOLO = ("flightNumber", "flight_number", "flightNo", "flight_no")
CH_ARR_VOLO = ("arrivalDate", "arrival_date", "arrivalDateTime", "arrivalTime", "arrival")
CH_DEP = ("departureTime", "departureAt", "departureDateTime", "departure",
          "departsAt", "departure_time", "depart")
CH_ARR = ("arrivalTime", "arrivalAt", "arrivalDateTime", "arrival",
          "arrivesAt", "arrival_time", "arrive")

ETICHETTE = {"NO_JOURNEYS": "NESSUN RISULTATO", "UNKNOWN_LOCATION": "LUOGO NON RICONOSCIUTO"}

FONTI = {}
NON_LEGGIBILI = {}
ESEMPI = set()
CACHE = {}
BUS = {}
ESITI = {}
ERRORI_FONTI = {}
CONTI = {}
MOTIVI = {}
ULT_ERR = [""]


# ---------- utilita ----------
def d(s):
    return date.fromisoformat(s)


def adesso():
    return datetime.now(ROMA)


def fmt(x):
    return "%s %d/%d" % (GIORNI[x.weekday()], x.day, x.month)


def hm(x):
    return x.strftime("%H:%M")


def dt_fmt(x):
    return "%s %s" % (fmt(x), hm(x))


def f_durata(td):
    m = int(td.total_seconds() // 60)
    return "%dh%02d" % (m // 60, m % 60)


def mm(m):
    return "%02d:%02d" % ((m // 60) % 24, m % 60)


def f_eur(p):
    return "n/d" if p is None else "%.2f €" % p


def f_link(link):
    return "[link alla ricerca] " + str(link) if link else ""


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
    except urllib.error.HTTPError as e:
        ULT_ERR[0] = "HTTP %d" % e.code
        print("Errore GET", url, ULT_ERR[0])
        return None
    except Exception as e:
        ULT_ERR[0] = repr(e)[:80]
        print("Errore GET", url, ULT_ERR[0])
        return None


def conta(nome, ok):
    f = FONTI.setdefault(nome, [0, 0])
    f[1] += 1
    if ok:
        f[0] += 1


def esito(fonte, etichetta):
    ESITI[(fonte, etichetta)] = ESITI.get((fonte, etichetta), 0) + 1


def registra_errore(fonte, codice, messaggio, tratta):
    e = ERRORI_FONTI.setdefault((fonte, codice), {"n": 0, "msg": (messaggio or "")[:120], "tratte": []})
    e["n"] += 1
    if tratta not in e["tratte"] and len(e["tratte"]) < 3:
        e["tratte"].append(tratta)


def trovata(cat):
    CONTI.setdefault(cat, [0, 0])[0] += 1


def scarta_piu(cat, motivi):
    CONTI.setdefault(cat, [0, 0])[1] += 1
    for m in motivi:
        MOTIVI[(cat, m)] = MOTIVI.get((cat, m), 0) + 1


def scarta(cat, motivo):
    scarta_piu(cat, [motivo])


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


# ---------- Busitalia Padova Stanga <-> VCE (pronto per i voli, oggi non usato) ----------
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
    return {"andata": [(335 + 40 * k, 375 + 40 * k) for k in range(27)],
            "ritorno": [(390 + 40 * k, 430 + 40 * k) for k in range(27)],
            "prezzo": BUSITALIA_PREZZO_FALLBACK,
            "fonte": "tabella statica copiata dal sito ufficiale (verificata il 15/11/2026)"}


def servizio_vce_attivo(giorno):
    return not (giorno.month == 12 and giorno.day == 25)


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
            num = prendi_ric(x, CH_NUM_VOLO)
            arr = leggi_ora(prendi_ric(x, CH_ARR_VOLO))
            if arr is not None and arr.time() == ora_t.min:
                arr = None
            out.append({"data": dt.date(), "prezzo": prezzo, "ore": sorted(set(ore)),
                        "numero": str(num) if num is not None else "", "arr": arr})
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


def motivi_volo(v, direz):
    """Elenca perche un volo non puo ancora far parte di una soluzione completa."""
    motivi = []
    if not v.get("numero"):
        motivi.append("numero volo assente nel JSON")
    if not v.get("ore"):
        motivi.append("orario di partenza completo assente")
    if not v.get("arr"):
        motivi.append("orario di arrivo assente")
    nord = v["o"] if direz == "ANDATA" else v["dst"]
    sud = v["dst"] if direz == "ANDATA" else v["o"]
    if nord != "VCE":
        motivi.append("nessuna fonte verificata per Padova-%s" % nord)
    motivi.append("nessuna fonte con orari e prezzo verificati per %s-Milazzo" % sud)
    return motivi


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


def leggi_legs(j):
    legs = prendi_ric(j, ("legs", "segments"))
    out = []
    if isinstance(legs, list):
        for tratta in legs:
            if not isinstance(tratta, dict):
                continue
            num = primo_top(tratta, ("trainNumber", "train_number", "number", "tripNumber", "lineNumber"))
            out.append({"num": str(num) if num is not None else "",
                        "da": nome_comp(tratta.get("origin")),
                        "a": nome_comp(tratta.get("destination")),
                        "dep": ora_viaggio(tratta, CH_DEP),
                        "arr": ora_viaggio(tratta, CH_ARR, ultimo=True)})
    return out


def leggi_viaggio(j):
    if not isinstance(j, dict):
        return None
    cambi = primo_top(j, ("changes", "transfers", "numberOfChanges", "numChanges"))
    legs = leggi_legs(j)
    if not isinstance(cambi, (int, float)) or isinstance(cambi, bool):
        cambi = len(legs) - 1 if legs else None
    linea = primo_top(j, ("lineName", "line_name", "line", "lineNumber", "routeName", "route_name"))
    if isinstance(linea, dict):
        linea = primo_top(linea, ("name", "number", "id", "code"))
    return {
        "dep": ora_viaggio(j, CH_DEP),
        "arr": ora_viaggio(j, CH_ARR, ultimo=True),
        "da": nome_comp(j.get("origin")),
        "a": nome_comp(j.get("destination")),
        "prezzo": valore_prezzo(primo_top(j, ("price", "totalPrice", "total", "fare", "amount"))),
        "cambi": cambi,
        "legs": legs,
        "linea": str(linea) if linea else "",
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
    tratta = "%s→%s %s" % (orig, dest, fmt(giorno))
    url = "%s/%s?origin=%s&destination=%s&date=%s&adults=1&children=0%s" % (
        SERVER, percorso, urllib.parse.quote(orig), urllib.parse.quote(dest),
        giorno.isoformat(), extra)
    js = get_json(url)
    out = []
    if js is None:
        conta(nome, False)
        esito(nome, "ERRORE FONTE")
        registra_errore(nome, ULT_ERR[0] or "ERRORE", "nessuna risposta utilizzabile dal server locale", tratta)
    else:
        lista = prendi_ric(js, ("journeys",)) or []
        errori = js.get("errors") if isinstance(js, dict) else None
        errori = [e for e in errori if isinstance(e, dict)] if isinstance(errori, list) else []
        gravi = []
        for e in errori:
            codice = e.get("code") or "ERRORE"
            fonte_e = e.get("provider") or nome
            esito(fonte_e, ETICHETTE.get(codice, "ERRORE FONTE"))
            if codice != "NO_JOURNEYS":
                gravi.append(e)
                registra_errore(fonte_e, codice, e.get("message"), tratta)
        if lista:
            esito(nome, "VERIFICATO")
            esempio(nome, lista[0])
        conta(nome, bool(lista) or not gravi)
        for j in lista:
            v = leggi_viaggio(j)
            if v and v["dep"]:
                out.append(v)
        if lista and not out:
            NON_LEGGIBILI.setdefault(nome, json.dumps(lista[0], default=str)[:300])
    CACHE[chiave] = out
    return out


# ---------- catene porta-a-porta ----------
def seg(tipo, op, ident, da, a, dep, arr, prezzo, link=None, legs=None):
    return {"tipo": tipo, "op": op, "id": ident, "da": da, "a": a,
            "dep": dep, "arr": arr, "prezzo": prezzo, "link": link, "legs": legs or []}


def citta(nome):
    parti = (nome or "").strip().lower().split(" ")
    return parti[0] if parti else ""


def valida_catena(segs, direz):
    """Ritorna None se la catena e completa e coerente, altrimenti il motivo."""
    if not segs:
        return "catena vuota"
    inizio, fine = ("padova", "milazzo") if direz == "ANDATA" else ("milazzo", "padova")
    if citta(segs[0]["da"]) != inizio or citta(segs[-1]["a"]) != fine:
        return "origine o destinazione non coerenti con la direzione"
    for s in segs:
        if not s.get("da") or not s.get("a"):
            return "luogo mancante (%s)" % s.get("tipo")
        if not s.get("dep") or not s.get("arr") or s["arr"] <= s["dep"]:
            return "orari non coerenti (%s)" % s.get("tipo")
        if s.get("prezzo") is None:
            return "prezzo mancante (%s)" % s.get("tipo")
        if s["tipo"] in ("volo", "treno") and not s.get("id"):
            return "identificativo mancante (%s)" % s["tipo"]
        if s["tipo"] == "bus" and RICHIEDI_LINEA_BUS and not s.get("id"):
            return "nome ufficiale della linea mancante (bus)"
    for a, b in zip(segs, segs[1:]):
        if citta(a["a"]) != citta(b["da"]):
            return "inversione geografica: %s non coincide con %s" % (a["a"], b["da"])
        margine = BUFFER_VOLO if b["tipo"] == "volo" else BUFFER_TRANSITO
        if b["dep"] < a["arr"] + timedelta(minutes=margine):
            return "coincidenza non compatibile a %s (margine minimo %d minuti)" % (a["a"], margine)
    return None


def segmento_treno(t):
    legs = t.get("legs") or []
    if not legs:
        return None, "dettaglio delle tratte non fornito"
    for tratta in legs:
        if not tratta["num"]:
            return None, "numero treno assente"
        if not (tratta["da"] and tratta["a"] and tratta["dep"] and tratta["arr"]):
            return None, "stazioni o orari di una tratta incompleti"
        if tratta["arr"] <= tratta["dep"]:
            return None, "tratta con arrivo prima della partenza"
    for x, y in zip(legs, legs[1:]):
        if y["dep"] < x["arr"]:
            return None, "tratte non in sequenza cronologica"
        if citta(x["a"]) != citta(y["da"]):
            return None, "tratte non in sequenza geografica"
    if t["prezzo"] is None:
        return None, "prezzo non disponibile"
    ident = " + ".join(tratta["num"] for tratta in legs)
    return seg("treno", t["comp"] or "Treno", ident, legs[0]["da"], legs[-1]["a"],
               legs[0]["dep"], legs[-1]["arr"], t["prezzo"], t["link"], legs), None


def segmento_bus(b):
    if not (b["dep"] and b["arr"] and b["da"] and b["a"]):
        return None, "orari o luoghi incompleti"
    if b["arr"] <= b["dep"]:
        return None, "arrivo prima della partenza"
    if b["prezzo"] is None:
        return None, "prezzo non disponibile"
    if RICHIEDI_LINEA_BUS and not b["linea"]:
        return None, "nome ufficiale della linea non fornito dalla fonte"
    return seg("bus", b["comp"] or "Bus", b["linea"], b["da"], b["a"],
               b["dep"], b["arr"], b["prezzo"], b["link"]), None


def catene_treno_diretto(direz, giorni):
    cat = "Treno diretto Padova-Milazzo"
    o, dst = ("Padova", "Milazzo") if direz == "ANDATA" else ("Milazzo", "Padova")
    out = []
    for g in giorni:
        for t in cerca_viaggi("treno", o, dst, g):
            trovata(cat)
            s, perche = segmento_treno(t)
            if not s:
                scarta(cat, perche)
                continue
            problema = valida_catena([s], direz)
            if problema:
                scarta(cat, problema)
                continue
            out.append({"segmenti": [s], "totale": s["prezzo"]})
    return out


def catene_flix(direz, giorni):
    cat = "FlixBus + treno regionale"
    margine = timedelta(minutes=BUFFER_TRANSITO)
    out = []
    for g in giorni:
        if direz == "ANDATA":
            corse = cerca_viaggi("bus", "Padova", "Messina", g)
        else:
            corse = cerca_viaggi("bus", "Messina", "Padova", g)
        for b in corse:
            trovata(cat)
            sb, perche = segmento_bus(b)
            if not sb:
                scarta(cat, perche)
                continue
            if direz == "ANDATA":
                trenini = cerca_viaggi("treno", STAZIONE_MESSINA, "Milazzo", sb["arr"].date())
                cand = []
                for t in trenini:
                    st, _ = segmento_treno(t)
                    if st and st["dep"] >= sb["arr"] + margine:
                        cand.append(st)
                catena = [sb, min(cand, key=lambda x: x["dep"])] if cand else None
            else:
                trenini = cerca_viaggi("treno", "Milazzo", STAZIONE_MESSINA, g)
                cand = []
                for t in trenini:
                    st, _ = segmento_treno(t)
                    if st and st["arr"] <= sb["dep"] - margine:
                        cand.append(st)
                catena = [max(cand, key=lambda x: x["arr"]), sb] if cand else None
            if not catena:
                scarta(cat, "nessun treno Messina Centrale-Milazzo compatibile (stesso giorno, margine %d minuti)" % BUFFER_TRANSITO)
                continue
            problema = valida_catena(catena, direz)
            if problema:
                scarta(cat, problema)
                continue
            out.append({"segmenti": catena, "totale": catena[0]["prezzo"] + catena[1]["prezzo"]})
    return out


# ---------- report ----------
def righe_soluzione(i, sol):
    segs = sol["segmenti"]
    inizio, fine = segs[0]["dep"], segs[-1]["arr"]
    righe = ["%d) %s → %s | TOTALE %s | durata %s" % (
        i, dt_fmt(inizio), dt_fmt(fine), f_eur(sol["totale"]), f_durata(fine - inizio))]
    for k, s in enumerate(segs):
        if s["tipo"] == "treno":
            for tratta in s["legs"]:
                righe.append("   Treno %s %s → %s | %s → %s" % (
                    tratta["num"], tratta["da"], tratta["a"], dt_fmt(tratta["dep"]), dt_fmt(tratta["arr"])))
            righe.append("   prezzo %s %s %s" % (s["op"], f_eur(s["prezzo"]), f_link(s["link"])))
        elif s["tipo"] == "bus":
            linea = (" " + s["id"]) if s["id"] else ""
            righe.append("   %s%s %s → %s | %s → %s | %s %s" % (
                s["op"], linea, s["da"], s["a"], dt_fmt(s["dep"]), dt_fmt(s["arr"]),
                f_eur(s["prezzo"]), f_link(s["link"])))
        if k < len(segs) - 1:
            righe.append("   attesa a %s: %s" % (s["a"], f_durata(segs[k + 1]["dep"] - s["arr"])))
    return righe


def sezione_nuova(direz, titolo, gruppi):
    righe = [direz + ": " + titolo]
    tutte = []
    for nome, sols in gruppi:
        righe.append(nome)
        if not sols:
            righe.append("Nessuna soluzione completa e verificata.")
            continue
        for i, s in enumerate(sorted(sols, key=lambda x: x["totale"])[:3], 1):
            righe += righe_soluzione(i, s)
        tutte += [(s["totale"], nome, s) for s in sols]
    if tutte:
        tot, nome, s = min(tutte, key=lambda x: x[0])
        righe.append("MIGLIORE %s: %.2f € (%s, partenza %s)" % (
            direz, tot, nome, dt_fmt(s["segmenti"][0]["dep"])))
    else:
        righe.append("Nessuna soluzione senza pernottamento, completa e verificata per queste date.")
    righe.append("")
    return righe


# ---------- simulazione (dati inventati, nessuna chiamata) ----------
def simula():
    ora_ora = adesso()
    oggi = ora_ora.date()
    ga = oggi + timedelta(days=30)
    ga1 = ga + timedelta(days=1)
    gr = oggi + timedelta(days=37)
    gr1 = gr + timedelta(days=1)

    def h(g, ore, minuti):
        return datetime.combine(g, ora_t(ore, minuti))

    def S(tipo, op, ident, da, a, dep, arr, prezzo, legs=None):
        return seg(tipo, op, ident, da, a, dep, arr, prezzo, "https://esempio.invalid/" + tipo, legs)

    def T(num, da, a, dep, arr):
        return {"num": num, "da": da, "a": a, "dep": dep, "arr": arr}

    def sol(*segs):
        return {"segmenti": list(segs), "totale": sum(s["prezzo"] for s in segs)}

    treno_a = S("treno", "Treno Fittizio", "FITT-100 + FITT-200", "Padova", "Milazzo",
                h(ga, 7, 56), h(ga, 20, 19), 44.44,
                [T("FITT-100", "Padova", "Napoli Centrale", h(ga, 7, 56), h(ga, 12, 48)),
                 T("FITT-200", "Napoli Centrale", "Milazzo", h(ga, 13, 45), h(ga, 20, 19))])
    bus_a = S("bus", "Bus Fittizio", "LINEA-FITT-1", "Padova", "Messina",
              h(ga, 17, 10), h(ga1, 11, 5), 33.33)
    reg_a = S("treno", "Treno Fittizio", "FITT-300", "Messina Centrale", "Milazzo",
              h(ga1, 12, 15), h(ga1, 12, 45), 2.22,
              [T("FITT-300", "Messina Centrale", "Milazzo", h(ga1, 12, 15), h(ga1, 12, 45))])
    treno_r = S("treno", "Treno Fittizio", "FITT-500 + FITT-600", "Milazzo", "Padova",
                h(gr, 9, 40), h(gr, 22, 27), 55.55,
                [T("FITT-500", "Milazzo", "Roma Termini", h(gr, 9, 40), h(gr, 15, 0)),
                 T("FITT-600", "Roma Termini", "Padova", h(gr, 15, 30), h(gr, 22, 27))])
    reg_r = S("treno", "Treno Fittizio", "FITT-400", "Milazzo", "Messina Centrale",
              h(gr, 17, 54), h(gr, 18, 27), 2.22,
              [T("FITT-400", "Milazzo", "Messina Centrale", h(gr, 17, 54), h(gr, 18, 27))])
    bus_r = S("bus", "Bus Fittizio", "LINEA-FITT-2", "Messina", "Padova",
              h(gr, 20, 15), h(gr1, 14, 25), 44.44)

    # Autotest del validatore (dati inventati)
    ok_valida = (valida_catena([treno_a], "ANDATA") is None
                 and valida_catena([bus_a, reg_a], "ANDATA") is None
                 and valida_catena([reg_r, bus_r], "RITORNO") is None
                 and valida_catena([treno_r], "RITORNO") is None)
    stretta = dict(reg_a)
    stretta["dep"], stretta["arr"] = h(ga1, 11, 10), h(ga1, 11, 40)
    senza_id = dict(treno_a)
    senza_id["id"] = ""
    fuori_citta = dict(reg_a)
    fuori_citta["da"] = "Catania Centrale"
    ko = (valida_catena([bus_a, stretta], "ANDATA") is not None,
          valida_catena([senza_id], "ANDATA") is not None,
          valida_catena([bus_a, fuori_citta], "ANDATA") is not None,
          valida_catena([treno_a], "RITORNO") is not None)
    esito_test = "PASS" if (ok_valida and all(ko)) else "FAIL (soluzioni valide=%s, scarti attesi=%s)" % (ok_valida, ko)

    corpo = ["REPORT VIAGGI - SIMULAZIONE", "Aggiornato: %s %s" % (fmt(ora_ora), hm(ora_ora)), ""]
    corpo += sezione_nuova("ANDATA", "Padova → Milazzo", [
        ("VOLI", []),
        ("TRENO diretto Padova-Milazzo", [sol(treno_a)]),
        ("FLIXBUS + treno regionale", [sol(bus_a, reg_a)])])
    corpo += sezione_nuova("RITORNO", "Milazzo → Padova", [
        ("VOLI", []),
        ("TRENO diretto Padova-Milazzo", [sol(treno_r)]),
        ("FLIXBUS + treno regionale", [sol(reg_r, bus_r)])])
    corpo += ["AUTOTEST VALIDATORE: " + esito_test]

    righe = ["TEST – DATI NON REALI",
             "Simulazione: nessuna chiamata a API o siti di trasporto.", ""]
    righe += ["[FITTIZIO] " + r if r.strip() else r for r in corpo]
    righe += ["TEST – DATI NON REALI: date, numeri di treno, linee, importi e link sono inventati.",
              "Serve solo a verificare la formattazione e il validatore."]
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
    ora_ora = adesso()
    oggi = ora_ora.date()

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

    nome, a_da, a_a, r_da, r_a = evento
    domani = oggi + timedelta(days=1)
    finestre = (("ANDATA", "Padova → Milazzo", max(d(a_da), domani), d(a_a)),
                ("RITORNO", "Milazzo → Padova", max(d(r_da), domani), d(r_a)))

    righe = ["REPORT VIAGGI - " + nome, "Aggiornato: %s %s" % (fmt(ora_ora), hm(ora_ora))]
    if REALE_MANUALE:
        righe.append("Esecuzione manuale in modalità reale: solo dati ottenuti dalle fonti.")
    if nota:
        righe.append(nota)
    if bloccate:
        righe.append("Date escluse: " + ", ".join(fmt(d(x)) for x in sorted(bloccate)))
    righe.append("")

    for direz, titolo, ini, fin in finestre:
        treni_s, flix_s = [], []
        if ini <= fin:
            giorni = [ini + timedelta(days=i) for i in range((fin - ini).days + 1)]
            giorni = [g for g in giorni if g.isoformat() not in bloccate]
            voli_l = []
            for nord in AEROPORTI_NORD:
                for sud in AEROPORTI_SUD:
                    voli_l += voli(direz, nord, sud, ini, fin, bloccate)
            for v in voli_l:
                trovata("Voli")
                scarta_piu("Voli", motivi_volo(v, direz))
            scelti = scegli(giorni, DATE_TRENO)
            treni_s = catene_treno_diretto(direz, scelti)
            flix_s = catene_flix(direz, scelti)
        righe += sezione_nuova(direz, titolo, [
            ("VOLI", []),
            ("TRENO diretto Padova-Milazzo", treni_s),
            ("FLIXBUS + treno regionale", flix_s)])

    righe.append("RIEPILOGO CONTROLLI (nel report compaiono solo soluzioni complete e verificate)")
    for cat, (tr, sc) in sorted(CONTI.items()):
        righe.append("%s: %d trovate, %d scartate" % (cat, tr, sc))
        for (c2, motivo), n in sorted(MOTIVI.items()):
            if c2 == cat:
                righe.append("   %s: %d" % (motivo, n))
    righe.append("Fonti (ricerche riuscite): " + ", ".join(
        "%s %d/%d" % (n, f[0], f[1]) for n, f in FONTI.items()))
    if ESITI:
        righe.append("Esiti ricerche: " + "; ".join(
            "%s %s x%d" % (f, e, n) for (f, e), n in sorted(ESITI.items())))
    for (fonte, codice), e in sorted(ERRORI_FONTI.items()):
        righe.append("ERRORE FONTE %s %s x%d (%s): %s" % (
            fonte, codice, e["n"], "; ".join(e["tratte"]), e["msg"]))
    for n, s in NON_LEGGIBILI.items():
        righe.append("Dati non leggibili da %s, esempio: %s" % (n, s))
    righe.append("I link portano alla pagina di ricerca del vettore, non alla singola corsa.")
    righe.append("Itabus escluso (inaffidabile). Italo incluso solo se restituisce corse.")
    telegram("\n".join(righe))


main()
