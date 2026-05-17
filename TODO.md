# System Design — Hands-On TODO

## Progress
- [x] 1. Caching
- [ ] 2. Rate Limiting
- [ ] 3. Database Design
- [ ] 4. Async Processing & Queues
- [ ] 5. Circuit Breaker / Fallback
- [ ] 6. Horizontal Scaling & Statelessness
- [ ] 7. Monitoring & Observability
- [ ] 8. Autocomplete / Search
- [ ] 9. Authentication & Multi-tenancy

---

## 1. Caching ✅
**Concept:** Reduce redundant API calls, improve latency.

- [x] Two-tier in-memory cache: weather data (TTL 10 min) + LLM summary (TTL 1 hr)
- [x] LLM cache key includes a hash of the weather fields used in the prompt — weather change automatically invalidates the summary (hash-based coherence, no stale summaries possible)
- [x] Three-state `X-Cache` header: `HIT` (zero calls) / `PARTIAL` (LLM only) / `MISS` (both calls)
- [x] 9 cache tests covering hit/miss/partial, call counts, case-insensitivity, coherence, TTL expiry
- [ ] Evolve to Redis so cache is shared across gunicorn workers (prerequisite for item #6)

**Key decisions:**
- Cache the expensive derived result (LLM), not just the cheap source (weather API)
- Key on a weather hash so consistency is guaranteed by construction, not by TTL math
- Weather TTL (10 min) = freshness policy — how long is the fact still true?
- LLM TTL (1 hr) = eviction policy — garbage collection only; hash already handles staleness. Could be 24h with zero correctness impact.

**Remaining limitation:** in-process dicts are not shared across workers — Redis needed for true horizontal scaling (tracked in item #6).

**Interview talking point:** "Cache what's expensive. Key derived results on a hash of their inputs — coherence by construction, not by TTL math."

---

## 2. Rate Limiting
**Concept:** Protect your service and downstream APIs.

- [ ] Implement per-IP rate limiting on `/api/weather` using token bucket algorithm
- [ ] Return `429 Too Many Requests` with a `Retry-After` header
- [ ] Write tests for rate limit behavior

**Interview talking point:** "How would you prevent abuse and API cost blowout?"

---

## 3. Database Design
**Concept:** Schema design, indexing, reads vs writes.

- [ ] Add SQLite to store query history (`city`, `provider`, `model`, `summary`, `timestamp`)
- [ ] Add a `/api/history` endpoint with pagination
- [ ] Add indexes on `city` and `timestamp`; measure query speed with/without
- [ ] Migrate schema cleanly (understand migrations vs. drop-and-recreate)

**Interview talking point:** "Design the schema for a weather history feature"

---

## 4. Async Processing & Queues
**Concept:** Decouple slow work from the request/response cycle.

- [ ] Move LLM calls (~2-5s) to a background worker (Celery + Redis)
- [ ] Return `{ job_id }` immediately from `/api/weather`
- [ ] Add `/api/status/<job_id>` polling endpoint
- [ ] Update frontend to poll until result is ready

**Interview talking point:** "How do you handle long-running operations at scale?"

---

## 5. Circuit Breaker / Fallback
**Concept:** Resilience when dependencies fail.

- [ ] If primary LLM fails, auto-fallback to next provider
- [ ] If all LLMs fail, return weather data without a summary (graceful degradation)
- [ ] Track failure counts; open circuit after N failures, retry after timeout
- [ ] Write tests simulating provider failures

**Interview talking point:** "How do you design for third-party failures?"

---

## 6. Horizontal Scaling & Statelessness
**Concept:** Stateless services scale out; stateful ones don't.

- [ ] Move in-memory cache (from step 1) to Redis so multiple app instances share it
- [ ] Run 2 gunicorn workers; verify both serve correctly with shared cache
- [ ] Document what would break if you added a third instance without Redis

**Interview talking point:** "How does your app behave behind a load balancer?"

---

## 7. Monitoring & Observability
**Concept:** You can't fix what you can't see.

- [ ] Add structured JSON logging (`city`, `provider`, `latency_ms`, `cache_hit`, `status`)
- [ ] Track metrics: cache hit rate, LLM latency p50/p95, error rate
- [ ] Add a `/api/metrics` endpoint exposing current stats

**Interview talking point:** "How would you know if your system is healthy?"

---

## 8. Autocomplete / Search
**Concept:** Read-heavy systems, prefix search, typeahead.

- [ ] Pre-load top 1000 cities into a sorted list
- [ ] Add `/api/cities?q=<prefix>` endpoint returning matches
- [ ] Wire autocomplete into the frontend input
- [ ] Evolve to Redis sorted sets for sub-millisecond prefix search

**Interview talking point:** "Design a typeahead search system"

---

## 9. Authentication & Multi-tenancy
**Concept:** Identity, API keys, per-user data isolation.

- [ ] Add user accounts (email + password, hashed with bcrypt)
- [ ] Issue API keys; protect `/api/weather` with key auth
- [ ] Scope query history per user
- [ ] Rate limit per-key instead of per-IP

**Interview talking point:** "How do you build a multi-tenant SaaS API?"
