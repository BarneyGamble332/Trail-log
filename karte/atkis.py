#!/usr/bin/env python3
"""Wahrscheinliche Forstwege: OSM-Pfade, die laut amtlichem Landschaftsmodell (ATKIS Basis-DLM Bayern)
befahrbare Wege sind.

1. Befahrbare Wege (Objektart AX_Fahrwegachse: Haupt-/Wirtschaftswege) für das Gebiet vom kennungsfreien
   WFS der Bayerischen Vermessungsverwaltung laden (OpenData, CC BY 4.0). Außerhalb Bayerns gibt es keine Daten.
2. Alle OSM-Pfade (path, footway, bridleway) lesen.
3. Ein Pfad gilt als wahrscheinlicher Forstweg, wenn mindestens 70 % seiner Länge näher als 8 m an einem
   amtlichen Fahrweg liegen. Diese Pfade werden als eigene Linien (GeoJSON) mit der OSM-Geometrie ausgegeben.

Aufruf: python3 karte/atkis.py region.osm.pbf kreis.geojson ausgabe.geojson
Benötigt: pip install osmium shapely numpy
"""
import concurrent.futures as cf
import json
import math
import re
import sys
import time
import urllib.request

import numpy as np
import osmium
import shapely
from shapely import STRtree

WFS = 'https://geoservices.bayern.de/wfs/v1/ogc_atkis_basisdlm.cgi'
NS = 'namespaces=xmlns(adv,http://www.adv-online.de/namespaces/adv/gid/7.1)'
STEP = 0.08          # Kachelgröße in Grad für die Abfrage
PAGE = 5000
NEAR_M = 8.0         # so nah muss der Pfad am amtlichen Fahrweg liegen
SHARE = 0.7          # Anteil der Pfadlänge
SAMPLE_M = 10.0

MEMBER = re.compile(r'<AX_Fahrwegachse\b.*?</AX_Fahrwegachse>', re.S)
POS = re.compile(r'<gml:posList[^>]*>([^<]+)</gml:posList>')
FUNK = re.compile(r'<funktion>(\d+)</funktion>')


def get(url, tries=5):
    for i in range(tries):
        try:
            with urllib.request.urlopen(url, timeout=180) as r:
                return r.read().decode('utf-8', 'replace')
        except Exception as e:  # Dienst ist manchmal langsam
            if i == tries - 1:
                raise
            print('   WFS erneut:', e, flush=True)
            time.sleep(10 * (i + 1))


def tile(s, w, n, e):
    """Alle Fahrwegachsen einer Kachel (lat/lon), mit Seitenweise-Abruf."""
    out = []
    start = 0
    while True:
        url = (f'{WFS}?service=WFS&version=2.0.0&request=GetFeature&typeNames=adv:AX_Fahrwegachse'
               f'&srsName=urn:ogc:def:crs:EPSG::4326&count={PAGE}&startIndex={start}&{NS}'
               f'&bbox={s},{w},{n},{e},urn:ogc:def:crs:EPSG::4326')
        xml = get(url)
        members = MEMBER.findall(xml)
        for m in members:
            f = FUNK.search(m)
            for p in POS.findall(m):
                v = [float(x) for x in p.split()]
                pts = [(v[i + 1], v[i]) for i in range(0, len(v) - 1, 2)]  # (lon, lat)
                if len(pts) >= 2:
                    out.append((pts, int(f.group(1)) if f else 0))
        if len(members) < PAGE:
            return out
        start += PAGE


class Paths(osmium.SimpleHandler):
    KEEP = {'path', 'footway', 'bridleway'}

    def __init__(self):
        super().__init__()
        self.ways = []  # (id, [(lon,lat)], tags)

    def way(self, w):
        if w.tags.get('highway') not in self.KEEP or w.tags.get('footway') in ('sidewalk', 'crossing'):
            return
        try:
            pts = [(n.lon, n.lat) for n in w.nodes if n.location.valid()]
        except osmium.InvalidLocationError:
            return
        if len(pts) >= 2:
            self.ways.append((w.id, pts, {k: w.tags.get(k) for k in ('highway', 'name') if w.tags.get(k)}))


def main():
    osm, kreis, dst = sys.argv[1:4]
    ring = json.load(open(kreis))['features'][0]['geometry']['coordinates'][0]
    lons = [p[0] for p in ring]; lats = [p[1] for p in ring]
    w0, e0, s0, n0 = min(lons), max(lons), min(lats), max(lats)
    lat0, lon0 = (s0 + n0) / 2, (w0 + e0) / 2
    kx, ky = math.cos(math.radians(lat0)) * 111320, 110540
    area = shapely.Polygon(ring)

    def m(pts):
        a = np.asarray(pts, dtype=float)
        return np.column_stack(((a[:, 0] - lon0) * kx, (a[:, 1] - lat0) * ky))

    # 1) amtliche Fahrwege laden
    tiles = []
    la = s0
    while la < n0:
        lo = w0
        while lo < e0:
            box = shapely.box(lo, la, lo + STEP, la + STEP)
            if box.intersects(area):
                tiles.append((round(la, 4), round(lo, 4), round(min(la + STEP, n0), 4), round(min(lo + STEP, e0), 4)))
            lo += STEP
        la += STEP
    print(f'   ATKIS: {len(tiles)} Kacheln laden', flush=True)
    lines, done = [], 0
    with cf.ThreadPoolExecutor(4) as ex:
        for res in ex.map(lambda t: tile(*t), tiles):
            lines.extend(res)
            done += 1
            if done % 25 == 0:
                print(f'   ATKIS: {done}/{len(tiles)} Kacheln, {len(lines)} Wege', flush=True)
    print(f'   ATKIS: {len(lines)} amtliche Fahrwege', flush=True)

    feats = []
    if lines:
        tree = STRtree([shapely.LineString(m(p)) for p, _ in lines])

        # 2) OSM-Pfade
        h = Paths()
        h.apply_file(osm, locations=True, idx='flex_mem')
        print(f'   OSM-Pfade: {len(h.ways)}', flush=True)

        # 3) Abgleich über Stichpunkte alle 10 m
        geoms, samples, owner = [], [], []
        for i, (_, pts, _) in enumerate(h.ways):
            g = shapely.LineString(m(pts))
            geoms.append(g)
            n = max(2, int(g.length // SAMPLE_M) + 1)
            if g.length < 30:
                continue
            d = np.linspace(0, g.length, n)
            samples.append(shapely.line_interpolate_point(g, d))
            owner.append(np.full(n, i))
        if samples:
            P = np.concatenate(samples); O = np.concatenate(owner)
            hit = np.zeros(len(P), bool)
            q = tree.query(P, predicate='dwithin', distance=NEAR_M)
            hit[np.unique(q[0])] = True
            tot = np.bincount(O, minlength=len(h.ways))
            near = np.bincount(O, weights=hit, minlength=len(h.ways))
            for i in np.nonzero((tot > 0) & (near >= SHARE * np.maximum(tot, 1)))[0]:
                wid, pts, tags = h.ways[i]
                feats.append({'type': 'Feature', 'properties': {'fw': 1, 'osm': wid, **tags},
                              'geometry': {'type': 'LineString', 'coordinates': [[round(x, 6), round(y, 6)] for x, y in pts]}})
    with open(dst, 'w') as f:
        json.dump({'type': 'FeatureCollection', 'features': feats}, f, ensure_ascii=False)
    print(f'   Wahrscheinliche Forstwege: {len(feats)} Pfade', flush=True)


if __name__ == '__main__':
    main()
