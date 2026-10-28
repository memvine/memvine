/**
 * Model adapter for the coding-agent eval. Two modes:
 *
 * - Real: talks to any OpenAI-compatible /v1/chat/completions endpoint, so it
 *   works with a local mlx-lm server (`mlx_lm.server`), vLLM, Ollama's OpenAI
 *   shim, or a hosted API. Configure via env:
 *     MEMVINE_MODEL_BASE_URL   e.g. http://localhost:8080
 *     MEMVINE_MODEL_NAME       e.g. mlx-community/Qwen3-8B-4bit
 *     MEMVINE_MODEL_KEY        optional bearer token
 *
 * - Mock (MEMVINE_EVAL_MOCK=1): no network. The "agent" emits a patch that
 *   echoes the context it was given, so the mock grader can verify whether the
 *   needed knowledge actually reached the model. This exercises the full runner
 *   wiring — conditions, seeding, recall, prediction files — without a GPU.
 */
const MOCK = process.env.MEMVINE_EVAL_MOCK === "1";
export const mock = MOCK;

export async function generate({ system, prompt }) {
  if (MOCK) {
    // The mock agent "writes a patch" that carries forward whatever it was told,
    // so grading can detect whether the relevant memory was in context.
    return `diff --git a/mock b/mock\n@@ mock @@\n# system: ${system}\n# context-seen:\n${prompt}`;
  }
  const base = process.env.MEMVINE_MODEL_BASE_URL;
  const model = process.env.MEMVINE_MODEL_NAME;
  if (!base || !model) {
    throw new Error(
      "model adapter not configured — set MEMVINE_MODEL_BASE_URL and MEMVINE_MODEL_NAME " +
        "(any OpenAI-compatible endpoint), or run with MEMVINE_EVAL_MOCK=1",
    );
  }
  const res = await fetch(`${base.replace(/\/$/, "")}/v1/chat/completions`, {
    method: "POST",
    headers: {
      "content-type": "application/json",
      ...(process.env.MEMVINE_MODEL_KEY
        ? { authorization: `Bearer ${process.env.MEMVINE_MODEL_KEY}` }
        : {}),
    },
    body: JSON.stringify({
      model,
      temperature: 0,
      messages: [
        { role: "system", content: system },
        { role: "user", content: prompt },
      ],
    }),
  });
  if (!res.ok) {
    throw new Error(`model HTTP ${res.status}: ${(await res.text()).slice(0, 500)}`);
  }
  const j = await res.json();
  return j.choices?.[0]?.message?.content ?? "";
}
