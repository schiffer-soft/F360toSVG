"""Verdeckungs-Analyse: entfernt Flaechen, die im SVG unsichtbar waeren.

Der Painter's Algorithm malt von hinten nach vorn — Flaechen, die
komplett von naeher liegenden ueberdeckt werden, stehen trotzdem als
Pfade im SVG (z. B. die Vorderseite einer Rueckplatte). Diese Analyse
geht die Zeichenliste von VORN nach HINTEN durch, sammelt die bereits
abgedeckte Region als Polygon-Union und verwirft jede Flaeche, die
vollstaendig darin liegt. Die Geometrie behaltener Flaechen bleibt
unveraendert.

Aufkleber werden nie entfernt und verdecken nichts (Transparenz).
Benoetigt shapely; ohne shapely wird nichts entfernt (Warnung).
"""
from __future__ import annotations

import sys

# Union nicht nach jeder Flaeche neu aufbauen — Batches halten GEOS schnell
UNION_BATCH_SIZE = 25


def _face_geometry(loops):
    """Loops (aussen + Loecher, even-odd) zu einer shapely-Geometrie."""
    from shapely.geometry import Polygon

    geometry = None
    for loop in loops:
        points = loop.get("points") or []
        if len(points) < 3:
            continue
        try:
            polygon = Polygon(points).buffer(0)  # heilt Selbstkontakte
        except (ValueError, TypeError):
            continue
        if polygon.is_empty:
            continue
        geometry = polygon if geometry is None else geometry.symmetric_difference(polygon)
    return geometry


def cull_hidden_shapes(shapes: list) -> tuple[list, int]:
    """Filtert komplett verdeckte Flaechen aus der sortierten Zeichenliste.

    shapes: Eintraege ((z_max, z_min), kind, payload) in Zeichenreihenfolge
    (hinten -> vorn). Rueckgabe: (gefilterte Liste, Anzahl entfernt).
    """
    try:
        from shapely import prepare, union_all
    except ImportError:
        print(
            "Warnung: shapely fehlt — verdeckte Flächen bleiben im SVG "
            "(py -3 -m pip install shapely).",
            file=sys.stderr,
        )
        return shapes, 0

    keep = [True] * len(shapes)
    covered = None
    pending = []  # noch nicht in die Union eingeflossene Geometrien

    def merge_pending():
        nonlocal covered, pending
        if not pending:
            return
        parts = ([covered] if covered is not None else []) + pending
        covered = union_all(parts)
        prepare(covered)
        pending = []

    # von vorn (zuletzt gezeichnet) nach hinten. Der Batch-Check ist
    # konservativ: eine Flaeche, die nur durch die KOMBINATION mehrerer
    # frischer Flaechen verdeckt wuerde, bleibt ggf. stehen — unsichtbar
    # ist sie trotzdem, es geht nur etwas Entruempelung verloren.
    for index in range(len(shapes) - 1, -1, -1):
        _, kind, payload = shapes[index]
        if kind != "face":
            continue  # Aufkleber: nie entfernen, nie verdeckend
        geometry = _face_geometry(payload[2])
        if geometry is None or geometry.is_empty:
            keep[index] = False
            continue
        hidden = (
            (covered is not None and covered.covers(geometry))
            or any(recent.covers(geometry) for recent in pending)
        )
        if hidden:
            keep[index] = False
            continue
        pending.append(geometry)
        if len(pending) >= UNION_BATCH_SIZE:
            merge_pending()

    kept = [shape for shape, flag in zip(shapes, keep) if flag]
    return kept, len(shapes) - len(kept)
