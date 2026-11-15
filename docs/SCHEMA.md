# Μοντέλο δεδομένων (πρόταση v0)

Στόχος: ένα καθαρό, σταθερό μοντέλο που να δέχεται πολλές πηγές, να κρατάει ιστορικό και προέλευση, και να μπορεί αργότερα να εξαχθεί σε EU ePI / FHIR.

## Οντότητες

```
Substance ──< ProductSubstance >── Product ──< Package ──< PriceEvent
    │                                  │           │
   ATC                                MAH          ├──< ReimbursementEvent
                                       │           └──< Availability (μελλοντικό)
                                       └──< Document (ΠΧΠ/ΦΟΧ, εκδόσεις)
Source ──< (κάθε εγγραφή γεγονότος έχει source_id)
```

### Package (συσκευασία) — η βασική μονάδα

| πεδίο | τύπος | σημείωση |
|---|---|---|
| `eof_code` | char(9) PK | κωδικός ΕΟΦ |
| `barcode` | char(13) unique | EAN-13, πρόθεμα 280, ενσωματώνει `eof_code` |
| `product_id` | FK | |
| `pack_description` | text | π.χ. `BT X 30 TABS ΣΕ PVC/PVDC-ALU BLISTER` |
| `pack_size` | numeric? | εξαγόμενο, nullable |
| `vat` | numeric | 0.06 / 0.13 / 0.24 |
| `raw_name` | text | όπως στο δελτίο, για ιχνηλασιμότητα |
| `status` | enum | `active` / `withdrawn` / `unknown` |

### Product (σκεύασμα: όνομα + μορφή + περιεκτικότητα)

`product_id`, `brand_name`, `dose_form_raw` (π.χ. `F.C.TAB`), `dose_form` (κανονικοποιημένο, λεξικό EDQM Standard Terms αργότερα), `strength_raw`, `mah_id`, `route` (από S2).

### Substance

`substance_id`, `inn` (αγγλικά, κεφαλαία όπως στο δελτίο), `inn_el` (μελλοντικό), `atc` (7 χαρ. όταν υπάρχει), εξωτερικά ids (DDInter, EMA, WHO ATC) σε πίνακα `substance_xref`.

### MAH (Κάτοχος Άδειας Κυκλοφορίας)

`mah_id`, `name_raw`, `name_normalized`. Ίδια εταιρεία εμφανίζεται με παραλλαγές ονόματος· η αποδιπλοποίηση γίνεται με χειροκίνητο πίνακα alias.

### PriceEvent (ιστορικό τιμών, append-only)

`eof_code`, `published` (date), `effective` (date, nullable μέχρι να λυθεί το Α-5), `price_exfactory`, `price_wholesale`, `price_retail`, `non_reimbursed` (bool), `source_id`, `change`.

Η «τρέχουσα τιμή» είναι view: το τελευταίο γεγονός ανά `eof_code`.

Στο `price_history.csv` (στήλες `eof_code, field, old, new, change, published, moh_article`) κάθε γεγονός γράφεται ως μία γραμμή ανά πεδίο (`price_exfactory`, `price_wholesale`, `price_retail`, `non_reimbursed`). Κενό `old`/`new` σημαίνει «δεν είναι στον κατάλογο». Ανά (`eof_code`, `field`) το `old` κάθε γραμμής είναι το `new` της προηγούμενης, οπότε η σειρά των `new` είναι η σειρά τιμών (με κενά όσο η συσκευασία είναι αποσυρμένη).

| `change` | πότε | `old` | `new` |
|---|---|---|---|
| `first_seen` | ο κωδικός εμφανίζεται πρώτη φορά, σε πλήρη κατάλογο (`full`)· όλοι οι κωδικοί του παλαιότερου `full` | κενό | τιμή |
| `new` | ο κωδικός εμφανίζεται πρώτη φορά, σε δελτίο αλλαγών (`delta`), π.χ. νέα γενόσημα | κενό | τιμή |
| `update` | άλλαξε τιμή: με `delta`, ή στη σύγκριση της κατάστασης με το επόμενο `full` | προηγούμενη | νέα |
| `withdrawn` | λείπει από `full` ενώ υπήρχε στην κατάσταση (Α-4) | τελευταία | κενό |
| `reappeared` | αποσυρμένος κωδικός ξαναβγαίνει, σε `full` ή `delta` | κενό | τιμή |

Ένα γράφημα τιμής ξεκινά από το `first_seen` ή το `new` της συσκευασίας.

### ReimbursementEvent

`eof_code`, `published`, `list` (`main` / `n3816`), `reimbursement_price`, `retail_in_list`, `ddd`, `ddd_unit`, `daily_doses_per_pack` (ΑΗΔ), `daily_cost` (ΚΗΘ), `dispensing` (τρόπος διάθεσης), `restriction_id`, `source_id`, `flag_s`, `flag_t` (μέχρι να αποκωδικοποιηθούν).

`Restriction`: `restriction_id`, `text`, `scope_atc` (σε ποια ομάδα ATC/οδό ισχύει).

#### ReimbursementRow (έξοδος `ingest/reimbursement.py`, `reimbursement_<list>.csv`)

Μία γραμμή ανά συσκευασία του καταλόγου, όπως τυπώνεται. Δεν είναι ακόμη `ReimbursementEvent`: δεν έχει `published`/`source_id` (τα προσθέτει το build ή το CLI με `--article`/`--published`) και κρατά τα κείμενα των περιορισμών αντί για `restriction_id`.

| πεδίο | τύπος | σημείωση |
|---|---|---|
| `eof_code` | char(9) | ΚΩΔΙΚΟΣ, μοναδικός ανά αρχείο |
| `list_name` | text | `main` / `n3816` |
| `atc` | text | όπως τυπώνεται· 12 γραμμές έχουν ψευδο-ATC (`A12AX-1`, `D05AXxx`, `D05AX--`, `L01FFF0`) |
| `name_pdf`, `dose_form`, `strength`, `pack`, `dispensing`, `mah_pdf` | text | κομμένα στο pdf· τα πλήρη έρχονται από S1 |
| `ddd`, `ddd_unit` | numeric?, text? | κενό όταν τυπώνεται 0 (δεν δίνεται ή < 0,0005 της μονάδας) |
| `daily_doses_per_pack` | numeric? | ΑΗΔ· κενό όταν τυπώνεται 0 |
| `daily_cost` | numeric? | ΚΗΘ = ΛΙΑΝΙΚΗ / ΑΗΔ· κενό όταν τυπώνεται 0 |
| `retail_in_list` | numeric | ΛΙΑΝΙΚΗ όπως στον κατάλογο |
| `reimbursement_price` | numeric | ΑΠΟΖΗΜ., πάντα > 0 |
| `flag_s`, `flag_t` | text | στήλες `Σ`, `Τ` όπως τυπώνονται (`Φ`, `Ν`, `N`, `(Φ)`, `(Ν)`, κενό)· σημασία άγνωστη |
| `route_group` | text | οδός χορήγησης (κόκκινη επικεφαλίδα) |
| `therapeutic_group` | text? | ομάδα «G01 …» μέσα σε ATC4 (πράσινη επικεφαλίδα), αν υπάρχει |
| `restrictions` | list[text] | όλες οι σημειώσεις περιορισμού που ισχύουν, από το εξωτερικό επίπεδο προς το εσωτερικό· στο CSV ενωμένες με ` \|\| ` |
| `page` | int | σελίδα του pdf (από 1) |
| `source_file`, `moh_article`, `published` | text | μόνο στο CSV: αρχείο, άρθρο και ημερομηνία δημοσίευσης |

### Document

`document_id`, `eof_code` ή `product_id`, `type` (`smpc`/`pil`/`label`), `language`, `version_date`, `url_original`, `sha256`, `source_id`, `sections` (δομημένες ενότητες 4.1–4.9 κ.λπ., συμβατές με ePI· για τα κεντρικά εγκεκριμένα, βλ. παρακάτω).

#### Πληροφορίες προϊόντος EMA (έξοδος `ingest/ema_pi.py`, `ema_pi/<EMEA-H-C-NNNNNN>.json`)

Ένα αρχείο JSON ανά προϊόν της EMA, από το ελληνικό PDF πληροφοριών προϊόντος (S4). Το όνομα αρχείου είναι ο αριθμός προϊόντος με `-` αντί για `/`. Σύνδεση με τις συσκευασίες: `ema_links.csv` (`ema_product_number`). Τα ονόματα πεδίων είναι δημόσιο API (`schema_version`).

| πεδίο | τύπος | σημείωση |
|---|---|---|
| `schema_version` | int | σήμερα `1` |
| `product_number` | text | `EMEA/H/C/000071` |
| `name` | text | όνομα στην EMA |
| `language` | text | `el` |
| `source` | object | `publisher` (`European Medicines Agency`), `url` (το ελληνικό PDF), `sha256` και `retrieved_at` (UTC, ISO 8601) του αρχείου που διαβάστηκε, `last_modified` (κεφαλίδα HTTP), `pages`, `attribution` (αναφορά πηγής· εμφανίζεται μαζί με κάθε κείμενο) |
| `parts` | object | σελίδες `[πρώτη, τελευταία]` ανά μέρος, μαζί με τις σελίδες-διαχωριστικά: `smpc` (Παράρτημα Ι), `annex_ii`, `annex_iii` (μόνο το διαχωριστικό), `labelling` (ΙΙΙ Α), `pil` (ΙΙΙ Β), σπανιότερα `front` (πριν από το Παράρτημα Ι) και `annex_iv`. Τα `annex_ii`, `labelling`, `annex_iv` δεν αναλύονται |
| `front` | list[Block] | κείμενο πάνω από το «ΠΑΡΑΡΤΗΜΑ Ι», π.χ. σημείωση ότι στο έγγραφο επισημαίνονται αλλαγές· συνήθως κενό |
| `smpc` | list[Document] | μία ή περισσότερες ΠΧΠ: ένα PDF έχει είτε μία κοινή ΠΧΠ για όλες τις περιεκτικότητες/μορφές είτε μία ανά περιεκτικότητα/μορφή |
| `pil` | list[Document] | ένα ή περισσότερα ΦΟΧ (συνήθως ένα ανά περιεκτικότητα/μορφή) |
| `warnings` | list[text] | ό,τι δεν εμπόδισε την ανάλυση αλλά αξίζει έλεγχο: ενότητες της ΠΧΠ που δεν βρέθηκαν εκτός από τις υποχρεωτικές, κενές ενότητες, επικεφαλίδες με λάθος αριθμό ή χωρίς αριθμό στο PDF που διαβάστηκαν από τον τίτλο τους |

**Document** (ΠΧΠ ή ΦΟΧ):

| πεδίο | τύπος | σημείωση |
|---|---|---|
| `type` | text | `smpc` / `pil` |
| `title` | text | μόνο `pil`: ο τίτλος όπως τυπώνεται, π.χ. «Φύλλο οδηγιών χρήσης: Πληροφορίες για τον χρήστη» |
| `pages` | [int, int] | πρώτη και τελευταία σελίδα |
| `products` | list[text] | `smpc`: οι γραμμές της ενότητας 1· `pil`: οι έντονες γραμμές κάτω από τον τίτλο (όνομα, περιεκτικότητα, μορφή) |
| `preamble` | list[Block] | κείμενο πριν από την ενότητα 1: ▼ «συμπληρωματική παρακολούθηση», «Διαβάστε προσεκτικά…», ο κατάλογος «Τι περιέχει το παρόν φύλλο οδηγιών» |
| `sections` | list[Section] | με τη σειρά του PDF, επίπεδη λίστα: το `4` ακολουθείται από `4.1` … `4.9`· η ιεραρχία βγαίνει από τον αριθμό |

**Section**: `number` (text: `"1"`, `"4.8"`, `"10"`· στο ΦΟΧ `"1"`–`"6"`), `title` (όπως τυπώνεται, χωρίς τον αριθμό), `page` (σελίδα της επικεφαλίδας), `blocks` (list[Block]). Μια ενότητα που έχει μόνο υποενότητες (`4`, `5`, `6`) έχει κενό `blocks`. Στο ΦΟΧ οι «Οδηγίες χρήσης» που ακολουθούν την ενότητα 6 μένουν μέσα στην 6.

**Block**, κατά `type`:

| `type` | πεδία | σημείωση |
|---|---|---|
| `paragraph` | `text` | `\n` μέσα στο κείμενο = αλλαγή γραμμής του πρωτοτύπου (διευθύνσεις, κατάλογοι εκδόχων, αριθμοί άδειας)· οι υπόλοιπες γραμμές ενώνονται με κενό. Αριθμημένα σημεία («1)», «α.») μένουν παράγραφοι με τον αριθμό τους |
| `subheading` | `text` | σύντομη γραμμή σε έντονα ή πλάγια, χωρίς τελεία στο τέλος |
| `list` | `items` (list[text]), `level` (1 ή 2) | λίστα με κουκκίδες· η κουκκίδα δεν κρατιέται. `level` 2 = πιο εσωτερική από την προηγούμενη λίστα |
| `table` | `rows` (list[list[text]]) | πίνακας με γραμμές πλαισίου, όλες οι γραμμές ίδιου πλάτους· η πρώτη είναι συνήθως η κεφαλίδα (δεν σημειώνεται). `""` = κενό ή συγχωνευμένο κελί |

Παράδειγμα (συντομευμένο):

```json
{
  "schema_version": 1,
  "product_number": "EMEA/H/C/000184",
  "name": "Evista",
  "language": "el",
  "source": {"publisher": "European Medicines Agency", "url": "https://www.ema.europa.eu/el/documents/product-information/evista-epar-product-information_el.pdf",
             "sha256": "…", "retrieved_at": "2026-11-14T12:09:51Z", "last_modified": "…", "pages": 31, "attribution": "Πηγή: Ευρωπαϊκός Οργανισμός Φαρμάκων (EMA), …"},
  "parts": {"smpc": [1, 15], "annex_ii": [16, 17], "annex_iii": [18, 18], "labelling": [19, 24], "pil": [25, 31]},
  "front": [],
  "smpc": [{"type": "smpc", "pages": [2, 15], "products": ["Evista 60 mg επικαλυμμένα με υμένιο δισκία"], "preamble": [],
            "sections": [{"number": "4.3", "title": "Αντενδείξεις", "page": 3,
                          "blocks": [{"type": "paragraph", "text": "Υπερευαισθησία στη δραστική ουσία ή σε κάποιο από τα έκδοχα που αναφέρονται στην παράγραφο 6.1."}]}]}],
  "pil": [{"type": "pil", "title": "Φύλλο οδηγιών χρήσης: Πληροφορίες για τον χρήστη", "pages": [26, 31], "products": ["Evista 60 mg επικαλυμμένα με υμένιο δισκία"], "preamble": [],
           "sections": [{"number": "2", "title": "Τι πρέπει να γνωρίζετε πριν να πάρετε το Evista", "page": 26,
                         "blocks": [{"type": "subheading", "text": "Μην πάρετε το Evista:"},
                                    {"type": "list", "level": 1, "items": ["Εάν έχετε σοβαρής μορφής νεφρικά προβλήματα.", "…"]}]}]}],
  "warnings": []
}
```

Αναφορά της εκτέλεσης: `ema_pi_report.json` δίπλα στον φάκελο: `generated`, `summary` (`pdfs`, `parsed`, `failed`, `json_bytes`), `parsed` (ανά προϊόν: `product_number`, `name`, `file`, πλήθος ενοτήτων ανά ΠΧΠ/ΦΟΧ, `warnings`), `failed` (`product_number`, `name`, `file`, `reason`). Ένα PDF που δεν αναλύεται καθαρά δεν γράφεται καθόλου· εμφανίζεται μόνο στο `failed`.

Συμβατότητα με ePI (FHIR): κάθε Document αντιστοιχεί σε `Composition`, κάθε Section σε `Composition.section` (αριθμός και τίτλος του προτύπου QRD), τα Block σε XHTML (`<p>`, `<h3>`, `<ul>`, `<table>`).

### Source

`source_id`, `publisher` (`moh`/`eof`/`ema`/…), `article_id`, `title`, `published`, `url`, `file_name`, `sha256`, `retrieved_at`, `kind`.

## Εξαγωγές

- `packages.csv` / `packages.json`: τρέχουσα εικόνα (έχει ήδη υλοποιηθεί σε απλή μορφή).
- `price_history.csv`: όλα τα γεγονότα.
- `farmaka.sqlite`: πλήρες μοντέλο, για όποιον θέλει να τρέξει τοπικά (π.χ. με Datasette).
- Κάθε release: tag με ημερομηνία, checksums, changelog «τι άλλαξε από το προηγούμενο».
- Επίπεδο αλληλεπιδράσεων (DDInter): **ξεχωριστό** dump με δική του άδεια.

## Σταθερότητα

- Τα ονόματα πεδίων των εξαγωγών είναι δημόσιο API. Αλλαγή = νέα major έκδοση σχήματος (`schema_version` σε κάθε dump).

## Ελλείψεις (`shortages.csv`)

Μία γραμμή ανά συσκευασία και λίστα, από `python -m ingest.shortages build` (S9).

| πεδίο | σημείωση |
|---|---|
| `eof_code` | κωδικός ΕΟΦ· κενό όταν η εγγραφή δεν αντιστοιχίζεται με σιγουριά |
| `list_type` | `limited_availability` / `discontinuation` / `shortage` / `eu_shortage` (`export_ban` όταν γίνει διαθέσιμο σε κείμενο) |
| `status` | αιτία όπως γράφεται στη λίστα (π.χ. «Καθυστέρηση παράδοσης») |
| `published` | ημερομηνία της λίστας |
| `start`, `valid_until` | έναρξη / λήξη όπως γράφονται στη λίστα |
| `name`, `barcode` | όπως στη λίστα |
| `alternatives` | πρόταση εναλλακτικής του ΕΟΦ (λίστα διακοπών) |
| `matched_by` | `barcode`, `eof_code`, `brand+strength`, `ema_links` |
| `in_catalogue` | `packages` / `otc` / κενό |
| `source_url`, `source_file`, `source_title` | ανακοίνωση και αρχείο της πηγής |

Στο API και στο `farmaka.json` κάθε συσκευασία έχει `availability`: τις τρέχουσες εγγραφές (`type`, `reason`,
`published`, `start`, `valid_until`, `alternatives`, `source_url`, `source_title`). Εγγραφές περιορισμένης
διαθεσιμότητας με ημερομηνία λήξης που πέρασε δεν εμφανίζονται.

## Αλλαγές (`allages.csv`)

Οι αλλαγές της σελίδας `/allages/` («Τι άλλαξε»), από `sitegen/changes.py` σε κάθε build: όσα έγιναν τις 60 ημέρες
έως το νεότερο δελτίο ή λίστα. Μία γραμμή ανά αλλαγή συσκευασίας, νεότερα πρώτα. Η ίδια επιλογή, ανά ημερομηνία,
είναι και η ροή RSS `/allages/rss.xml`.

| πεδίο | σημείωση |
|---|---|
| `published` | ημερομηνία δημοσίευσης του δελτίου (`price_history.csv`) ή ημερομηνία της λίστας (`shortages.csv`) |
| `publisher` | `moh` (δελτίο τιμών του Υπουργείου Υγείας) / `eof` (λίστα ή ανακοίνωση του ΕΟΦ) |
| `source_id` | άρθρο του moh.gov.gr (`moh_article`)· κενό για τον ΕΟΦ |
| `source_title`, `source_url` | τίτλος και σύνδεσμος του δελτίου ή της ανακοίνωσης |
| `change` | `price`: άλλαξε η λιανική (`update`)· `new`: πρώτη εμφάνιση του κωδικού (`new`, ή `first_seen` σε πλήρη κατάλογο μετά τον πρώτο που έχουμε· τα `first_seen` του πρώτου δεν είναι αλλαγές)· `reappeared`· `withdrawn`· `limited_availability` / `shortage` / `discontinuation`: η συσκευασία είναι στη λίστα ή την ανακοίνωση του ΕΟΦ που δημοσιεύτηκε (όλη η λίστα, όχι μόνο όσα προστέθηκαν) |
| `eof_code` | κωδικός ΕΟΦ· κενό για εγγραφή λίστας του ΕΟΦ που δεν αντιστοιχίστηκε |
| `name` | ονομασία όπως στο δελτίο· για ό,τι βγήκε από τον νεότερο πλήρη κατάλογο, η τελευταία του ονομασία· για ό,τι δεν αντιστοιχίστηκε, όπως στη λίστα |
| `old`, `new` | λιανική (€) πριν και μετά· κενό όταν δεν υπάρχει (π.χ. `old` σε `new`, `new` σε `withdrawn`, και τα δύο στις λίστες) |
| `pct` | μεταβολή της λιανικής %, ένα δεκαδικό, μόνο για `price` |
