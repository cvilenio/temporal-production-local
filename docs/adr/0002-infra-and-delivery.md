# ADR-0002: Infrastructure & delivery — Terraform control plane + ArgoCD/Helm

- **Status:** Accepted
- **Date:** 2026-06-23

## Context

We want a production-like local lifecycle on kind that also provisions Temporal Cloud,
lets us change k8s and Cloud "quickly and reliably," and avoids deep GitOps rabbit holes.
Terraform is familiar and strong for cluster + Cloud lifecycle but clunky for raw k8s YAML.
ArgoCD is excellent for declarative k8s delivery but adds a learning curve. Crossplane was
considered and rejected as too heavy for the goal.

## Decision

Split by plane:

- **Control plane → Terraform.** Provision the kind cluster, Temporal Cloud (namespaces,
  users, API keys via the `temporalio` provider), and install ArgoCD + the root app-of-apps.
- **Workloads → ArgoCD → Helm.** Everything that runs on kind (Temporal server, workers,
  apps, codec, observability) is a Helm chart delivered by ArgoCD using an app-of-apps.

Scope is deliberately small: Temporal Cloud is the only external system, everything else is
one machine, ArgoCD is used as a reliable app-of-apps + Helm runner — not as a GitOps
research project.

## Consequences

- Two tools, each used where it is strongest; one `terraform apply` stands up the platform,
  ArgoCD keeps workloads in sync.
- The colleague reference uses FluxCD; its Helm values, CNPG Postgres, kind config, and
  observability wiring port directly — only the delivery layer (Flux → Argo) changes.
- A future "ramp via GitOps" story is available by switching the Worker Controller to its
  `Progressive` strategy (ADR-0004).
