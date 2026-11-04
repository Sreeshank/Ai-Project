import assert from "node:assert/strict";
import test from "node:test";
import { answerSources } from "./answer-sources.ts";

const source = (
  id,
  document,
  page = 1,
  excerpt = "passage",
  sample = false,
) => ({
  id,
  title: document,
  page,
  excerpt,
  sample,
  url: `/api/documents/${document}/source#page=${page}`,
});

test("the timetable answer shows only its two cited documents", () => {
  const sources = [
    source(1, "fee"),
    source(2, "fee"),
    source(3, "almanac"),
    source(4, "procedures", 1, "sample policy", true),
  ];
  const result = answerSources(
    "The timetable will be announced [2]. Check the noticeboard [4].",
    sources,
  );
  assert.deepEqual(
    result.sources.map((item) => item.title),
    ["fee", "procedures"],
  );
  assert.equal(
    result.answer,
    "The timetable will be announced [1]. Check the noticeboard [2].",
  );
  assert.deepEqual(
    result.sources.map((item) => item.id),
    [1, 2],
  );
  assert.equal(sources[1].id, 2);
});

test("cited passages on the same page share a chip without losing evidence", () => {
  const sources = [
    source(1, "fee", 1, "Regular fee: INR 2200"),
    source(2, "fee", 1, "Deadline: 29 October"),
  ];
  const result = answerSources(
    "Pay INR 2200 [1] [2]. The deadline is 29 October [2].",
    sources,
  );
  assert.equal(result.sources.length, 1);
  assert.equal(
    result.answer,
    "Pay INR 2200 [1]. The deadline is 29 October [1].",
  );
  assert.match(result.sources[0].excerpt, /Regular fee: INR 2200/);
  assert.match(result.sources[0].excerpt, /Deadline: 29 October/);
  assert.equal(sources[0].excerpt, "Regular fee: INR 2200");
});

test("different pages and different documents retain separate references", () => {
  const sources = [
    source(1, "notice", 1),
    source(2, "notice", 2),
    { ...source(3, "other", 1), title: "notice" },
  ];
  assert.equal(
    answerSources("A [1]. B [2]. C [3].", sources).sources.length,
    3,
  );
});

test("uncited sample candidates do not make a real answer look like a sample", () => {
  const result = answerSources("Pay INR 2200 [2].", [
    source(1, "sample", 1, "demo", true),
    source(2, "fee"),
  ]);
  assert.equal(
    result.sources.some((item) => item.sample),
    false,
  );
});

test("an answer without citations shows no source chips", () => {
  assert.deepEqual(
    answerSources("No information is available.", [source(1, "unrelated")])
      .sources,
    [],
  );
});

test("unknown citations cannot be renumbered to appear valid", () => {
  const result = answerSources("A [4]. Unknown [1].", [source(4, "notice")]);
  assert.equal(result.answer, "A [4]. Unknown [1].");
  assert.deepEqual(
    result.sources.map((item) => item.id),
    [4],
  );
});
