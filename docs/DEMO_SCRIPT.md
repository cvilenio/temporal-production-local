# Temporal Retail Demo Script

## Section 1: Discovery (5 min)

**Goal:** Establish a consultative posture. Ask questions that show you've done this before, not just read the docs.

1. **"Which failure mode hurts your business the most right now?"**
   * *If duplicate charges:* "This means idempotency is currently leaking. We'll focus on how Temporal guarantees single-execution semantics even when the network drops."
   * *If stuck inventory:* "This is a saga problem. We'll talk about compensation and how Temporal makes sure compensating actions actually complete."
   * *If visibility gap:* "You have a blind spot on in-flight orders. I'll show you how Temporal's event history replaces log-diving."

2. **"Are your external system calls (like the payment gateway) already idempotent end-to-end?"**
   * *If yes:* "Great, Temporal leverages that. We'll pass idempotency keys directly from the workflow."
   * *If no:* "We can use Temporal's deterministic execution to generate those keys so you don't double-charge when a timeout happens."

3. **"Who owns on-call today for a stuck order, and who will own the Temporal workers?"**
   * *If same team:* "Temporal gives you a single pane of glass to debug."
   * *If different teams:* "We should talk about task queue routing so the payment team owns payment activities and the shipping team owns shipping."

4. **"You mentioned a 'phased rollout'. Does that mean workflow-by-workflow, or a percentage of all traffic?"**
   * *If % of traffic:* "Temporal Cloud handles the scaling, but we'll need to set up a feature flag or a Kafka shadow-write to control the dial."

## Section 2: Architecture Walkthrough (12 min)

**Goal:** Ground the demo in their existing stack and draw the boundaries clearly.

1. **The Problem:** 
   "Right now, your order service is orchestrating HTTP calls. When the shipping provider hangs, your service eventually times out. But did the label get created? You don't know. If you retry, you might create two. If you don't, the order is stuck. You have to dive into centralized logs to figure it out."

2. **The Architecture (Current vs. Proposed):**
   * **Current:** REST API -> Order Service -> Postgres + External APIs (messy, tangled).
   * **Proposed:** REST API -> Temporal. Temporal -> Workers. Workers -> External APIs + Postgres. 
   * "Notice we didn't rip out Postgres. Postgres is still the system of record for the *order entity*. Temporal is the system of record for the *execution process*."

3. **The Three-Layer Write Model (Option X):**
   * "To solve the ambiguous timeout, we broke the workflow down. For every step, we do three things:"
     1. **External Call:** Call the API with an idempotency key.
     2. **Persistence:** Update Postgres (e.g., set `tracking_id`).
     3. **Notification:** Send a message back to the user.
   * "Each of these is a distinct Temporal Activity. If the external call fails, we only retry that call. If the DB update fails, we only retry the DB update."

4. **Retry Policies (The 'Aha' Moment):**
   * "Not all failures are equal. We configured three tiers of retries:"
     * *External (Shipping):* "Aggressive retries, up to 10 times, because this is where the ambiguous side-effects live."
     * *Persistence (DB):* "Fast retries. If Postgres blips, we want to recover immediately."
     * *Notify:* "Lower priority. If the notification system is down, it shouldn't hold up the order."

## Section 3: Live Demo Click Path (8 min)

**Window Layout:**
* Left: Retail Demo Console (Orders tab)
* Top Right: Temporal UI (ziggymart namespace)
* Bottom Right: pgweb (notifications table)

**Action 1: Happy Path**
1. On Demo Console, click **"Trigger scenarios"** for `Happy Path` (qty 2).
2. "These orders flow through instantly."
3. Switch to Demo Console -> Messages tab. "The UI is polling the Postgres notifications table, which the workflow is actively writing to."
4. Switch to Temporal UI. Show one completed workflow. Point out the ~16 distinct activities. "It's extremely legible. You see exactly what happened."

**Action 2: The Ambiguous Timeout (The Money Shot)**
1. On Demo Console, click **"Trigger scenarios"** for `Shipping Response Lost` (qty 3) and `Happy Path` (qty 2) concurrently.
2. "Notice the happy path orders breeze through, but the shipping response lost orders are hanging."
3. Switch to Temporal UI -> Recent Workflows. Click a running workflow.
4. Point to the `create_shipment` activity in `Pending` state. "The mock API created the label, but then the network connection was severed. The order service is waiting for a response that will never come."
5. "The activity will eventually exhaust its retry policy, and the workflow will pivot to a verification step (read-after-write) to see if that label actually exists on the provider's side."
6. Once the workflow recovers: "The verification succeeded. We found the ghost label, so we saved the tracking ID and moved on. We avoided double-shipping and recovered automatically."

**Action 3: Customer Support Lookup**
1. Copy an order ID like `ORD-01K8XYZ1A2B3C4D5` from the Demo Console submission log.
2. In the Temporal UI, paste it into the search bar: `OrderId = "ORD-01K8XYZ1A2B3C4D5"`.
3. Hit enter. You instantly find the workflow execution. The order ID is a searchable Temporal attribute.

**Action 4: Duplicate Submit Dedupe**
1. On the Demo Console, click **"Replay Batch (Idempotent)"** on any completed batch in the submission log.
2. "The UI just re-sent the exact same HTTP payload and `X-Idempotency-Key` headers to the backend."
3. Notice the new log entry shows the exact same `order_id`s. The backend caught the retry via its 24h cache and instantly returned the previous response.
4. Switch to pgweb. Refresh the `orders` table. Zero new rows were created. Temporal UI also shows zero new workflows.

## Section 4: Q&A Objection Handling (20 min)

**1. "What happens when we deploy a new version of the workflow code while orders are in-flight?"**
* *Answer:* "Temporal has a feature called Worker Versioning. We tell Temporal 'this new code is v2', and Temporal ensures that any order started on v1 finishes on v1 workers, while new orders start on v2. No in-flight orders break."

**2. "How do we debug this in production?"**
* *Answer:* "Two ways. First, the Temporal UI gives you the exact event history—no more grepping logs across microservices to find where an order died. Second, we wire the Temporal SDK to export OpenTelemetry metrics to your existing Datadog/Prometheus setup, so you get dashboard alerts just like your current services."

**3. "Why Temporal Cloud instead of hosting it ourselves on Kubernetes?"**
* *Answer:* "You certainly can self-host. But Temporal Cloud removes the operational burden of managing the Cassandra/Postgres backend, the Elasticsearch cluster for visibility, and scaling the history service. Your team focuses on writing workflow code, not running distributed databases."

**4. "Is Temporal replacing our Postgres database?"**
* *Answer:* "No. Postgres remains the system of record for your business entities (Orders, Users). Temporal is the system of record for the *process* of fulfilling that order. Your activities still write to Postgres, but Temporal ensures those writes happen reliably and exactly once."

**5. "What if there is PII (like credit cards) in the workflow payloads?"**
* *Answer:* "Temporal Cloud encrypts data at rest, but we also use a Codec Server. The payloads are encrypted *before* they leave your worker. Temporal Cloud only sees ciphertext. When you view the UI, the UI calls your internal Codec Server to decrypt it for display."

**6. "How much does Temporal Cloud cost? Are we going to get a huge bill?"**
* *Answer:* "Cloud is priced on 'actions' (state transitions). This is why architecture matters. We design workflows to minimize unnecessary chatter. For example, we don't use 'Signals' as a high-frequency heartbeat mechanism. We use it for actual business events like cancellation."

**7. "Should we use one Task Queue or many?"**
* *Answer:* "Start with one per business domain (e.g., `orders-queue`, `billing-queue`). As teams grow, you give each team their own queue so they can deploy independently without stepping on each other."

**8. "How do we migrate our existing traffic safely?"**
* *Answer:* "Phased rollout. We don't do a big bang. First, we dual-write: your existing system runs, and it drops an event on Kafka that triggers a Temporal workflow in 'shadow mode' to verify it works. Then we cut over 1% of traffic, then 10%, then 100%."

**9. "What if the user wants to cancel the order mid-flight?"**
* *Answer:* "We'd implement a Temporal `Signal`. The UI sends a cancel signal to the running workflow. The workflow receives it, stops processing new steps, and runs compensating activities (like refunding the card) for the steps it already completed."

**10. "Why didn't you use Temporal Queries for the frontend UI?"**
* *Answer:* "Directly querying Temporal from a customer-facing UI is an anti-pattern at scale—it puts read load on your orchestration engine. Instead, our activities write state changes to your Postgres replica, and the UI reads from there. We reserve Queries for SRE debugging."
