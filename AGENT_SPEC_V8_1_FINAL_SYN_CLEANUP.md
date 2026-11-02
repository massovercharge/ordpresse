# AGENT_SPEC_V8_1_FINAL_SYN_CLEANUP.md — Prune ODS Morpheme Hyphens & Parentheses from `.syn`
**Target Project:** `ordpresse` (Den Danske Ordpresse)
**Target Server:** `192.168.50.5`
**Target Agent:** Gemini 3.8 Flash

## Context: Binary Inspection of 2026.11.02 Build (`wordcount=36963`, `synwordcount=124096`)
The V8 build is 99.9% complete. However, binary inspection of `ordpresse.syn` revealed one last minor artifact from raw ODS headword/variant strings:

1. **ODS Morpheme Hyphens & Parentheses Leaking into `.syn`:**
   When ODS headwords were cleaned for `.idx` in V6 (e.g., `"aarvaagen-hed"` -> `"aarvaagenhed"`, `"bark(e)brød"` -> `"barkebrød"`, `"bag-ben"` -> `"bagben"`), the raw uncleaned ODS string was preserved as an inflection/variant in `.syn`.
   As a result, `ordpresse.syn` currently contains artificial ODS hyphenated/parenthesized keys such as:
   - `"aa-kande"`, `"aande-pust"`, `"aar-gammel"`, `"aare-vis"`, `"aarvaagen-hed"`
   - `"al-mægtig"`, `"al-sidig"`, `"al-vidende"`, `"al-viis"`, `"aller-bedst"`, `"aller-helst"`, `"aller-mest"`, `"aller-mindst"`, `"aller-naadigst"`
   - `"bag-ben"`, `"bag-binde"`, `"bag-fjerding"`, `"bag-hold"`, `"bag-klog"`, `"bag-vende"`, `"bag-væg"`, `"baske-tag"`, `"be(j)an"`, `"bark(e)brød"`, `"bark-(e-)brød"`, `"bark-brød"`, `"bark-e-brød"`, `"befje(d)re"`
   - `"bonde-datter"`, `"bonde-folk"`, `"bonde-hjem"`, `"døds-angst"`, `"døds-dag"`, `"døds-fare"`, `"døds-kamp"`, `"efter-abe"`, `"efter-gøre"`, `"efter-som"`, `"efter-syn"`

---

## Implementation Tasks

### Task 1: Filter Artificial Hyphens & Parentheses from `.syn` (`stardict_writer.py`)
Create a helper function `is_artificial_ods_synonym(form: str, headword: str) -> bool` in `stardict_writer.py` (or `orthography.py`) and apply it to every candidate synonym before adding it to `.syn` (and before generating `aa` variants from it):

1. **Parenthesis Rule:**
   - If `"("` in `form` or `")"` in `form` (e.g., `"bark(e)brød"`, `"bark-(e-)brød"`, `"be(j)an"`, `"befje(d)re"`):
     - Return `True` (skip it! Parentheses never occur in real e-book word lookups).

2. **Artificial Morpheme Hyphen Rule:**
   - If `"-"` in `form` and `"-"` not in `headword`:
     - Normalize both `form` and `headword` by lowercasing and replacing `"å"` with `"aa"`:
       `norm_form = form.lower().replace("å", "aa")`
       `norm_hw = headword.lower().replace("å", "aa")`
     - Strip hyphens from `norm_form`: `dehyphenated = norm_form.replace("-", "")`
     - If `dehyphenated == norm_hw` (e.g., `"bag-ben"` vs `"bagben"`, `"aar-gammel"` vs `"aargammel"`, `"efter-tragtelses-værdig"` vs `"eftertragtelsesværdig"`, `"avind-s-mand"` vs `"avindsmand"`):
       - Return `True` (skip it!).
     - Also, if `dehyphenated.startswith(norm_hw)` or `norm_hw.startswith(dehyphenated[:4])` and none of the hyphen-split parts of `form` is a single letter or digits (which preserves legitimate hyphenated variants like `"a-38"` for `"a38"` or `"co₂-afgift"` for `"co₂afgift"`, while dropping `"bark-brød"` / `"bark-e-brød"` under `"barkebrød"`):
       - Specifically: if `all(len(part) >= 2 and part.isalpha() for part in form.split("-"))`:
         - Return `True` (skip artificial ODS hyphenation when the canonical headword has no hyphen!).
   - *Note:* Legitimate hyphenated headwords (where `"-"` IS in `headword`, such as `"a-indkomst"`, `"1. g'er"`, `"ic3-tog"`, `"tv-avis"`, `"arbejd efter reglerne-aktion"`) and legitimate multi-word/hyphenated compounds (`"a conto-beløb"` for `"acontobeløb"`, `"drive-in-biograf"` for `"driveinbiograf"`, `"clara frijs-pære"` for `"clarafrijspære"`) must be preserved:
     - Whitelist or keep any `form` containing a space `" "` (like `"a conto-beløb"`, `"clara frijs-pære"`) or `"drive-in"`.

### Task 2: Unit Tests & Full Rebuild on `192.168.50.5`
1. Add tests in `tests/test_aa_and_genitives.py` (or a new test function):
   - Assert that `"bag-ben"`, `"aar-gammel"`, `"bark(e)brød"`, `"bark-(e-)brød"`, `"be(j)an"`, `"døds-angst"`, and `"efter-abe"` are **NOT** emitted to `.syn`.
   - Assert that legitimate `.syn` entries like `"a-indkomsten"`, `"a-38"`, `"co₂-afgift"`, `"drive-in-biograf"`, and `"baad"` / `"baaden"` **ARE** still emitted to `.syn`.
2. Run `.venv/bin/pytest -v`, commit & push, deploy to `192.168.50.5`, rebuild (`ordpresse build --full --no-network`), and report the final `wordcount` and `synwordcount`.