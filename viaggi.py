import json
import os
import random
import re
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from datetime import date, datetime, timedelta, timezone
from datetime import time as ora_t
from html import escape as esc
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
RICHIEDI_LINEA_BUS = False

AEROPORTI_NORD = ["VCE", "TSF", "VRN"]
AEROPORTI_SUD = ["CTA", "PMO", "REG"]
NOME_APT = {"VCE": "Venezia Marco Polo", "TSF": "Treviso Aeroporto", "VRN": "Verona Aeroporto",
            "CTA": "Catania Aeroporto", "PMO": "Palermo Aeroporto", "REG": "Reggio Calabria Aeroporto"}

# Stadio 1 (Hermes / listino Wizz): si scelgono i giorni piu economici entro questa tolleranza sul
# minimo della rotta; il prezzo finale e SOLO quello verificato live.
TOLLERANZA_HERMES = 0.30
LIVE_PER_ROTTA = 2       # giorni verificati live per ogni rotta
LIVE_MAX = 9             # verifiche live massime per direzione e per compagnia
WIZZ_LIVE_MAX = 3        # verifiche live Wizz Air per direzione (solo combinazioni gia promettenti)
PAUSA_SERVER = 1.1       # secondi tra due richieste al server locale (limite 60 al minuto)

# Collegamenti aeroporto -> Milazzo cercati sul server locale (Trenitalia).
# CTA ha anche Giuntabus (pagina di prenotazione). Nomi verificati dai log.
PERCORSI_SUD = {
    "CTA": [[("Catania Aeroporto Fontanarossa", "Milazzo")]],
    "PMO": [[("Palermo Aeroporto", "Milazzo")]],
    "REG": [[("Reggio Di Calabria Aeroporto", "Milazzo")],
            [("Reggio Di Calabria Aeroporto", "Villa S. Giovanni"), ("Villa S. Giovanni", "Milazzo")],
            [("Reggio Di Calabria Aeroporto", "Villa S. Giovanni"),
             ("Villa S. Giovanni", "Messina Centrale"), ("Messina Centrale", "Milazzo")]],
}

# Navette aeroporto del Nord: treno Trenitalia Padova <-> stazione + navetta stazione <-> aeroporto.
# I luoghi "VERONA AEROPORTO" e "TREVISO AEROPORTO" del server danno HTTP 503, quindi si usa il treno
# fino alla stazione. Non c'e una tabella oraria verificata: si usano i LIMITI NEL CASO PEGGIORE
# calcolati dalla frequenza dichiarata. "primo"/"ultimo" = fascia in cui la frequenza e garantita;
# None = navetta disattivata. Prezzi: Trenitalia (pagine Verona Airlink 7,00 e Treviso Airlink 5,00).
NAVETTE = {
    "VRN": {"op": "ATV Verona Airlink", "linea": "linea 199", "stazione": "Verona Porta Nuova",
            "stop_apt": "Verona Aeroporto", "freq": 20, "durata": 15, "prezzo": 7.00,
            "primo": (7, 10), "ultimo": (23, 10)},
    "TSF": {"op": "MOM Airlink", "linea": "linea 6", "stazione": "Treviso Centrale",
            "stop_apt": "Treviso Aeroporto", "freq": 30, "durata": 15, "prezzo": 5.00,
            "primo": None, "ultimo": None},
}

# Italo non serve Sicilia e sud: escluso dalle ricerche che coinvolgono questi nodi.
NODI_SUD = ("milazzo", "messina", "catania", "palermo", "reggio", "villa")

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


def f_eur(p):
    return "n/d" if p is None else "%.2f €" % p


def f_link(link):
    return "[link alla ricerca] " + str(link) if link else ""


def da_utc(s):
    """Converte un orario UTC (con o senza Z) in ora italiana (CET/CEST), senza fuso."""
    if not isinstance(s, str):
        return None
    try:
        x = datetime.fromisoformat(s.replace("Z", "+00:00"))
    except ValueError:
        return None
    if x.tzinfo is None:
        x = x.replace(tzinfo=timezone.utc)
    return x.astimezone(ROMA).replace(tzinfo=None)


def ripulisci(testo):
    testo = re.sub(r"\bcirca\b\s*", "", testo, flags=re.I)
    return testo.replace("~", "")


def telegram(testo):
    testo = ripulisci(testo)
    blocchi, corrente = [], ""
    for p in testo.split("\n\n"):
        if not p.strip():
            continue
        while len(p) > 3800:
            taglio = p.rfind("\n", 0, 3800)
            taglio = taglio if taglio > 0 else 3800
            if corrente:
                blocchi.append(corrente)
                corrente = ""
            blocchi.append(p[:taglio])
            p = p[taglio:].lstrip("\n")
        if corrente and len(corrente) + len(p) + 2 > 3800:
            blocchi.append(corrente)
            corrente = ""
        corrente = (corrente + "\n\n" + p) if corrente else p
    if corrente:
        blocchi.append(corrente)
    for b in blocchi:
        dati = urllib.parse.urlencode({
            "chat_id": TG_CHAT, "text": b, "parse_mode": "HTML",
            "disable_web_page_preview": "true",
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


def scarta_voce(cat, motivo):
    trovata(cat)
    scarta(cat, motivo)


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
        pagina = urllib.request.urlopen(req, timeout=60).read().decode("utf-8", "ignore")
        p = LettoreTabelle()
        p.feed(pagina)
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


def segmento_busitalia(direz, riferimento):
    """ANDATA: riferimento = partenza del volo. RITORNO: riferimento = arrivo del volo a VCE."""
    giorno = riferimento.date()
    if not servizio_vce_attivo(giorno):
        return None, "Busitalia non attivo il 25 dicembre"
    base = datetime.combine(giorno, ora_t.min)
    if direz == "ANDATA":
        limite = riferimento - timedelta(minutes=BUFFER_VOLO)
        utili = [(p, a) for p, a in BUS["andata"] if base + timedelta(minutes=a) <= limite]
        if not utili:
            return None, "nessuna corsa Busitalia utile prima del volo (margine %d minuti)" % BUFFER_VOLO
        p, a = max(utili, key=lambda x: x[1])
        return seg("bus", "Busitalia", "Padova-Venezia Marco Polo", "Padova Stanga via Venezia",
                   NOME_APT["VCE"], base + timedelta(minutes=p), base + timedelta(minutes=a),
                   BUS["prezzo"], URL_BUSITALIA), None
    limite = riferimento + timedelta(minutes=BUFFER_TRANSITO)
    utili = [(p, a) for p, a in BUS["ritorno"] if base + timedelta(minutes=p) >= limite]
    if not utili:
        return None, "nessuna corsa Busitalia utile dopo l'atterraggio (margine %d minuti)" % BUFFER_TRANSITO
    p, a = min(utili, key=lambda x: x[0])
    return seg("bus", "Busitalia", "Padova-Venezia Marco Polo", NOME_APT["VCE"],
               "Padova Stanga via Venezia", base + timedelta(minutes=p),
               base + timedelta(minutes=a), BUS["prezzo"], URL_BUSITALIA), None


# ---------- voli: stadio 1 (Hermes / listino Wizz) ----------
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
    ULT_ERR[0] = ""
    jr = get_json("https://hermes.brinkhorst.dev/api/v1/routes/%s/%s/cheapest" % (o, dst))
    r_non_operata = jr is None and ULT_ERR[0] == "HTTP 404"
    jw, w_non_operata = None, False
    try:
        from flywizz import WizzAir, TimetableSearch
        ric = TimetableSearch(origin=o, destination=dst,
                              date_from=datetime.combine(inizio, ora_t.min),
                              date_to=datetime.combine(fine, ora_t.min))
        jw = normalizza(list(WizzAir().get_timetable(ric)))
    except Exception as e:
        w_non_operata = "InvalidMarket" in str(e)
        if not w_non_operata:
            print("Errore Wizz", o, dst, repr(e)[:200])
    sorgenti = [("Ryanair", jr, r_non_operata), ("Wizz Air", jw, w_non_operata)]

    out = []
    for nome, js, non_operata in sorgenti:
        if non_operata:
            esito(nome, "ROTTA NON OPERATA")
            continue
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


def scegli_live(candidati):
    """Stadio 1: per ogni rotta tiene i giorni piu economici entro la tolleranza."""
    per_rotta = {}
    for v in candidati:
        per_rotta.setdefault((v["o"], v["dst"]), []).append(v)
    scelti = []
    for lista in per_rotta.values():
        lista.sort(key=lambda v: v["prezzo"])
        minimo = lista[0]["prezzo"]
        for v in lista[:LIVE_PER_ROTTA]:
            if v["prezzo"] <= minimo * (1 + TOLLERANZA_HERMES):
                scelti.append(v)
    scelti.sort(key=lambda v: v["prezzo"])
    return scelti[:LIVE_MAX]


# ---------- voli: stadio 2 (verifica live sulle pagine ufficiali, mercato italiano, EUR) ----------
def da_flightkey(chiave):
    """Esempio Ryanair: 'FR~ 531~ ~~VCE~12/16/2026 07:25~PMO~12/16/2026 09:00~~' (orari locali)."""
    p = chiave.split("~") if isinstance(chiave, str) else []
    if len(p) >= 8:
        try:
            dep = datetime.strptime(p[5], "%m/%d/%Y %H:%M")
            arr = datetime.strptime(p[7], "%m/%d/%Y %H:%M")
        except ValueError:
            return "", None, None
        return "%s %s" % (p[0], p[1].strip()), dep, arr
    return "", None, None


def leggi_availability(js, o, dst, url):
    out = []
    if not isinstance(js, dict):
        return out
    valuta = js.get("currency")
    trips = js.get("trips")
    if not isinstance(trips, list):
        return out
    for trip in trips:
        if not isinstance(trip, dict):
            continue
        if trip.get("origin") not in (None, o) or trip.get("destination") not in (None, dst):
            continue
        for dd in trip.get("dates") or []:
            for f in ((dd.get("flights") or []) if isinstance(dd, dict) else []):
                segs = f.get("segments")
                if not isinstance(segs, list) or not segs:
                    segs = [f]
                if len(segs) != 1:
                    continue  # solo voli diretti
                s0 = segs[0]
                num = s0.get("flightNumber") or f.get("flightNumber")
                chiave_num, k_dep, k_arr = da_flightkey(f.get("flightKey"))
                if not num or str(num).strip().isdigit():
                    num = chiave_num or num
                t_utc = s0.get("timeUTC") or f.get("timeUTC")
                t_loc = s0.get("time") or f.get("time")
                dep = arr = None
                if isinstance(t_utc, list) and len(t_utc) == 2:
                    dep, arr = da_utc(t_utc[0]), da_utc(t_utc[1])
                elif isinstance(t_loc, list) and len(t_loc) == 2:
                    dep, arr = leggi_ora(t_loc[0]), leggi_ora(t_loc[1])
                if dep is None and k_dep:
                    dep, arr = k_dep, k_arr
                prezzo = None
                for fa in (f.get("regularFare") or {}).get("fares") or []:
                    if isinstance(fa, dict) and fa.get("type") == "ADT" and fa.get("amount") is not None:
                        prezzo = round(float(fa["amount"]), 2)
                        break
                out.append({"numero": str(num).strip() if num else "", "dep": dep, "arr": arr,
                            "prezzo": prezzo, "valuta": valuta, "link": url})
    return out


def cattura_risposte(url, filtro_url, riscalda=None):
    """Apre la pagina ufficiale in Chromium e cattura le risposte JSON il cui indirizzo contiene
    filtro_url. Se riscalda e indicato, visita prima quella pagina nella stessa sessione (cookie)."""
    risposte, errore = [], None
    try:
        from playwright.sync_api import sync_playwright
        with sync_playwright() as p:
            browser = p.chromium.launch(headless=True, args=["--disable-blink-features=AutomationControlled"])
            try:
                ctx = browser.new_context(locale="it-IT", timezone_id="Europe/Rome", user_agent=UA_BROWSER,
                                          extra_http_headers={"Accept-Language": "it-IT,it;q=0.9,en;q=0.8"})
                page = ctx.new_page()

                def on_risposta(r):
                    if all(k in r.url.lower() for k in filtro_url):
                        try:
                            risposte.append((r.status, r.json() if r.status == 200 else None))
                        except Exception:
                            risposte.append((r.status, None))

                page.on("response", on_risposta)
                if riscalda:
                    page.goto(riscalda, wait_until="domcontentloaded", timeout=45000)
                    page.wait_for_timeout(int(random.uniform(4000, 7000)))
                page.goto(url, wait_until="domcontentloaded", timeout=45000)
                for _ in range(25):
                    if any(js for st, js in risposte):
                        break
                    page.wait_for_timeout(1000)
            finally:
                browser.close()
    except Exception as e:
        errore = "verifica live non eseguibile (%s)" % repr(e)[:80]
    return risposte, errore


def motivo_cattura(risposte, errore):
    if errore:
        return errore
    if risposte:
        return "la pagina ufficiale ha risposto HTTP %s senza dati" % risposte[0][0]
    return "nessuna risposta dalla pagina ufficiale (probabile blocco anti-bot)"


def verifica_live_ryanair(o, dst, giorno):
    chiave = ("live", "Ryanair", o, dst, giorno)
    if chiave in CACHE:
        return CACHE[chiave]
    url = ("https://www.ryanair.com/it/it/trip/flights/select?adults=1&teens=0&children=0&infants=0"
           "&dateOut=%s&originIata=%s&destinationIata=%s") % (giorno.isoformat(), o, dst)
    risposte, errore = cattura_risposte(url, ("/api/booking/", "availability"))
    voli_ok, motivo = [], None
    buone = [js for st, js in risposte if js]
    if buone:
        esempio("Ryanair live", buone[0])
        for f in leggi_availability(buone[0], o, dst, url):
            if f["dep"] and f["dep"].date() == giorno:
                voli_ok.append(f)
        if not voli_ok:
            motivo = "nessun volo diretto %s-%s il %s nella risposta ufficiale" % (o, dst, fmt(giorno))
    else:
        motivo = motivo_cattura(risposte, errore)
    CACHE[chiave] = (voli_ok, motivo)
    return CACHE[chiave]


def raccogli_importi(x, importi, valute):
    if isinstance(x, dict):
        for k, v in x.items():
            kl = str(k).lower()
            if kl == "amount" and isinstance(v, (int, float)) and not isinstance(v, bool):
                importi.add(round(float(v), 2))
            elif kl in ("currencycode", "currency") and isinstance(v, str):
                valute.add(v)
            else:
                raccogli_importi(v, importi, valute)
    elif isinstance(x, list):
        for v in x:
            raccogli_importi(v, importi, valute)


def leggi_wizz_live(js, url, ora_listino, prezzo_listino):
    """Accetta un volo solo se orario e prezzo coincidono con il listino ufficiale Wizz (ancoraggio)."""
    out = []
    voli_js = js.get("outboundFlights") if isinstance(js, dict) else None
    for f in voli_js or []:
        if not isinstance(f, dict):
            continue
        dep, arr = leggi_ora(f.get("departureDateTime")), leggi_ora(f.get("arrivalDateTime"))
        num = str(f.get("flightNumber") or "").strip()
        cod = str(f.get("carrierCode") or "W6").strip()
        if not (dep and arr and num):
            continue
        if ora_listino is not None and dep != ora_listino:
            continue
        importi, valute = set(), set()
        raccogli_importi(f.get("fares"), importi, valute)
        if valute != {"EUR"}:
            continue
        if not any(abs(x - prezzo_listino) < 0.015 for x in importi):
            continue
        out.append({"numero": "%s %s" % (cod, num), "dep": dep, "arr": arr,
                    "prezzo": round(prezzo_listino, 2), "valuta": "EUR", "link": url})
    return out


def verifica_live_wizz(o, dst, giorno, ora_listino, prezzo_listino):
    chiave = ("live", "Wizz Air", o, dst, giorno)
    if chiave in CACHE:
        return CACHE[chiave]
    url = "https://www.wizzair.com/it-it/booking/select-flight/%s/%s/%s/null/1/0/0/null" % (
        o, dst, giorno.isoformat())
    home = "https://www.wizzair.com/it-it"
    time.sleep(random.uniform(5, 9))
    risposte, errore = cattura_risposte(url, ("/api/search/search",), home)
    if not any(js for st, js in risposte) and any(st == 429 for st, js in risposte):
        time.sleep(45)  # limite di richieste (HTTP 429): attesa e un nuovo tentativo
        risposte, errore = cattura_risposte(url, ("/api/search/search",), home)
    voli_ok, motivo = [], None
    buone = [js for st, js in risposte if js]
    if buone:
        esempio("Wizz live", buone[0])
        voli_ok = leggi_wizz_live(buone[0], url, ora_listino, prezzo_listino)
        if not voli_ok:
            motivo = "risposta Wizz catturata ma nessun volo coincide per orario e prezzo con il listino ufficiale"
    else:
        motivo = motivo_cattura(risposte, errore)
    CACHE[chiave] = (voli_ok, motivo)
    return CACHE[chiave]


# ---------- treni e bus (server locale) ----------
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
    senza_italo = tipo == "treno" and any(n in (orig + " " + dest).lower() for n in NODI_SUD)
    tratta = "%s→%s %s" % (orig, dest, fmt(giorno))
    url = "%s/%s?origin=%s&destination=%s&date=%s&adults=1&children=0%s" % (
        SERVER, percorso, urllib.parse.quote(orig), urllib.parse.quote(dest),
        giorno.isoformat(), extra)
    time.sleep(PAUSA_SERVER)
    ULT_ERR[0] = ""
    js = get_json(url)
    if js is None and ULT_ERR[0] == "HTTP 503":
        time.sleep(6)  # errore temporaneo del server locale: un nuovo tentativo
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
        if senza_italo:
            lista = [j for j in lista if str(j.get("provider", "")).lower() != "italo"]
            errori = [e for e in errori if str(e.get("provider", "")).lower() != "italo"]
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
def seg(tipo, op, ident, da, a, dep, arr, prezzo, link=None, legs=None, tratta_txt=None):
    return {"tipo": tipo, "op": op, "id": ident, "da": da, "a": a, "dep": dep, "arr": arr,
            "prezzo": prezzo, "link": link, "legs": legs or [], "tratta_txt": tratta_txt}


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
        if b["tipo"] == "volo":
            margine = BUFFER_VOLO
        elif b.get("frequenza"):
            margine = 0  # navetta a frequenza: il margine e gia incluso nei suoi limiti orari
        else:
            margine = BUFFER_TRANSITO
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


def corse_giuntabus(direz, giorno):
    """Corse reali Giuntabus (pagina di prenotazione) tra Catania Aeroporto e Milazzo."""
    chiave = ("giuntabus", direz, giorno)
    if chiave in CACHE:
        return CACHE[chiave]
    da, a = ("Catania Aeroporto", "Milazzo") if direz == "ANDATA" else ("Milazzo", "Catania Aeroporto")
    corse = []
    try:
        import giuntabus
        corse = giuntabus.leggi(da, a, giorno)["corse"]
        conta("Giuntabus", bool(corse))
    except Exception as e:
        print("Errore Giuntabus", repr(e)[:200])
        conta("Giuntabus", False)
    CACHE[chiave] = corse
    return corse


def alternativa_giuntabus(direz, rif):
    """ANDATA: rif = arrivo del volo a CTA. RITORNO: rif = partenza del volo da CTA."""
    corse = corse_giuntabus(direz, rif.date())
    if direz == "ANDATA":
        cand = [c for c in corse if c["dep"] >= rif + timedelta(minutes=BUFFER_TRANSITO)]
        if not cand:
            return None
        c = min(cand, key=lambda x: (x["prezzo"], x["dep"]))
    else:
        limite = rif - timedelta(minutes=BUFFER_VOLO)
        cand = [c for c in corse if c["arr"] <= limite]
        if not cand:
            return None
        c = min(cand, key=lambda x: (x["prezzo"], -x["arr"].timestamp()))
    return [seg("bus", "Giuntabus", "", c["da"], c["a"], c["dep"], c["arr"], c["prezzo"], c.get("link"))]


def percorri(hops, giorno, avanti, riferimento):
    """hops: tratte in ordine cronologico di viaggio [(da, a), ...], cercate sul server locale.
    avanti=True: la prima tratta parte non prima di `riferimento`.
    avanti=False: l'ultima tratta arriva non oltre `riferimento`.
    Tra una tratta e l'altra ci sono almeno BUFFER_TRANSITO minuti. Ritorna i segmenti o None."""
    margine = timedelta(minutes=BUFFER_TRANSITO)
    catena = []
    t = riferimento
    if avanti:
        for a, b in hops:
            cand = []
            for j in cerca_viaggi("treno", a, b, giorno):
                s, _ = segmento_treno(j)
                if s and s["dep"] >= t:
                    cand.append(s)
            if not cand:
                return None
            s = min(cand, key=lambda x: (x["prezzo"], x["dep"]))
            catena.append(s)
            t = s["arr"] + margine
    else:
        for a, b in reversed(hops):
            cand = []
            for j in cerca_viaggi("treno", a, b, giorno):
                s, _ = segmento_treno(j)
                if s and s["arr"] <= t:
                    cand.append(s)
            if not cand:
                return None
            s = min(cand, key=lambda x: (x["prezzo"], -x["arr"].timestamp()))
            catena.insert(0, s)
            t = s["dep"] - margine
    return catena


def collegamento_sud(apt, direz, rif):
    """Collegamento reale aeroporto <-> Milazzo. ANDATA: rif = arrivo del volo.
    RITORNO: rif = partenza del volo."""
    giorno = rif.date()
    alternative = []
    for percorso in PERCORSI_SUD.get(apt, []):
        if direz == "ANDATA":
            c = percorri(percorso, giorno, True, rif + timedelta(minutes=BUFFER_TRANSITO))
        else:
            hops = [(b, a) for a, b in reversed(percorso)]
            c = percorri(hops, giorno, False, rif - timedelta(minutes=BUFFER_VOLO))
        if c:
            alternative.append(c)
    if apt == "CTA":
        gb = alternativa_giuntabus(direz, rif)
        if gb:
            alternative.append(gb)
    if alternative:
        return min(alternative, key=lambda c: (sum(s["prezzo"] for s in c), c[-1]["arr"])), None
    motivo = "nessun collegamento %s-Milazzo con numeri, orari e prezzo completi nello stesso giorno (Trenitalia%s)" % (
        apt, " e Giuntabus" if apt == "CTA" else "")
    return None, motivo


def nord_disponibile(apt):
    if apt == "VCE":
        return True
    n = NAVETTE.get(apt)
    return bool(n and n.get("primo") and n.get("ultimo"))


def navetta_seg(n, da, a, dep, arr):
    s = seg("bus", n["op"], n["linea"], da, a, dep, arr, n["prezzo"], None)
    s["frequenza"] = n["freq"]
    return s


def collegamento_con_navetta(apt, direz, rif):
    """Treno Padova <-> stazione + navetta stazione <-> aeroporto, con limiti nel caso peggiore.
    ANDATA: rif = partenza del volo. RITORNO: rif = arrivo del volo."""
    n = NAVETTE[apt]
    if not n.get("primo") or not n.get("ultimo"):
        return None, "navetta %s: fascia oraria del servizio non indicata (disattivata)" % apt
    giorno = rif.date()
    freq = timedelta(minutes=n["freq"])
    dur = timedelta(minutes=n["durata"])
    inizio_s = datetime.combine(giorno, ora_t(*n["primo"]))
    fine_s = datetime.combine(giorno, ora_t(*n["ultimo"]))
    stazione = n["stazione"]
    fascia = "%s-%s" % (hm(inizio_s), hm(fine_s))
    if direz == "ANDATA":
        limite_treno = rif - timedelta(minutes=BUFFER_VOLO) - dur - freq
        treno = percorri([("Padova", stazione)], giorno, False, limite_treno)
        if not treno:
            return None, "nessun treno Padova-%s con numeri, orari e prezzo completi nello stesso giorno" % stazione
        t = treno[-1]["arr"]
        if t < inizio_s or t + freq > fine_s:
            return None, "navetta %s: arrivo del treno fuori dalla fascia con frequenza garantita (%s)" % (apt, fascia)
        return treno + [navetta_seg(n, stazione, n["stop_apt"], t, t + freq + dur)], None
    dep = rif + timedelta(minutes=BUFFER_TRANSITO)
    if dep < inizio_s or dep + freq > fine_s:
        return None, "navetta %s: atterraggio fuori dalla fascia con frequenza garantita (%s)" % (apt, fascia)
    nav = navetta_seg(n, n["stop_apt"], stazione, dep, dep + freq + dur)
    treno = percorri([(stazione, "Padova")], giorno, True, nav["arr"] + timedelta(minutes=BUFFER_TRANSITO))
    if not treno:
        return None, "nessun treno %s-Padova con numeri, orari e prezzo completi nello stesso giorno" % stazione
    return [nav] + treno, None


def collegamento_nord(apt, direz, rif):
    """Collegamento reale Padova <-> aeroporto del Nord. ANDATA: rif = partenza del volo.
    RITORNO: rif = arrivo del volo. VCE usa Busitalia (pagina ufficiale), gli altri treno + navetta."""
    if apt == "VCE":
        s, perche = segmento_busitalia(direz, rif)
        return ([s], None) if s else (None, perche)
    if apt in NAVETTE:
        return collegamento_con_navetta(apt, direz, rif)
    return None, "nessuna fonte verificata per Padova-%s" % apt


def costruisci_catena_volo(direz, f, o, dst, comp):
    andata = direz == "ANDATA"
    if f["valuta"] != "EUR":
        return None, "valuta non EUR (%s)" % f["valuta"]
    if not (f["numero"] and f["dep"] and f["arr"]) or f["prezzo"] is None:
        return None, "dati del volo incompleti (numero, orari o prezzo)"
    volo = seg("volo", comp, f["numero"], NOME_APT[o], NOME_APT[dst], f["dep"], f["arr"],
               f["prezzo"], f["link"], None, "%s → %s" % (o, dst))
    nord = o if andata else dst
    sud = dst if andata else o
    nord_l, perche = collegamento_nord(nord, direz, f["dep"] if andata else f["arr"])
    if not nord_l:
        return None, perche
    coll, perche = collegamento_sud(sud, direz, f["arr"] if andata else f["dep"])
    if not coll:
        return None, perche
    catena = nord_l + [volo] + coll if andata else coll + [volo] + nord_l
    return catena, None


def catene_voli(direz, voli_l):
    andata = direz == "ANDATA"
    cand = {"Ryanair": [], "Wizz Air": []}
    for v in voli_l:
        nord = v["o"] if andata else v["dst"]
        sud = v["dst"] if andata else v["o"]
        if not nord_disponibile(nord):
            scarta_voce("Voli non verificabili", "nessuna fonte verificata per Padova-%s" % nord)
        elif not PERCORSI_SUD.get(sud):
            scarta_voce("Voli non verificabili", "nessuna fonte verificata per %s-Milazzo" % sud)
        elif v["comp"] in cand:
            cand[v["comp"]].append(v)
    # Wizz: la verifica live parte solo se il collegamento a terra dal lato della partenza e gia valido
    cat_w = "Voli Wizz Air (verifica live)"
    promettenti = []
    for v in cand["Wizz Air"]:
        if not v["ore"]:
            scarta_voce(cat_w, "orario di partenza del listino assente")
            continue
        dep = v["ore"][0]
        if andata:
            ok, _ = collegamento_nord(v["o"], direz, dep)
        else:
            ok, _ = collegamento_sud(v["o"], direz, dep)
        if ok:
            promettenti.append(v)
        else:
            scarta_voce(cat_w, "collegamento a terra non compatibile con l'orario di partenza del listino")
    cand["Wizz Air"] = promettenti
    out = []
    for comp, lista in cand.items():
        cat = "Voli %s (verifica live)" % comp
        scelti = scegli_live(lista)
        if comp == "Wizz Air":
            scelti = scelti[:WIZZ_LIVE_MAX]
        for v in scelti:
            if comp == "Ryanair":
                live, motivo = verifica_live_ryanair(v["o"], v["dst"], v["data"])
            else:
                live, motivo = verifica_live_wizz(v["o"], v["dst"], v["data"],
                                                  v["ore"][0] if v["ore"] else None, v["prezzo"])
            conta(comp + " live", bool(live))
            if not live:
                scarta_voce(cat, motivo or "nessun volo restituito dalla pagina ufficiale")
                continue
            for f in live:
                trovata(cat)
                catena, perche = costruisci_catena_volo(direz, f, v["o"], v["dst"], comp)
                if not catena:
                    scarta(cat, perche)
                    continue
                problema = valida_catena(catena, direz)
                if problema:
                    scarta(cat, problema)
                    continue
                out.append({"segmenti": catena, "tipo": "Volo",
                            "totale": round(sum(s["prezzo"] for s in catena), 2)})
    return out


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
            out.append({"segmenti": [s], "tipo": "Treno", "totale": round(s["prezzo"], 2)})
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
            out.append({"segmenti": catena, "tipo": "FlixBus + treno",
                        "totale": round(catena[0]["prezzo"] + catena[1]["prezzo"], 2)})
    return out


# ---------- report compatto: top 3 andate e top 3 ritorni ----------
def etichetta_mezzo(t):
    da, a = (t.get("da") or "").lower(), (t.get("a") or "").lower()
    if "bus" in (da + " " + a).split():
        return "Autobus"
    if (da.startswith("villa s") and a.startswith("messina")) or (a.startswith("villa s") and da.startswith("messina")):
        return "Attraversamento Stretto (nave)"
    return "Treno"


def orari_corti(dep, arr, base):
    a = hm(dep) + (" (%s)" % fmt(dep) if dep.date() != base else "")
    b = hm(arr) + (" (%s)" % fmt(arr) if arr.date() != base else "")
    return "%s → %s" % (a, b)


def indice_principale(segs):
    for k, s in enumerate(segs):
        if s["tipo"] == "volo":
            return k
    return max(range(len(segs)), key=lambda k: segs[k]["arr"] - segs[k]["dep"])


def desc_seg(s, base):
    costo = "costo %s" % f_eur(s["prezzo"])
    if s["tipo"] == "treno":
        parti = ["%s %s %s → %s, %s" % (etichetta_mezzo(t), t["num"], t["da"], t["a"],
                                         orari_corti(t["dep"], t["arr"], base)) for t in s["legs"]]
        return "; ".join(parti) + ", " + costo
    if s["tipo"] == "volo":
        return "%s %s %s, %s (ora italiana), %s (prezzo verificato live)" % (
            s["op"], s["id"], s.get("tratta_txt") or "%s → %s" % (s["da"], s["a"]),
            orari_corti(s["dep"], s["arr"], base), costo)
    if s.get("frequenza"):
        f = timedelta(minutes=s["frequenza"])
        return ("%s %s %s → %s, corse ogni %d min: partenza tra le %s e le %s, arrivo entro le %s, %s "
                "(limiti nel caso peggiore dalla frequenza dichiarata; tabella oraria ufficiale non verificata)") % (
            s["op"], s["id"], s["da"], s["a"], s["frequenza"], hm(s["dep"]), hm(s["dep"] + f),
            hm(s["arr"]), costo)
    linea = (" " + s["id"]) if s["id"] else ""
    return "%s%s %s → %s, %s, %s" % (s["op"], linea, s["da"], s["a"],
                                      orari_corti(s["dep"], s["arr"], base), costo)


def righe_compatte(direz, i, sol):
    andata = direz == "ANDATA"
    segs = sol["segmenti"]
    base = segs[0]["dep"].date()
    k = indice_principale(segs)
    prima, principale, dopo = segs[:k], segs[k], segs[k + 1:]
    righe = ["  - <b>SOLUZIONE %s [%d] — Totale %s: %s</b> (%s)" % (
        direz, i, "Andata" if andata else "Ritorno", f_eur(sol["totale"]), esc(fmt(segs[0]["dep"])))]
    numeri = ["1️⃣", "2️⃣", "3️⃣"]
    n = 0
    if prima:
        et = "Trasferimento Locale" if andata else "Collegamento iniziale"
        righe.append("    %s %s: %s" % (numeri[n], et, esc(" + ".join(desc_seg(s, base) for s in prima))))
        n += 1
    nome_mezzo = {"volo": "Volo", "treno": "Treno", "bus": "Autobus"}.get(principale["tipo"], "Mezzo")
    righe.append("    %s Tratta Principale (%s): %s" % (numeri[n], nome_mezzo, esc(desc_seg(principale, base))))
    n += 1
    if dopo:
        et = "Collegamento in Sicilia/Calabria" if andata else "Trasferimento finale"
        righe.append("    %s %s: %s" % (numeri[n], et, esc(" + ".join(desc_seg(s, base) for s in dopo))))
    fine = segs[-1]["arr"]
    righe.append("    🏁 <b>Arrivo a %s:</b> ore %s%s" % (
        "Milazzo" if andata else "Padova", hm(fine), (" (%s)" % fmt(fine)) if fine.date() != base else ""))
    if principale.get("link"):
        righe.append("    🔗 " + esc(f_link(principale["link"])))
    return righe


def sezioni_top(SOL):
    righe = []
    for direz, titolo in (("ANDATA", "🟢 <b>TOP 3 MIGLIORI ANDATE</b>"),
                          ("RITORNO", "🔵 <b>TOP 3 MIGLIORI RITORNI</b>")):
        righe.append(titolo)
        top = sorted(SOL[direz], key=lambda s: s["totale"])[:3]
        if not top:
            righe.append("Nessuna soluzione completa e verificata per queste date.")
            righe.append("")
        for i, s in enumerate(top, 1):
            righe += righe_compatte(direz, i, s)
            righe.append("")
    return righe


# ---------- simulazione (dati inventati, nessuna chiamata) ----------
# Testo reale di una pagina Giuntabus (struttura osservata nel log del 18/11/2026), usato per l'autotest.
ESEMPIO_GIUNTABUS = "\n".join([
    "Salta al contenuto", "IT", "Accedi", "Giuntabus Srl", "Indietro", "Catania Aeroporto", "Milazzo",
    "17/12/2026", "1 Passeggero", "Modifica ricerca", "ANDATA", "Abbonamenti su questa tratta",
    "slide 30 to 34 of 410", "Mar 15 dic", "€ 13,00", "Mer 16 dic", "€ 13,00", "Gio 17 dic", "€ 13,00",
    "11:00", "Catania", "Aeroporto - Terminal Arrivi", "Durata", "2h", "13:00", "Milazzo", "Porto",
    "Prezzo totale", "€ 13,00", "Posti disponibili", "Seleziona", "Info viaggio",
    "15:30", "Catania", "Aeroporto - Terminal Arrivi", "Durata", "2h", "17:30", "Milazzo", "Porto",
    "Prezzo totale", "€ 13,00", "Posti disponibili", "Seleziona", "Info viaggio"])


def test_giuntabus():
    try:
        import giuntabus
        corse = giuntabus.interpreta(ESEMPIO_GIUNTABUS, date(2026, 12, 17))
        return (len(corse) == 2
                and corse[0]["dep"] == datetime(2026, 12, 17, 11, 0)
                and corse[0]["arr"] == datetime(2026, 12, 17, 13, 0)
                and corse[0]["prezzo"] == 13.0
                and corse[0]["da"] == "Catania Aeroporto - Terminal Arrivi"
                and corse[0]["a"] == "Milazzo Porto"
                and corse[1]["dep"] == datetime(2026, 12, 17, 15, 30))
    except Exception as e:
        print("Autotest Giuntabus:", repr(e)[:100])
        return False


def percorri_finto(hops, giorno, avanti, riferimento):
    """Solo per l'autotest: treno inventato che rispetta esattamente il riferimento."""
    a, b = hops[0]
    if avanti:
        dep = riferimento
        arr = dep + timedelta(minutes=60)
    else:
        arr = riferimento
        dep = arr - timedelta(minutes=60)
    s = seg("treno", "Fittizio", "FITT-9", a, b, dep, arr, 9.99, None,
            [{"num": "FITT-9", "da": a, "a": b, "dep": dep, "arr": arr}])
    return [s]


def test_navetta(giorno):
    """Autotest della logica a frequenza (con percorri sostituito da percorri_finto)."""
    try:
        volo_dep = datetime.combine(giorno, ora_t(14, 0))
        c1, _ = collegamento_con_navetta("VRN", "ANDATA", volo_dep)
        ok1 = bool(c1) and c1[-1]["frequenza"] == 20 and c1[-1]["arr"] + timedelta(minutes=BUFFER_VOLO) <= volo_dep
        c2, _ = collegamento_con_navetta("VRN", "ANDATA", datetime.combine(giorno, ora_t(8, 0)))
        ok2 = c2 is None
        volo_arr = datetime.combine(giorno, ora_t(10, 0))
        c3, _ = collegamento_con_navetta("VRN", "RITORNO", volo_arr)
        ok3 = (bool(c3) and c3[0]["dep"] == volo_arr + timedelta(minutes=BUFFER_TRANSITO)
               and c3[1]["dep"] >= c3[0]["arr"] + timedelta(minutes=BUFFER_TRANSITO))
        c4, _ = collegamento_con_navetta("TSF", "ANDATA", volo_dep)
        ok4 = c4 is None
        return ok1 and ok2 and ok3 and ok4
    except Exception as e:
        print("Autotest navetta:", repr(e)[:100])
        return False


def simula():
    global percorri
    ora_ora = adesso()
    oggi = ora_ora.date()
    ga = oggi + timedelta(days=30)
    gr = oggi + timedelta(days=37)
    gr1 = gr + timedelta(days=1)

    def h(g, ore, minuti):
        return datetime.combine(g, ora_t(ore, minuti))

    def S(tipo, op, ident, da, a, dep, arr, prezzo, legs=None):
        return seg(tipo, op, ident, da, a, dep, arr, prezzo, "https://esempio.invalid/" + tipo, legs)

    def T(num, da, a, dep, arr):
        return {"num": num, "da": da, "a": a, "dep": dep, "arr": arr}

    def sol(*segs):
        return {"segmenti": list(segs), "totale": round(sum(s["prezzo"] for s in segs), 2), "tipo": "Fittizio"}

    bus_n = S("bus", "Bus Fittizio", "LINEA-FITT-N", "Padova Stanga via Venezia", "Venezia Marco Polo",
              h(ga, 6, 5), h(ga, 6, 45), 11.11)
    volo = S("volo", "Compagnia Fittizia", "XX 100", "Venezia Marco Polo", "Palermo Aeroporto",
             h(ga, 8, 15), h(ga, 9, 40), 22.22)
    volo["tratta_txt"] = "VCE → PMO"
    hop = S("treno", "Operatore Fittizio", "FITT-1 + FITT-2", "Palermo Aeroporto", "Milazzo",
            h(ga, 10, 45), h(ga, 14, 5), 4.44,
            [T("FITT-1", "Palermo Aeroporto", "Palermo Centrale", h(ga, 10, 45), h(ga, 11, 40)),
             T("FITT-2", "Palermo Centrale", "Milazzo", h(ga, 12, 20), h(ga, 14, 5))])
    andata_s = sol(bus_n, volo, hop)

    reg_r = S("treno", "Operatore Fittizio", "FITT-R1", "Milazzo", "Messina Centrale",
              h(gr, 17, 54), h(gr, 18, 27), 2.22,
              [T("FITT-R1", "Milazzo", "Messina Centrale", h(gr, 17, 54), h(gr, 18, 27))])
    bus_r = S("bus", "Bus Fittizio", "", "Messina", "Padova", h(gr, 20, 15), h(gr1, 14, 25), 44.44)
    ritorno_s = sol(reg_r, bus_r)

    # Autotest (dati inventati o testi reali di esempio)
    ok_valide = (valida_catena(andata_s["segmenti"], "ANDATA") is None
                 and valida_catena(ritorno_s["segmenti"], "RITORNO") is None)
    stretta = dict(hop)
    stretta["dep"], stretta["arr"] = h(ga, 9, 50), h(ga, 10, 30)
    senza_id = dict(volo)
    senza_id["id"] = ""
    geo = dict(hop)
    geo["da"] = "Catania Aeroporto"
    ko = (valida_catena([bus_n, volo, stretta], "ANDATA") is not None,
          valida_catena([bus_n, senza_id, hop], "ANDATA") is not None,
          valida_catena([bus_n, volo, geo], "ANDATA") is not None,
          valida_catena(ritorno_s["segmenti"], "ANDATA") is not None)
    ok_tz = (da_utc("2026-12-18T05:25:00.000Z") == datetime(2026, 12, 18, 6, 25)
             and da_utc("2027-07-15T05:25:00Z") == datetime(2027, 7, 15, 7, 25)
             and da_flightkey("FR~8597~ ~~VCE~12/17/2026 10:00~REG~12/17/2026 11:40~~")[0] == "FR 8597"
             and da_flightkey("FR~ 531~ ~~VCE~12/16/2026 07:25~PMO~12/16/2026 09:00~~")[0] == "FR 531")
    ok_gb = test_giuntabus()
    percorri_originale = percorri
    percorri = percorri_finto
    try:
        ok_nav = test_navetta(ga)
    finally:
        percorri = percorri_originale
    esito_test = "PASS" if (ok_valide and all(ko) and ok_tz and ok_gb and ok_nav) else \
        "FAIL (valide=%s, scarti attesi=%s, fusi orari e chiavi volo=%s, Giuntabus=%s, navetta=%s)" % (
            ok_valide, ko, ok_tz, ok_gb, ok_nav)

    corpo = ["<b>✈️ REPORT VIAGGI PORTA-A-PORTA (Padova ⇄ Milazzo)</b>",
             "Simulazione | Aggiornato: %s %s" % (fmt(ora_ora), hm(ora_ora)), ""]
    corpo += sezioni_top({"ANDATA": [andata_s], "RITORNO": [ritorno_s]})
    corpo += ["AUTOTEST (validatore, fusi orari, chiavi volo, Giuntabus, navetta a frequenza): " + esito_test]

    righe = ["<b>TEST – DATI NON REALI</b>",
             "Simulazione: nessuna chiamata a API o siti di trasporto.", ""]
    righe += ["[FITTIZIO] " + r if r.strip() else r for r in corpo]
    righe += ["", "<b>TEST – DATI NON REALI</b>: date, numeri, linee, importi e link sono inventati.",
              "Serve solo a verificare formattazione, validatore e letture di prova."]
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

    BUS.update(carica_bus())

    nome, a_da, a_a, r_da, r_a = evento
    domani = oggi + timedelta(days=1)
    finestre = (("ANDATA", max(d(a_da), domani), d(a_a)),
                ("RITORNO", max(d(r_da), domani), d(r_a)))

    SOL = {}
    for direz, ini, fin in finestre:
        sols = []
        if ini <= fin:
            giorni = [ini + timedelta(days=i) for i in range((fin - ini).days + 1)]
            giorni = [g for g in giorni if g.isoformat() not in bloccate]
            voli_l = []
            for nord in AEROPORTI_NORD:
                for sud in AEROPORTI_SUD:
                    voli_l += voli(direz, nord, sud, ini, fin, bloccate)
            sols += catene_voli(direz, voli_l)
            scelti = scegli(giorni, DATE_TRENO)
            sols += catene_treno_diretto(direz, scelti)
            sols += catene_flix(direz, scelti)
        SOL[direz] = sols

    righe = ["<b>✈️ REPORT VIAGGI PORTA-A-PORTA (Padova ⇄ Milazzo)</b>",
             "Evento: %s | Aggiornato: %s %s (ora italiana)" % (esc(nome), fmt(ora_ora), hm(ora_ora))]
    if REALE_MANUALE:
        righe.append("Esecuzione manuale in modalità reale: solo dati ottenuti dalle fonti.")
    if nota:
        righe.append(esc(nota))
    if bloccate:
        righe.append("Date escluse: " + esc(", ".join(fmt(d(x)) for x in sorted(bloccate))))
    righe.append("Soluzioni complete: andate %d, ritorni %d" % (len(SOL["ANDATA"]), len(SOL["RITORNO"])))
    righe.append("")
    righe += sezioni_top(SOL)

    righe.append("<b>RIEPILOGO CONTROLLI</b> (nel report compaiono solo soluzioni complete e verificate)")
    for cat, (tr, sc) in sorted(CONTI.items()):
        righe.append(esc("%s: %d trovate, %d scartate" % (cat, tr, sc)))
        for (c2, motivo), n in sorted(MOTIVI.items()):
            if c2 == cat:
                righe.append(esc("   %s: %d" % (motivo, n)))
    righe.append(esc("Fonti (ricerche riuscite): " + ", ".join(
        "%s %d/%d" % (n, f[0], f[1]) for n, f in FONTI.items())))
    if ESITI:
        righe.append(esc("Esiti ricerche: " + "; ".join(
            "%s %s x%d" % (f, e, n) for (f, e), n in sorted(ESITI.items()))))
    for (fonte, codice), e in sorted(ERRORI_FONTI.items()):
        righe.append(esc("ERRORE FONTE %s %s x%d (%s): %s" % (
            fonte, codice, e["n"], "; ".join(e["tratte"]), e["msg"])))
    for n, s in NON_LEGGIBILI.items():
        righe.append(esc("Dati non leggibili da %s, esempio: %s" % (n, s)))
    righe.append("I link portano alla pagina di ricerca del vettore, non alla singola corsa.")
    righe.append("Italo escluso dalle ricerche su Sicilia e sud. Itabus escluso (inaffidabile).")
    telegram("\n".join(righe))


main()
