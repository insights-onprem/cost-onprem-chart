# Cost Management Operator Upload Verification Checklist

This checklist ensures 100% confidence that the Cost Management Metrics Operator is successfully uploading payloads and that they are being processed through the entire ROS pipeline.

## Prerequisites

- Namespace with `cost_management_optimizations=true` label exists
- Cost Management Operator is deployed and configured
- ROS components (ingress, processor, Kruize) are running

---

## Step 1: Verify Operator Status

Check that the operator is running and has successfully uploaded:

```bash
# Check operator pod status
oc get pods -n costmanagement-metrics-operator

# Check CostManagementMetricsConfig status
oc get costmanagementmetricsconfig -n costmanagement-metrics-operator operator-configuration -o yaml

# Look for:
# - last_successful_upload_time: <recent timestamp>
# - upload: "Upload successful"
# - data_collection_message: Should NOT say "No namespaces..."
```

**Expected Results:**
- ✅ Operator pod is Running
- ✅ `last_successful_upload_time` is recent (within last upload cycle)
- ✅ `upload: "Upload successful"`
- ✅ `data_collection_message` confirms metrics were collected

**If Failed:** Operator is not running or not uploading. Check operator logs.

---

## Step 2: Check Operator Logs for Upload

Verify the operator logged the upload:

```bash
# Get operator pod name
OPERATOR_POD=$(oc get pods -n costmanagement-metrics-operator -l app=costmanagement-metrics-operator -o jsonpath='{.items[0].metadata.name}')

# Check logs for recent upload
oc logs -n costmanagement-metrics-operator $OPERATOR_POD --tail=200 | grep -A 10 "Uploading"

# Look for:
# - "Uploading payload to http://..."
# - "Upload successful" with 200 status code
# - ROS metrics files packaged
```

**Expected Results:**
- ✅ Log line: `Uploading payload to http://cost-onprem-ingress...`
- ✅ Log line: `Upload successful` with HTTP 200 or 202
- ✅ ROS metrics files mentioned in packaging

**If Failed:** Check network connectivity, ingress endpoint, JWT token validity.

---

## Step 3: Verify Ingress Received Payload

Check that the ingress service received and acknowledged the upload:

```bash
# Get ingress pod name
INGRESS_POD=$(oc get pods -n cost-onprem -l app.kubernetes.io/component=ingress -o jsonpath='{.items[0].metadata.name}')

# Check ingress logs
oc logs -n cost-onprem $INGRESS_POD -c ingress --tail=200 | grep -E "upload|ros|202"

# Look for:
# - POST /api/ingress/v1/upload with 202 response
# - File extraction logs
# - S3/storage upload confirmation
```

**Expected Results:**
- ✅ Log line: `POST /api/ingress/v1/upload` with 202 status
- ✅ Files extracted successfully
- ✅ Files uploaded to storage (S3/ODF)

**If Failed:** Check ingress logs for errors, JWT authentication issues, or storage problems.

---

## Step 4: Check Kafka Topic for Messages

Verify the payload was published to Kafka:

```bash
# Get Kafka pod name
KAFKA_POD=$(oc get pods -n kafka -l app.kubernetes.io/name=kafka -o jsonpath='{.items[0].metadata.name}')

# Check topic for recent messages
oc exec -n cost-onprem $KAFKA_POD -- kafka-console-consumer.sh \
  --bootstrap-server localhost:9092 \
  --topic hccm.ros.events \
  --from-beginning \
  --max-messages 10 \
  --timeout-ms 5000

# Look for JSON messages with:
# - "path": "ros/..."
# - "cluster_id": <your-cluster-id>
# - Recent timestamps
```

**Expected Results:**
- ✅ Messages exist in `hccm.ros.events` topic
- ✅ Message contains correct `path` to uploaded file
- ✅ Message timestamp is recent

**If Failed:** Ingress may not be publishing to Kafka. Check ingress Kafka configuration.

---

## Step 5: Verify Processor Consumed and Processed

Check that the processor consumed the Kafka message and sent data to Kruize:

```bash
# Get processor pod name
PROCESSOR_POD=$(oc get pods -n cost-onprem -l app.kubernetes.io/component=ros-processor -o jsonpath='{.items[0].metadata.name}')

# Check processor logs
oc logs -n cost-onprem $PROCESSOR_POD --tail=200 | grep -E "Processing|Kruize|experiment|recommendation"

# Look for:
# - "Processing message from Kafka"
# - "Sending data to Kruize"
# - "Experiment created/updated"
# - No errors about missing columns or validation failures
```

**Expected Results:**
- ✅ Log line: Kafka message consumed
- ✅ Log line: Data sent to Kruize
- ✅ No validation errors (e.g., "CSV file does not have all required columns")

**If Failed:** Check processor logs for CSV parsing errors, Kruize API errors.

---

## Step 6: Confirm Kruize Received Experiments

Verify Kruize has new or updated experiments:

```bash
# Query Kruize database for experiments
./scripts/query-kruize.sh --experiments

# Or query Kruize API directly
KRUIZE_POD=$(oc get pods -n cost-onprem -l app.kubernetes.io/component=ros-optimization -o jsonpath='{.items[0].metadata.name}')
oc exec -n cost-onprem $KRUIZE_POD -- curl -s http://localhost:8080/listExperiments

# Look for experiments with recent interval_end_time
```

**Expected Results:**
- ✅ New experiments exist with recent `interval_end_time`
- ✅ Experiment names match workload names from monitored namespace
- ✅ Container metrics are present (CPU, memory)

**If Failed:** Processor may not be successfully calling Kruize API. Check processor logs and Kruize API health.

---

## Step 7: Verify Kruize Generated Recommendations (Optional)

After sufficient data is collected (15+ minutes), check for recommendations:

```bash
# Query for recommendations
./scripts/query-kruize.sh --recommendations

# Or check via Kruize API
oc exec -n cost-onprem $KRUIZE_POD -- curl -s http://localhost:8080/listRecommendations
```

**Expected Results:**
- ✅ Recommendations exist for experiments
- ✅ Recommendations include resource limits/requests
- ✅ Recommendations have cost information

**Note:** This may take time depending on Kruize's `MEASUREMENT_DURATION_THRESHOLD_MINUTES` setting.

---

## Summary: Confidence Assessment

After completing all steps, assess your confidence:

| Step | Status | Issue |
|------|--------|-------|
| 1. Operator Status | ✅/❌ | |
| 2. Operator Upload Logs | ✅/❌ | |
| 3. Ingress Received | ✅/❌ | |
| 4. Kafka Message | ✅/❌ | |
| 5. Processor Processed | ✅/❌ | |
| 6. Kruize Experiments | ✅/❌ | |
| 7. Kruize Recommendations | ✅/❌ | (Optional) |

**Confidence Level:**
- **100% Confidence:** All steps 1-6 pass ✅
- **75% Confidence:** Steps 1-5 pass, Kruize experiments pending
- **50% Confidence:** Steps 1-3 pass, backend processing uncertain
- **0% Confidence:** Step 1 or 2 fails - operator not uploading

---

## Quick Check Script

For convenience, you can run all checks in sequence:

```bash
#!/bin/bash
set -e

echo "=== Step 1: Operator Status ==="
oc get costmanagementmetricsconfig -n costmanagement-metrics-operator operator-configuration -o jsonpath='{.status.prometheus.last_query_success_time}{"\n"}'

echo -e "\n=== Step 2: Operator Logs ==="
OPERATOR_POD=$(oc get pods -n costmanagement-metrics-operator -l app=costmanagement-metrics-operator -o jsonpath='{.items[0].metadata.name}')
oc logs -n costmanagement-metrics-operator $OPERATOR_POD --tail=50 | grep "Upload successful"

echo -e "\n=== Step 3: Ingress Logs ==="
INGRESS_POD=$(oc get pods -n cost-onprem -l app.kubernetes.io/component=ingress -o jsonpath='{.items[0].metadata.name}')
oc logs -n cost-onprem $INGRESS_POD -c ingress --tail=50 | grep "202"

echo -e "\n=== Step 4: Kafka Messages ==="
KAFKA_POD=$(oc get pods -n kafka -l app.kubernetes.io/name=kafka -o jsonpath='{.items[0].metadata.name}')
oc exec -n cost-onprem $KAFKA_POD -- kafka-console-consumer.sh \
  --bootstrap-server localhost:9092 \
  --topic hccm.ros.events \
  --from-beginning \
  --max-messages 1 \
  --timeout-ms 5000 2>/dev/null || echo "No messages (may be consumed already)"

echo -e "\n=== Step 5: Processor Logs ==="
PROCESSOR_POD=$(oc get pods -n cost-onprem -l app.kubernetes.io/component=ros-processor -o jsonpath='{.items[0].metadata.name}')
oc logs -n cost-onprem $PROCESSOR_POD --tail=50 | grep -E "Kruize|experiment"

echo -e "\n=== Step 6: Kruize Experiments ==="
./scripts/query-kruize.sh --experiments | head -20

echo -e "\n=== Verification Complete ==="
```

---

## Troubleshooting Tips

### Issue: Operator not uploading
- Check namespace has `cost_management_optimizations=true` label
- Verify operator has valid JWT token (if auth enabled)
- Check network connectivity to ingress endpoint
- Verify upload cycle configured correctly

### Issue: Ingress rejecting payload
- Check JWT authentication (401 errors)
- Verify X-Rh-Identity header present (with Envoy)
- Check storage (S3/ODF) is accessible
- Review ingress logs for specific error messages

### Issue: Processor not processing
- Check Kafka connectivity
- Verify CSV format matches expected schema
- Check Kruize API is accessible
- Review processor logs for validation errors

### Issue: No Kruize experiments
- Verify processor successfully sent data (check logs)
- Check Kruize API health: `oc get pods -n cost-onprem -l app.kubernetes.io/component=ros-optimization`
- Ensure Kruize database is healthy
- Check Kruize logs for errors

### Issue: No recommendations generated
- Verify experiments exist first
- Check Kruize time thresholds: `MEASUREMENT_DURATION_THRESHOLD_MINUTES` (default 15)
- Ensure sufficient data points collected
- Review Kruize logs for recommendation generation

---

## Related Scripts

- `scripts/query-kruize.sh` - Query Kruize database for experiments and recommendations
- `scripts/install-helm-chart.sh` - Deploy/upgrade ROS Helm chart
- `scripts/setup-cost-mgmt-tls.sh` - Configure Cost Management Operator

