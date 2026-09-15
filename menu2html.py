#!/usr/bin/env python3
"""Bouwt het menubord uit de maandmenu-pdf van basisschoolhetpark.be.

De pdf staat elke maand op een ander adres, dus we zoeken de link eerst op de
pagina zelf. Het resultaat wordt in de HTML gebakken; de browser kiest daarna
zelf welke dag ze toont, elke minuut opnieuw.

Werkt de parser niet op een nieuwe pdf-opmaak, draai dan:

    python menu2html.py --dump

Dat toont de ruwe tekst en de tabellen zoals ze uit de pdf komen.
"""

from __future__ import annotations

import argparse
import datetime as dt
import json
import re
import sys
import statistics
import unicodedata
from pathlib import Path
from zoneinfo import ZoneInfo

import requests
from bs4 import BeautifulSoup

MENU_PAGINA = "https://www.basisschoolhetpark.be/maandmenu"
BASIS = "https://www.basisschoolhetpark.be"
SCHOOL_NAAM = "Basisschool Het Park"
WEBSITE = "www.basisschoolhetpark.be"

ROOT = Path(__file__).resolve().parent
CACHE = ROOT / "menu-cache.json"

TZ = ZoneInfo("Europe/Brussels")
TIMEOUT = 30
USER_AGENT = "menu2html/2.0 (schoolbord; +github actions)"

WEEKDAGEN = ["maandag", "dinsdag", "woensdag", "donderdag", "vrijdag",
             "zaterdag", "zondag"]
MAANDEN = {"januari": 1, "februari": 2, "maart": 3, "april": 4, "mei": 5,
           "juni": 6, "juli": 7, "augustus": 8, "september": 9,
           "oktober": 10, "november": 11, "december": 12}
MAAND_NAAM = {v: k for k, v in MAANDEN.items()}

# Regels die nooit een menu-item zijn.
RUIS = re.compile(
    r"^([*#]|menu|maandmenu|week|dessert|allergen|wijziging|voorbehoud|"
    r"de maaltijden|de ingredi|pagina|\d+\s*$|bron|tel|e-?mail|www\.|"
    r"https?://)", re.I)

# Cellen die alleen een allergenencode zijn: (1-9), (1,3,6,7), (11)
ALLERGEEN = re.compile(r"^\(\s*\d[\da-z,\-\s]*\)?$", re.I)
# Dezelfde code achteraan een gerechtnaam: "Kalfsburger (1-6-7)"
ALLERGEEN_ACHTER = re.compile(r"\s*\(\s*\d[\da-z,\-\s]*\)?\s*$", re.I)
# Alleen in de kopregel van een weekblok: 31/08/2026
RASTERDATUM = re.compile(r"^\s*(\d{1,2})/(\d{1,2})/(\d{4})\s*$")
WEEKDAG_CEL = re.compile(r"^\s*(?:" + "|".join(WEEKDAGEN) + r")\s*$", re.I)
VEGGIE = re.compile(r"vegetarisch|veggie|veggy|plantaardig", re.I)


# --------------------------------------------------------------------------- #
# 1. De pdf van deze maand terugvinden
# --------------------------------------------------------------------------- #

def zoek_pdf_links(pagina_url: str = MENU_PAGINA) -> list[tuple[str, str]]:
    """Geeft [(label, url)] van alle pdf-downloads op de maandmenupagina."""
    resp = requests.get(pagina_url, timeout=TIMEOUT,
                        headers={"User-Agent": USER_AGENT})
    resp.raise_for_status()
    soup = BeautifulSoup(resp.text, "html.parser")

    gevonden = []
    for a in soup.find_all("a", href=True):
        href = a["href"]
        if ".pdf" not in href.lower():
            continue
        if href.startswith("/"):
            href = BASIS + href
        label = " ".join(a.get_text(" ", strip=True).split())
        if not label:
            # De downloadknop heeft soms geen tekst; pak dan het label van de
            # link ernaast in hetzelfde lijstitem.
            blok = a.find_parent("li") or a.parent
            label = " ".join(blok.get_text(" ", strip=True).split())[:60]
        gevonden.append((label or href.rsplit("/", 1)[-1], href))

    # dubbele urls eruit, volgorde bewaren
    uniek, gezien = [], set()
    for label, url in gevonden:
        if url not in gezien:
            gezien.add(url)
            uniek.append((label, url))
    return uniek


def kies_pdf(links: list[tuple[str, str]], vandaag: dt.date) -> tuple[str, str]:
    """Kiest de pdf van deze maand. Staat die er niet, dan die van volgende
    maand, want eind van de maand hangt de school de nieuwe alvast op."""
    if not links:
        raise SystemExit("Geen pdf gevonden op de maandmenupagina.")

    deze = MAAND_NAAM[vandaag.month]
    volgende = MAAND_NAAM[vandaag.month % 12 + 1]
    for wens in (deze, volgende):
        for label, url in links:
            if wens in label.lower():
                return label, url

    print(f"!! geen maandnaam herkend in {[l for l, _ in links]}, "
          f"eerste genomen", file=sys.stderr)
    return links[0]


# --------------------------------------------------------------------------- #
# 2. De pdf uitlezen
# --------------------------------------------------------------------------- #

def pdf_tekst_en_tabellen(pad: Path) -> tuple[str, list[list[list]]]:
    import pdfplumber
    stukken, tabellen = [], []
    with pdfplumber.open(pad) as pdf:
        for bladzijde in pdf.pages:
            # layout=True houdt de horizontale witruimte aan. Zonder dat worden
            # kolommen van een raster zonder lijnen samengeplakt tot één zin.
            try:
                tekst = bladzijde.extract_text(layout=True) or ""
            except Exception:                              # noqa: BLE001
                tekst = bladzijde.extract_text() or ""
            stukken.append(tekst)
            for tabel in bladzijde.extract_tables() or []:
                tabellen.append(tabel)
    return "\n".join(stukken), tabellen


def _schoon(cel) -> str:
    if not cel:
        return ""
    tekst = unicodedata.normalize("NFKC", str(cel))
    return " ".join(tekst.replace("\n", " ").split())


_DATUM_LANG = re.compile(
    r"(?:(?:" + "|".join(WEEKDAGEN) + r")\s+)?(\d{1,2})\s+("
    + "|".join(MAANDEN) + r")", re.I)
_DATUM_KORT = re.compile(r"\b(\d{1,2})[/\-.](\d{1,2})(?:[/\-.](\d{2,4}))?\b")
_WEEKDAG_ALLEEN = re.compile(r"^\s*(?:" + "|".join(WEEKDAGEN) + r")\b", re.I)


def lees_datum(tekst: str, jaar: int) -> dt.date | None:
    """Herkent 'maandag 1 september', '1 september', '01/09' en '1-9-2026'."""
    m = _DATUM_LANG.search(tekst)
    if m:
        try:
            return dt.date(jaar, MAANDEN[m.group(2).lower()], int(m.group(1)))
        except ValueError:
            return None
    m = _DATUM_KORT.search(tekst)
    if m:
        dag, maand = int(m.group(1)), int(m.group(2))
        jr = int(m.group(3) or jaar)
        if jr < 100:
            jr += 2000
        try:
            return dt.date(jr, maand, dag)
        except ValueError:
            return None
    return None


def _ontdubbel(items: list[str]) -> list[str]:
    gezien, uit = set(), []
    for i in items:
        sleutel = i.lower()
        if sleutel and sleutel not in gezien:
            gezien.add(sleutel)
            uit.append(i)
    return uit


def cellenrijen(pad: Path) -> list[list[list[tuple[str, float, float]]]]:
    """Leest de pdf als een raster van cellen op basis van de x-coördinaten
    van de woorden. Nodig omdat veel menu-pdf's een tabel tekenen zonder
    lijnen én zonder brede tussenruimte: dan vindt extract_tables() niets en
    plakt extract_text() de kolommen aan elkaar."""
    import pdfplumber
    import statistics

    paginas: list[list[list[tuple[str, float, float]]]] = []
    with pdfplumber.open(pad) as pdf:
        for bladzijde in pdf.pages:
            rijen: list[list[tuple[str, float, float]]] = []
            woorden = bladzijde.extract_words() or []
            if not woorden:
                continue

            # Regels clusteren op verticale positie. Afronden op een vast
            # raster gaat mis zodra twee cellen van dezelfde rij een halve
            # punt verschillen: die belanden dan in twee aparte regels.
            hoogte = statistics.median(
                [w["bottom"] - w["top"] for w in woorden]) or 8.0
            speling = hoogte * 0.6

            regels: list[list] = []
            for w in sorted(woorden, key=lambda w: (w["top"], w["x0"])):
                if regels and abs(w["top"] - regels[-1][0]["top"]) <= speling:
                    regels[-1].append(w)
                else:
                    regels.append([w])

            # De drempel voor "dit is een kolomgrens" wordt over de hele
            # bladzijde bepaald, niet per regel. Op een regel die grotendeels
            # uit kolomgrenzen bestaat is de mediaan zelf al een kolomgrens,
            # en dan wordt er niets meer gesplitst.
            alle_gaten = []
            for groep in regels:
                ws = sorted(groep, key=lambda w: w["x0"])
                alle_gaten += [ws[i + 1]["x0"] - ws[i]["x1"]
                               for i in range(len(ws) - 1)]
            if len(alle_gaten) >= 4:
                spatie = statistics.quantiles(alle_gaten, n=4)[0]
            elif alle_gaten:
                spatie = min(alle_gaten)
            else:
                spatie = 2.0
            drempel = max(4.0, 2.5 * spatie)

            for groep in regels:
                ws = sorted(groep, key=lambda w: w["x0"])
                if not ws:
                    continue
                gaten = [ws[i + 1]["x0"] - ws[i]["x1"] for i in range(len(ws) - 1)]

                cellen, huidig = [], [ws[0]]
                for i, gat in enumerate(gaten):
                    if gat > drempel:
                        cellen.append(huidig)
                        huidig = []
                    huidig.append(ws[i + 1])
                cellen.append(huidig)

                rij = []
                for groep_w in cellen:
                    tekst = " ".join(w["text"] for w in groep_w).strip()
                    if tekst:
                        rij.append((tekst, groep_w[0]["x0"], groep_w[-1]["x1"]))
                if rij:
                    rijen.append(rij)
            paginas.append(rijen)
    return paginas


def woordrijen(pad: Path) -> list[list[str]]:
    """Alleen de tekst, voor de eenvoudige parsers en voor --dump."""
    return [[c[0] for c in rij] for rijen in cellenrijen(pad) for rij in rijen]


def parse_woordrijen(rijen: list[list[str]], jaar: int) -> list[dict]:
    """Zet de cellenrijen om in dagen: een rij die met een datum begint start
    een nieuwe dag, de rest van de rij zijn de gerechten."""
    dagen: list[dict] = []
    huidig = None
    for cellen in rijen:
        if not cellen:
            continue
        datum = lees_datum(cellen[0], jaar)
        if datum:
            huidig = {"datum": datum.isoformat(), "items": []}
            dagen.append(huidig)
            rest = cellen[1:]
        elif huidig is not None:
            rest = cellen
        else:
            continue
        for cel in rest:
            if cel and not RUIS.match(cel):
                huidig["items"].append(cel)

    for d in dagen:
        d["items"] = _ontdubbel(d["items"])
    return [d for d in dagen if d["items"]]


def parse_weekraster(paginas) -> list[dict]:
    """Voor het maandmenu van de cateraar: per week een blok met de dagen als
    kolommen en de gangen als rijen (Soep, Eiwitcomponent, Saus, Groenten,
    Zetmeel, Vegetarisch).

    De kolommen zijn niet op index te herkennen: een dag zonder soep levert
    gewoon een cel minder op, zodat elke rij een ander aantal cellen heeft.
    Daarom wordt elke cel toegekend aan de dag waarvan het midden van de
    datumkop er het dichtst bij ligt."""
    dagen: dict[dt.date, dict[str, list[str]]] = {}

    for rijen in paginas:
        kolommen: list[tuple[float, dt.date]] = []
        grens_links = 0.0

        for cellen in rijen:
            # 1. Kopregel van een weekblok?
            datums = [(t, (x0 + x1) / 2) for t, x0, x1 in cellen
                      if RASTERDATUM.match(t)]
            if len(datums) >= 2:
                kolommen = []
                for tekst, midden in datums:
                    d, m, j = RASTERDATUM.match(tekst).groups()
                    try:
                        kolommen.append((midden, dt.date(int(j), int(m), int(d))))
                    except ValueError:
                        pass
                kolommen.sort()
                if len(kolommen) >= 2:
                    afstand = statistics.median(
                        [kolommen[i + 1][0] - kolommen[i][0]
                         for i in range(len(kolommen) - 1)])
                    grens_links = kolommen[0][0] - afstand / 2
                for _, datum in kolommen:
                    dagen.setdefault(datum, {"items": [], "veggie": []})
                continue

            if not kolommen:
                continue
            if all(WEEKDAG_CEL.match(t) for t, _, _ in cellen):
                continue

            # De allergenenlegende en de voetnoten staan onderaan het blok en
            # lopen over de volle breedte. Alles daarna hoort bij geen enkele
            # dag meer, dus we sluiten het weekblok hier af.
            if any(RUIS.match(t) for t, _, _ in cellen):
                kolommen = []
                continue

            # 2. Het label van de gang staat links van de eerste dagkolom.
            label = ""
            inhoud = []
            for tekst, x0, x1 in cellen:
                if (x0 + x1) / 2 < grens_links:
                    label = tekst
                else:
                    inhoud.append((tekst, (x0 + x1) / 2))

            if RUIS.match(label) or (not label and inhoud
                                     and RUIS.match(inhoud[0][0])):
                continue

            veggie = bool(VEGGIE.search(label))

            # 3. Elke cel bij de dichtstbijzijnde dagkolom leggen.
            for tekst, midden in inhoud:
                if ALLERGEEN.match(tekst):
                    continue
                schoon = ALLERGEEN_ACHTER.sub("", tekst).strip(" -:\u2013")
                if not schoon or RUIS.match(schoon):
                    continue
                _, datum = min(kolommen, key=lambda k: abs(k[0] - midden))
                sleutel = "veggie" if veggie else "items"
                dagen[datum][sleutel].append(schoon)

    uit = []
    for datum in sorted(dagen):
        items = _ontdubbel(dagen[datum]["items"])
        veggie = _ontdubbel(dagen[datum]["veggie"])
        if items or veggie:
            uit.append({"datum": datum.isoformat(), "items": items,
                        "veggie": veggie})
    return uit


def parse_tabellen(tabellen, jaar: int) -> list[dict]:
    """Veel cateraars leveren een raster: één dag per rij of per kolom."""
    dagen: dict[dt.date, list[str]] = {}

    for tabel in tabellen:
        for rij in tabel:                      # dag per rij
            cellen = [_schoon(c) for c in rij]
            if not any(cellen):
                continue
            datum = lees_datum(cellen[0], jaar)
            if datum:
                items = [c for c in cellen[1:] if c and not RUIS.match(c)]
                if items:
                    dagen.setdefault(datum, []).extend(items)

        if tabel and tabel[0]:                 # dag per kolom
            for kol in range(len(tabel[0])):
                datum = lees_datum(_schoon(tabel[0][kol]), jaar)
                if not datum:
                    continue
                items = []
                for rij in tabel[1:]:
                    if kol < len(rij):
                        cel = _schoon(rij[kol])
                        if cel and not RUIS.match(cel):
                            items.append(cel)
                if items:
                    dagen.setdefault(datum, []).extend(items)

    return [{"datum": d.isoformat(), "items": _ontdubbel(v)}
            for d, v in sorted(dagen.items())]


_START_DATUM = re.compile(
    r"^\s*(?:(?:" + "|".join(WEEKDAGEN) + r")\b\s*)?\d{1,2}[\s/\-.]", re.I)


def _cellen(rauw: str) -> list[str]:
    """Splitst een regel op kolommen. In layout-modus houdt pdfplumber de
    horizontale witruimte aan, dus twee of meer spaties is een kolomgrens."""
    stukken = re.split(r"\s{2,}|\s*\|\s*|\s+[\u2022\u00b7]\s+", rauw.strip())
    return [s.strip(" -:\u2013\u2014") for s in stukken if s.strip()]


def parse_tekst(tekst: str, jaar: int) -> list[dict]:
    """Vangnet en tegelijk de beste aanpak voor rasters zonder tabellijnen:
    loopt de regels af en begint een nieuwe dag zodra er een datum vooraan
    staat. De rest van diezelfde regel zijn de gerechten van die dag."""
    dagen: list[dict] = []
    huidig = None

    for rauw in tekst.splitlines():
        rauw = rauw.rstrip()
        compact = " ".join(rauw.split())
        if not compact:
            continue

        datum = lees_datum(compact, jaar)
        begint_met_datum = bool(
            _WEEKDAG_ALLEEN.match(compact) or _START_DATUM.match(compact))

        if datum and begint_met_datum:
            huidig = {"datum": datum.isoformat(), "items": []}
            dagen.append(huidig)
            for cel in _cellen(rauw):
                if lees_datum(cel, jaar) and len(cel) < 30:
                    continue                      # dit is de datumkolom zelf
                if not RUIS.match(cel):
                    huidig["items"].append(cel)
            continue

        if huidig is not None:
            for cel in _cellen(rauw):
                if not RUIS.match(cel):
                    huidig["items"].append(cel)

    for d in dagen:
        d["items"] = _ontdubbel(d["items"])
    return [d for d in dagen if d["items"]]


def splits_veggie(dagen: list[dict]) -> list[dict]:
    """Zet alles na een 'vegetarisch'-regel apart."""
    for dag in dagen:
        gewoon, veggie, in_veggie = [], [], False
        for item in dag["items"]:
            if VEGGIE.search(item):
                in_veggie = True
                rest = VEGGIE.sub("", item).strip(" -:\u2013()")
                if rest:
                    veggie.append(rest)
                continue
            (veggie if in_veggie else gewoon).append(item)
        dag["items"], dag["veggie"] = gewoon, veggie
    return dagen


# --------------------------------------------------------------------------- #
# 3. De pagina
# --------------------------------------------------------------------------- #

def bouw_html(dagen: list[dict], vandaag: dt.date, bron: str) -> str:
    payload = json.dumps(
        {"bijgewerkt_op": vandaag.isoformat(), "bron": bron, "dagen": dagen},
        ensure_ascii=False,
    ).replace("<", "\\u003c")
    bijgewerkt = f"{vandaag.day} {MAAND_NAAM[vandaag.month]} {vandaag.year}"

    return f"""<!DOCTYPE html>
<html lang="nl">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=1920, initial-scale=1">
<title>Menu {SCHOOL_NAAM}</title>
<link rel="preconnect" href="https://api.fontshare.com" crossorigin>
<link rel="stylesheet"
      href="https://api.fontshare.com/v2/css?f[]=general-sans@400,500,600,700&display=swap">
<link rel="stylesheet" href="styles.css">
</head>
<body>

<div id="scherm">
  <header>
    <div>
      <div class="merk">{SCHOOL_NAAM}</div>
      <div class="titel">Wat eten we vandaag</div>
    </div>
    <div class="vandaag" id="vandaag"></div>
  </header>

  <main id="inhoud">
    <noscript><p class="leeg">Zet JavaScript aan om het menu te tonen.</p></noscript>
  </main>

  <footer>
    <span>Volledig maandmenu op {WEBSITE}</span>
    <span class="dim">Bijgewerkt op {bijgewerkt}</span>
  </footer>
</div>

<script>
const DATA = {payload};

// Hoeveel dagen er rechts naast de maaltijd van vandaag passen.
const MAX_VOORUIT = 3;
const HERLAAD_MS = 30 * 60 * 1000;

const WEEKDAGEN = ["zondag", "maandag", "dinsdag", "woensdag", "donderdag",
                   "vrijdag", "zaterdag"];
const MAANDEN = ["januari", "februari", "maart", "april", "mei", "juni", "juli",
                 "augustus", "september", "oktober", "november", "december"];

function alsDatum(iso) {{
  const [j, m, d] = iso.split("-").map(Number);
  return new Date(j, m - 1, d);
}}
function dagVan(d) {{ return new Date(d.getFullYear(), d.getMonth(), d.getDate()); }}
function esc(s) {{
  return String(s).replace(/[&<>"]/g, c =>
    ({{ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;" }}[c]));
}}
function langeDatum(d) {{
  return WEEKDAGEN[d.getDay()] + " " + d.getDate() + " " + MAANDEN[d.getMonth()];
}}
function korteDatum(d) {{
  return WEEKDAGEN[d.getDay()] + " " + d.getDate() + " " +
         MAANDEN[d.getMonth()].slice(0, 3);
}}

function gerechtenGroot(dag) {{
  let h = '<ul class="groot">';
  dag.items.forEach(i => {{ h += '<li>' + esc(i) + '</li>'; }});
  h += '</ul>';
  if (dag.veggie && dag.veggie.length) {{
    h += '<p class="veggiekop">Vegetarisch: ' +
         esc(dag.veggie.join(", ")) + '</p>';
  }}
  return h;
}}

// De dagen erna staan als één doorlopende regel. Een volledige lijst per dag
// past niet naast het menu van vandaag, en voor vooruitkijken volstaat de
// grote lijn.
function gerechtenKlein(dag) {{
  let h = '<p class="kort">' + esc(dag.items.join(" \u00b7 ")) + '</p>';
  if (dag.veggie && dag.veggie.length) {{
    h += '<p class="kortveggie">Veggie: ' + esc(dag.veggie.join(", ")) + '</p>';
  }}
  return h;
}}

function teken() {{
  const nu = new Date();
  const vandaag = dagVan(nu);
  document.getElementById("vandaag").textContent = langeDatum(nu);

  const komend = DATA.dagen.filter(d => alsDatum(d.datum) >= vandaag);
  const inhoud = document.getElementById("inhoud");

  if (komend.length === 0) {{
    inhoud.innerHTML =
      '<p class="leeg">Geen menu beschikbaar. Kijk op {WEBSITE}</p>';
    return;
  }}

  const eerste = komend[0];
  const eersteDatum = alsDatum(eerste.datum);
  const isVandaag = eersteDatum.getTime() === vandaag.getTime();

  let html = '<section id="hoofd">' +
    '<p class="aanloop">' + (isVandaag ? "Vandaag" : "Volgende maaltijd") + '</p>' +
    '<p class="dagnaam">' + esc(langeDatum(eersteDatum)) + '</p>' +
    gerechtenGroot(eerste) +
    '</section>';

  const rest = komend.slice(1, 1 + MAX_VOORUIT);
  html += '<section id="later">';
  if (rest.length) {{
    html += '<h2>De volgende dagen</h2><div class="rijen">';
    rest.forEach(d => {{
      html += '<article><h3>' + esc(korteDatum(alsDatum(d.datum))) + '</h3>' +
              '<div class="gerechten">' + gerechtenKlein(d) +
              '</div></article>';
    }});
    html += '</div>';
  }}
  html += '</section>';

  inhoud.innerHTML = html;
}}

// Een scherm dat maandenlang hetzelfde toont brandt in. Elke vijf minuten
// een paar pixels opschuiven is genoeg om dat te voorkomen.
const SCHUIF = [[0,0],[2,1],[3,0],[2,-1],[0,-2],[-2,-1],[-3,0],[-2,1]];
let schuifIndex = 0;
function pixelShift() {{
  const [x, y] = SCHUIF[schuifIndex % SCHUIF.length];
  document.getElementById("scherm").style.transform =
    "translate(" + x + "px," + y + "px)";
  schuifIndex++;
}}

teken();
pixelShift();
setInterval(teken, 60 * 1000);
setInterval(pixelShift, 5 * 60 * 1000);
setTimeout(() => location.reload(), HERLAAD_MS);
</script>

</body>
</html>
"""


# --------------------------------------------------------------------------- #
# Main
# --------------------------------------------------------------------------- #

def main() -> int:
    p = argparse.ArgumentParser()
    p.add_argument("--pdf", help="lokale pdf i.p.v. downloaden")
    p.add_argument("--dump", action="store_true",
                   help="toon de ruwe tekst en tabellen uit de pdf en stop")
    p.add_argument("--vandaag", help="ISO-datum, handig om te testen")
    args = p.parse_args()

    vandaag = (dt.date.fromisoformat(args.vandaag) if args.vandaag
               else dt.datetime.now(TZ).date())

    if args.pdf:
        pad, bron = Path(args.pdf), Path(args.pdf).name
    else:
        links = zoek_pdf_links()
        print(f"{len(links)} pdf-link(s) gevonden:")
        for label, url in links:
            print(f"  {label}  ->  {url}")
        bron, url = kies_pdf(links, vandaag)
        print(f"Gekozen: {bron}")
        pad = ROOT / "maandmenu.pdf"
        r = requests.get(url, timeout=TIMEOUT, headers={"User-Agent": USER_AGENT})
        r.raise_for_status()
        pad.write_bytes(r.content)
        print(f"{len(r.content)} bytes gedownload")

    tekst, tabellen = pdf_tekst_en_tabellen(pad)

    if args.dump:
        print("\n===== RUWE TEKST =====")
        print(tekst)
        print("\n===== ALS WEEKRASTER =====")
        for dag in parse_weekraster(cellenrijen(pad)):
            print(f"  {dag['datum']}: {' | '.join(dag['items'])}")
            if dag["veggie"]:
                print(f"       veggie: {' | '.join(dag['veggie'])}")
        print("\n===== CELLEN OP WOORDPOSITIE =====")
        for cellen in woordrijen(pad)[:40]:
            print("  | " + " | ".join(cellen))
        print(f"\n===== {len(tabellen)} TABEL(LEN) MET LIJNEN =====")
        for n, tabel in enumerate(tabellen, 1):
            print(f"--- tabel {n}: {len(tabel)} rijen ---")
            for rij in tabel[:30]:
                print("  | " + " | ".join(_schoon(c) for c in rij))
        return 0

    jaar = vandaag.year
    if "januari" in bron.lower() and vandaag.month == 12:
        jaar += 1

    # 1. Het weekraster van de cateraar: dagen als kolommen, gangen als rijen.
    dagen = parse_weekraster(cellenrijen(pad))
    hoe = "weekraster"

    # 2 t.e.m. 4: vangnetten voor een andere opmaak.
    if len(dagen) < 3:
        dagen = splits_veggie(parse_tabellen(tabellen, jaar))
        hoe = "tabellijnen"
    if len(dagen) < 3:
        dagen = splits_veggie(parse_woordrijen(woordrijen(pad), jaar))
        hoe = "woordposities"
    if len(dagen) < 3:
        dagen = splits_veggie(parse_tekst(tekst, jaar))
        hoe = "tekstregels"

    print(f"{len(dagen)} dag(en) herkend via {hoe}")
    for d in dagen[:8]:
        print(f"  {d['datum']}: {' | '.join(d['items'])[:70]}")

    if len(dagen) < 3:
        if CACHE.exists():
            print("!! te weinig herkend, cache gebruikt", file=sys.stderr)
            dagen = json.loads(CACHE.read_text(encoding="utf-8"))["dagen"]
        else:
            raise SystemExit(
                "Te weinig dagen herkend en geen cache. Draai --dump en pas "
                "de parser aan; het bord blijft liever staan dan leeg.")
    else:
        CACHE.write_text(json.dumps({"bron": bron, "dagen": dagen},
                                    indent=2, ensure_ascii=False) + "\n",
                         encoding="utf-8")

    (ROOT / "index.html").write_text(bouw_html(dagen, vandaag, bron),
                                     encoding="utf-8")
    print("index.html geschreven")
    return 0


if __name__ == "__main__":
    sys.exit(main())
