import { test } from "node:test";
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { skeleton, queryTokens, codeFromQuery, parseIndex, search } from "../static/search-core.js";

const vectors = JSON.parse(readFileSync(new URL("../../tests/skeleton_vectors.json", import.meta.url), "utf8"));

test("skeleton matches the Python implementation", () => {
  for (const [input, expected] of vectors.skeleton) assert.equal(skeleton(input), expected, input);
});

test("Greek and greeklish queries find Latin names", () => {
  for (const [q, text] of vectors.matches) {
    const hay = skeleton(text);
    assert.ok(queryTokens(q).every((t) => hay.includes(t)), `${q} -> ${text}`);
  }
});

test("codes and barcodes", () => {
  assert.equal(codeFromQuery("312080101"), "312080101");
  assert.equal(codeFromQuery("2803120801017"), "312080101");
  assert.equal(codeFromQuery("depon"), null);
});

test("search ranks name prefix first", () => {
  const tsv = [
    ["1", "", "ZZZ TAB", "PARACETAMOL", "A", "1", "0", "0"],
    ["2", "", "PANADOL TAB", "PARACETAMOL", "B", "2", "0", "1"],
    ["3", "", "PARACETAMOL/X TAB", "PARACETAMOL", "C", "3", "1", "0"],
  ]
    .map((f) => [...f, skeleton(f[2]), skeleton(f[3]), skeleton(f[4])].join("\t"))
    .join("\n");
  const rows = parseIndex(tsv);
  const r = search(rows, "παρακεταμόλη");
  assert.equal(r.total, 3);
  assert.equal(r.results[0].eof_code, "3");
  assert.equal(search(rows, "panadol").results[0].eof_code, "2");
});

test("index carries strength and pack size, empty in older rows", () => {
  const f = ["1", "", "APINOVIA F.C.TAB 2.5MG/TAB BT X 56 TABS", "APIXABAN", "A", "42.17", "0", "1"];
  const sk = [skeleton(f[2]), skeleton(f[3]), skeleton(f[4])];
  const [withPack, without] = parseIndex(
    [[...f, ...sk, "2,5 mg · 56 τεμ."].join("\t"), [...f, ...sk].join("\t")].join("\n"),
  );
  assert.equal(withPack.pack, "2,5 mg · 56 τεμ.");
  assert.equal(without.pack, "");
});

test("worker shard function matches Python (tests/test_sitegen.py)", async () => {
  const { shardOf } = await import("../worker.js");
  assert.equal(shardOf("312080101"), "5e");
  assert.equal(shardOf("000090201"), "3c");
});

test("a product name without a page goes to the search", async () => {
  const worker = (await import("../worker.js")).default;
  const r = await worker.fetch(new Request("https://farmaka.info/onoma/depon-cold-flu/"), {});
  assert.equal(r.status, 302);
  assert.equal(r.headers.get("location"), "https://farmaka.info/anazitisi?q=depon%20cold%20flu");
});
