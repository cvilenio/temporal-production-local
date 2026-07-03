# temporal-worker-autoscaler — code generation + build.
# Kept self-contained (first Go app in the repo); `just` recipes wrap the image build.
CONTROLLER_GEN := go run sigs.k8s.io/controller-tools/cmd/controller-gen@v0.19.0

.PHONY: generate manifests build test vet fmt all

# Generate DeepCopy methods for the API types.
generate:
	$(CONTROLLER_GEN) object paths=./api/...

# Generate the CRD + RBAC role from kubebuilder markers.
manifests:
	$(CONTROLLER_GEN) crd paths=./api/... output:crd:dir=config/crd/bases
	$(CONTROLLER_GEN) rbac:roleName=temporal-worker-autoscaler paths=./internal/... output:rbac:dir=config/rbac

fmt:
	go fmt ./...

vet:
	go vet ./...

test:
	go test ./...

build:
	go build ./...

all: generate manifests fmt vet build test
