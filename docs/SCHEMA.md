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

`eof_code`, `published` (date), `effective` (date, nullable μέχρι να λυθεί το Α-5), `price_exfactory`, `price_wholesale`, `price_retail`, `non_reimbursed` (bool), `source_id`, `change` (`new`/`update`).

Η «τρέχουσα τιμή» είναι view: το τελευταίο γεγονός ανά `eof_code`.

### ReimbursementEvent

`eof_code`, `published`, `list` (`main` / `n3816`), `reimbursement_price`, `retail_in_list`, `ddd`, `ddd_unit`, `daily_doses_per_pack` (ΑΗΔ), `daily_cost` (ΚΗΘ), `dispensing` (τρόπος διάθεσης), `restriction_id`, `source_id`, `flag_s`, `flag_t` (μέχρι να αποκωδικοποιηθούν).

`Restriction`: `restriction_id`, `text`, `scope_atc` (σε ποια ομάδα ATC/οδό ισχύει).

### Document

`document_id`, `eof_code` ή `product_id`, `type` (`smpc`/`pil`/`label`), `language`, `version_date`, `url_original`, `sha256`, `source_id`, `sections` (μελλοντικό: δομημένες ενότητες 4.1–4.9 κ.λπ., συμβατές με ePI).

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
