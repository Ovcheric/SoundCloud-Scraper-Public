"""
Starting lists for a brand-new library: festival names and place words.

The resolver cuts known festival names out of repost titles, and the
duplicate detector refuses to join two titles that name different cities or
stages. Both lists grow with use - `manage.py event` and `manage.py place` -
but a new library has neither, so a fresh install would credit "Lost Lands"
as an artist on its first playlist. These seed it, once.

Public: KNOWN_EVENTS, LOCATION_QUALIFIERS, seed_new_library

Imports nothing from src/. Writes only artist_settings, and only into a
library with no tracks and no lists of these kinds.
"""

from __future__ import annotations

import sqlite3

# Matched case-blind and stripped longest-first, so "EDC" may sit beside
# "EDC Las Vegas". Only names distinctive enough never to be part of an
# artist's own name: a bare "Ultra" would eat it out of "Ultra Nate".
KNOWN_EVENTS = (
    "Tomorrowland", "Tomorrowland Winter", "Ultra Music Festival",
    "Ultra Miami", "Ultra Europe", "EDC", "EDC Las Vegas", "EDC Orlando",
    "EDC Mexico", "EDC China", "Electric Daisy Carnival", "Coachella",
    "Lollapalooza", "Creamfields", "Mysteryland", "Defqon.1", "Qlimax",
    "Awakenings", "Dekmantel", "Time Warp", "Movement Detroit", "Sónar",
    "Glastonbury", "Electric Forest", "Lost Lands", "Bass Canyon",
    "Shambhala", "Wakaan Festival", "Okeechobee", "Hard Summer",
    "Beyond Wonderland", "Nocturnal Wonderland", "Escape Halloween",
    "Dreamstate", "Decadence", "Countdown NYE", "Holy Ship", "Burning Man",
    "Boiler Room", "Cercle", "Red Rocks", "Exit Festival",
    "Kappa FuturFestival", "Parookaville", "World Club Dome",
    "Untold Festival", "Electric Zoo", "Lightning in a Bottle",
    "Forbidden Kingdom", "Elements Festival", "Spring Awakening",
    "Sunset Music Festival", "Ever After", "Global Gathering",
    "Rampage Open Air", "Let It Roll", "Hospitality On The Beach",
    "Outlook Festival", "Hideout Festival", "Ministry of Sound",
)

# Single lowercase words, as event tokens are. Two copies of one set never
# name two different places, so a different city or stage on each side is a
# different recording. One side naming none is just a shorter title.
LOCATION_QUALIFIERS = (
    "miami", "vegas", "orlando", "mexico", "europe", "croatia", "ibiza",
    "london", "berlin", "amsterdam", "paris", "chicago", "detroit",
    "denver", "toronto", "montreal", "tokyo", "seoul", "sydney",
    "melbourne", "belgium", "brazil", "thailand", "china", "india",
    "winter", "camp", "mainstage", "basspod", "wasteland", "kineticfield",
    "circuitgrounds", "cosmicmeadow", "neongarden", "quantumvalley",
    "stereobloom",
)


def seed_new_library(conn: sqlite3.Connection) -> int:
    """
    Add the starting lists to a library that has never been used. Returns
    how many rows were written - 0 for any library with tracks or with
    events or places of its own, so a list someone pruned stays pruned.
    """
    used = conn.execute(
        "SELECT EXISTS (SELECT 1 FROM tracks) OR EXISTS ("
        " SELECT 1 FROM artist_settings"
        " WHERE kind IN ('known_event', 'location_qualifier'))").fetchone()[0]
    if used:
        return 0
    rows = ([("known_event", name) for name in KNOWN_EVENTS]
            + [("location_qualifier", word) for word in LOCATION_QUALIFIERS])
    conn.executemany(
        "INSERT OR IGNORE INTO artist_settings (kind, value) VALUES (?, ?)",
        rows)
    return len(rows)
