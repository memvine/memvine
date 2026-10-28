/**
 * Patch grader for the coding-agent eval.
 *
 * Real grading of SWE-Bench-CL patches requires a container runtime and the
 * official harness — that's the source of truth, and it's environment-specific.
 * So in real mode the runner does NOT grade inline; it writes SWE-bench-format
 * prediction files (predictions_<condition>.jsonl) that you feed to:
 *
 *   python -m swebench.harness.run_evaluation \
 *     --predictions_path predictions_memvine.jsonl \
 *     --run_id memvine --max_workers 4
 *
 * See benchmark/AGENT-EVAL.md.
 *
 * In mock mode (MEMVINE_EVAL_MOCK=1) we grade inline with a stand-in rule: the
 * task "passes" iff every key fact reached the model (i.e. appears in the
 * produced patch text). This is a WIRING check, not a task-success measurement —
 * it proves the harness routes the right memory into the right condition.
 */
const MOCK = process.env.MEMVINE_EVAL_MOCK === "1";
export const gradesInline = MOCK;

export async function grade({ task, patch }) {
  if (!MOCK) {
    throw new Error(
      "real grading runs via the official SWE-bench harness (Docker) — feed the " +
        "predictions_<condition>.jsonl files to run_evaluation. See benchmark/AGENT-EVAL.md",
    );
  }
  const keys = task.mock?.keyFacts ?? [];
  const passed = keys.length > 0 && keys.every((f) => patch.includes(f));
  return { passed };
}
