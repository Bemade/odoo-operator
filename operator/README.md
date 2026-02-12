# operator

Go/kubebuilder source for the odoo-operator controller manager.

See the [root README](../README.md) for installation and usage.

## Structure

```
api/v1alpha1/          — CRD types and validating webhook
cmd/main.go            — entrypoint, flag parsing, controller registration
internal/controller/   — reconcilers for all five CRD types
config/                — kubebuilder-generated kustomize manifests (CRDs, RBAC)
```

## Development

```sh
# Run tests (uses envtest, no cluster required)
go test ./...

# After changing types in api/v1alpha1/
make manifests    # regenerates CRDs and RBAC annotations
make helm-crds    # copies generated CRDs into charts/odoo-operator/templates/crds/

# Build binary
go build ./cmd/main.go
```

## Adding a new field to OdooInstance

1. Add the field to `api/v1alpha1/odooinstance_types.go`
2. Update `zz_generated.deepcopy.go` (or run `make generate`)
3. Run `make manifests && make helm-crds` to update CRDs
4. Handle the field in `internal/controller/odooinstance_controller.go`
5. Add tests in `internal/controller/odooinstance_controller_test.go`
6. If the field should be immutable after creation, add validation to `api/v1alpha1/odooinstance_webhook.go`
