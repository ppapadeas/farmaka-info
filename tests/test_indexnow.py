"""IndexNow (sitegen/indexnow.py): only the addresses whose data changed are sent, no network."""

from sitegen import indexnow


def _sitemap(rows):
    return (
        '<?xml version="1.0" encoding="UTF-8"?>\n<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">\n'
        + "".join(f"<url><loc>{u}</loc>{f'<lastmod>{d}</lastmod>' if d else ''}</url>\n" for u, d in rows)
        + "</urlset>\n"
    )


OLD = [("https://farmaka.info/", "2026-11-20"), ("https://farmaka.info/farmako/1/", "2026-10-05")]
NEW = [
    ("https://farmaka.info/", "2026-11-25"),  # new data
    ("https://farmaka.info/farmako/1/", "2026-10-05"),  # unchanged
    ("https://farmaka.info/onoma/x/", "2026-10-05"),  # new page
]


class _Answer:
    def __init__(self, code):
        self.status_code = code


def test_only_changed_addresses(tmp_path):
    old, new = tmp_path / "old.xml", tmp_path / "new.xml"
    old.write_text(_sitemap(OLD), encoding="utf-8")
    new.write_text(_sitemap(NEW), encoding="utf-8")
    sent, logs = [], []

    def post(url, json, headers, timeout):
        sent.append(json)
        return _Answer(202)

    assert indexnow.main(["--old", str(old), "--new", str(new)], post=post, log=logs.append) == 0
    (body,) = sent
    assert body["urlList"] == ["https://farmaka.info/", "https://farmaka.info/onoma/x/"]
    assert body["host"] == "farmaka.info" and body["keyLocation"].endswith(f"/{indexnow.KEY}.txt")


def test_without_the_previous_sitemap_nothing_is_sent(tmp_path):
    new = tmp_path / "new.xml"
    new.write_text(_sitemap(NEW), encoding="utf-8")
    logs = []

    def post(*a, **k):
        raise AssertionError("nothing should be sent")

    assert (
        indexnow.main(["--old", str(tmp_path / "missing.xml"), "--new", str(new)], post=post, log=logs.append)
        == 0
    )
    assert "nothing sent" in logs[0]
    (tmp_path / "broken.xml").write_text("<html>", encoding="utf-8")
    assert (
        indexnow.main(["--old", str(tmp_path / "broken.xml"), "--new", str(new)], post=post, log=logs.append)
        == 0
    )


def test_batches_and_refusal():
    urls = [f"https://farmaka.info/farmako/{i}/" for i in range(indexnow.BATCH + 5)]
    sizes = []

    def post(url, json, headers, timeout):
        sizes.append(len(json["urlList"]))
        return _Answer(200)

    assert indexnow.submit(urls, post=post, log=lambda _: None)
    assert sizes == [indexnow.BATCH, 5]
    assert not indexnow.submit(urls, post=lambda *a, **k: _Answer(403), log=lambda _: None)
