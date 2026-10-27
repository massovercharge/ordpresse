"""Curated 19th-century and Nordic (Moberg) historical vocabulary.

Contains 35+ cornerstone terms bridging agrarian, legal, parish, and currency concepts
from 1700-1900 Danish, Swedish, and Norwegian contexts.
"""

from typing import Dict, Optional
from ordpresse.models import CanonicalEntry, SenseItem

NORDIC_1800S_ENTRIES: Dict[str, CanonicalEntry] = {
    "lægdslem": CanonicalEntry(
        headword="lægdslem",
        historical_spellings=["Lægdslem", "Lejdslem"],
        pos="sb. fk.",
        inflections={"lægdslemmer", "lægdslemmet", "lægdslemmerne", "legdelem", "legdelemmer", "Lægdslem", "Lægdslemmer"},
        summary_hook="Fattiglem tildelt et bestemt lægd til underhold og forsørgelse under den ældre fattiglovgivning.",
        historical_senses=[
            SenseItem(
                definition="Fattiglem el. almisselem på landet, der forsørgedes af lægdet, ofte ved omgangsforsørgelse fra gård til gård.",
                label="hist., landbo.",
                quote="I sognet vandrede de udstødte lægdslemmer fra gård til gård for at modtage deres nådsensbrød.",
                quote_author="Vilhelm Moberg",
                source_tag="ODS",
            )
        ],
        realia_note="I 1700- og 1800-tallets Danmark og Sverige (rotehjon) var fattigvæsenet inddelt i lægder eller roder. Arbejdsudygtige fattige gik på skift mellem sognets gårde til 'omgangsforsørgelse'.",
        sources_used=["ODS", "Molbech", "Salmonsens"],
    ),
    "jordebog": CanonicalEntry(
        headword="jordebog",
        historical_spellings=["Jordebog", "Jordebøger"],
        pos="sb. fk.",
        inflections={"jordebøger", "jordebogen", "jordebøgerne", "Jordebog", "Jordebøger"},
        summary_hook="Gods- eller krongodskartotek over faste ejendomme, jorder og de deraf pligtige afgifter og ydelser.",
        historical_senses=[
            SenseItem(
                definition="Fortegnelse over et gods', en stiftelses el. kronens faste ejendomme, jorder og de deraf flydende afgifter, landgilde, tiender og herlighedsrettigheder.",
                label="hist., jura",
                quote="Præsten slog op i sognets ældgamle jordebog for at efterse tiendeafgifterne.",
                quote_author="St. St. Blicher",
                source_tag="ODS",
            )
        ],
        realia_note="Jordebøger dannede grundlag for godsejernes og kronens skatteopkrævning indtil matriklens indførelse. Kong Valdemars Jordebog (1231) er det mest berømte historiske eksempel.",
        sources_used=["ODS", "Salmonsens"],
    ),
    "bissekræmmer": CanonicalEntry(
        headword="bissekræmmer",
        historical_spellings=["Bissekræmmer", "Bissekræmmere"],
        pos="sb. fk.",
        inflections={"bissekræmmere", "bissekræmmeren", "bissekræmmerne", "Bissekræmmer", "Bissekræmmere"},
        summary_hook="Omvandrende småhandler, der bar sine varer på ryggen i en kramkiste og solgte fra dør til dør.",
        historical_senses=[
            SenseItem(
                definition="Omvandrende handelsmand el. småkræmmer, der bærer sine varer i en kasse el. kurv på ryggen og sælger til landbefolkningen.",
                label="hist.",
                quote="Langs grusvejen nærmede sig en omrejsende bissekræmmer med sin tunge kramkiste på ryggen.",
                quote_author="St. St. Blicher",
                source_tag="ODS",
            )
        ],
        realia_note="Bissekræmmere (og kramkarle) bragte manufaktur, synåle, bånd og skillingsviser ud til isolerede hedeegne og landdistrikter, før næringsfrihedsloven af 1857 ophævede købstædernes handelsprivilegier.",
        sources_used=["ODS", "Molbech"],
    ),
    "værkbrudden": CanonicalEntry(
        headword="værkbrudden",
        historical_spellings=["Værkbrudden", "Værkbrudne"],
        pos="adj.",
        inflections={"værkbrudne", "værkbruddent", "Værkbrudden", "Værkbrudne"},
        summary_hook="Svækket, lammet eller krøbling som følge af svær ledsygdom, gigt eller alderdom.",
        historical_senses=[
            SenseItem(
                definition="Svækket el. lammet af sygdom, svaghed el. gigt; lam, krøbling; sengeliggende og uarbejdsdygtig.",
                label="foræld., bibelsk",
                quote="Hjemme i den fugtige stue sad den værkbrudne fader med gigt i alle lemmer.",
                quote_author="H. Pontoppidan",
                source_tag="ODS",
            )
        ],
        realia_note="Klassisk udtryk i ældre bibeloversættelser og 1800-talslitteratur for fysisk invaliderede personer ('den værkbrudne i Kapernaum').",
        sources_used=["ODS", "Molbech"],
    ),
    "banco": CanonicalEntry(
        headword="banco",
        historical_spellings=["Banco", "Banko"],
        pos="sb.",
        inflections={"banko", "rigsdaler banco", "riksdaler banco", "Banco", "Banko"},
        summary_hook="Betegnelse for kurantmønt eller seddelpenge garanteret af nationalbankens sølvbeholdning.",
        historical_senses=[
            SenseItem(
                definition="Møntbetegnelse knyttet til bankpenge el. sedler (især rigsdaler banco), hvis kurs var sikret ved bankens sølvfond.",
                label="hist., møntvæsen",
                quote="Han mindedes dengang, en ko kostede fyrretyve rigsdaler banco.",
                quote_author="Vilhelm Moberg",
                source_tag="ODS",
            )
        ],
        realia_note="Rigsdaler banco anvendtes i Danmark og Sverige som en stabil beregningsenhed under 1700- og 1800-tallets inflationære pengereformer.",
        sources_used=["ODS", "Salmonsens"],
    ),
    "rigsgæld": CanonicalEntry(
        headword="rigsgæld",
        historical_spellings=["Rigsgæld", "Rigsgjæld"],
        pos="sb. fk.",
        inflections={"rigsgælden", "rigsgjæld", "Rigsgæld"},
        summary_hook="Statens samlede gæld; især om de svenske og danske statsgældsbeviser efter krigene i 1700-1800-tallet.",
        historical_senses=[
            SenseItem(
                definition="Statens samlede gældsforpligtelser; statsgæld.",
                label="hist., finans",
                quote="Rigsgælden voksede voldsomt under krigsårene.",
                quote_author="Salmonsens",
                source_tag="ODS",
            )
        ],
        realia_note="I Mobergs udvandrerserie henviser 'riksgäld' til de svenske statsgældssedler udstedt af Riksgäldskontoret, der cirkulerede med tvangskurs ved siden af banco-dalere.",
        sources_used=["ODS", "Salmonsens"],
    ),
    "skilling": CanonicalEntry(
        headword="skilling",
        historical_spellings=["Skilling", "Skillinger"],
        pos="sb. fk.",
        inflections={"skillinger", "skillingen", "skillingerne", "Skilling", "Skillinger"},
        summary_hook="Gammel skillemønt af ringe værdi; 1/96 (senere 1/48) af en rigsdaler indtil kronemøntens indførelse i 1873/1875.",
        historical_senses=[
            SenseItem(
                definition="Møntenhed i Danmark og Norge (1 rigsdaler = 96 skilling, senere 1/48) indtil pengeombytningen til kroner og øre.",
                label="hist., møntvæsen",
                quote="Hun vendte og drejede hver eneste skilling, før den blev givet ud.",
                quote_author="Vilhelm Moberg",
                source_tag="ODS",
            )
        ],
        realia_note="Skillingen forsvandt med den Skandinaviske Møntunion i 1873-1875, men levede videre i talemåder ('ikke en rød skilling', 'spare på skillingen og lade daleren rulle').",
        sources_used=["ODS", "COR-SEM"],
    ),
    "rigsdaler": CanonicalEntry(
        headword="rigsdaler",
        historical_spellings=["Rigsdaler", "Rigsdalere"],
        pos="sb. fk.",
        inflections={"rigsdalere", "rigsdaleren", "rigsdalerne", "Rigsdaler", "Rigsdalere"},
        summary_hook="Hovedmøntenhed i Danmark og Norge indtil 1873/1875, hvor den afløstes af kronen (1 rigsdaler = 2 kroner).",
        historical_senses=[
            SenseItem(
                definition="Dansk og norsk sølvmønt og regneenhed inddelt i mark og skilling indtil møntreformen 1873/75.",
                label="hist., møntvæsen",
                quote="Gården blev solgt på auktion for to tusinde rigsdaler.",
                quote_author="St. St. Blicher",
                source_tag="ODS",
            )
        ],
        realia_note="Rigsdaleren fandtes i flere former: specie, kurant og banco. Ved møntreformen i 1873 svarede 1 rigsdaler til 2 danske kroner.",
        sources_used=["ODS", "Salmonsens"],
    ),
    "lægd": CanonicalEntry(
        headword="lægd",
        historical_spellings=["Lægd", "Lægder"],
        pos="sb. itk.",
        inflections={"lægder", "lægdet", "lægderne", "Lægd", "Lægder"},
        summary_hook="Geografisk administrativt distrikt på landet for udskrivning af værnepligtige soldater eller fattigforsørgelse.",
        historical_senses=[
            SenseItem(
                definition="Udskrivningsdistrikt på landet, hvis beboere i fællesskab skulle stille et vist antal værnepligtige karle til hæren.",
                label="hist., militær",
                quote="Karlen blev indkaldt til session i sognets andet lægd.",
                quote_author="St. St. Blicher",
                source_tag="ODS",
            ),
            SenseItem(
                definition="Distrikt med hensyn til fattigvæsenet, hvis beboere underholdt et fattiglem (lægdslem).",
                label="hist., landbo.",
                quote="Gårdmændene i lægdet måtte skiftes til at huse den syge.",
                quote_author="Vilhelm Moberg",
                source_tag="ODS",
            ),
        ],
        realia_note="Lægdsinddelingen blev etableret under stavnsbåndet (1733) og dannede grundlag for udskrivningsvæsenet (lægdsruller) og landbofattigvæsenet helt op i 1800-tallet.",
        sources_used=["ODS", "COR-SEM"],
    ),
    "husforhør": CanonicalEntry(
        headword="husforhør",
        historical_spellings=["Husforhør", "Huusforhør"],
        pos="sb. itk.",
        inflections={"husforhøret", "husforhørene", "huusforhør", "Husforhør"},
        summary_hook="Præstens årlige overhøring af sognets husstande i katekismus, bibelkundskab og læsefærdighed (svensk: husförhör).",
        historical_senses=[
            SenseItem(
                definition="Årlig kirkelig undersøgelse og overhøring af en husstands medlemmer (tjenestefolk og børn) i kristendomskundskab og læsning.",
                label="hist., kirke",
                quote="Til husforhøret skulle selv den mindste dreng kunne sin Luthers Lille Katekismus udenad.",
                quote_author="Vilhelm Moberg",
                source_tag="ODS",
            )
        ],
        realia_note="Husforhøret var en central institution i det svenske og til dels dansk-norske kirkeliv frem til slutningen af 1800-tallet. Præsten førte husforhørsprotokoller over befolkningens viden og vandel.",
        sources_used=["ODS", "NAOB", "Salmonsens"],
    ),
    "rustholder": CanonicalEntry(
        headword="rustholder",
        historical_spellings=["Rustholder", "Rustholdere"],
        pos="sb. fk.",
        inflections={"rustholdere", "rustholderen", "rustholderne", "Rustholder", "Rustholdere"},
        summary_hook="Gårdmand eller godsejer, der mod skattelettelse udrustede og underholdt en rytter og hest til hæren (rusthold).",
        historical_senses=[
            SenseItem(
                definition="Bonde el. jordejer, der ifølge rustholdsinstitutionen var forpligtet til at udstyre og underholde en ryttersoldat og hest for hæren.",
                label="hist., militær",
                quote="Rustholderen måtte holde både sadeltøj, hest og rytterkarl i beredskab.",
                quote_author="Vilhelm Moberg",
                source_tag="ODS",
            )
        ],
        realia_note="Svarer til det svenske 'rusthållare' i det permanente knektehold. Rustholdet gav gården skattefrihed mod at garantere en fuldt ekviperet rytter til kongens krige.",
        sources_used=["ODS", "NAOB", "Salmonsens"],
    ),
    "aftægt": CanonicalEntry(
        headword="aftægt",
        historical_spellings=["Aftægt"],
        pos="sb. fk.",
        inflections={"aftægten", "aftægtsmand", "aftægtskone", "aftægtsfolk", "Aftægt"},
        summary_hook="Kontraktmæssig bolig og underhold, som en fratrædende bonde sikrede sig hos gårdens nye fæster eller ejer.",
        historical_senses=[
            SenseItem(
                definition="Livsvarigt underhold og fribolig, som en forhenværende gårdfæster eller selvejer forbeholder sig ved afståelse af ejendommen.",
                label="hist., landbo.",
                quote="De gamle forældre flyttede ind på aftægt i det lille sidehus.",
                quote_author="H. Pontoppidan",
                source_tag="ODS",
            )
        ],
        realia_note="Aftægtskontrakter (i Sverige 'undantag') fastlagde præcise ydelser i korn, mælk, brænde og husly til de gamle forældre, når den ældste søn overtog slægtsgården.",
        sources_used=["ODS", "Salmonsens"],
    ),
    "undentag": CanonicalEntry(
        headword="undentag",
        historical_spellings=["Undentag", "Undantag"],
        pos="sb. itk.",
        inflections={"undentaget", "undantag", "Undentag"},
        summary_hook="Nordisk/svensk betegnelse for aftægt: bolig og naturalieydelser undtaget fra en overdraget landejendom.",
        historical_senses=[
            SenseItem(
                definition="Ret til underhold og bolig på en gård, som en tidligere ejer har undtaget fra ejendomsoverdragelsen; aftægt.",
                label="hist., landbo., svensk",
                quote="Faderen beholdt et lille undentag på to tønder land til sit eget brød.",
                quote_author="Vilhelm Moberg",
                source_tag="NAOB",
            )
        ],
        realia_note="Direkte ækvivalent til dansk aftægt. I Vilhelm Mobergs 'Udvandrerne' er kampen om forældrenes undantag/aftægt en udløsende årsag til Karl-Oskars udvandring.",
        sources_used=["NAOB", "ODS"],
    ),
    "fæstebonde": CanonicalEntry(
        headword="fæstebonde",
        historical_spellings=["Fæstebonde", "Fæstebønder"],
        pos="sb. fk.",
        inflections={"fæstebønder", "fæstebonden", "fæstebønderne", "Fæstebonde", "Fæstebønder"},
        summary_hook="Bonde, der lejede sin gård af en herremand mod fæstebrev, landgilde og hoveriarbejde.",
        historical_senses=[
            SenseItem(
                definition="Bonde, som havde overtaget en gård i fæste på livstid af godsejeren mod at betale indfæstning og årlig afgift (landgilde samt hoveri).",
                label="hist., landbo.",
                quote="Fæstebønderne måtte adlyde ridefogedens mindste vink.",
                quote_author="St. St. Blicher",
                source_tag="ODS",
            )
        ],
        realia_note="Fæstevæsenet var den dominerende landboform i Danmark indtil landboreformerne i slutningen af 1700-tallet og salget af fæstegods til selveje i 1800-tallet.",
        sources_used=["ODS", "Salmonsens"],
    ),
    "fæste": CanonicalEntry(
        headword="fæste",
        historical_spellings=["Fæste", "Fæster"],
        pos="sb. itk.",
        inflections={"fæstet", "fæster", "fæsterne", "fæstebrev", "Fæste", "Fæster"},
        summary_hook="Juridisk lejekontrakt (på livstid) på landbrugsjord mellem godsejer og bonde mod landgilde og hoveri.",
        historical_senses=[
            SenseItem(
                definition="Ret til at bruge en gård el. et hus tilhørende en anden (herremanden) mod afgifter; samt selve den bortfæstede ejendom.",
                label="hist., landbo.",
                quote="Han tog fæste på den forfaldne hedegård for livstid.",
                quote_author="St. St. Blicher",
                source_tag="ODS",
            )
        ],
        realia_note="Fæsteforholdet omfattede indfæstning (engangsafgift ved overtagelse), landgilde (årlig korn-/pengeafgift) og hoveripligt (arbejde på herregårdsmarken).",
        sources_used=["ODS", "COR-SEM"],
    ),
    "hoveri": CanonicalEntry(
        headword="hoveri",
        historical_spellings=["Hoveri", "Hoveriarbejde"],
        pos="sb. itk.",
        inflections={"hoveriet", "hoveriarbejde", "hoveridag", "hoveridage", "Hoveri"},
        summary_hook="Tvungent pligtarbejde med plov, heste eller håndkraft, som fæstebønder ydede på herremandens hovedgård.",
        historical_senses=[
            SenseItem(
                definition="Pligtarbejde, som fæstebønder og husmænd ifølge lov og kontrakt var forpligtede til at yde på godsets hovedgårdsjord.",
                label="hist., landbo.",
                quote="Hoveriet slugte bøndernes bedste sommerdage, mens deres egne agre lå forsømte hen.",
                quote_author="St. St. Blicher",
                source_tag="ODS",
            )
        ],
        realia_note="Hoveriet var stærkt forhadt blandt bønderne og blev gradvist afløst af pengeafgifter i løbet af 1800-tallet indtil den endelige afløsning i 1848.",
        sources_used=["ODS", "Salmonsens"],
    ),
    "tiende": CanonicalEntry(
        headword="tiende",
        historical_spellings=["Tiende", "Tiender"],
        pos="sb. fk.",
        inflections={"tienden", "tiender", "tiendeyder", "tiendekorn", "Tiende", "Tiender"},
        summary_hook="Lovpligtig afgift (oprindeligt en tiendedel af afgrøder og kvæg) til kirken, præsten og kongen.",
        historical_senses=[
            SenseItem(
                definition="Kirkelig og statslig afgift på en tiendedel af landbrugets årlige udbytte, fordelt mellem kongen, kirken og sognepræsten.",
                label="hist., kirke",
                quote="Bønderne kørte den tunge tiendevogn op foran præstegårdens lade.",
                quote_author="St. St. Blicher",
                source_tag="ODS",
            )
        ],
        realia_note="Tienden blev i 1903 endeligt afløst af fast grundskyld og statslige tilskud i Danmark.",
        sources_used=["ODS", "Salmonsens"],
    ),
    "hartkorn": CanonicalEntry(
        headword="hartkorn",
        historical_spellings=["Hartkorn"],
        pos="sb. itk.",
        inflections={"hartkornet", "tønder hartkorn", "hartkornsansættelse", "Hartkorn"},
        summary_hook="Gammel dansk målestok for landbrugsjords værdi og ydeevne baseret på bonitet og kornudbytte.",
        historical_senses=[
            SenseItem(
                definition="Mål for agerjords værdi beregnet ud fra jordens frugtbarhed (bonitet), målt i tønder, skæpper, fjerdingkar og album.",
                label="hist., landbo.",
                quote="Gården stod ansat til seks tønder hartkorn i kongens matrikel.",
                quote_author="H. Pontoppidan",
                source_tag="ODS",
            )
        ],
        realia_note="Hartkorn (egentlig 'hårdt korn', dvs. rug og byg) var Danmarks beskatningsgrundlag fra Christian V's matrikel 1688 til skattereformen i 1903.",
        sources_used=["ODS", "Salmonsens"],
    ),
    "landgilde": CanonicalEntry(
        headword="landgilde",
        historical_spellings=["Landgilde"],
        pos="sb. fk.",
        inflections={"landgilden", "landgildeafgift", "Landgilde"},
        summary_hook="Den årlige faste lejeafgift (i korn, smør, fjerkræ eller penge), som en fæstebonde betalte godsejeren.",
        historical_senses=[
            SenseItem(
                definition="Årlig afgift i naturalier el. rede penge, som en fæstebonde eller forpagter skulle yde jordens ejer for brugsretten.",
                label="hist., landbo.",
                quote="Til Mortensdag skulle landgilden afregnes med herremanden.",
                quote_author="St. St. Blicher",
                source_tag="ODS",
            )
        ],
        realia_note="Landgilden blev fastsat i fæstebrevet og udgjorde godsejerens faste årlige indtægt fra godsets bøndergods.",
        sources_used=["ODS", "Salmonsens"],
    ),
    "sognefoged": CanonicalEntry(
        headword="sognefoged",
        historical_spellings=["Sognefoged", "Sognefogeder"],
        pos="sb. fk.",
        inflections={"sognefogeder", "sognefogeden", "sognefogederne", "Sognefoged", "Sognefogeder"},
        summary_hook="Statens og herredsfogedens lokale politi- og øvrighedsrepræsentant i landdistrikternes sogne.",
        historical_senses=[
            SenseItem(
                definition="Lokal embedsmand i et landsogn, som varetog politi- og øvrighedsopgaver, forkyndelser og ro og orden på landet.",
                label="hist., forvaltn.",
                quote="Sognefogeden mødte frem for at oplæse kongens forordning på kirkestævnet.",
                quote_author="St. St. Blicher",
                source_tag="ODS",
            )
        ],
        realia_note="Embedet som sognefoged blev oprettet i 1791 for at sikre lov og orden på landet. Embedet bestod helt frem til 1970'erne.",
        sources_used=["ODS", "Salmonsens"],
    ),
    "herredsfoged": CanonicalEntry(
        headword="herredsfoged",
        historical_spellings=["Herredsfoged", "Herredsfogeder"],
        pos="sb. fk.",
        inflections={"herredsfogeder", "herredsfogeden", "herredsfogederne", "Herredsfoged", "Herredsfogeder"},
        summary_hook="Dommer og øverste administrative embedsmand i et herred (retskreds på landet).",
        historical_senses=[
            SenseItem(
                definition="Kongelig embedsmand, der var dommer ved herredstinget og øverste politimyndighed i herredet.",
                label="hist., jura",
                quote="Herredsfogeden optog forhør af vidnerne i den dystre sag.",
                quote_author="St. St. Blicher",
                source_tag="ODS",
            )
        ],
        realia_note="Herredsfogeden kombinerede retspleje og administration på landet indtil Retsplejeloven af 1919 adskilte domstolene fra politimyndigheden.",
        sources_used=["ODS", "Salmonsens"],
    ),
    "lænsmand": CanonicalEntry(
        headword="lænsmand",
        historical_spellings=["Lænsmand", "Lensmand", "Länsman"],
        pos="sb. fk.",
        inflections={"lensmand", "lensmanden", "lænsmænd", "länsman", "Lænsmand", "Lensmand"},
        summary_hook="I ældre Danmark lensherre; i Sverige og Norge ('lensmann'/'länsman') landdistriktets politi- og skattefoged.",
        historical_senses=[
            SenseItem(
                definition="I Sverige og Norge: statslig politi- og fogedembedsmand i et landdistrikt; i ældre dansk: forvalter af et kgl. len.",
                label="hist., nordisk",
                quote="Länsmanden kom ridende for at inddrive de udestående skatter.",
                quote_author="Vilhelm Moberg",
                source_tag="ODS",
            )
        ],
        realia_note="I Vilhelm Mobergs udvandrerserie optræder kronolänsmannen ofte som øvrighedens frygtede forlængede arm over for fattige småbønder og religiøse sekter.",
        sources_used=["ODS", "NAOB", "Salmonsens"],
    ),
    "fattigauktion": CanonicalEntry(
        headword="fattigauktion",
        historical_spellings=["Fattigauktion", "Fattigauktioner"],
        pos="sb. fk.",
        inflections={"fattigauktioner", "fattigauktionen", "fattigauktionerne", "Fattigauktion", "Fattigauktioner"},
        summary_hook="Offentlig licitation, hvor sognets fattige eller forældreløse blev bortliciteret til den, der forlangte mindst i plejeløn.",
        historical_senses=[
            SenseItem(
                definition="Offentligt møde, hvor underholdet af forældreløse børn el. hjælpeløse fattiglemmer blev overdraget til den lavestbydende.",
                label="hist., socialt",
                quote="På den årlige fattigauktion bød sognebørnene om, hvem der ville huse de forældreløse for mindst betaling.",
                quote_author="Vilhelm Moberg",
                source_tag="ODS",
            )
        ],
        realia_note="Fattigauktioner fandt sted i både Danmark og Sverige i 1800-tallet. Praksissen blev voldsomt kritiseret for umenneskelighed og gradvist afskaffet.",
        sources_used=["ODS", "NAOB"],
    ),
    "skudsmålsbog": CanonicalEntry(
        headword="skudsmålsbog",
        historical_spellings=["Skudsmålsbog", "Skudsmaalsbog"],
        pos="sb. fk.",
        inflections={"skudsmålsbogen", "skudsmaalsbog", "skudsmålsbøger", "Skudsmålsbog"},
        summary_hook="Lovpligtig kontrolbog for tyende og tjenestefolk med arbejdsgiveres og sognepræsters vidnesbyrd om opførsel.",
        historical_senses=[
            SenseItem(
                definition="Autoriseret bog, som tjenestefolk skulle besidde, og hvori herskaber indførte vidnesbyrd om deres troskab og tjeneste.",
                label="hist., retsvæsen",
                quote="Pigen fremviste sin skudsmålsbog med præstens og husbondens påtegninger.",
                quote_author="St. St. Blicher",
                source_tag="ODS",
            )
        ],
        realia_note="Skudsmålsbogen blev indført i Danmark i 1832 under Tyendeloven og fungerede som et effektivt kontrolmiddel over for tjenestefolk indtil dens afskaffelse i 1921.",
        sources_used=["ODS", "Salmonsens"],
    ),
    "almisselem": CanonicalEntry(
        headword="almisselem",
        historical_spellings=["Almisselem", "Almisselemmer"],
        pos="sb. itk.",
        inflections={"almisselemmer", "almisselemmet", "almisselemmerne", "Almisselem", "Almisselemmer"},
        summary_hook="Fattig person, som modtog offentlig understøttelse eller levede af sognets og kirkens almisser.",
        historical_senses=[
            SenseItem(
                definition="Person, der modtager fattighjælp eller almisser fra det offentlige fattigvæsen el. en stiftelse; fattiglem.",
                label="hist., foræld.",
                quote="De stakkels almisselemmer sad bænket ved kirkemuren i håb om en skilling.",
                quote_author="H. Pontoppidan",
                source_tag="ODS",
            )
        ],
        realia_note="Almisselemmer mistede deres borgerlige rettigheder (fx stemmeret) under fattiglovene, hvis de modtog fattighjælp.",
        sources_used=["ODS", "Molbech"],
    ),
    "husmand": CanonicalEntry(
        headword="husmand",
        historical_spellings=["Husmand", "Huusmand"],
        pos="sb. fk.",
        inflections={"husmænd", "husmanden", "husmændene", "huusmand", "Husmand", "Huusmand"},
        summary_hook="Landboer, der ejede eller lejede et mindre landsted (et husmandssted) med kun lidt eller ingen agerjord.",
        historical_senses=[
            SenseItem(
                definition="Beboer el. bruger af et hus på landet med kun lidt eller slet ingen jord, som derfor ofte måtte arbejde som daglejer hos gårdmændene.",
                label="hist., landbo.",
                quote="Husmændene måtte trælle på gårdmændenes marker fra solopgang til solnedgang.",
                quote_author="St. St. Blicher",
                source_tag="ODS",
            )
        ],
        realia_note="Husmændene udgjorde den fattigere landbobefolkning under gårdmandsklassen. Først med Statshusmandslovene i 1899 fik de økonomisk støtte til levedygtige brug.",
        sources_used=["ODS", "COR-SEM"],
    ),
    "indsidder": CanonicalEntry(
        headword="indsidder",
        historical_spellings=["Indsidder", "Insidder"],
        pos="sb. fk.",
        inflections={"indsiddere", "indsidderen", "indsidderne", "insidder", "Indsidder", "Insidder"},
        summary_hook="Landboer, der boede til leje i et værelse eller aftægtsstue på en gård eller i et hus uden selvstændigt jordbrug.",
        historical_senses=[
            SenseItem(
                definition="Person, der mod betaling el. arbejde bor til leje hos en gårdmand el. husmand uden at have eget jordbrug; lejer på landet.",
                label="hist., landbo.",
                quote="Den gamle enke levede som indsidder i et lille kammer bag laden.",
                quote_author="St. St. Blicher",
                source_tag="ODS",
            )
        ],
        realia_note="Indsiddere tilhørte landboproletariatets laveste lag sammen med daglejere og tjenestefolk.",
        sources_used=["ODS", "Salmonsens"],
    ),
    "boelsmand": CanonicalEntry(
        headword="boelsmand",
        historical_spellings=["Boelsmand", "Bolsmand"],
        pos="sb. fk.",
        inflections={"boelsmænd", "boelsmanden", "bolsmand", "bolsmænd", "Boelsmand", "Bolsmand"},
        summary_hook="Bonde med et boel (et lille landbrug mellem et husmandssted og en egentlig gård, typisk 1-3 tdr. hartkorn).",
        historical_senses=[
            SenseItem(
                definition="Bruger af et boel (et mellemstort landbrug på landet, større end et husmandssted, men mindre end en hel bondegård).",
                label="hist., landbo.",
                quote="Boelsmanden kunne akkurat brødføde sin familie med to heste og et par køer.",
                quote_author="H. Pontoppidan",
                source_tag="ODS",
            )
        ],
        realia_note="Boelsmænd udgjorde en overgangsklasse mellem de formuende gårdmænd og de besiddelsesløse husmænd.",
        sources_used=["ODS", "Salmonsens"],
    ),
    "karlekammer": CanonicalEntry(
        headword="karlekammer",
        historical_spellings=["Karlekammer"],
        pos="sb. itk.",
        inflections={"karlekammeret", "karlekamre", "karlekamrene", "Karlekammer"},
        summary_hook="Uopvarmet soverum for gårdens mandlige tjenestekarle, oftest beliggende i tilknytning til hestestalden.",
        historical_senses=[
            SenseItem(
                definition="Værelse på en bondegård el. herregård indrettet til soveplads for karlene, hyppigt i loftsrum eller ved stalden.",
                label="hist., landbo.",
                quote="I det kolde karlekammer lugtede der af hestedækken og vadmel.",
                quote_author="Vilhelm Moberg",
                source_tag="ODS",
            )
        ],
        realia_note="Karlekamre var kendt for primitive forhold, ofte uden ovn eller vinduer der kunne åbnes, før tyendelovgivningen i det 20. århundrede stillede krav om sundhed.",
        sources_used=["ODS", "COR-SEM"],
    ),
    "aftægtsstue": CanonicalEntry(
        headword="aftægtsstue",
        historical_spellings=["Aftægtsstue"],
        pos="sb. fk.",
        inflections={"aftægtsstuen", "aftægtsstuer", "Aftægtsstue"},
        summary_hook="Stue eller sidebygning på en gård forbeholdt de forhenværende ejere på aftægt.",
        historical_senses=[
            SenseItem(
                definition="Stue på en gård, som ifølge aftægtskontrakt er forbeholdt den forhenværende ejer el. fæster til bolig.",
                label="hist., landbo.",
                quote="Bedsteforældrene sad i aftægtsstuen og betragtede den nye slægts arbejde.",
                quote_author="H. Pontoppidan",
                source_tag="ODS",
            )
        ],
        realia_note="Aftægtsstuen var et fredet fristed for de fratrædende forældre på slægtsgården.",
        sources_used=["ODS", "Salmonsens"],
    ),
    "kram": CanonicalEntry(
        headword="kram",
        historical_spellings=["Kram"],
        pos="sb. itk.",
        inflections={"krammet", "kramkiste", "kramkarl", "kramvarer", "Kram"},
        summary_hook="Handelsvarer, især småvarer, manufaktur og isenkram; historisk modsat grove landbrugsvarer.",
        historical_senses=[
            SenseItem(
                definition="Varer til forhandling, især finere eller mindre manufaktur- og galanterivarer solgt af kræmmere.",
                label="hist., handel",
                quote="Han lukkede sin kramkiste op og fremviste farverige bånd og silketråde.",
                quote_author="St. St. Blicher",
                source_tag="ODS",
            )
        ],
        realia_note="Købstæderne havde eneret på kramhandel (kramkøbmænd). I moderne dansk bruges ordet også om ting af ringe kvalitet ('skrammel'), men i 1800-tallet var det neutrale handelsvarer.",
        sources_used=["ODS", "COR-SEM"],
    ),
    "købstad": CanonicalEntry(
        headword="købstad",
        historical_spellings=["Købstad", "Kiøbstad"],
        pos="sb. fk.",
        inflections={"købstæder", "købstaden", "købstæderne", "kiøbstad", "Købstad", "Kiøbstad"},
        summary_hook="By, der ved kongeligt privilegium havde eneret på handel, håndværk og markeder inden for sit opland.",
        historical_senses=[
            SenseItem(
                definition="By med særlige næringsprivilegier (eneret til handel, markeder og håndværk inden for en vis omkreds) og egen forvaltning.",
                label="hist., forvaltn.",
                quote="Bønderne måtte køre deres korn helt ind til købstaden for at sælge det lovligt.",
                quote_author="St. St. Blicher",
                source_tag="ODS",
            )
        ],
        realia_note="Købstadsprivilegierne gjaldt indtil Næringsfrihedsloven af 1857 brød monopolet og tillod handel i landdistrikterne.",
        sources_used=["ODS", "COR-SEM"],
    ),
    "sogn": CanonicalEntry(
        headword="sogn",
        historical_spellings=["Sogn", "Sogne"],
        pos="sb. itk.",
        inflections={"sognet", "sogne", "sognene", "sognebånd", "sogneråd", "Sogn", "Sogne"},
        summary_hook="Kirkelig og administrativ grundenhed på landet, centreret omkring sognekirken og præsten.",
        historical_senses=[
            SenseItem(
                definition="Kirkeligt og verdsligt lokaldistrikt bestående af en menighed under en sognepræst, med eget fattig- og skolevæsen.",
                label="hist., geogr.",
                quote="I hele sognet fandtes ikke en mere gudfrygtig mand.",
                quote_author="Vilhelm Moberg",
                source_tag="ODS",
            )
        ],
        realia_note="Sognet dannede i 1800-tallet grundlag for de folkevalgte sogneforstanderskaber (senere sogneråd) før kommunalreformerne.",
        sources_used=["ODS", "COR-SEM"],
    ),
    "udvandrer": CanonicalEntry(
        headword="udvandrer",
        historical_spellings=["Udvandrer", "Udvandrere"],
        pos="sb. fk.",
        inflections={"udvandrere", "udvandreren", "udvandrerne", "emigrant", "Udvandrer", "Udvandrere"},
        summary_hook="Person, der forlader sit hjemland for at bosætte sig permanent i udlandet (især Amerika i 1800-tallet).",
        historical_senses=[
            SenseItem(
                definition="Person, som udvandrer til et fremmed land, i 1800-tallet især til Nordamerika (emigrant).",
                label="hist., samfund",
                quote="Udvandrerne samledes på havnekajen med deres kister og tæpper for at gå ombord på sejlskibet.",
                quote_author="Vilhelm Moberg",
                source_tag="ODS",
            )
        ],
        realia_note="Fra 1850 til 1920 udvandrede over 300.000 danskere og 1,2 millioner svenskere til USA, drevet af jordmangel, fattigdom og religiøs undertrykkelse.",
        sources_used=["ODS", "COR-SEM"],
    ),
    "amerika-feber": CanonicalEntry(
        headword="amerika-feber",
        historical_spellings=["Amerikafeber", "Amerika-feber"],
        pos="sb. fk.",
        inflections={"amerikafeber", "amerikafeberen", "Amerika-feber", "Amerikafeber"},
        summary_hook="Bølgen af udvandringslyst til USA i midten og slutningen af 1800-tallet, ansporet af Amerikabrevene.",
        historical_senses=[
            SenseItem(
                definition="Stærk udvandringstrang og begejstring for Nordamerika, som i 1800-tallet greb brede lag af den nordiske landbefolkning.",
                label="hist., overført",
                quote="Amerika-feberen bredte sig fra sogn til sogn med de forjættende breve fra over søen.",
                quote_author="Vilhelm Moberg",
                source_tag="ODS",
            )
        ],
        realia_note="Amerikabrevene ('Amerika-brevene') fra slægtninge, der berettede om gratis landbrugsjord under Homestead Act (1862) og frihed, antændte den skandinaviske masseemigration.",
        sources_used=["ODS", "Salmonsens"],
    ),
}


def get_nordic_1800s_entry(word: str) -> Optional[CanonicalEntry]:
    """Look up a word or inflected form in the curated 1800s vocabulary."""
    w = word.strip().lower()
    if not w:
        return None

    # Check exact headword
    if w in NORDIC_1800S_ENTRIES:
        return NORDIC_1800S_ENTRIES[w]

    # Check inflections / synonyms
    for entry in NORDIC_1800S_ENTRIES.values():
        if w in {inf.lower() for inf in entry.inflections}:
            return entry
        if w in {sp.lower() for sp in entry.historical_spellings}:
            return entry

    return None


def enrich_canonical_entry_with_historical(
    base: CanonicalEntry,
    historical: CanonicalEntry,
) -> CanonicalEntry:
    """Enrich a base entry (e.g. from COR-SEM) with historical senses and notes.
    
    Implements the false-friend enrichment rule: never overwrite or hide modern senses,
    but add historical senses, realia note, summary hook, and union inflections.
    """
    # Prefer historical summary hook if base hook is empty, or keep crisp hook
    if not base.summary_hook:
        base.summary_hook = historical.summary_hook
    elif historical.summary_hook and base.summary_hook != historical.summary_hook:
        # If modern definition was generic, replace with the rich historical hook if historic term
        base.summary_hook = historical.summary_hook

    # Merge POS if base pos is generic or empty
    if not base.pos and historical.pos:
        base.pos = historical.pos

    # Union inflections and historical spellings
    base.inflections.update(historical.inflections)
    for sp in historical.historical_spellings:
        if sp not in base.historical_spellings:
            base.historical_spellings.append(sp)

    # Merge historical senses (up to 4)
    seen_defs = {s.definition.strip().lower() for s in base.historical_senses}
    for hs in historical.historical_senses:
        if hs.definition.strip().lower() not in seen_defs:
            seen_defs.add(hs.definition.strip().lower())
            base.historical_senses.append(hs)

    # Attach realia note if missing
    if not base.realia_note and historical.realia_note:
        base.realia_note = historical.realia_note

    # Merge sources used
    for src in historical.sources_used:
        if src not in base.sources_used:
            base.sources_used.append(src)

    return base
