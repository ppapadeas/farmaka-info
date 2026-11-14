# farmaka.info

Ελεύθερη, ανοιχτή βάση δεδομένων φαρμάκων για την Ελλάδα: τιμές, αποζημίωση και κωδικοί από τα επίσημα
δελτία του Υπουργείου Υγείας, με ιστορικό και πηγή σε κάθε στοιχείο. Δωρεάν για όλους, χωρίς λογαριασμό,
χωρίς παρακολούθηση.

- Site: https://farmaka.info
- Δεδομένα και API: https://farmaka.info/dedomena/
- Κατάσταση, πηγές, γνωστά κενά: `docs/`

## Πώς δουλεύει

```
moh.gov.gr ──fetch──> data/raw (xlsx, pdf) ──ingest.build──> data/current (csv, json)
                                                   │
                                          sitegen.build ──> dist/ (σελίδες, λήψεις, API shards)
                                                   │
                                     Cloudflare Workers (web/worker.js: API, αναζήτηση, 404)
```

Ένα job στο GitHub Actions (`.github/workflows/update.yml`) τρέχει κάθε πρωί: διαβάζει τις δύο πρώτες
σελίδες των δελτίων τιμών, κατεβάζει ό,τι νέο, ξαναχτίζει δεδομένα και site και κάνει deploy.

## Τοπικά

```bash
python -m pip install -e ".[dev,pdf]"
python -m pytest -q
node --test "web/test/*.test.mjs"
python -m ingest.build --samples                     # offline, πάνω στα samples/raw
python -m sitegen.build --data data/current --out dist
npx wrangler dev                                     # http://localhost:8787
```

Ενημέρωση από το Υπουργείο (ευγενικά: ένα αίτημα κάθε 3 s, μόνο ό,τι λείπει):

```bash
python -m ingest.fetch --section prices --out data --max-pages 2
python -m ingest.build --manifest data/manifest_prices.json --raw data/raw --out data/current
```

## Σημαντικό

Η πληροφορία είναι ενημερωτική και δεν υποκαθιστά τη γνώμη γιατρού ή φαρμακοποιού. Η επίσημη πηγή
υπερισχύει πάντα.

## Άδειες

- Κώδικας: [AGPL-3.0-or-later](LICENSE)
- Δεδομένα: [CC0 1.0](LICENSE-DATA)

Λάθη στα δεδομένα: ανοίξτε issue με τον κωδικό ΕΟΦ και, αν γίνεται, το έγγραφο του Υπουργείου που δείχνει
τη σωστή τιμή.
