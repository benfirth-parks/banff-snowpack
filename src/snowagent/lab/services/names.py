"""Display names for evolved agents (owner, 2026-10-06: "give our agents interesting and unique names ... the Wire,
Sopranos, Curb Your Enthusiasm and the Crown for inspiration").

A name is a first name of a fictional character from one of those shows and a surname or place from another,
chosen from the genome hash, so the same agent has the same name in the lab app, its reports and on the site. Real
people's full names are never produced (the Crown contributes places, not people). A family's default agent keeps
its plain label, so the baseline stays obvious. Display only: genome files, labels and hashes do not change."""

from __future__ import annotations

FIRST = (
    # The Wire
    "Omar", "Stringer", "Avon", "Bunk", "Bubbles", "Lester", "Kima", "Herc", "Carver", "Snoop", "Cutty", "Bodie",
    "Poot", "Wallace", "Prez", "Marlo", "Brother", "Proposition",
    # The Sopranos
    "Tony", "Paulie", "Silvio", "Christopher", "Carmela", "Meadow", "Junior", "Bobby", "Furio", "Adriana", "Janice",
    "Artie", "Hesh", "Vito",
    # Curb Your Enthusiasm
    "Leon", "Susie", "Jeff", "Cheryl", "Marty", "Mocha",
)
LAST = (
    # The Wire
    "Barksdale", "McNulty", "Freamon", "Greggs", "Daniels", "Stanfield", "Moreland", "Sobotka", "Rawls",
    # The Sopranos
    "Soprano", "Moltisanti", "Dante", "Gualtieri", "Baccalieri", "Melfi", "Satriale", "Bucco", "Bada Bing",
    # Curb Your Enthusiasm
    "Funkhouser", "Greene", "Black", "Pretty Good",
    # The Crown (places, never people)
    "Balmoral", "Sandringham", "Windsor", "Highgrove", "Kensington", "Clarence House", "Corgi",
)


def nickname(genome_hash: str, label: str | None = None) -> str:
    """'Omar Balmoral' for an evolved agent; a default agent (label '<family>-default') keeps its label."""
    if label and label.endswith("-default"):
        return "standard SNOWPACK" if label == "snowpack-default" else label
    n = int(genome_hash[:16], 16)
    first, last = FIRST[n % len(FIRST)], LAST[(n // len(FIRST)) % len(LAST)]
    if first == "Mocha":
        first = "Mocha Joe"
    return f"{first} {last}"


def display(genome_hash: str, label: str | None) -> str:
    """'Omar Balmoral (r20-m05-snowpack)': the name with the lab label that says where it came from."""
    name = nickname(genome_hash, label)
    return name if not label or name == label or name == "standard SNOWPACK" else f"{name} ({label})"
