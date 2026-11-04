export type AnswerSource = {
  id: number;
  title: string;
  page: number;
  excerpt: string;
  sample: boolean;
  url: string;
};

// Present only cited evidence; retrieval candidates remain available to the API.
export function answerSources<T extends AnswerSource>(
  answer: string,
  sources: T[],
): { answer: string; sources: T[] } {
  const citedIds = [...answer.matchAll(/\[(\d+)\]/g)].map((match) =>
    Number(match[1]),
  );
  const available = new Map(sources.map((source) => [source.id, source]));
  if (citedIds.some((id) => !available.has(id))) {
    // Do not turn an unknown citation into a seemingly valid reference.
    return {
      answer,
      sources: sources.filter((source) => citedIds.includes(source.id)),
    };
  }
  const displayed: T[] = [];
  const pages = new Map<string, T>();
  const numbers = new Map<number, number>();
  const excerpts = new Map<string, Set<string>>();
  for (const id of citedIds) {
    if (numbers.has(id)) continue;
    const source = available.get(id)!;
    const key = JSON.stringify([source.url, source.page]);
    let existing = pages.get(key);
    if (!existing) {
      existing = { ...source, id: displayed.length + 1 };
      displayed.push(existing);
      pages.set(key, existing);
      excerpts.set(key, new Set([source.excerpt]));
    } else if (!excerpts.get(key)!.has(source.excerpt)) {
      // Two cited passages from one page share a chip, keeping both excerpts.
      existing.excerpt += "\n\n" + source.excerpt;
      excerpts.get(key)!.add(source.excerpt);
    }
    numbers.set(id, existing.id);
  }
  const text = answer
    .replace(/\[(\d+)\]/g, (_, id: string) => `[${numbers.get(Number(id))}]`)
    .replace(/(\[\d+\])(?:[ \t]*\1)+/g, "$1");
  return { answer: text, sources: displayed };
}
