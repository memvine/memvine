/**
 * Model adapter for the one benchmark axis that needs an LLM: does a fresh
 * agent complete the task better WITH memvine's recalled memory than without,
 * and than with a static AGENTS.md holding the same fact?
 *
 * The deterministic harness (run.mjs) does not import this — retrieval and
 * staleness need no model. Wire this up only when you want the task-success
 * axis, e.g. against your local mlx model.
 *
 * Implement `generate` and `judge`, then extend run.mjs to, per case, run four
 * conditions over the SAME task prompt and compare judgments:
 *   - no-memory:   task prompt only
 *   - static-doc:  task prompt + the fact as a plain AGENTS.md line
 *   - memvine:     task prompt + rendered recall() output
 *   - (optional)   memvine with a competing memory system holding the same fact
 *
 * Keep temperature 0 and use the SAME model for generate and judge, as your
 * MemoryArena smoke test did, so the comparison is apples-to-apples.
 */

/** @typedef {{ prompt: string }} GenInput */

/** Return the model's answer text for a prompt. */
export async function generate(_input /* : GenInput */) {
  throw new Error(
    "model adapter not configured — implement generate() against your local model (e.g. mlx-community/Qwen3-8B-4bit)",
  );
}

/**
 * Judge whether `answer` satisfies the task's reference expectation.
 * Return true/false. Use the same model at temperature 0.
 */
export async function judge(_task, _answer) {
  throw new Error("model adapter not configured — implement judge()");
}

export const configured = false;
