#!/usr/bin/env python3
"""Populate Grafana Cloud panels via the platform-console submit-batch API.

Drives the orders happy-path so workers poll, StartWorkflow latency moves, and
(optionally) a concurrent start burst tries to trip the namespace APS ceiling.

What each mode lights up
------------------------
populate (default, small):
  - Sync match rate
  - StartWorkflowExecution latency
  - RPS usage

aps-burst (large concurrent starts via console → orders-api):
  - Same panels as above; often capped by orders-api/DB below action_limit

direct-aps (Temporal client StartWorkflow fan-out; skips orders-api):
  - Aimed at actually exceeding action_limit with starts alone
  - Requires: uv run python …  (needs temporalio / orders / appkit)
  - Credentials: TEMPORAL_* env, or kubectl secret orders-client-apikey

signal-aps (Temporal client Signal fan-out; skips orders-api and Workers):
  - Starts a few probe Workflows on an intentionally unpolled Task Queue
  - Sustains Signals above action_limit until ApsLimit is observed or time expires
  - Reuses the same Workflow Executions, then terminates them during cleanup

NOT covered by APS burst
------------------------
`temporal_cloud_v1_resource_exhausted_error_count` EXCLUDES namespace-limit
throttling (BusyWorkflow, Visibility ~30 RPS, Worker Deployment ~50 RPS, …).
APS trip shows on `*_throttled_count`, not that series. See Capacity panel
"Rate limit vs other Resource Exhausted".

Cloud cost / footprint
----------------------
This starts real Workflows on Temporal Cloud when the cluster backend is cloud.
Ask before running any mode against Temporal Cloud. The load modes can create
hundreds of executions or thousands of requests. Prefer OSS
(`just platform-up oss`) when you only need the shape of the boards.

Usage
-----
  # console must be up (just host-up / just preflight)
  python3 compose/scripts/probe-dashboard-metrics.py              # populate
  python3 compose/scripts/probe-dashboard-metrics.py aps-burst    # via API
  uv run python compose/scripts/probe-dashboard-metrics.py direct-aps
  uv run python compose/scripts/probe-dashboard-metrics.py signal-aps
"""

from __future__ import annotations

import argparse
import base64
import json
import os
import subprocess
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
import uuid

CONSOLE = "http://localhost:8086"
SUBMIT = f"{CONSOLE}/api/submit-batch"
HEALTHZ = f"{CONSOLE}/healthz"
PROMETHEUS = "http://localhost:9009"
PROBE_ID_PREFIX = "PROBE-APS-"


def _get(url: str, timeout: float = 5.0) -> dict | list | str:
    with urllib.request.urlopen(url, timeout=timeout) as resp:
        body = resp.read().decode()
        try:
            return json.loads(body)
        except json.JSONDecodeError:
            return body


def _post_json(url: str, payload: dict, timeout: float) -> dict:
    data = json.dumps(payload).encode()
    req = urllib.request.Request(
        url,
        data=data,
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return json.loads(resp.read().decode())


def _prometheus_query(query: str) -> list[dict]:
    params = urllib.parse.urlencode({"query": query})
    payload = _get(f"{PROMETHEUS}/api/v1/query?{params}")
    if not isinstance(payload, dict) or payload.get("status") != "success":
        raise RuntimeError(f"Prometheus query failed: {payload}")
    result = payload.get("data", {}).get("result")
    if not isinstance(result, list):
        raise RuntimeError(f"Prometheus query returned invalid data: {payload}")
    return result


def _load_budget(args: argparse.Namespace) -> str:
    if args.mode == "signal-aps":
        attempts = int(args.signal_rate * args.duration)
        return (
            f"{args.probe_workflows} Workflow Executions and up to "
            f"{attempts} Signal attempts"
        )
    executions = args.count * args.waves
    return f"up to {executions} Workflow Executions"


def preflight(args: argparse.Namespace) -> None:
    try:
        health = _get(HEALTHZ)
    except Exception as e:
        sys.exit(
            f"console not reachable at {HEALTHZ}: {e}\n"
            "Start it first: just host-up  (then just preflight)"
        )
    backend = health.get("backend", "?") if isinstance(health, dict) else "?"
    print(f"console ok  backend={backend}")
    cloud_load = str(backend).lower() == "cloud" or args.mode in {
        "direct-aps",
        "signal-aps",
    }
    if not cloud_load:
        return
    budget = _load_budget(args)
    if not args.approve_cloud_load:
        sys.exit(
            f"Temporal Cloud load blocked: {budget}. Obtain approval, then rerun "
            "with --approve-cloud-load."
        )
    print(f"Temporal Cloud load approved: {budget}")


def submit(counts: dict[str, int], timeout: float) -> dict:
    print(f"submit-batch {counts} …", flush=True)
    t0 = time.monotonic()
    try:
        entry = _post_json(SUBMIT, {"counts": counts}, timeout=timeout)
    except urllib.error.HTTPError as e:
        detail = e.read().decode(errors="replace")
        sys.exit(f"submit-batch HTTP {e.code}: {detail}")
    except Exception as e:
        sys.exit(f"submit-batch failed: {e}")
    elapsed = time.monotonic() - t0
    triggered = entry.get("triggered", 0)
    failed = entry.get("failed", 0)
    print(
        f"  done in {elapsed:.1f}s  triggered={triggered}  failed={failed}  "
        f"batch_id={entry.get('batch_id')}"
    )
    if failed:
        # Surface a few errors so APS / ResourceExhausted is obvious in the CLI too.
        errors = [
            r.get("error")
            for r in entry.get("results", [])
            if not r.get("ok") and r.get("error")
        ][:5]
        for err in errors:
            print(f"  err: {err}")
    return entry


def cmd_populate(args: argparse.Namespace) -> None:
    """Modest concurrent happy-path, enough to paint sync-match and start latency."""
    entry = submit({"happy_path": args.count}, timeout=args.timeout)
    if entry.get("triggered", 0) != args.count or entry.get("failed", 0):
        sys.exit(
            "populate did not trigger every requested Workflow Execution; "
            "dashboard metrics may be incomplete"
        )
    _print_where_to_look(aps=False)


def cmd_aps_burst(args: argparse.Namespace) -> None:
    """Fan-out concurrent StartWorkflowExecution calls via console gather.

    Each wave is one submit-batch; the console fires every order concurrently
    (asyncio.gather). Count should exceed action_limit (~500) so starts alone
    can breach APS in a single second if orders-api/DB can push that hard.
    Waves stack running Workflows so activity Actions add pressure as workers
    drain the backlog.
    """
    print(
        f"APS burst: {args.waves} wave(s) × {args.count} happy_path "
        f"(target action_limit≈{args.aps_limit})"
    )
    if args.count < args.aps_limit:
        print(
            f"WARN: --count {args.count} < --aps-limit {args.aps_limit}; "
            "starts alone may not breach APS (activity Actions may still help)."
        )
    for i in range(1, args.waves + 1):
        print(f"wave {i}/{args.waves}")
        submit({"happy_path": args.count}, timeout=args.timeout)
        if i < args.waves and args.wave_gap > 0:
            time.sleep(args.wave_gap)
    _print_where_to_look(aps=True)


def _load_temporal_creds() -> tuple[str, str, str]:
    """Return (address, namespace, api_key). Prefer env; else kubectl secret."""
    address = os.environ.get("TEMPORAL_ADDRESS", "").strip()
    namespace = os.environ.get("TEMPORAL_NAMESPACE", "").strip()
    api_key = os.environ.get("TEMPORAL_API_KEY", "").strip()
    if address and namespace and api_key:
        return address, namespace, api_key

    kubeconfig = os.environ.get(
        "KUBECONFIG",
        str(
            os.path.join(
                os.path.dirname(__file__),
                "..",
                "..",
                ".secrets",
                "kube",
                "kind.kubeconfig",
            )
        ),
    )
    env = {**os.environ, "KUBECONFIG": kubeconfig}

    def _kubectl(*args: str) -> str:
        out = subprocess.check_output(["kubectl", *args], env=env, text=True)
        return out.strip()

    if not address:
        try:
            address = _kubectl(
                "-n",
                "orders",
                "get",
                "deploy",
                "orders-api",
                "-o",
                "jsonpath={.spec.template.spec.containers[0].env[?(@.name=='TEMPORAL_ADDRESS')].value}",
            )
        except Exception as e:
            sys.exit(f"TEMPORAL_ADDRESS unset and kubectl lookup failed: {e}")
    if not namespace:
        try:
            namespace = _kubectl(
                "-n",
                "orders",
                "get",
                "deploy",
                "orders-api",
                "-o",
                "jsonpath={.spec.template.spec.containers[0].env[?(@.name=='TEMPORAL_NAMESPACE')].value}",
            )
        except Exception as e:
            sys.exit(f"TEMPORAL_NAMESPACE unset and kubectl lookup failed: {e}")
    if not api_key:
        try:
            b64 = _kubectl(
                "-n",
                "orders",
                "get",
                "secret",
                "orders-client-apikey",
                "-o",
                "jsonpath={.data.api-key}",
            )
            api_key = base64.b64decode(b64).decode()
        except Exception as e:
            sys.exit(f"TEMPORAL_API_KEY unset and kubectl secret lookup failed: {e}")

    if not (address and namespace and api_key):
        sys.exit("incomplete Temporal credentials (need address, namespace, api_key)")
    return address, namespace, api_key


def cmd_direct_aps(args: argparse.Namespace) -> None:
    """Bypass orders-api: fan-out StartWorkflowExecution via the Temporal SDK."""
    try:
        import asyncio

        from appkit import build_temporal_client, resolve_data_converter
        from orders.shared.temporal_ids import TaskQueue
        from orders.shared.workflow_io import (
            ORDER_WORKFLOW_EXECUTION_TIMEOUT,
            OrderWorkflowInput,
        )
        from orders.workflows.order_workflow import OrderWorkflow
        from temporalio.client import WorkflowExecutionStatus
        from temporalio.service import RPCError, RPCStatusCode
    except ImportError as e:
        sys.exit(
            f"direct-aps needs the repo Python env ({e}).\n"
            "Run: uv run python compose/scripts/probe-dashboard-metrics.py direct-aps"
        )

    address, namespace, api_key = _load_temporal_creds()
    total = args.count * args.waves
    print(
        f"direct-aps: {args.waves} wave(s) × {args.count} StartWorkflow "
        f"(total={total}) → {namespace} @ {address}  "
        f"(action_limit≈{args.aps_limit}, wave_gap={args.wave_gap}s)"
    )
    if args.count < args.aps_limit:
        print(
            f"WARN: --count {args.count} < --aps-limit {args.aps_limit}; "
            "may not breach APS."
        )

    async def _run() -> None:
        client = await build_temporal_client(
            address=address,
            namespace=namespace,
            tls=True,
            api_key=api_key,
            data_converter=resolve_data_converter("default"),
        )

        batch = uuid.uuid4().hex[:12]
        all_ids: list[str] = []
        tot_ok = tot_exhausted = tot_other = 0
        samples: list[str] = []
        wall0 = time.monotonic()

        async def _start(wf_id: str) -> str:
            inp = OrderWorkflowInput(
                order_id=wf_id,
                item_id="ITEM-001",
                quantity=1,
                user_id=str(uuid.uuid4()),
                address="probe load street",
                payment_authorization_id=f"PAUTH-{uuid.uuid4().hex}",
                amount_minor=4999,
                trace_id=uuid.uuid4().hex,
            )
            await client.start_workflow(
                OrderWorkflow.run,
                inp,
                id=wf_id,
                task_queue=TaskQueue.ORDERS_WORKFLOW,
                execution_timeout=ORDER_WORKFLOW_EXECUTION_TIMEOUT,
            )
            return "ok"

        term_fail = 0
        try:
            for w in range(1, args.waves + 1):
                ids = [
                    f"{PROBE_ID_PREFIX}{batch}-w{w:02d}-{i:04d}"
                    for i in range(args.count)
                ]
                all_ids.extend(ids)
                print(
                    f"wave {w}/{args.waves}: firing {len(ids)} starts …",
                    flush=True,
                )
                t0 = time.monotonic()
                results = await asyncio.gather(
                    *[_start(i) for i in ids], return_exceptions=True
                )
                elapsed = time.monotonic() - t0

                ok = exhausted = other = 0
                for r in results:
                    if r == "ok":
                        ok += 1
                    elif (
                        isinstance(r, RPCError)
                        and r.status == RPCStatusCode.RESOURCE_EXHAUSTED
                    ):
                        exhausted += 1
                        if len(samples) < 5:
                            samples.append(f"RESOURCE_EXHAUSTED: {r}")
                    elif isinstance(r, BaseException):
                        other += 1
                        if len(samples) < 8:
                            samples.append(f"{type(r).__name__}: {r}")
                    else:
                        other += 1

                tot_ok += ok
                tot_exhausted += exhausted
                tot_other += other
                rps = len(ids) / elapsed if elapsed else 0
                print(
                    f"  wave done in {elapsed:.2f}s (~{rps:.0f} start attempts/s)  "
                    f"ok={ok}  resource_exhausted={exhausted}  other_err={other}"
                )
                if w < args.waves and args.wave_gap > 0:
                    await asyncio.sleep(args.wave_gap)

            wall = time.monotonic() - wall0
            print(
                f"TOTAL in {wall:.2f}s  starts={len(all_ids)}  "
                f"ok={tot_ok}  resource_exhausted={tot_exhausted}  "
                f"other_err={tot_other}"
            )
            for sample in samples:
                print(f"  err: {sample}")
        finally:
            if args.terminate and all_ids:
                print(
                    f"checking {len(all_ids)} {PROBE_ID_PREFIX}{batch}-* "
                    "attempts for cleanup …",
                    flush=True,
                )
                term_ok = 0
                term_skipped = 0
                sem = asyncio.Semaphore(50)

                async def _term(wf_id: str) -> None:
                    nonlocal term_ok, term_fail, term_skipped
                    async with sem:
                        handle = client.get_workflow_handle(wf_id)
                        try:
                            description = await handle.describe()
                        except RPCError as error:
                            if error.status == RPCStatusCode.NOT_FOUND:
                                term_skipped += 1
                                return
                            term_fail += 1
                            if term_fail <= 5:
                                print(
                                    f"  describe failed for {wf_id}: {error}",
                                    file=sys.stderr,
                                )
                            return
                        except Exception as error:
                            term_fail += 1
                            if term_fail <= 5:
                                print(
                                    f"  describe failed for {wf_id}: {error}",
                                    file=sys.stderr,
                                )
                            return
                        if description.status != WorkflowExecutionStatus.RUNNING:
                            term_skipped += 1
                            return
                        try:
                            await handle.terminate("direct-aps probe cleanup")
                            term_ok += 1
                        except Exception as error:
                            try:
                                description = await handle.describe()
                                if (
                                    description.status
                                    != WorkflowExecutionStatus.RUNNING
                                ):
                                    term_skipped += 1
                                    return
                            except RPCError as describe_error:
                                if describe_error.status == RPCStatusCode.NOT_FOUND:
                                    term_skipped += 1
                                    return
                            except Exception:
                                pass
                            term_fail += 1
                            if term_fail <= 5:
                                print(
                                    f"  terminate failed for {wf_id}: {error}",
                                    file=sys.stderr,
                                )

                await asyncio.gather(*[_term(workflow_id) for workflow_id in all_ids])
                print(
                    f"  terminate ok={term_ok} already closed={term_skipped} "
                    f"failed={term_fail}"
                )

        if tot_other:
            sys.exit(f"direct-aps had {tot_other} non-RESOURCE_EXHAUSTED failures")
        if term_fail:
            sys.exit(f"failed to terminate {term_fail} Workflow Executions")

    asyncio.run(_run())
    _print_where_to_look(aps=True)


def cmd_signal_aps(args: argparse.Namespace) -> None:
    """Sustain APS pressure with Signals while keeping execution count tiny.

    Probe Workflows use an intentionally unpolled Task Queue. They remain open
    without app code or Worker capacity, so every accepted Signal is one Action
    and the probe is not paced by order Activities or mock-service latency.
    """
    try:
        import asyncio
        from collections import Counter
        from datetime import timedelta

        from appkit import build_temporal_client, resolve_data_converter
        from temporalio.client import WorkflowExecutionStatus
        from temporalio.runtime import PrometheusConfig, Runtime, TelemetryConfig
        from temporalio.service import RPCError, RPCStatusCode
    except ImportError as e:
        sys.exit(
            f"signal-aps needs the repo Python env ({e}).\n"
            "Run: uv run python compose/scripts/probe-dashboard-metrics.py signal-aps"
        )

    address, namespace, api_key = _load_temporal_creds()
    batch = uuid.uuid4().hex[:12]
    run_service_name = f"{args.sdk_metrics_service_name}-{batch}"
    total_budget = int(args.signal_rate * args.duration)
    print(
        f"signal-aps: {args.probe_workflows} probe Workflow Executions, "
        f"target={args.signal_rate:.0f} Signals/s for <= {args.duration:.0f}s "
        f"(attempt budget={total_budget}) -> {namespace} @ {address}"
    )
    print(
        "Probe Task Queue has no Worker by design; cleanup terminates every "
        "probe Workflow Execution."
    )
    if args.sdk_metrics_port != 9465:
        print(
            "WARN: provisioned Prometheus scrape target uses port 9465; update "
            "its target to ingest this custom SDK metrics port."
        )
    print(
        f"SDK metrics: http://127.0.0.1:{args.sdk_metrics_port}/metrics "
        f"service_name={run_service_name}"
    )

    async def _run() -> None:
        runtime = Runtime(
            telemetry=TelemetryConfig(
                metrics=PrometheusConfig(
                    bind_address=f"0.0.0.0:{args.sdk_metrics_port}",
                    durations_as_seconds=True,
                    unit_suffix=True,
                ),
                attach_service_name=False,
                global_tags={"service_name": run_service_name},
            )
        )
        client = await build_temporal_client(
            address=address,
            namespace=namespace,
            runtime=runtime,
            tls=True,
            api_key=api_key,
            data_converter=resolve_data_converter("default"),
        )

        print(
            f"waiting up to {args.metrics_warmup_seconds:.0f}s for Prometheus "
            "to scrape the SDK metrics endpoint",
            flush=True,
        )
        scrape_deadline = time.monotonic() + args.metrics_warmup_seconds
        last_scrape_error = "target has not reported up"
        while True:
            try:
                scrape_result = await asyncio.to_thread(
                    _prometheus_query,
                    'timestamp(up{job="dashboard-aps-probe-client"} == 1)',
                )
                fresh_scrape = any(
                    time.time() - float(series["value"][1]) <= 10
                    for series in scrape_result
                )
                if fresh_scrape:
                    print("Prometheus SDK metrics scrape confirmed", flush=True)
                    break
                last_scrape_error = "latest successful scrape is stale"
            except Exception as error:
                last_scrape_error = str(error)
            if time.monotonic() >= scrape_deadline:
                raise RuntimeError(
                    "Prometheus did not scrape the SDK metrics endpoint before "
                    f"the probe started: {last_scrape_error}"
                )
            await asyncio.sleep(1)

        workflow_ids = [
            f"{PROBE_ID_PREFIX}signal-{batch}-{i:02d}"
            for i in range(args.probe_workflows)
        ]

        async def _terminate_workflow_ids(workflow_ids: list[str]) -> int:
            print(
                f"checking {len(workflow_ids)} probe Workflow IDs for cleanup",
                flush=True,
            )
            sem = asyncio.Semaphore(20)

            async def _terminate_one(workflow_id: str) -> Exception | None:
                async with sem:
                    handle = client.get_workflow_handle(workflow_id)
                    try:
                        description = await handle.describe()
                    except RPCError as error:
                        if error.status == RPCStatusCode.NOT_FOUND:
                            return None
                        return error
                    except Exception as error:
                        return error
                    if description.status != WorkflowExecutionStatus.RUNNING:
                        return None
                    try:
                        await handle.terminate("signal-aps probe cleanup")
                        return None
                    except Exception as error:
                        try:
                            description = await handle.describe()
                            if description.status != WorkflowExecutionStatus.RUNNING:
                                return None
                        except RPCError as describe_error:
                            if describe_error.status == RPCStatusCode.NOT_FOUND:
                                return None
                        except Exception:
                            pass
                        return error

            cleanup = await asyncio.gather(
                *[_terminate_one(workflow_id) for workflow_id in workflow_ids]
            )
            failures = [result for result in cleanup if result is not None]
            for failure in failures[:5]:
                print(f"  termination failed: {failure}", file=sys.stderr)
            return len(failures)

        handles = []
        attempted_workflow_ids: list[str] = []
        try:
            for workflow_id in workflow_ids:
                attempted_workflow_ids.append(workflow_id)
                handle = await client.start_workflow(
                    "DashboardApsProbeWorkflow",
                    id=workflow_id,
                    task_queue=args.probe_task_queue,
                    execution_timeout=timedelta(minutes=5),
                )
                handles.append(handle)
        except BaseException:
            cleanup_failures = await _terminate_workflow_ids(attempted_workflow_ids)
            if cleanup_failures:
                print(
                    f"cleanup failed for {cleanup_failures} partially started "
                    "probe Workflow Executions",
                    file=sys.stderr,
                )
            raise
        print(f"started {len(handles)} probe Workflow Executions", flush=True)

        stats: Counter[str] = Counter()
        aps_limit_seen = asyncio.Event()
        pending: set[asyncio.Task[None]] = set()
        started_at = time.monotonic()
        deadline = started_at + args.duration
        next_start = started_at
        sequence = 0
        pressure_elapsed = 0.0

        async def _signal(seq: int) -> None:
            stats["attempted"] += 1
            handle = handles[seq % len(handles)]
            try:
                await handle.signal(
                    "aps_probe",
                    seq,
                    rpc_timeout=timedelta(seconds=args.rpc_timeout),
                )
                stats["accepted"] += 1
            except RPCError as e:
                if e.status == RPCStatusCode.RESOURCE_EXHAUSTED:
                    stats["resource_exhausted"] += 1
                    message = str(e)
                    if "aps" in message.lower():
                        stats["aps_limit"] += 1
                        if not aps_limit_seen.is_set():
                            print(f"APS LIMIT OBSERVED: {message}", flush=True)
                        aps_limit_seen.set()
                    elif stats["resource_exhausted"] <= 5:
                        print(f"other ResourceExhausted: {message}", flush=True)
                else:
                    stats["rpc_error"] += 1
                    if stats["rpc_error"] <= 5:
                        print(f"RPC error: {e}", flush=True)
            except Exception as e:
                stats["other_error"] += 1
                if stats["other_error"] <= 5:
                    print(f"{type(e).__name__}: {e}", flush=True)

        try:
            while (
                time.monotonic() < deadline
                and sequence < total_budget
                and not aps_limit_seen.is_set()
            ):
                while len(pending) >= args.max_in_flight:
                    _, pending = await asyncio.wait(
                        pending,
                        return_when=asyncio.FIRST_COMPLETED,
                    )
                    if aps_limit_seen.is_set():
                        break
                if aps_limit_seen.is_set():
                    break

                now = time.monotonic()
                if now < next_start:
                    await asyncio.sleep(next_start - now)
                task = asyncio.create_task(_signal(sequence))
                pending.add(task)
                task.add_done_callback(pending.discard)
                sequence += 1
                next_start = started_at + (sequence / args.signal_rate)

                if sequence % max(int(args.signal_rate * 2), 1) == 0:
                    elapsed = time.monotonic() - started_at
                    print(
                        f"t={elapsed:.1f}s attempted={stats['attempted']} "
                        f"accepted={stats['accepted']} "
                        f"resource_exhausted={stats['resource_exhausted']} "
                        f"in_flight={len(pending)}",
                        flush=True,
                    )

            if pending:
                _, pending = await asyncio.wait(
                    pending,
                    timeout=args.rpc_timeout + 1,
                )
                for task in pending:
                    task.cancel()
                if pending:
                    await asyncio.gather(*pending, return_exceptions=True)

            pressure_elapsed = time.monotonic() - started_at
            if stats["aps_limit"]:
                from urllib.request import urlopen

                def _read_sdk_failure_metrics() -> list[str]:
                    with urlopen(
                        f"http://127.0.0.1:{args.sdk_metrics_port}/metrics",
                        timeout=5,
                    ) as response:
                        body = response.read().decode("utf-8")
                    return [
                        line
                        for line in body.splitlines()
                        if line.startswith(
                            (
                                "temporal_request_failure{",
                                "temporal_long_request_failure{",
                            )
                        )
                    ]

                sdk_failure_metrics = await asyncio.to_thread(_read_sdk_failure_metrics)
                print("SDK FAILURE METRICS", flush=True)
                if sdk_failure_metrics:
                    for metric in sdk_failure_metrics:
                        print(f"  {metric}", flush=True)
                else:
                    print("  no request_failure series exported", flush=True)

            if stats["aps_limit"] and args.metrics_hold_seconds:
                print(
                    f"holding SDK metrics endpoint for "
                    f"{args.metrics_hold_seconds:.0f}s after ApsLimit",
                    flush=True,
                )
                await asyncio.sleep(args.metrics_hold_seconds)
            if stats["aps_limit"]:
                failure_query = (
                    'sum({__name__=~"temporal_(long_)?request_failure(_total)?",'
                    f"service_name={json.dumps(run_service_name)},"
                    f"namespace={json.dumps(namespace)}}})"
                )
                ingested = await asyncio.to_thread(
                    _prometheus_query,
                    failure_query,
                )
                if not any(float(series["value"][1]) > 0 for series in ingested):
                    raise RuntimeError(
                        "Prometheus did not ingest the probe's SDK request-failure "
                        "metric before cleanup"
                    )
                print("Prometheus SDK request-failure metric confirmed", flush=True)
        finally:
            cleanup_failures = await _terminate_workflow_ids(attempted_workflow_ids)

        print(
            "TOTAL "
            f"elapsed={pressure_elapsed:.2f}s attempted={stats['attempted']} "
            f"accepted={stats['accepted']} aps_limit={stats['aps_limit']} "
            f"other_resource_exhausted="
            f"{stats['resource_exhausted'] - stats['aps_limit']} "
            f"other_errors={stats['rpc_error'] + stats['other_error']} "
            f"cleanup_failures={cleanup_failures}"
        )
        if not stats["aps_limit"]:
            sys.exit("ApsLimit was not observed before the probe budget expired")
        if cleanup_failures:
            sys.exit(
                f"failed to terminate {cleanup_failures} probe Workflow Executions"
            )

    asyncio.run(_run())
    _print_where_to_look(aps=True)


def _print_where_to_look(*, aps: bool) -> None:
    print(
        "\nGrafana (http://localhost:3000) - allow about 3-4 min for "
        "Temporal Cloud OpenMetrics ingestion:\n"
        "  Sync match rate             Overall and Task Queue panels\n"
        "  Start latency               StartWorkflowExecution percentiles\n"
        "  Namespace limits and throttling  RPS usage and limit\n"
    )
    if aps:
        print(
            "  Namespace limits and throttling  APS usage, limit, and "
            "throttling\n"
            "  Request errors             SDK RESOURCE_EXHAUSTED when the "
            "probe exports SDK metrics\n"
        )


def main() -> None:
    p = argparse.ArgumentParser(description=__doc__.split("\n\n", 1)[0])
    p.add_argument(
        "mode",
        nargs="?",
        choices=("populate", "aps-burst", "direct-aps", "signal-aps"),
        default="populate",
        help=(
            "populate | aps-burst (via API) | direct-aps (start fan-out) | "
            "signal-aps (bounded Signal fan-out)"
        ),
    )
    p.add_argument(
        "--count",
        type=int,
        default=None,
        help="starts per wave (populate 20; aps-burst 600; direct-aps 800)",
    )
    p.add_argument(
        "--waves",
        type=int,
        default=1,
        help="aps-burst / direct-aps: how many concurrent batches (default 1)",
    )
    p.add_argument(
        "--wave-gap",
        type=float,
        default=None,
        help="seconds between waves (aps-burst default 0.5; direct-aps default 0)",
    )
    p.add_argument(
        "--aps-limit",
        type=int,
        default=500,
        help="expected namespace action_limit for warnings (default 500)",
    )
    p.add_argument(
        "--timeout",
        type=float,
        default=180.0,
        help="HTTP timeout per submit-batch seconds (default 180)",
    )
    p.add_argument(
        "--terminate",
        action="store_true",
        help="direct-aps: terminate PROBE-APS-* workflows after the burst",
    )
    p.add_argument(
        "--signal-rate",
        type=float,
        default=1200.0,
        help="signal-aps: target Signals per second (default 1200)",
    )
    p.add_argument(
        "--duration",
        type=float,
        default=40.0,
        help="signal-aps: maximum send duration in seconds (default 40)",
    )
    p.add_argument(
        "--probe-workflows",
        type=int,
        default=20,
        help="signal-aps: Workflow Executions sharing the Signal load (default 20)",
    )
    p.add_argument(
        "--probe-task-queue",
        default="dashboard-aps-probe-unpolled",
        help="signal-aps: intentionally unpolled Task Queue",
    )
    p.add_argument(
        "--max-in-flight",
        type=int,
        default=2048,
        help="signal-aps: maximum concurrent Signal RPCs (default 2048)",
    )
    p.add_argument(
        "--rpc-timeout",
        type=float,
        default=5.0,
        help="signal-aps: per-Signal RPC timeout in seconds (default 5)",
    )
    p.add_argument(
        "--sdk-metrics-port",
        type=int,
        default=9465,
        help="signal-aps: Prometheus SDK metrics port (default 9465)",
    )
    p.add_argument(
        "--sdk-metrics-service-name",
        default="dashboard-aps-probe-client",
        help="signal-aps: per-run service_name label prefix on SDK metrics",
    )
    p.add_argument(
        "--metrics-warmup-seconds",
        type=float,
        default=15.0,
        help="signal-aps: maximum wait for the first metrics scrape (default 15)",
    )
    p.add_argument(
        "--metrics-hold-seconds",
        type=float,
        default=40.0,
        help="signal-aps: keep metrics endpoint up after ApsLimit (default 40)",
    )
    p.add_argument(
        "--approve-cloud-load",
        action="store_true",
        help=(
            "confirm separate approval for the displayed Temporal Cloud "
            "execution and request budget"
        ),
    )
    args = p.parse_args()

    if args.count is None:
        args.count = {
            "populate": 20,
            "aps-burst": 600,
            "direct-aps": 800,
            "signal-aps": 1,
        }[args.mode]
    if args.wave_gap is None:
        args.wave_gap = 0.0 if args.mode == "direct-aps" else 0.5
    if args.count < 1:
        sys.exit("--count must be >= 1")
    if args.waves < 1:
        sys.exit("--waves must be >= 1")

    preflight(args)

    if args.mode == "populate":
        cmd_populate(args)
    elif args.mode == "aps-burst":
        cmd_aps_burst(args)
    elif args.mode == "direct-aps":
        cmd_direct_aps(args)
    else:
        if args.signal_rate <= 0:
            sys.exit("--signal-rate must be > 0")
        if args.duration <= 0:
            sys.exit("--duration must be > 0")
        if args.probe_workflows < 1:
            sys.exit("--probe-workflows must be >= 1")
        if args.max_in_flight < 1:
            sys.exit("--max-in-flight must be >= 1")
        if args.rpc_timeout <= 0:
            sys.exit("--rpc-timeout must be > 0")
        if not 1 <= args.sdk_metrics_port <= 65535:
            sys.exit("--sdk-metrics-port must be between 1 and 65535")
        if not args.sdk_metrics_service_name:
            sys.exit("--sdk-metrics-service-name must not be empty")
        if args.metrics_warmup_seconds < 0:
            sys.exit("--metrics-warmup-seconds must be >= 0")
        if args.metrics_hold_seconds < 0:
            sys.exit("--metrics-hold-seconds must be >= 0")
        try:
            cmd_signal_aps(args)
        except KeyboardInterrupt:
            print("signal-aps interrupted after cleanup")
            raise SystemExit(130) from None


if __name__ == "__main__":
    main()
