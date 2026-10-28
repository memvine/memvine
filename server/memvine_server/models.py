"""Request and response bodies for the public API."""
from __future__ import annotations

from datetime import datetime
from decimal import Decimal
from typing import Annotated, Literal
from uuid import UUID

from pydantic import AfterValidator, BaseModel, Field

from .redaction import redact

# Text that reaches storage or a model: secrets are stripped on the way in (redaction.py).
Redacted = Annotated[str, AfterValidator(redact)]

Kind = Literal["episodic", "semantic", "procedural", "prospective", "preference"]
Status = Literal["active", "superseded", "retired", "stale"]
Assertion = Literal["observed", "stated", "claimed", "inferred"]
Category = Literal["decision", "rule", "gotcha", "fact", "preference"]
ThreadKind = Literal["release", "task", "pr", "ticket", "topic"]
ThreadStatus = Literal["open", "blocked", "done", "abandoned"]


class CodeContext(BaseModel):
    """The code a conversation touched: repo-relative paths with their git blob hashes. Facts
    naming these files go stale when the files change (code.py)."""
    repo: str = Field(min_length=1, max_length=300)
    commit: str | None = Field(default=None, max_length=64)
    files: dict[str, str] = Field(default_factory=dict, max_length=500)


class CodeCheck(BaseModel):
    """The versions a client has now: path -> blob hash, or null for a deleted file."""
    repo: str = Field(min_length=1, max_length=300)
    files: dict[str, str | None] = Field(max_length=5000)


class EpisodeIn(BaseModel):
    id: str = Field(min_length=1, max_length=200)
    content: Redacted = Field(min_length=1, max_length=200_000)
    timestamp: datetime
    identity: str | None = Field(default=None, max_length=200)
    label: str | None = Field(default=None, max_length=200)
    metadata: dict = Field(default_factory=dict)
    code: CodeContext | None = None
    # History being imported in bulk: synthesized after the project's live work.
    background: bool = False
    # Who may read what is learned from this episode: an audience slug, or 'project' for everyone.
    audience: str = Field(default="project", max_length=40)


class IngestRequest(BaseModel):
    items: list[EpisodeIn] = Field(min_length=1, max_length=100)
    # Learn from one small episode within the request instead of queueing it (instant.py): the
    # response carries what was learned with its edges. One item of at most 4,000 characters.
    instant: bool = False


class IngestItemResult(BaseModel):
    id: str
    # processed: an instant ingest learned from it within the request.
    outcome: Literal["queued", "unchanged", "replaced", "processed"]


class IngestResponse(BaseModel):
    items: list[IngestItemResult]
    queued: int
    instant: InstantResult | None = None  # only for `instant: true`


class Evidence(BaseModel):
    episode_id: str
    quote: str
    identity: str | None = None  # who said it: the identity of the source episode, when it names one


class MemoryOut(BaseModel):
    id: UUID
    kind: Kind
    statement: str
    subjects: list[str]
    valid_from: datetime
    valid_to: datetime | None
    recorded_at: datetime
    status: Status
    supersedes: UUID | None
    superseded_by: UUID | None
    assertion: Assertion
    confidence: Literal["high", "medium", "low"]
    audience: str
    evidence: list[Evidence] = []
    conflicts_with: list[UUID] = []
    standing: bool = False
    # Set when code this memory describes changed since it was learned, e.g. "src/queue.py changed since 3 Oct 2026".
    stale_reason: str | None = None
    # Knowledge: what kind of durable item it is, why it matters, its thread.
    category: Category | None = None
    why: str | None = None
    thread_id: UUID | None = None
    # "project" (anyone in the project), "thread" (only beside its thread's page); None reads as project.
    scope: Literal["project", "thread"] | None = None


# Edges between memories (edges.py). supersedes, contradicts, extends and derived join two facts;
# anchored joins a fact to a file at a version; decided_in joins a fact to its thread.
EdgeKind = Literal["supersedes", "contradicts", "extends", "derived", "anchored", "decided_in"]
# Seen from the memory asked about: "out" it points at the other (it supersedes, extends or was derived
# from the other; it is anchored to the file; it was decided in the thread), "in" the other points at
# it, "both" for contradicts, which has no direction.
EdgeDirection = Literal["out", "in", "both"]


class RelatedRef(BaseModel):
    """An edge as recall lists it beside a served item. id: the other memory, or the thread for
    decided_in, or null for anchored (statement is then "path@version", with " (changed)" or
    " (deleted)" when the file no longer matches)."""
    kind: EdgeKind
    direction: EdgeDirection
    id: UUID | None
    statement: str


class RelatedFile(BaseModel):
    repo: str
    path: str
    version: str  # the git blob the memory is anchored to
    anchored_at: datetime
    current_version: str | None = None  # the blob last reported for the file; null when deleted or never reported
    # Whether the anchored version is still the file's latest reported one; null when no version was reported since.
    current: bool | None = None


class RelatedThread(BaseModel):
    id: UUID
    key: str
    kind: ThreadKind
    title: str
    status: ThreadStatus
    summary: str


class RelatedEdge(BaseModel):
    """One edge of GET /v1/memories/{id}/related: `item` for edges to another memory, `file` for
    anchored, `thread` for decided_in."""
    kind: EdgeKind
    direction: EdgeDirection
    item: MemoryOut | None = None
    file: RelatedFile | None = None
    thread: RelatedThread | None = None


class MemoryRelated(BaseModel):
    memory: MemoryOut
    edges: list[RelatedEdge]


class InstantResult(BaseModel):
    """What an instant ingest learned. status "done": `memories` is every memory the episode is now
    evidence for, each with all its edges (the shape of GET /v1/memories/{id}/related), ordered so
    a memory comes after those it supersedes or extends. status "queued": it could not run within
    the request (`reason` says why) and was queued like any episode; `memories` is empty."""
    status: Literal["done", "queued"]
    reason: str | None = None
    memories: list[MemoryRelated] = []


class GraphFile(BaseModel):
    repo: str
    path: str
    version: str | None = None  # latest reported blob; null when deleted or never reported
    deleted: bool = False


class GraphNode(BaseModel):
    """id: the memory id for a fact, "thread:<uuid>", "file:<repo>:<path>" or "person:<identity>".
    Exactly one of memory / thread / file is set for those types; a person carries only its label."""
    id: str
    type: Literal["fact", "thread", "file", "person"]
    label: str
    grew: bool = False  # added or changed since `since`
    memory: MemoryOut | None = None
    said_by: list[str] = []  # facts: identities of the sources it came from (draw as edges to "person:<identity>")
    thread: RelatedThread | None = None
    file: GraphFile | None = None


class GraphEdge(BaseModel):
    model_config = {"populate_by_name": True}

    kind: EdgeKind
    from_: str = Field(alias="from")  # supersedes: newer -> older; extends: newer -> older; derived: inferred -> cited;
    to: str                           # contradicts: either way, once per pair; anchored: fact -> file; decided_in: fact -> thread
    version: str | None = None  # anchored: the blob the fact is anchored to
    current: bool | None = None  # anchored: still the file's latest reported version (null: none reported since)


class GraphOut(BaseModel):
    nodes: list[GraphNode]
    edges: list[GraphEdge]
    truncated: bool  # more of the neighborhood exists than the node cap allowed
    since: datetime | None = None


class RecallInclude(BaseModel):
    related: bool = False  # each served item lists its edges (RecallItem.related)


class RecallRequest(BaseModel):
    query: Redacted = Field(min_length=1, max_length=4000)
    identity: str | None = None
    budget: int | None = Field(default=None, ge=100)
    as_of: datetime | None = None
    exclude: list[UUID] = Field(default_factory=list, max_length=500)
    include_history: bool | None = None
    # The caller's own live session: its messages are already in the caller's context, so they are
    # left out of the pending messages recall adds from other sessions.
    session_id: str | None = Field(default=None, max_length=200)
    # The caller's git branch: that branch's thread page comes first.
    branch: str | None = Field(default=None, max_length=200)
    # Extra detail per served item: {"related": true} adds each item's edges.
    include: RecallInclude | None = None


class RecallItem(BaseModel):
    memory: MemoryOut
    score: float
    previous: str | None = None
    conflicts: list[str] = []  # statements of active items that contradict this one
    # With include.related: this item's edges, at most 6. Not in the briefing; counted in `tokens`. Null when not asked.
    related: list[RelatedRef] | None = None


class RecallThread(BaseModel):
    id: UUID
    key: str
    title: str
    status: ThreadStatus
    summary: str


class RecallPassage(BaseModel):
    episode_id: str
    occurred_at: datetime
    content: str


class PendingMessage(BaseModel):
    session_id: str
    seq: int
    role: str
    at: datetime
    content: str


class RecallResponse(BaseModel):
    briefing: str
    pending: list[PendingMessage] = []  # recent messages of other live sessions not yet extracted
    standing: list[str] = []  # standing instructions, included in every recall
    threads: list[RecallThread] = []  # the thread pages the briefing includes
    passages: list[RecallPassage] = []
    items: list[RecallItem]
    tokens: int
    abstained: bool
    # Why nothing was served: "empty_store" (no memory to search), "no_candidates" (memory exists, but
    # none is visible to this question: everything excluded, or nothing recorded by as_of), or
    # "below_threshold" (the best match is under min_relevance). None when not abstained.
    abstain_reason: str | None = None
    # The relevance threshold this recall used (project setting, else the embedder's calibrated default).
    min_relevance: float | None = None
    # Best relevance of any candidate memory: its cosine similarity to the question, plus a small bonus
    # when it also matches the question's words. None when there was no candidate.
    top_relevance: float | None = None


class SessionEventIn(BaseModel):
    role: Literal["user", "assistant", "system", "tool"]
    content: Redacted = Field(min_length=1, max_length=100_000)
    at: datetime | None = None


class SessionEventsRequest(BaseModel):
    events: list[SessionEventIn] = Field(min_length=1, max_length=500)
    agent: str | None = Field(default=None, max_length=100)
    identity: str | None = Field(default=None, max_length=200)
    label: str | None = Field(default=None, max_length=200)
    code: CodeContext | None = None
    # False for a past session being imported (backfill): no working-state refresh (nobody is
    # working in it), and the session keeps its events' own times.
    live: bool = True


class SessionState(BaseModel):
    goal: str = ""
    decisions: list[str] = []
    open_questions: list[str] = []
    in_play: list[str] = []
    recent_facts: list[str] = []
    progress_claims: list[str] = []


class SessionOut(BaseModel):
    id: str
    agent: str | None
    identity: str | None
    label: str | None
    status: Literal["live", "synthesized"]
    started_at: datetime
    last_event_at: datetime
    turns: int
    state: SessionState
    state_through: int
    state_updated_at: datetime | None
    synthesized_at: datetime | None
    sealed_through: int = 0
    processed_through: int = 0


class SessionBrief(BaseModel):
    """What an agent gets back after a compaction or reset: its own state, then relevant history."""
    session: SessionOut
    text: str
    tokens: int


class KeptMemory(BaseModel):
    id: UUID
    category: str  # decision, rule, gotcha, fact, preference (without a category, from its kind: fact, procedure, event, plan)
    statement: str
    supersedes: UUID | None = None
    previous: str | None = None  # the statement it superseded


class SessionKept(BaseModel):
    """What memory kept from a session: the memories first drawn from its sources (still current,
    not forgotten), how many older facts they superseded, and a one-line summary a plugin prints.
    `tending` is true while the session is live or its sources are still being synthesized, so the
    counts may still grow."""
    session_id: str
    tending: bool
    kept: list[KeptMemory]
    superseded: int
    contradictions: int
    summary: str


class MemoryCreate(BaseModel):
    statement: Redacted = Field(min_length=3, max_length=2000)
    kind: Kind = "semantic"
    subjects: list[str] = Field(default_factory=list, max_length=3)
    assertion: Assertion = "stated"
    audience: str = Field(default="project", max_length=40)
    standing: bool | None = None
    category: Category | None = None
    why: Redacted | None = Field(default=None, max_length=500)
    supersedes: UUID | None = Field(
        default=None,
        description="The id of a current memory this statement replaces, as a person said: no judge is asked, the new "
                    "memory supersedes it and inherits its kind, subjects, category, thread, scope and audience unless given.")


class MemoryCreated(BaseModel):
    memory: MemoryOut
    relation: str
    related: UUID | None = None


class MemoryPatch(BaseModel):
    status: Literal["active", "retired"] | None = None
    confirm: bool | None = None


TAG = r"^[a-z0-9][a-z0-9-]{0,38}[a-z0-9]$|^[a-z0-9]$"


class ProjectCreate(BaseModel):
    name: str = Field(min_length=1, max_length=100)
    slug: str | None = Field(default=None, pattern=r"^[a-z0-9][a-z0-9-]{1,38}[a-z0-9]$")
    tags: list[Annotated[str, Field(pattern=TAG)]] = Field(default_factory=list, max_length=20)
    org: str | None = Field(default=None, description="operator only: the organization slug")
    description: str | None = Field(default=None, max_length=2000)
    # Start with the sample memory (POST /v1/sample) so recall can be tried before any agent is connected.
    sample: bool = False


class OrgCreate(BaseModel):
    name: str = Field(min_length=1, max_length=100)
    slug: str | None = Field(default=None, pattern=r"^[a-z0-9][a-z0-9-]{1,38}[a-z0-9]$")


class ProjectPatch(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=100)
    tags: list[Annotated[str, Field(pattern=TAG)]] | None = Field(default=None, max_length=20)
    # Send null to remove a cap or keep sources forever; leave the field out to keep it as it is.
    monthly_credit_cap: Decimal | None = Field(default=None, ge=0)
    retention_days: int | None = Field(default=None, ge=1, le=3650)
    # Paused: what is sent is stored and queued, but no synthesis runs (no model spend) until resumed.
    processing_paused: bool | None = None
    # What the project is: extraction judges relevance against it. Send null to clear.
    description: str | None = Field(default=None, max_length=2000)
    # Recall abstains unless some memory is at least this relevant (cosine similarity to the question,
    # see recall.py). Send null to use the server default for its embedding model.
    recall_min_relevance: float | None = Field(default=None, ge=0, le=1)


class ProjectOut(BaseModel):
    id: UUID
    slug: str
    name: str
    created_at: datetime
    tags: list[str] = []
    memory_count: int = 0
    source_count: int = 0
    sample_source_count: int = 0  # sources that are sample data (POST /v1/sample), deletable with DELETE /v1/sample
    key_count: int = 0
    last_activity_at: datetime | None = None
    monthly_credit_cap: Decimal | None = None
    retention_days: int | None = None
    month_credits: Decimal = Decimal(0)
    processing_paused: bool = False
    description: str | None = None
    recall_min_relevance: float | None = None  # null: the server default for its embedding model


class ThreadItemEvidence(BaseModel):
    episode_id: str
    quote: str


class ThreadItem(BaseModel):
    text: str
    date: str | None = None  # YYYY-MM-DD
    evidence: list[ThreadItemEvidence] = []


class ThreadSection(BaseModel):
    label: str
    items: list[ThreadItem]


class ThreadSummary(BaseModel):
    id: UUID
    kind: ThreadKind
    key: str
    title: str
    status: ThreadStatus
    summary: str
    first_seen_at: datetime | None
    last_activity_at: datetime | None
    session_count: int = 0
    knowledge_count: int = 0


class ThreadSource(BaseModel):
    episode_id: str
    label: str | None
    occurred_at: datetime


class ThreadDetail(ThreadSummary):
    aliases: list[str] = []
    sections: list[ThreadSection] = []
    related: list[ThreadSummary] = []
    knowledge: list[MemoryOut] = []
    sources: list[ThreadSource] = []


class KeyCreate(BaseModel):
    name: str = Field(min_length=1, max_length=60)


class KeyOut(BaseModel):
    id: UUID
    name: str
    key: str | None = None
    suffix: str
    created_at: datetime
    last_used_at: datetime | None
    last_used_ip: str | None = None
    revoked_at: datetime | None
