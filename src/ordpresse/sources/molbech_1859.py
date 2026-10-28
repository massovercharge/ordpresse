"""Chr. Molbech: Dansk Ordbog (2. udg. 1859, Public Domain) offline fallback lexicon."""

from typing import Dict, Optional

from ordpresse.formatter import render_canonical_html
from ordpresse.models import CanonicalEntry, SenseItem

# Curated high-frequency and historical terms from Chr. Molbech (1859)
MOLBECH_1859_ENTRIES: Dict[str, CanonicalEntry] = {
    "uefterrettelig": CanonicalEntry(
        headword="uefterrettelig",
        pos="adj.",
        inflections={
            "uefterrettelig",
            "uefterrettelige",
            "uefterretteligt",
            "uefterretlig",
            "uefterretlige",
            "uefterretligt",
        },
        historical_spellings=["uefterretlig", "u-efterrettelig"],
        summary_hook="som man ikke kan rette sig efter; upålidelig; ulydig.",
        historical_senses=[
            SenseItem(
                definition="som man ikke kan rette sig efter; upålidelig; ulydig.",
                label="hist.",
                source_tag="Molbech",
            ),
            SenseItem(
                definition="(jur.) om lov, dom eller befaling: som man ikke behøver eller bør rette sig efter; uberettiget, ulovmedholdelig.",
                label="jur.",
                source_tag="Molbech",
            ),
        ],
        realia_note="Chr. Molbech: Dansk Ordbog (1859). Betegner oprindeligt noget eller nogen, som man ikke kan 'rette sig efter' eller fæste lid til.",
        sources_used=["Molbech"],
    ),
    "uefterrettelighed": CanonicalEntry(
        headword="uefterrettelighed",
        pos="sb.",
        inflections={"uefterrettelighed", "uefterretteligheden", "uefterretteligheder"},
        historical_spellings=["uefterretlighed", "u-efterrettelighed"],
        summary_hook="det at være uefterrettelig; upålidelighed, ulydighed.",
        historical_senses=[
            SenseItem(
                definition="det at være uefterrettelig; upålidelighed, efterladenhed, ulydighed.",
                label="hist.",
                source_tag="Molbech",
            )
        ],
        realia_note="Chr. Molbech: Dansk Ordbog (1859).",
        sources_used=["Molbech"],
    ),
}


def get_molbech_1859_entry(word: str) -> Optional[CanonicalEntry]:
    """Lookup a word or inflected/archaic spelling in Molbech (1859) lexicon."""
    w = word.strip().lower()
    if not w:
        return None
    if w in MOLBECH_1859_ENTRIES:
        return MOLBECH_1859_ENTRIES[w]
    for hw, entry in MOLBECH_1859_ENTRIES.items():
        all_forms = {inf.lower() for inf in entry.inflections} | {sp.lower() for sp in entry.historical_spellings}
        if w in all_forms:
            return entry
    return None
