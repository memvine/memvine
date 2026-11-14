# Plan: conversation memory that keeps little and knows the person

Status: plan, 14 Nov 2026. Step 1 done (below); the rest not started. Coding mode is not touched by anything here.

## Why

Conversation mode was built to "keep every concrete detail" of a person's long conversation with an
assistant. Scored the published way on BEAM at 100K (15 of 20 conversations, 300 questions) it is
third of three:

| | Score | What it costs to ingest |
| --- | ---: | --- |
| Full context (the whole conversation in the prompt) | 0.903 | nothing |
| Plain search (embeddings plus word overlap over the raw turns) | 0.889 | embeddings only |
| Memvine, conversation mode | 0.869 | hours of model calls per conversation |

It costs the most and scores the least. This plan says why, and what to build instead.

## What the benchmark conversations taught us

Measured over the 19 stored 100K conversations (15,310 active memories), with no model involved:

| Finding | Number |
| --- | ---: |
| Memories kept per 100K conversation | 806 |
| Memories that restate what the assistant said | 52% |
| Memories never served to any of the 20 questions | 53% |
| Memories with a near-twin in the same conversation (similarity 0.75 or more) | 88% |
| Memories that are a preference, a decision or a rule | 4% |
| Size of the memory text against the conversation it came from | about a fifth |

1. **The store is a second, lossier copy of the transcript.** Half of it is the assistant's own advice
   restated ("a review should include a summary, the reader's thoughts, a rating out of 5 stars").
   Any model can produce that advice again; none of it is about the person.
2. **Search over the original words already finds the details.** BEAM asks for a download date or a
   page count. Those are in the transcript, and plain search finds them without extraction.
3. **Extracted lines push the original words out and mislead the answer.** Memory lines take a quarter
   of the briefing. On the abstention questions Memvine lost, a restated assistant suggestion read
   like something the person had done, and the answer model answered where it should have said
   "not in the conversation".
4. **Updates fail because nothing has a place.** Knowledge update is the worst category (0.733 against
   0.833). With hundreds of similar lines, the model retired "the new April 25 deadline" as replaced
   by a different deadline, and kept "2 remote days" beside "three days" with no link. A whole
   session carries one date, so the order two values were said in is lost.
5. **The tools built for coding mode do not fit.** Evidence quote, trust level, confidence and a review
   queue make sense for a rare team fact. Nobody reviews 806 lines, and "confirm as true" has no
   answer for a book-review template.
6. **Grading strictness changes the picture.** Under a strict grader Memvine's digests won summaries
   (0.88 against 0.57). Under the published grader every system scores about 1.0 on summaries, so
   that advantage does not count there.
7. **Where every system loses points:** multi-session reasoning (about 0.58), contradiction resolution
   (about 0.72) and knowledge update (0.73 to 0.83). That is where a memory can do what search cannot.

## What the lecture gives us as a guide

Reference: Frank Longo, "Learning and Memory" (Stanford Mini Med School), and the taxonomy slide
after Gazzaniga et al., Cognitive Neuroscience (2009). It is a guide for the design, not evidence
that the design will score better.

| In human memory | In a conversation with an assistant | Where it belongs |
| --- | --- | --- |
| Short-term and working memory: about seven items, half a minute | The model's context window | Not stored |
| Episodic: events and personal experiences | "On 13 Feb she downloaded the first novel" | The transcript, searched |
| Semantic: facts | "Her book budget is $50 a month" | Memory: a small profile of the person |
| Procedural: skills used without thinking | "Name the narrator when recommending audiobooks" | Always-on instructions |

Five ideas carry over:

- **Attention, rehearsal and emotion decide what is stored.** Keep what the person said more than once,
  decided, or gave something up for. Generic advice has none of these.
- **Schemas.** A rat that had learned one food location learned the next in two trials instead of six;
  a golfer remembers every shot because he has a frame for golf. A profile with named slots gives a
  new fact an obvious place to land, and an update an obvious target.
- **Consolidation.** Over months a memory moves from the small, fast store to the large one, and
  sleep helps. Recent conversation stays detailed; older conversation shrinks to what it meant.
- **Use it or lose it.** Connections that are never active do not stabilise. A memory never recalled,
  or recalled without helping, fades.
- **Too much memory is a burden.** The woman who remembers every day of her life calls it horrendous
  and was not good at school. Conversation mode today is built like her.

## What to build

Two stores with different jobs. Memory leads, the transcript supports, and plain search is the floor.

1. **Transcript search is the floor.** Most of the briefing is original passages, found by keyword
   and by meaning and fused (already built: `passages`, `relevant_passages`). Memory comes first and
   stays short. Memvine then cannot score below plain search, because plain search is inside it.
2. **Keep only what is about the person.** A memory is kept only if it is about them, would change a
   later answer, and is something they said or decided. What the assistant said is kept only when
   the person took it up. Target: 20 to 60 memories per 100K conversation, not 806.
3. **A profile with slots.** Each memory belongs to a named slot (budget, goal, a person in their
   life, a deadline, a taste). A new value updates its slot and keeps the old value as history, in
   the order said. Reconciling becomes a lookup by slot, not a guess among near-twins.
4. **Contradictions are stored as such.** Two incompatible statements with no announced change become
   one memory holding both, and the briefing says to point the conflict out.
5. **Instructions are always on.** "From now on, when I ask X, do Y" is applied to every answer and
   never searched for (the standing section exists; extraction must catch these reliably).
6. **Consolidation between sessions.** A background pass merges duplicates, keeps the timeline digests
   for summaries and order, and maintains the lists and counts a person builds up over sessions.
7. **Use it or lose it.** Count whether a served memory was needed, not only that it was served;
   let the unused fade.
8. **A console page for a person, not a review queue.** Thirty lines a human can read, correct and
   delete in a minute.

## Where this helps

- **Developers of coding agents (today, proven).** Coding mode keeps the few things a teammate's agent
  could not learn from the repo. Its check passes 25 of 25, and in its agent test Claude Code
  answered 9 of 9 right with memory and 0 of 9 without. This plan brings the same rule, keep little
  and keep what matters, to the other mode.
- **Companies building assistants and agents that talk to the same person over time:** support,
  tutoring, coaching, personal assistants, sales follow-up. What they need and search alone does
  not give:
  - the current value after something changed, with the history behind it;
  - conflicts surfaced instead of one side picked silently;
  - standing instructions applied every time;
  - a profile small enough for a person to read, correct and delete, which is also what privacy
    requests need;
  - exact details still answerable from the transcript, with the source shown.
- **What Memvine should not claim:** that extraction beats search for recalling details. It does not.

## How we will know

Before more ingestion, on conversations already stored:

| Check | Pass |
| --- | --- |
| Step 1 alone, published grader, same 15 conversations | no lower than plain search (0.889) |
| Memories per 100K conversation after steps 2 and 3, on three conversations first | 60 or fewer |
| Share that restates the assistant | under 5% |
| Knowledge update and contradiction resolution | above plain search |
| Overall on the full 100K set | above plain search; full context (0.903) is the stretch |
| Memory check for coding mode | still 25 of 25 |

Cost: step 1 needs no re-ingestion. Steps 2 to 4 need it, at a fraction of today's model calls, since
most of today's calls reconcile hundreds of lines that will no longer be written.

## Step 1, done (14 Nov 2026)

On the same 15 conversations, published grader: kept details off scored 0.878 (from 0.869); with
one passage per turn and keyword hits ranked by density, 0.892, beside plain search at 0.889.
Two things were found on the way:

- Packing turns into 1,500-character passages buried the person's short turns in the assistant's
  long ones: a briefing held about 7 of the person's turns, and now holds about 21.
- Unnormalized keyword rank favours the longest passages. The person's turn holding the answer
  ranked 21st or lower on four checked questions, and 3rd on three of them by density.

The pass mark was "no lower than plain search". It is met by one question, on the conversations the
change was worked out on.

## Order

1. Briefing roles and the floor (recall only).
2. The keep rule and the profile slots (extraction and reconcile, conversation mode only).
3. Contradiction memories.
4. Consolidation: lists, counts and fading.
5. The person page in the console.
6. Publish the result with all three arms, at 100K and then at a size where full context no longer fits.

## Not decided

- Whether the slots are a fixed list or named by the model as it goes.
- Whether BM25 should replace Postgres `ts_rank` for keyword ranking (measure first).
- Whether a small profile needs searching at all, or is always given whole.
- How multi-session counting questions can be helped; consolidation is a guess.
