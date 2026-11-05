#!/usr/bin/env python3
"""Baut die Offline-Karten aus karte/regionen.json.

Pro Region:
  1. OSM-Daten von Geofabrik laden und auf den Kreis zuschneiden (osmium)
  2. Höhenmodell (Copernicus DEM, 30 m) laden und Höhenlinien erzeugen (GDAL)
  3. Vektorkacheln mit Planetiler und karte/schema.yml bauen (PMTiles)
  4. Ergebnis in 48-MB-Teile schneiden und eine Beschreibung <id>.json schreiben

Aufruf: python3 karte/bauen.py [region-id ...]
Benötigt: osmium, gdal (gdalbuildvrt, gdalwarp, gdal_contour, ogr2ogr), java 21, planetiler.jar
"""
import datetime
import glob
import json
import math
import os
import shutil
import struct
import subprocess
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
WORK = os.path.join(ROOT, 'work')
OUT = os.path.join(ROOT, 'out')
DL = os.path.join(WORK, 'download')
PART = 48 * 1024 * 1024
JAR = os.environ.get('PLANETILER_JAR', os.path.join(WORK, 'planetiler.jar'))
XMX = os.environ.get('PLANETILER_XMX', '6g')


def sh(*args):
    print('+', ' '.join(args), flush=True)
    subprocess.run(args, check=True)


def fetch(url, dest, optional=False):
    if os.path.exists(dest) and os.path.getsize(dest) > 0:
        return True
    tmp = dest + '.tmp'
    r = subprocess.run(['curl', '-fL', '--retry', '5', '--retry-delay', '10', '-sS', '-o', tmp, url])
    if r.returncode != 0:
        if os.path.exists(tmp):
            os.remove(tmp)
        if optional:
            print('  (nicht vorhanden)', url, flush=True)
            return False
        raise SystemExit('Download fehlgeschlagen: ' + url)
    os.replace(tmp, dest)
    return True


def circle(lat0, lon0, radius_km, n=180):
    """Kreis als Polygon (lon, lat). Für 100 km reicht die Näherung über den Breitengrad."""
    pts = []
    k_lat = radius_km / 111.32
    k_lon = radius_km / (111.32 * math.cos(math.radians(lat0)))
    for i in range(n):
        t = 2 * math.pi * i / n
        pts.append([round(lon0 + k_lon * math.sin(t), 6), round(lat0 + k_lat * math.cos(t), 6)])
    pts.append(pts[0])
    return pts


def pmtiles_header(path):
    with open(path, 'rb') as f:
        h = f.read(127)
    if h[:7] != b'PMTiles' or h[7] != 3:
        raise SystemExit('Keine gültige PMTiles-Datei: ' + path)
    min_zoom, max_zoom = h[100], h[101]
    w, s, e, n = struct.unpack('<iiii', h[102:118])
    return {'minzoom': min_zoom, 'maxzoom': max_zoom,
            'bounds': [w / 1e7, s / 1e7, e / 1e7, n / 1e7]}


def build(region):
    rid = region['id']
    lat0, lon0 = region['center']
    radius = region['radius_km']
    rdir = os.path.join(WORK, rid)
    os.makedirs(rdir, exist_ok=True)
    os.makedirs(DL, exist_ok=True)
    os.makedirs(OUT, exist_ok=True)

    ring = circle(lat0, lon0, radius)
    w = min(p[0] for p in ring); e = max(p[0] for p in ring)
    s = min(p[1] for p in ring); n = max(p[1] for p in ring)
    poly = os.path.join(rdir, 'kreis.geojson')
    with open(poly, 'w') as f:
        json.dump({'type': 'FeatureCollection', 'features': [{'type': 'Feature', 'properties': {},
                   'geometry': {'type': 'Polygon', 'coordinates': [ring]}}]}, f)
    print(f'== {rid}: Kreis {radius} km um {lat0},{lon0}  bbox {w:.3f},{s:.3f},{e:.3f},{n:.3f}', flush=True)

    # 1) OSM
    parts = []
    for i, area in enumerate(region['osm']):
        src = os.path.join(DL, area.replace('/', '_') + '.osm.pbf')
        fetch(f'https://download.geofabrik.de/{area}-latest.osm.pbf', src)
        cut = os.path.join(rdir, f'osm_{i}.osm.pbf')
        sh('osmium', 'extract', '-p', poly, '-s', 'smart', src, '-o', cut, '--overwrite')
        parts.append(cut)
    osm = os.path.join(rdir, 'region.osm.pbf')
    if len(parts) == 1:
        shutil.copy(parts[0], osm)
    else:
        sh('osmium', 'merge', *parts, '-o', osm, '--overwrite')
    print(f'   OSM: {os.path.getsize(osm) / 1e6:.0f} MB', flush=True)

    # 2) Höhenlinien
    tifs = []
    for la in range(math.floor(s), math.floor(n) + 1):
        for lo in range(math.floor(w), math.floor(e) + 1):
            ns = 'N' if la >= 0 else 'S'
            ew = 'E' if lo >= 0 else 'W'
            name = f'Copernicus_DSM_COG_10_{ns}{abs(la):02d}_00_{ew}{abs(lo):03d}_00_DEM'
            dest = os.path.join(DL, name + '.tif')
            if fetch(f'https://copernicus-dem-30m.s3.amazonaws.com/{name}/{name}.tif', dest, optional=True):
                tifs.append(dest)
    if not tifs:
        raise SystemExit('Keine Höhendaten gefunden')
    vrt = os.path.join(rdir, 'dem.vrt')
    sh('gdalbuildvrt', '-overwrite', vrt, *tifs)
    dem = os.path.join(rdir, 'dem_utm.tif')
    # In Meter-Koordinaten (UTM 32N) umrechnen, leicht glätten durch kubische Interpolation auf 20 m
    sh('gdalwarp', '-overwrite', '-t_srs', 'EPSG:32632', '-tr', '20', '20', '-r', 'cubic',
       '-te_srs', 'EPSG:4326', '-te', str(w), str(s), str(e), str(n),
       '-co', 'COMPRESS=DEFLATE', '-co', 'TILED=YES', '-multi', '-wo', 'NUM_THREADS=ALL_CPUS', vrt, dem)
    raw = os.path.join(rdir, 'contours_raw.gpkg')
    if os.path.exists(raw):
        os.remove(raw)
    sh('gdal_contour', '-a', 'ele', '-i', '20', '-f', 'GPKG', dem, raw)
    outs = {}
    for kind, cond in (('major', '= 0'), ('minor', '<> 0')):
        dst = os.path.join(rdir, f'contours_{kind}.gpkg')
        if os.path.exists(dst):
            os.remove(dst)
        sh('ogr2ogr', '-f', 'GPKG', dst, raw, '-dialect', 'SQLite', '-sql',
           f'SELECT geom, CAST(ele AS INTEGER) AS ele FROM contour WHERE CAST(ele AS INTEGER) % 100 {cond}',
           '-simplify', '4', '-t_srs', 'EPSG:4326', '-clipdst', poly,
           '-nln', 'contour', '-nlt', 'PROMOTE_TO_MULTI')
        outs[kind] = dst
        print(f'   Höhenlinien {kind}: {os.path.getsize(dst) / 1e6:.0f} MB', flush=True)

    # 3) Vektorkacheln
    pm = os.path.join(rdir, rid + '.pmtiles')
    sh('java', f'-Xmx{XMX}', '-jar', JAR, 'generate-custom',
       '--schema=' + os.path.join(ROOT, 'karte', 'schema.yml'),
       '--osm_path=' + osm,
       '--contours_major=' + outs['major'],
       '--contours_minor=' + outs['minor'],
       '--output=' + pm,
       f'--bounds={w},{s},{e},{n}',
       '--tmpdir=' + os.path.join(rdir, 'tmp'),
       '--maxzoom=14', '--force')
    head = pmtiles_header(pm)
    size = os.path.getsize(pm)
    print(f'   PMTiles: {size / 1e6:.0f} MB, Zoom {head["minzoom"]}-{head["maxzoom"]}', flush=True)

    # 4) Teile + Beschreibung
    for old in glob.glob(os.path.join(OUT, rid + '-*.bin')):
        os.remove(old)
    files = []
    with open(pm, 'rb') as f:
        i = 0
        while True:
            chunk = f.read(PART)
            if not chunk:
                break
            name = f'{rid}-{i:03d}.bin'
            with open(os.path.join(OUT, name), 'wb') as o:
                o.write(chunk)
            files.append(name)
            i += 1
    manifest = {
        'id': rid,
        'name': region['name'],
        'beschreibung': region.get('beschreibung', ''),
        'center': region['center'],
        'radius_km': radius,
        'bbox': [round(w, 4), round(s, 4), round(e, 4), round(n, 4)],
        'size': size,
        'part_size': PART,
        'files': files,
        'minzoom': head['minzoom'],
        'maxzoom': head['maxzoom'],
        'built': datetime.datetime.now(datetime.timezone.utc).strftime('%Y-%m-%dT%H:%M:%SZ'),
        'version': datetime.datetime.now(datetime.timezone.utc).strftime('%Y%m%d%H%M'),
    }
    with open(os.path.join(OUT, rid + '.json'), 'w') as f:
        json.dump(manifest, f, ensure_ascii=False, indent=1)
    print(f'   fertig: {len(files)} Teile', flush=True)


def main():
    with open(os.path.join(ROOT, 'karte', 'regionen.json')) as f:
        regions = json.load(f)
    want = set(sys.argv[1:])
    for r in regions:
        if not want or r['id'] in want:
            build(r)


if __name__ == '__main__':
    main()
