# Troubleshooting

## Common Issues

### No Resource Optimization Data Being Collected

**Problem**: Cost Management Metrics Operator is not collecting data from your namespace, no ROS files are being generated, or Kruize is not receiving metrics.

**Cause**: Missing required namespace label.

**Solution**:
```bash
# Check if the label exists
kubectl get namespace cost-onprem --show-labels | grep cost_management

# Apply the required label
kubectl label namespace cost-onprem cost_management_optimizations=true --overwrite

# Verify the label was applied
kubectl get namespace cost-onprem -o jsonpath='{.metadata.labels.cost_management_optimizations}'
# Should output: true
```

**Note**: The `scripts/install-helm-chart.sh` script automatically applies this label during deployment. If you deployed manually or the label was removed, you need to apply it manually.

**To remove the label (if testing):**
```bash
kubectl label namespace cost-onprem cost_management_optimizations-
```

**Legacy Label**: For backward compatibility, you can also use `insights_cost_management_optimizations=true` (the old label from koku-metrics-operator v4.0.x), but `cost_management_optimizations` is recommended for new deployments.

---

### Testing and Validating the Upload Pipeline

**Problem**: You want to test the end-to-end data flow (Operator → Ingress → Processor → Kruize) without waiting 6 hours for the automatic upload cycle.

**Solution**: Use the force upload feature to manually trigger packaging and upload immediately.

**Quick Test:**
```bash
# Run the convenience script
./scripts/force-operator-package-upload.sh
```

This bypasses the default 6-hour packaging/upload cycle and lets you validate:
- ✅ Operator is collecting ROS metrics (container and namespace CSVs)
- ✅ Ingress accepts and processes the upload
- ✅ Processor consumes Kafka messages
- ✅ Kruize receives experiment data

**Important Note**: Kruize uses a 15-minute default measurement duration and maintains a unique constraint on `(experiment_name, interval_end_time)`. It will reject duplicate uploads with the same `interval_end_time`. This is **expected behavior** when testing - the pipeline is still working correctly even if Kruize shows "already exists" errors. This actually **proves** the data reached Kruize! See the [Force Operator Upload Guide](force-operator-upload.md) for details.

**Manual Commands:**
```bash
# Step 1: Reset packaging timestamp to bypass 6-hour timer
kubectl patch costmanagementmetricsconfig \
  -n costmanagement-metrics-operator costmanagementmetricscfg-tls \
  --type='json' \
  -p='[{"op": "replace", "path": "/status/packaging/last_successful_packaging_time", "value": "2020-01-01T00:00:00Z"}]' \
  --subresource=status

# Step 2: Trigger operator reconciliation
kubectl annotate -n costmanagement-metrics-operator \
  costmanagementmetricsconfig costmanagementmetricscfg-tls \
  clusterconfig.openshift.io/force-collection="$(date +%s)" --overwrite

# Step 3: Verify upload (wait ~60 seconds)
kubectl get costmanagementmetricsconfig -n costmanagement-metrics-operator \
  costmanagementmetricscfg-tls -o jsonpath='{.status.upload.last_upload_status}'
# Should show: 202 Accepted
```

**Verification Steps:**

1. **Check Ingress Logs**:
   ```bash
   kubectl logs -n cost-onprem -l app.kubernetes.io/component=ingress -c ingress --tail=50
   # Look for: "Successfully identified ROS files", "Successfully sent ROS event message"
   ```

2. **Check Processor Logs**:
   ```bash
   kubectl logs -n cost-onprem -l app.kubernetes.io/component=processor --tail=50
   # Look for: "Message received from kafka hccm.ros.events"
   ```

3. **Check Kruize Logs**:
   ```bash
   kubectl logs -n cost-onprem -l app.kubernetes.io/name=kruize --tail=100 | grep experiment
   # Look for: experiment_name with your cluster UUID
   ```

**📖 See [Force Operator Upload Guide](force-operator-upload.md) for complete documentation, including:**
- Detailed explanation of what each command does
- All verification steps with expected outputs
- Troubleshooting common issues
- Understanding Kruize's 15-minute bucket behavior

---

### Pods Getting OOMKilled (Out of Memory)

**Problem**: Pods crashing with OOMKilled status.
```bash
# Check pod status for OOMKilled
kubectl get pods -n cost-onprem

# If you see OOMKilled status, increase memory limits
# Create custom values file
cat > low-resource-values.yaml << EOF
resources:
  kruize:
    requests:
      memory: "512Mi"
      cpu: "250m"
    limits:
      memory: "1Gi"
      cpu: "500m"

  database:
    requests:
      memory: "128Mi"
      cpu: "100m"
    limits:
      memory: "256Mi"
      cpu: "250m"

  application:
    requests:
      memory: "128Mi"
      cpu: "100m"s
    limits:
      memory: "256Mi"
      cpu: "200m"
EOF

# Upgrade with reduced resources
VALUES_FILE=low-resource-values.yaml ./install-helm-chart.sh
```

**Kruize listExperiments API error:**

The Kruize `/listExperiments` endpoint may show errors related to missing `KruizeLMExperimentEntry` entity. This is a known issue with the current Kruize image version, but experiments are still being created and processed correctly in the database.

```bash
# Workaround: Check experiments directly in database
kubectl exec -n cost-onprem cost-onprem-db-kruize-0 -- \
  psql -U postgres -d postgres -c "SELECT experiment_name, status FROM kruize_experiments;"
```

**Kafka connectivity issues (Connection refused errors):**

This is a common issue affecting multiple services (processor, recommendation-poller, housekeeper).

```bash
# Step 1: Check current Kafka status
kubectl get pods -n cost-onprem -l app.kubernetes.io/name=kafka
kubectl logs -n cost-onprem -l app.kubernetes.io/name=kafka --tail=20

# Step 2: Apply Kafka networking fix and restart
./install-helm-chart.sh
kubectl rollout restart statefulset/cost-onprem-kafka -n cost-onprem

# Step 3: Wait for Kafka to be ready
kubectl wait --for=condition=ready pod -l app.kubernetes.io/name=kafka -n cost-onprem --timeout=300s

# Step 4: Restart all dependent services
kubectl rollout restart deployment/cost-onprem-ros-processor -n cost-onprem
kubectl rollout restart deployment/cost-onprem-ros-recommendation-poller -n cost-onprem
kubectl rollout restart deployment/cost-onprem-ros-housekeeper -n cost-onprem
kubectl rollout restart deployment/cost-onprem-ingress -n cost-onprem

# Step 5: Verify connectivity
kubectl logs -n cost-onprem -l app.kubernetes.io/name=ros-processor --tail=10
kubectl exec -n cost-onprem deployment/cost-onprem-ros-processor -- nc -zv cost-onprem-kafka 29092
```

**Alternative: Complete redeployment if issues persist:**
```bash
# Delete and redeploy if Kafka issues persist
./install-helm-chart.sh cleanup --complete
./deploy-kind.sh
./install-helm-chart.sh
```

**Pods not starting:**
```bash
# Check pod status and events
kubectl get pods -n cost-onprem
kubectl describe pod -n cost-onprem <pod-name>

# Check logs
kubectl logs -n cost-onprem <pod-name>
```

**Services not accessible:**
```bash
# Check if services are created
kubectl get svc -n cost-onprem

# Test port forwarding as alternative
kubectl port-forward -n cost-onprem svc/cost-onprem-ingress 3000:3000
kubectl port-forward -n cost-onprem svc/cost-onprem-ros-api 8001:8000
```

**Storage issues:**
```bash
# Check persistent volume claims
kubectl get pvc -n cost-onprem

# Check storage class
kubectl get storageclass
```

### Network Policy Issues (OpenShift)

**Problem**: Service-to-service communication failing or Prometheus not scraping metrics.

**Symptoms**:
- External requests to backend services getting connection refused or timeouts
- Prometheus metrics missing in monitoring dashboards
- Services can't communicate with each other

**Diagnosis**:
```bash
# Check if network policies are deployed
oc get networkpolicies -n cost-onprem

# Describe specific policy
oc describe networkpolicy kruize-allow-ingress -n cost-onprem
oc describe networkpolicy ros-metrics-allow-ingress -n cost-onprem
oc describe networkpolicy sources-api-allow-ingress -n cost-onprem

# Test connectivity from within namespace (should work)
oc exec -n cost-onprem deployment/cost-onprem-ros-processor -- \
  curl -s http://cost-onprem-kruize:8080/listApplications

# Test connectivity from monitoring namespace (Prometheus - should work for metrics)
oc exec -n openshift-monitoring prometheus-k8s-0 -- \
  curl -s http://cost-onprem-kruize.cost-onprem.svc:8080/metrics
```

**Common Causes and Fixes**:

1. **External traffic not using Envoy sidecar (port 8080)**
   - **Symptom**: Direct access to backend ports (8000, 8001, 8081) fails
   - **Fix**: Ensure routes and ingresses point to port 8080, not backend ports
   ```bash
   # Check route configuration
   oc get route cost-onprem-main -n cost-onprem -o yaml | grep targetPort
   # Should show: targetPort: 8080
   ```

2. **Prometheus can't scrape metrics**
   - **Symptom**: Metrics missing from Prometheus/Grafana
   - **Fix**: Verify network policies allow `openshift-monitoring` namespace
   ```bash
   # Check if monitoring namespace selector is present
   oc get networkpolicy kruize-allow-ingress -n cost-onprem -o yaml | \
     grep -A3 "namespaceSelector"
   # Should include: name: openshift-monitoring
   ```

3. **Services in different namespaces can't communicate**
   - **Symptom**: Cross-namespace communication blocked
   - **Fix**: This is expected behavior. Network policies restrict to same namespace and monitoring.
   - **Solution**: Deploy services in the same namespace or add explicit network policy rules

**Reference**: See [JWT Authentication Guide - Network Policies](native-jwt-authentication.md#network-policies) for detailed configuration

---

### JWT Authentication Issues (OpenShift)

**Problem**: Authentication failures or missing X-Rh-Identity header.

**Symptoms**:
- 401 Unauthorized errors
- Logs show "Invalid or missing identity"
- Envoy sidecar not injecting headers

**Diagnosis**:
```bash
# Check if Envoy sidecars are running
oc get pods -n cost-onprem -o json | \
  jq -r '.items[] | select(.spec.containers | length > 1) | .metadata.name'
# Should show pods with multiple containers (app + envoy-proxy)

# Check Envoy logs
oc logs -n cost-onprem deployment/cost-onprem-ingress -c envoy-proxy --tail=50

# Check Envoy configuration
oc get configmap cost-onprem-envoy-config-ingress -n cost-onprem -o yaml

# Verify Keycloak connectivity
oc exec -n cost-onprem deployment/cost-onprem-ingress -c envoy-proxy -- \
  curl -k -I https://keycloak-keycloak.apps.example.com
```

**Common Causes and Fixes**:

1. **Envoy sidecar not deployed**
   - **Cause**: Platform not detected as OpenShift or JWT disabled
   - **Fix**: Verify OpenShift API groups are available
   ```bash
   kubectl api-resources | grep route.openshift.io
   ```

2. **Keycloak URL not reachable from Envoy**
   - **Cause**: Network connectivity or DNS issues
   - **Fix**: Check Keycloak route and connectivity
   ```bash
   oc get route keycloak -n keycloak -o jsonpath='{.spec.host}'
   ```

3. **JWT missing org_id claim**
   - **Cause**: Keycloak client not configured with org_id mapper
   - **Fix**: See [Keycloak Setup Guide](keycloak-jwt-authentication-setup.md)

**Reference**: See [JWT Authentication Guide](native-jwt-authentication.md) for detailed troubleshooting

---

**Strimzi operator OOMKilled:**

When this happens you typically see the operator pod cycling with logs full of repeated `Attempting reconnect` messages, `SessionExpiredException`, or `NoSuchElementException` just before it is killed by the OOM killer.
```bash
# Check pod status for OOMKilled
kubectl get pods -n kafka -l name=strimzi-cluster-operator

# Bump the operator memory limits on the fly
kubectl set resources deployment/strimzi-cluster-operator \
  -n kafka --containers=strimzi-cluster-operator \
  --limits=memory=768Mi --requests=memory=768Mi

# Confirm the pod restarts with the new limits
kubectl get pods -n kafka -l name=strimzi-cluster-operator
```

---

## E2E Validation Script Issues

### Trino Tables Not Created After Manual Table Drops

**Problem**: After manually dropping Trino tables, Koku doesn't recreate them and summarization fails with `Table 'org1234567.openshift_pod_usage_line_items_daily' does not exist`.

**Cause**: Koku caches the result of `table_exists()` in Redis. When you manually drop Trino tables, the Redis cache still returns `True`, causing Koku to skip table creation. This is a known Koku behavior where the cache is not invalidated on external table drops.

**Symptoms**:
- Listener logs show `'create_table': True` but no `attempting to create parquet table` message
- Listener logs show `WARNING TrinoUserError(type=USER_ERROR, name=NOT_FOUND, message="Table 'org1234567.openshift_xxx' not found")`
- Summarization fails with `TABLE_NOT_FOUND` error

**Solution**: Clear Redis/Valkey cache and restart the Koku listener:

```bash
# Step 1: Flush Redis/Valkey cache (uses valkey-cli, Redis-compatible)
REDIS_POD=$(kubectl get pod -n cost-onprem -l app.kubernetes.io/name=redis -o jsonpath='{.items[0].metadata.name}')
kubectl exec -n cost-onprem $REDIS_POD -- valkey-cli FLUSHALL

# Step 2: Restart Koku listener to clear in-memory state
kubectl delete pod -n cost-onprem -l app.kubernetes.io/component=listener
kubectl wait --for=condition=ready pod -l app.kubernetes.io/component=listener -n cost-onprem --timeout=60s

# Step 3: Trigger data processing again
./scripts/cost-mgmt-ocp-dataflow.sh --force
```

**Note**: The Redis deployment uses Valkey (Redis-compatible). If Redis has a PVC, restarting the pod alone won't clear the cache - you must use `valkey-cli FLUSHALL`.

**Note**: This issue only occurs when tables are manually dropped. In normal operation, tables are created once and persist, so this caching behavior is not problematic. The E2E script works like production - it doesn't drop/recreate tables.

---

### Starting Fresh: Complete E2E Test Environment Reset

**Problem**: You need to completely reset the E2E test environment to start fresh.

**When to use**: Only when you have corrupted state or need to test first-time installation behavior.

**Steps**:

```bash
# 1. Drop all OCP Trino tables
for table in openshift_pod_usage_line_items openshift_pod_usage_line_items_daily \
             openshift_node_labels_line_items openshift_node_labels_line_items_daily \
             openshift_namespace_labels_line_items openshift_namespace_labels_line_items_daily; do
    kubectl exec -n cost-onprem trino-coordinator-0 -- \
        trino --execute "DROP TABLE IF EXISTS hive.org1234567.$table"
done

# 2. Delete parquet files from S3
kubectl exec -n cost-onprem -l app.kubernetes.io/name=minio -- \
    mc rm --recursive --force myminio/cost-data/data/parquet/org1234567/OCP/

# 3. Clear Redis/Valkey cache (uses valkey-cli, Redis-compatible)
REDIS_POD=$(kubectl get pod -n cost-onprem -l app.kubernetes.io/name=redis -o jsonpath='{.items[0].metadata.name}')
kubectl exec -n cost-onprem $REDIS_POD -- valkey-cli FLUSHALL

# 4. Restart Koku listener
kubectl delete pod -n cost-onprem -l app.kubernetes.io/component=listener
kubectl wait --for=condition=ready pod -l app.kubernetes.io/component=listener -n cost-onprem --timeout=60s

# 5. Delete test data from S3
kubectl exec -n cost-onprem -l app.kubernetes.io/name=minio -- \
    mc rm --recursive --force myminio/cost-data/reports/

# 6. Run E2E test
./scripts/cost-mgmt-ocp-dataflow.sh --force
```

**Warning**: This is a destructive operation. Only use when you need a complete reset.

---

### Debug Commands

```bash
# Get all resources in namespace
kubectl get all -n cost-onprem

# Check Helm release status
helm status cost-onprem -n cost-onprem

# View Helm values
helm get values cost-onprem -n cost-onprem

# Check cluster info
kubectl cluster-info

# Check network policies (OpenShift)
oc get networkpolicies -n cost-onprem

# Check Envoy sidecars (OpenShift)
oc get pods -n cost-onprem -o jsonpath='{range .items[*]}{.metadata.name}{"\t"}{range .spec.containers[*]}{.name}{" "}{end}{"\n"}{end}'
```