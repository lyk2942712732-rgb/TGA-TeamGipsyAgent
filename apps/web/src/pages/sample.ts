/**
 * Reference-sample padding.
 *
 * The reference designs are drawn against a busy installation; a fresh one has
 * far fewer records — several catalogs are empty outright.  Every listing page
 * therefore renders its real API records first and pads the rest of the way to
 * the designed row count with the sample rows taken from the reference image.
 *
 * Rules that keep this honest:
 *  - real records always sort ahead of samples, and are never replaced;
 *  - a sample naming an entity that really exists is dropped, so the page never
 *    shows the same name twice;
 *  - sample rows carry `sample: true` and must not navigate to a fabricated id
 *    — route them to the section index instead;
 *  - nothing is labelled in the UI (the "样例" badges were removed on request);
 *    the design-vs-backend gap is tracked in the handover notes.
 */

export type Sampled = { sample: boolean };

export function padRows<T>(
  real: readonly T[],
  samples: readonly T[],
  rows: number,
  nameOf: (item: T) => string,
): T[] {
  const taken = new Set(real.map(nameOf));
  const fill = samples.filter((item) => !taken.has(nameOf(item)));
  return [...real, ...fill].slice(0, Math.max(rows, real.length));
}

/** Sums a real count with the sample rows standing behind the visible list. */
export function padCount(real: number, sampleOffset: number): number {
  return real + sampleOffset;
}
