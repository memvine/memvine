// The text an empty project's paste box starts with: a short standup in which a decision is made,
// then given its reason and a detail that extends it, a date moves, and a standing rule is set.
// server/tests/test_instant.py keeps the same text (and a fake model's reading of it); keep the
// two the same.
export const PASTE_SNIPPET = `Standup, Tue 27 Oct 2026

Sam: The nightly invoice export kept timing out on large accounts, so I capped export retries at 3 per batch.
Priya: Why 3?
Sam: The billing API rate-limits us to 100 requests a minute. Past 3 retries the batches just queue behind the limit, so each export retry now waits 30 seconds before it runs.
Priya: Good. Then the release moves from Thu 29 Oct to Tue 3 Nov, so finance gets a week to check invoices from the new export.
Sam: I'll keep the old CSV exporter as a fallback until 3 Nov; two customers still pull from it.
Priya: From now on any change that touches billing gets a review from Dana before merge.`;

/** The same text, whatever happened to its whitespace in a textarea. */
export const isPasteSnippet = (text: string) => squash(text) === squash(PASTE_SNIPPET);
const squash = (text: string) => text.replace(/\s+/g, " ").trim();
