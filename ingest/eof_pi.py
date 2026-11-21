"""EOF SmPCs and package leaflets in sections, from the files `python -m ingest.eof crawl`
downloaded (data/eof/manifest.json).

EOF documents follow the same QRD templates as the EMA product information, so the
section parser of ingest.ema_pi is reused as it is: one JSON per product (7-digit EOF
product code), in the shape sitegen/leaflets.py reads ({"pil": [...], "smpc": [...],
"source": {...}}).

- Word (.doc, .docx) and .rtf files are converted to PDF with LibreOffice first; the
  downloaded file is never changed (the PDF goes to <root>/converted/).
- A document that does not follow the template (leaflets from before 2005 number their
  own headings: "1. ΠΡΟΣΔΙΟΡΙΣΜΟΣ…", "2. ΤΙ ΠΡΕΠΕΙ ΝΑ ΓΝΩΡΙΖΕΤΕ…") is split at those
  numbered headings ("layout": "numbered"); one without headings is kept as one text
  ("layout": "text"). Every character read is written, as for the EMA files.
- Scanned documents (no text layer) are reported, not guessed.

    python -m ingest.eof_pi parse --root data/eof --out data/eof-links/eof_pi
"""

from __future__ import annotations

import argparse
import json
import os
import re
import shutil
import subprocess
import sys
from concurrent.futures import ProcessPoolExecutor
from datetime import UTC, datetime
from pathlib import Path

from ingest import ema_pi as E

SCHEMA_VERSION = 1
PARSER_VERSION = 4  # bump to parse every document again (4: fake bold read once)
LOGGED_ERRORS = 50  # errors printed one by one; all of them are in report.json
PUBLISHER = "Εθνικός Οργανισμός Φαρμάκων (ΕΟΦ)"
SHORT = "ΕΟΦ"
ATTRIBUTION = "Πηγή: Εθνικός Οργανισμός Φαρμάκων (ΕΟΦ), αναζήτηση φαρμάκων ανθρώπινης χρήσης."
CONVERT = ("doc", "docx", "rtf")

# "1. ΠΡΟΣΔΙΟΡΙΣΜΟΣ ΦΑΡΜΑΚΕΥΤΙΚΟΥ ΠΡΟΪΟΝΤΟΣ" (old leaflets): a number and a title in capitals
_NUMBERED_HEAD = re.compile(r"^(\d{1,2})[.)]?\s+(\S.{3,})$")


def soffice() -> str | None:
    """LibreOffice's command line: $FARMAKA_SOFFICE, the PATH, or the usual Windows place."""
    for c in (os.environ.get("FARMAKA_SOFFICE"), shutil.which("soffice"), shutil.which("libreoffice")):
        if c:
            return c
    win = Path(r"C:\Program Files\LibreOffice\program\soffice.exe")
    return str(win) if win.exists() else None


def convert(files: list[Path], outdir: Path, exe: str | None = None, chunk: int = 40) -> dict[Path, Path]:
    """PDFs of Word/RTF files (made once, kept in outdir); a file LibreOffice cannot read is left out."""
    outdir.mkdir(parents=True, exist_ok=True)
    todo = [f for f in files if not (outdir / f"{f.stem}.pdf").exists()]
    if todo:
        exe = exe or soffice()
        if not exe:
            raise RuntimeError("LibreOffice not found: set FARMAKA_SOFFICE or install it")
        for i in range(0, len(todo), chunk):
            part = todo[i : i + chunk]
            subprocess.run(
                [
                    exe,
                    "--headless",
                    "--norestore",
                    "--convert-to",
                    "pdf",
                    "--outdir",
                    str(outdir),
                    *map(str, part),
                ],
                check=False,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                timeout=60 * len(part),
            )
    return {f: outdir / f"{f.stem}.pdf" for f in files if (outdir / f"{f.stem}.pdf").exists()}


def _caps(text: str) -> bool:
    letters = [c for c in text if c.isalpha()]
    return len(letters) >= 6 and sum(c.isupper() for c in letters) >= 0.7 * len(letters)


def _numbered(lines: list[E.Line], kind: str) -> tuple[list[dict], int] | None:
    """Split at the document's own top-level numbered headings in capitals (1, 2, 3, …);
    (docs, characters not written: the "." or ")" after each number)."""
    heads: list[tuple[int, str, str]] = []
    want = 1
    for i, ln in enumerate(lines):
        if ln.table:
            continue
        m = _NUMBERED_HEAD.match(ln.text.strip())
        if m and int(m.group(1)) == want and _caps(m.group(2)):
            heads.append((i, m.group(1), m.group(2).strip()))
            want += 1
    if len(heads) < 3:
        return None
    tally = E.Tally(right=E._right_edge(lines))
    pre = E.blocks(lines[: heads[0][0]], tally)
    sections = []
    markers = 0
    for k, (i, num, title) in enumerate(heads):
        markers += E.nonspace(lines[i].text) - E.nonspace(num) - E.nonspace(title)
        end = heads[k + 1][0] if k + 1 < len(heads) else len(lines)
        sections.append(
            {
                "number": num,
                "title": title,
                "page": lines[i].page,
                "blocks": E.blocks(lines[i + 1 : end], tally),
            }
        )
    pages = [ln.page for ln in lines]
    doc = {
        "type": kind,
        **({"title": ""} if kind == "pil" else {}),
        "pages": [min(pages), max(pages)],
        "products": [],
        "preamble": pre,
        "sections": sections,
    }
    return [doc], markers + tally.markers  # plus bullets removed by blocks()


def _complete(docs: list[dict], kind: str) -> bool:
    """The template was found: most leaflet sections (1-6), or SmPC 4.1 and 4.2 and most of
    the rest. A short SmPC (water for injection has no 4.5-4.9) is still the template."""
    found = {s["number"] for d in docs for s in d["sections"]}
    if not any(s["blocks"] for d in docs for s in d["sections"]):
        return False
    if kind == "pil":
        return sum(n in found for n in E.PIL_EXPECTED) >= 4
    return {"4.1", "4.2"} <= found and sum(n in found for n in E.SMPC_EXPECTED) >= 12


def _drop_page_numbers(pages: list[E.Page]) -> tuple[list[E.Page], int]:
    """Page numbers the furniture pass leaves (they differ on every page): a line of 1-3 digits
    that is the first or the last line of its page. Returns the pages and their characters."""
    dropped = 0
    out = []
    for pg in pages:
        lines = list(pg.lines)
        for pos in (-1, 0):
            if lines and not lines[pos].table and re.fullmatch(r"\d{1,3}", lines[pos].text.strip()):
                dropped += E.nonspace(lines[pos].text)
                lines.pop(pos)
        out.append(E.Page(pg.number, pg.width, pg.height, lines, pg.chars_read, pg.chars_pdfium))
    return out, dropped


def parse_document(path: Path, kind: str) -> dict:
    """{"layout", "docs", "pages", "warnings"} for one SmPC or leaflet file; ParseError when unreadable."""
    pages = E.read_pdf(path)
    if not any(pg.lines for pg in pages):
        raise E.ParseError("no text in the file (scanned?)")
    share = E.greek_share(ln.text for pg in pages for ln in pg.lines)
    if share < 0.5:
        raise E.ParseError(f"not in Greek ({share:.0%} of the letters are Greek)")
    pages, _removed = E.strip_furniture(pages)
    chars_in = sum(E._line_chars(ln) for pg in pages for ln in pg.lines)
    pages, page_numbers = _drop_page_numbers(pages)
    lines = [ln for pg in pages for ln in pg.lines]

    tally = E.Tally(right=E._right_edge(lines))
    parse = E.parse_smpc if kind == "smpc" else E.parse_pil
    docs = [E._doc_json(d, tally) for d in parse(lines, tally)]
    layout, warnings = "qrd", list(tally.notes)
    if not _complete(docs, kind):
        layout = "numbered"
        numbered = _numbered(lines, kind)
        if numbered is not None:
            docs, tally_markers = numbered
        else:
            tally = E.Tally(right=E._right_edge(lines))
            pg = [ln.page for ln in lines]
            docs = [
                {
                    "type": kind,
                    **({"title": ""} if kind == "pil" else {}),
                    "pages": [min(pg), max(pg)],
                    "products": [],
                    "preamble": E.blocks(lines, tally),
                    "sections": [],
                }
            ]
            layout, tally_markers = "text", tally.markers
    else:
        tally_markers = tally.markers
    written = E._chars_out(docs) + tally_markers + page_numbers
    if written != chars_in:
        raise E.ParseError(f"internal: {chars_in} characters read, {written} written")
    # a repeated title page («Φύλλο οδηγιών χρήσης: Πληροφορίες για τον χρήστη» twice) makes a
    # document with nothing in it: dropped once the characters are accounted for
    empty = [d for d in docs if not d["sections"] and not d["preamble"]]
    if empty and len(empty) < len(docs):
        docs = [d for d in docs if d["sections"] or d["preamble"]]
        warnings.append(f"{len(empty)} title-only document(s) dropped: {[d.get('title', '') for d in empty]}")
    return {"layout": layout, "docs": docs, "pages": len(pages), "warnings": warnings}


def _date(doc: dict) -> str:
    return doc.get("pdf_modified") or doc.get("pdf_created") or ""


def product_json(code: str, product: dict, parsed: dict[str, list[tuple[dict, dict]]]) -> dict:
    """The product JSON; `parsed[kind]` holds (manifest document, parse result) pairs."""
    sources = {
        kind: {
            "publisher": PUBLISHER,
            "short": SHORT,
            "url": got[0][0]["url"],
            "date": _date(got[0][0]),
            "retrieved_at": got[0][0].get("retrieved_at", ""),
            "sha256": got[0][0].get("sha256", ""),
            "attribution": ATTRIBUTION,
        }
        for kind, got in parsed.items()
        if got
    }
    return {
        "schema_version": SCHEMA_VERSION,
        "product_number": f"EOF-{code}",
        "product_code": code,
        "name": product.get("name", ""),
        "eof_codes": product.get("eof_codes_on_page") or product.get("eof_codes") or [],
        "language": "el",
        "source": sources.get("pil") or sources.get("smpc") or {},
        "sources": sources,
        "layout": {kind: [r["layout"] for _, r in got] for kind, got in parsed.items()},
        "pil": [d for _, r in parsed.get("pil", []) for d in r["docs"]],
        "smpc": [d for _, r in parsed.get("smpc", []) for d in r["docs"]],
        "warnings": [w for got in parsed.values() for _, r in got for w in r["warnings"]],
    }


def _job(args: tuple[str, str, str]) -> tuple[str, dict | None, str]:
    key, path, kind = args
    try:
        return key, parse_document(Path(path), kind), ""
    except E.ParseError as e:
        return key, None, str(e)
    except Exception as e:  # a broken file must not stop the run; it is reported
        return key, None, f"{type(e).__name__}: {e}"


def parse_all(root: Path, out: Path, jobs: int = 1, log=print) -> dict:
    """Parse the documents of every product done in root/manifest.json; only what changed."""
    manifest = json.loads((root / "manifest.json").read_text(encoding="utf-8"))
    docs_by_url: dict[str, dict] = manifest.get("documents", {})
    out.mkdir(parents=True, exist_ok=True)
    state_path = out / "_state.json"
    state = json.loads(state_path.read_text(encoding="utf-8")) if state_path.exists() else {}
    if state.get("parser") != PARSER_VERSION:
        state = {"parser": PARSER_VERSION, "products": {}, "errors": {}}

    wanted: dict[str, list[dict]] = {}
    for code, prod in sorted(manifest.get("products", {}).items()):
        if prod.get("state") != "done":
            continue
        got = [
            docs_by_url[d["url"]] | {"url": d["url"]}
            for d in prod.get("documents", [])
            if d["url"] in docs_by_url
        ]
        if got:
            wanted[code] = got
    # new or changed documents, and every product with a document that failed before:
    # few, and a fix in the parser reaches them without a new parser version
    todo = {
        code: got
        for code, got in wanted.items()
        if state["products"].get(code) != sorted(d.get("sha256", "") for d in got)
        or not (out / f"{code}.json").exists()
        or any(d["url"] in state["errors"] for d in got)
    }
    log(f"{len(wanted)} products with documents, {len(todo)} to parse")

    to_convert = [root / d["path"] for got in todo.values() for d in got if d.get("format") in CONVERT]
    pdfs = convert(to_convert, root / "converted") if to_convert else {}
    jobs_in: list[tuple[str, str, str]] = []
    errors: dict[str, str] = {}
    for code, got in todo.items():
        for d in got:
            src = root / d["path"]
            path = src if d.get("format", "pdf") == "pdf" else pdfs.get(src)
            if path is None:
                errors[d["url"]] = f"{code}: {d.get('format')} file LibreOffice could not convert"
                continue
            jobs_in.append((d["url"], str(path), d["type"]))
    results: dict[str, dict] = {}
    run = ProcessPoolExecutor(max_workers=jobs).map if jobs > 1 else map
    for url, res, err in run(_job, jobs_in):
        if res is None:
            errors[url] = err
        else:
            results[url] = res

    for code, got in todo.items():
        parsed: dict[str, list[tuple[dict, dict]]] = {}
        for d in sorted(got, key=lambda d: (d["type"], d.get("version", ""))):
            if d["url"] in results:
                parsed.setdefault(d["type"], []).append((d, results[d["url"]]))
        for d in got:
            state["errors"].pop(d["url"], None)
            if d["url"] in errors:
                state["errors"][d["url"]] = f"{code} {d['type']}: {errors[d['url']]}"
        if parsed:
            prod = manifest["products"][code]
            (out / f"{code}.json").write_text(
                json.dumps(product_json(code, prod, parsed), ensure_ascii=False, indent=1), encoding="utf-8"
            )
        state["products"][code] = sorted(d.get("sha256", "") for d in got)
    state_path.write_text(json.dumps(state, ensure_ascii=False, indent=1), encoding="utf-8")

    layouts: dict[str, int] = {}
    for f in out.glob("[0-9]*.json"):
        for kind, ls in json.loads(f.read_text(encoding="utf-8")).get("layout", {}).items():
            for x in ls:
                layouts[f"{kind}:{x}"] = layouts.get(f"{kind}:{x}", 0) + 1
    report = {
        "parsed_at": datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "parser": PARSER_VERSION,
        "products": len(list(out.glob("[0-9]*.json"))),
        "layouts": dict(sorted(layouts.items())),
        "errors": len(state["errors"]),
        "error_list": sorted(state["errors"].values()),
    }
    (out / "report.json").write_text(json.dumps(report, ensure_ascii=False, indent=1), encoding="utf-8")
    log(f"{report['products']} products in sections; layouts {report['layouts']}; {report['errors']} errors")
    # each error on its own line, with the document, so it can be found from the job log
    errs = sorted(state["errors"].items(), key=lambda kv: kv[1])
    for url, msg in errs[:LOGGED_ERRORS]:
        msg = " ".join(msg.split())
        log(f"  error: {msg[:300]}{'…' if len(msg) > 300 else ''} <{url}>")
    if len(errs) > LOGGED_ERRORS:
        log(f"  … {len(errs) - LOGGED_ERRORS} more in report.json")
    return report


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="EOF SmPC and package leaflets in sections.")
    sub = ap.add_subparsers(dest="cmd", required=True)
    p = sub.add_parser("parse", help="parse the downloaded documents (only the ones that changed)")
    p.add_argument("--root", type=Path, default=Path("data/eof"))
    p.add_argument("--out", type=Path, default=Path("data/eof-links/eof_pi"))
    p.add_argument("--jobs", type=int, default=os.cpu_count() or 1)
    args = ap.parse_args(argv)
    if not (args.root / "manifest.json").exists():
        print(f"no {args.root / 'manifest.json'}: nothing to parse")
        return 0
    try:
        import pdfplumber  # noqa: F401
    except ImportError:
        print('pdfplumber is not installed: pip install -e ".[pdf]"')
        return 1
    parse_all(args.root, args.out, args.jobs)
    return 0


if __name__ == "__main__":
    sys.exit(main())
