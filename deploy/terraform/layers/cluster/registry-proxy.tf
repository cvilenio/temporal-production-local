# TLS proxy in front of the local (HTTP) registry, JUST for ArgoCD.
#
# ArgoCD v3's repo `insecure: true` means skip-TLS-verify, not plain-HTTP — its
# repo-server still speaks HTTPS, so it can't pull OCI charts from the plain-HTTP
# registry directly. Every other client (host docker push, node containerd, crane,
# helm) already works over HTTP and is left untouched. This nginx terminates HTTPS
# (self-signed; ArgoCD skip-verifies) and proxies to the HTTP registry Service.
#
# In-cluster chart repo for ArgoCD: registry-tls.kube-public.svc:5000/charts

resource "tls_private_key" "registry_proxy" {
  algorithm   = "ECDSA"
  ecdsa_curve = "P256"
}

resource "tls_self_signed_cert" "registry_proxy" {
  private_key_pem = tls_private_key.registry_proxy.private_key_pem

  subject {
    common_name = "registry-tls.kube-public.svc"
  }

  dns_names = [
    "registry-tls",
    "registry-tls.kube-public",
    "registry-tls.kube-public.svc",
    "registry-tls.kube-public.svc.cluster.local",
  ]

  validity_period_hours = 8760 # 1 year (local dev)
  allowed_uses          = ["key_encipherment", "digital_signature", "server_auth"]
}

resource "kubernetes_secret" "registry_proxy_tls" {
  metadata {
    name      = "registry-tls"
    namespace = "kube-public"
  }
  type = "kubernetes.io/tls"
  data = {
    "tls.crt" = tls_self_signed_cert.registry_proxy.cert_pem
    "tls.key" = tls_private_key.registry_proxy.private_key_pem
  }
}

resource "kubernetes_config_map" "registry_proxy_nginx" {
  metadata {
    name      = "registry-tls-nginx"
    namespace = "kube-public"
  }
  data = {
    "nginx.conf" = <<-EOT
      worker_processes 1;
      events { worker_connections 1024; }
      http {
        server {
          listen 8443 ssl;
          ssl_certificate     /tls/tls.crt;
          ssl_certificate_key /tls/tls.key;
          client_max_body_size 0;            # registry blobs can be large
          chunked_transfer_encoding on;
          location / {
            proxy_pass http://${var.registry_service}.kube-public.svc:5000;
            proxy_set_header Host              $host;
            proxy_set_header X-Forwarded-Proto https;
            proxy_read_timeout 300s;
          }
        }
      }
    EOT
  }
}

resource "kubernetes_deployment" "registry_proxy" {
  metadata {
    name      = "registry-tls"
    namespace = "kube-public"
    labels    = { app = "registry-tls" }
  }
  spec {
    replicas = 1
    selector { match_labels = { app = "registry-tls" } }
    template {
      metadata { labels = { app = "registry-tls" } }
      spec {
        container {
          name  = "nginx"
          image = "${local.deps.images.nginx.repository}:${local.deps.images.nginx.tag}" # from config/dependencies.yaml; pull-through-cached by zot
          port { container_port = 8443 }
          volume_mount {
            name       = "tls"
            mount_path = "/tls"
            read_only  = true
          }
          volume_mount {
            name       = "conf"
            mount_path = "/etc/nginx/nginx.conf"
            sub_path   = "nginx.conf"
            read_only  = true
          }
          readiness_probe {
            tcp_socket { port = 8443 }
            initial_delay_seconds = 2
            period_seconds        = 5
          }
        }
        volume {
          name = "tls"
          secret { secret_name = kubernetes_secret.registry_proxy_tls.metadata[0].name }
        }
        volume {
          name = "conf"
          config_map { name = kubernetes_config_map.registry_proxy_nginx.metadata[0].name }
        }
      }
    }
  }
}

resource "kubernetes_service" "registry_proxy" {
  metadata {
    name      = "registry-tls"
    namespace = "kube-public"
  }
  spec {
    selector = { app = "registry-tls" }
    port {
      name        = "https"
      port        = 5000
      target_port = 8443
      protocol    = "TCP"
    }
  }
}
