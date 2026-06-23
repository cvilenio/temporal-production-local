provider "temporalcloud" {
  # Bootstrap (account-level) API key. Supplied via env: TEMPORAL_CLOUD_API_KEY (read
  # directly by the provider) or TF_VAR_temporal_cloud_api_key. Never committed.
  # Pass the var only when set; otherwise null so the provider falls back to the
  # TEMPORAL_CLOUD_API_KEY env var (passing "" would override that fallback).
  api_key = var.temporal_cloud_api_key != "" ? var.temporal_cloud_api_key : null

  # Pin to the demo account so a misconfigured key can't mutate another account.
  allowed_account_id = var.account_id
}
