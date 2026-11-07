#!/usr/bin/env python3
"""Radrouten (Fernradwege, Bayernnetz für Radler, regionale Radrouten) aus OSM als Linien.

OSM speichert diese Routen als Relationen (route=bicycle) aus vielen Wegstücken. Planetiler liest
Relationen in YAML-Schemas nicht, deshalb werden die Wegstücke hier zu Linien mit Namen zusammengesetzt
und als GeoJSON geschrieben. Ein Wegstück, das zu mehreren Routen gehört, bekommt die wichtigste
(international > national > regional) und alle Namen.

Aufruf: python3 karte/radrouten.py region.osm.pbf radrouten.geojson
Benötigt: pip install osmium
"""
import json
import sys

import osmium

RANK = {'icn': 3, 'ncn': 2, 'rcn': 1}


class Rel(osmium.SimpleHandler):
    def __init__(self):
        super().__init__()
        self.ways = {}  # way id -> (rang, netz, [namen])

    def relation(self, r):
        t = r.tags
        if t.get('type') != 'route' or t.get('route') not in ('bicycle', 'mtb'):
            return
        if t.get('route') == 'mtb':
            return
        net = t.get('network', '')
        if net not in RANK:
            return
        name = t.get('name') or t.get('ref') or ''
        for m in r.members:
            if m.type != 'w':
                continue
            cur = self.ways.get(m.ref)
            if cur is None:
                self.ways[m.ref] = [RANK[net], net, [name] if name else []]
            else:
                if name in cur[2]:
                    cur[2].remove(name)
                if RANK[net] > cur[0]:
                    cur[0], cur[1] = RANK[net], net
                    if name:
                        cur[2].insert(0, name)  # Name der wichtigsten Route zuerst
                elif name:
                    cur[2].append(name)


class Ways(osmium.SimpleHandler):
    def __init__(self, want, out):
        super().__init__()
        self.want = want
        self.out = out
        self.n = 0

    def way(self, w):
        info = self.want.get(w.id)
        if info is None:
            return
        try:
            coords = [[round(n.lon, 6), round(n.lat, 6)] for n in w.nodes if n.location.valid()]
        except osmium.InvalidLocationError:
            return
        if len(coords) < 2:
            return
        names = info[2]
        props = {'net': info[1], 'rank': info[0]}
        if names:
            props['name'] = names[0]
            if len(names) > 1:
                props['names'] = ' · '.join(names[:4])
        if self.n:
            self.out.write(',\n')
        self.out.write(json.dumps({'type': 'Feature', 'properties': props,
                                   'geometry': {'type': 'LineString', 'coordinates': coords}}, ensure_ascii=False))
        self.n += 1


def main():
    src, dst = sys.argv[1], sys.argv[2]
    rel = Rel()
    rel.apply_file(src)
    print(f'   Radrouten: {len(rel.ways)} Wegstücke', flush=True)
    with open(dst, 'w') as f:
        f.write('{"type":"FeatureCollection","features":[\n')
        w = Ways(rel.ways, f)
        w.apply_file(src, locations=True, idx='flex_mem')
        f.write('\n]}\n')
    print(f'   Radrouten: {w.n} Linien geschrieben', flush=True)


if __name__ == '__main__':
    main()
