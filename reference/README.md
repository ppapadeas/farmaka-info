# Δεδομένα αναφοράς

Μικρά αρχεία που μπαίνουν στο git επειδή αλλάζουν σπάνια και κάνουν το build ντετερμινιστικό.

## substances_el.csv

Ελληνικά ονόματα δραστικών ουσιών από το [Wikidata](https://www.wikidata.org) (άδεια CC0). Μια ουσία παίρνει
ελληνικό όνομα μόνο όταν ο κωδικός ATC των συσκευασιών της οδηγεί σε στοιχείο του Wikidata με το ίδιο αγγλικό
όνομα (ή το ίδιο όνομα χωρίς άλας, π.χ. METFORMIN HYDROCHLORIDE → metformin). Για ουσίες που εμφανίζονται μόνο
σε συνδυασμούς χρειάζεται ακριβής ταύτιση του αγγλικού ονόματος. Ό,τι δεν ταιριάζει με σιγουριά μένει χωρίς
ελληνικό όνομα και το site δείχνει το διεθνές (INN).

Ανανέωση (ένα ερώτημα στο Wikidata):

```bash
python -m ingest.substances_el --fetch --wikidata data/wikidata_atc_el.csv --packages data/current/packages.csv --out reference/substances_el.csv
```
