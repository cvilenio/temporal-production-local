# Single source of truth for third-party versions (config/dependencies.yaml),
# read the same way the cloud layer reads namespaces.yaml. Pins the OSS chart
# versions (ArgoCD, add-on Applications' targetRevision) and image versions
# (the registry-proxy nginx) delivered through the local OCI mirror — so each
# version lives in exactly one place (shared with mirror-deps via deps.env).
locals {
  deps           = yamldecode(file("${path.module}/../../../../config/dependencies.yaml"))
  chart_versions = { for name, c in local.deps.charts : name => c.version }
}
