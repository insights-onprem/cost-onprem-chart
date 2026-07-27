# Sizing Profile Values Overlays

**Moved**: Profile overlays now live alongside the chart at
[`cost-onprem/values-{small,medium,large,xlarge}.yaml`](../../../cost-onprem/).

See the [sizing guide](../../performance/sizing-guide.md) for usage and the
[operator CRD mapping](../../performance/operator-profile-crd-mapping.md) for
the future operator recommendation.

```bash
# Deploy with medium profile
./scripts/deploy-test-cost-onprem.sh --namespace cost-onprem --sizing-profile medium

# Or manually
helm upgrade --install cost-onprem ./cost-onprem \
  -n cost-onprem \
  -f openshift-values.yaml \
  -f cost-onprem/values-medium.yaml \
  --wait
```
