# Cost Management Concepts

Comprehensive guide to cost management concepts, cost models, and data aggregation in Cost Management On-Premise.

## Table of Contents

- [Overview](#overview)
- [Core Concepts](#core-concepts)
- [Cost Models](#cost-models)
- [Data Collection and Processing](#data-collection-and-processing)
- [Cost Calculation](#cost-calculation)
- [Resource Optimization](#resource-optimization)

---

## Overview

Cost Management On-Premise provides cost tracking and resource optimization for OpenShift Container Platform (OCP) workloads. It collects usage metrics, applies cost models, and generates recommendations to help organizations understand and optimize their infrastructure spending.

### Key Capabilities

| Capability | Description |
|------------|-------------|
| **Usage Tracking** | Collects CPU, memory, and storage metrics from OpenShift clusters |
| **Cost Attribution** | Allocates infrastructure costs to projects, namespaces, and applications |
| **Cost Models** | Flexible cost models for CPU, memory, storage, and network resources |
| **Resource Optimization** | AI-powered recommendations via Kruize for right-sizing workloads |
| **Reporting** | Daily, weekly, and monthly cost reports with aggregation by project, namespace, or label |

---

## Core Concepts

### Providers

A **provider** represents a data source for cost and usage information. In Cost Management On-Premise, providers are OpenShift clusters configured to send metrics.

**Provider Types:**
- **OpenShift Container Platform (OCP)** - Primary provider type for on-premise deployments
- Each cluster is configured as a separate provider via the Sources API

**Provider Configuration:**
- Created via Sources API (production flow)
- Authenticated via cluster identifier and credentials
- Metrics uploaded by Cost Management Metrics Operator

### Sources

A **source** is the configuration entry that connects a provider to Cost Management. The source contains:
- Provider type (OCP)
- Authentication credentials
- Cluster identifier
- Endpoint configuration

**Source Creation Flow:**
```
User/API → Sources API → Kafka → Koku Sources Listener → Database
```

See [Sources API Production Flow](../architecture/sources-api-production-flow.md) for details.

### Usage Metrics

Cost Management collects the following usage metrics:

| Metric | Description | Unit |
|--------|-------------|------|
| **CPU Request** | Requested CPU cores for pods | Core-hours |
| **CPU Usage** | Actual CPU consumption | Core-hours |
| **CPU Limit** | Maximum CPU allocation | Core-hours |
| **Memory Request** | Requested memory for pods | GB-hours |
| **Memory Usage** | Actual memory consumption | GB-hours |
| **Memory Limit** | Maximum memory allocation | GB-hours |
| **Storage** | Persistent volume claims | GB-month |
| **Node Count** | Number of nodes in cluster | Node-hours |

### Cost Attribution

Costs are attributed to workloads based on:

1. **Resource Requests** (primary) - What the pod requested from the scheduler
2. **Resource Usage** (actual) - What the pod actually consumed
3. **Resource Limits** (maximum) - Maximum resources pod could use

**Attribution Hierarchy:**
```
Cluster → Namespace → Pod → Container
```

**Attribution Dimensions:**
- Project/Namespace
- Application labels
- Environment labels (dev, staging, prod)
- Cost center labels
- Custom labels

---

## Cost Models

Cost models define how infrastructure costs are calculated and allocated to workloads.

### Default Cost Model

The default cost model uses **distributed costs** based on resource requests:

| Resource | Default Rate | Unit |
|----------|--------------|------|
| CPU | $0.50 | per core-hour |
| Memory | $0.25 | per GB-hour |
| Storage (PVC) | $0.10 | per GB-month |
| Node | $1.00 | per node-hour |

**Formula:**
```
Total Cost = (CPU Hours × CPU Rate) + (Memory GB-Hours × Memory Rate) + 
             (Storage GB-Month × Storage Rate) + (Node Hours × Node Rate)
```

### Custom Cost Models

Cost models can be customized via the API to reflect actual infrastructure costs.

**Configuration:**
- CPU cost per core-hour
- Memory cost per GB-hour
- Storage cost per GB-month
- Node cost per hour
- Markup percentage (for overhead)

**Example Configuration:**
```json
{
  "name": "Production Cost Model",
  "description": "Based on actual infrastructure costs",
  "source_type": "OCP",
  "rates": {
    "cpu_core_per_hour": 0.75,
    "memory_gb_per_hour": 0.35,
    "storage_gb_per_month": 0.15,
    "node_per_hour": 1.50
  },
  "markup": {
    "value": 10,
    "unit": "percent"
  }
}
```

### Cost Allocation Methods

**1. Request-Based (Default)**
- Allocates costs based on resource requests
- Most predictable and fair for chargeback
- Encourages right-sizing requests

**2. Usage-Based**
- Allocates costs based on actual resource consumption
- Reflects true resource usage
- Can be unpredictable for budgeting

**3. Limit-Based**
- Allocates costs based on resource limits
- Conservative approach
- May over-allocate costs

---

## Data Collection and Processing

### Data Flow

```
Cost Management Metrics Operator → Ingress API (JWT) → S3 Storage → 
Kafka Topic → MASU Processor → PostgreSQL → Reports
```

### Collection Schedule

| Component | Schedule | Purpose |
|-----------|----------|---------|
| **Cost Management Metrics Operator** | Every 15 minutes | Collects current usage metrics from Prometheus |
| **MASU Processor** | Triggered by Kafka | Processes uploaded data files |
| **Summary Generation** | Daily at midnight | Generates daily summary reports |
| **Monthly Rollup** | 1st of month | Generates monthly aggregates |

### Data Retention

| Data Type | Retention Period |
|-----------|------------------|
| **Raw Usage Data** | 90 days |
| **Daily Summaries** | 1 year |
| **Monthly Summaries** | 3 years |
| **Cost Models** | Indefinite |

---

## Cost Calculation

### Daily Cost Calculation

**Step 1: Collect Usage Data**
```sql
SELECT 
  namespace,
  pod,
  SUM(cpu_request_hours) as total_cpu_hours,
  SUM(memory_request_gb_hours) as total_memory_gb_hours
FROM raw_usage_data
WHERE usage_date = CURRENT_DATE
GROUP BY namespace, pod;
```

**Step 2: Apply Cost Model**
```sql
SELECT 
  namespace,
  pod,
  (total_cpu_hours * 0.50) as cpu_cost,
  (total_memory_gb_hours * 0.25) as memory_cost,
  (total_cpu_hours * 0.50 + total_memory_gb_hours * 0.25) as total_cost
FROM usage_summary;
```

**Step 3: Aggregate by Namespace**
```sql
SELECT 
  namespace,
  SUM(total_cost) as namespace_daily_cost
FROM pod_costs
GROUP BY namespace;
```

### Cost Allocation Example

**Scenario:**
- Namespace: `frontend-prod`
- 3 pods running 24 hours
- Pod requests: 500m CPU, 1GB memory each

**Calculation:**
```
CPU Cost = (0.5 cores × 24 hours × 3 pods) × $0.50/core-hour = $18.00
Memory Cost = (1 GB × 24 hours × 3 pods) × $0.25/GB-hour = $18.00
Total Daily Cost = $36.00
Monthly Cost (30 days) = $1,080.00
```

---

## Resource Optimization

### Kruize Integration

Cost Management integrates with Kruize for AI-powered resource optimization recommendations.

**Recommendation Types:**

| Type | Focus | Optimization |
|------|-------|--------------|
| **Cost** | Minimize spend | Reduce CPU/memory requests to match usage |
| **Performance** | Maximize performance | Increase resources to reduce throttling |
| **Short-term** | 1-7 days | Quick wins for immediate savings |
| **Medium-term** | 7-30 days | Balanced recommendations |
| **Long-term** | 30+ days | Strategic optimizations |

### Optimization Workflow

```
1. Upload metrics to Kruize (via ROS Processor)
2. Kruize analyzes usage patterns (15-minute intervals)
3. Generate recommendations (CPU/memory right-sizing)
4. Apply recommendations to deployments
5. Monitor impact on costs and performance
```

### Recommendation Examples

**Example 1: Over-provisioned Pod**
```yaml
Current:
  CPU Request: 2000m
  CPU Usage (avg): 400m (20% utilization)
  Memory Request: 4Gi
  Memory Usage (avg): 1Gi (25% utilization)

Recommendation:
  CPU Request: 500m (save $0.75/hour)
  Memory Request: 1.5Gi (save $0.625/hour)
  Estimated Monthly Savings: $972
```

**Example 2: Under-provisioned Pod**
```yaml
Current:
  CPU Request: 500m
  CPU Usage (avg): 480m (96% utilization, frequent throttling)
  
Recommendation:
  CPU Request: 750m (improve performance)
  Cost Impact: +$0.125/hour (+$90/month)
  Benefit: Eliminate CPU throttling, improve response time
```

---

## Best Practices

### Cost Management

1. **Set Accurate Resource Requests**
   - Base on actual usage plus headroom (10-20%)
   - Review and adjust quarterly
   - Use Kruize recommendations

2. **Use Labels for Attribution**
   - `cost-center`: Business unit or department
   - `environment`: dev, staging, prod
   - `application`: Application name
   - `team`: Team or project owner

3. **Implement Cost Models**
   - Reflect actual infrastructure costs
   - Update quarterly to match hardware refresh cycles
   - Include markup for overhead (networking, storage, management)

4. **Monitor Trends**
   - Review cost trends weekly
   - Identify anomalies (sudden spikes)
   - Correlate with deployments or incidents

### Resource Optimization

1. **Regular Reviews**
   - Review Kruize recommendations weekly
   - Test recommendations in dev/staging first
   - Apply to production during maintenance windows

2. **Right-Sizing Strategy**
   - Start with obviously over-provisioned workloads
   - Focus on high-cost namespaces first
   - Monitor performance after changes

3. **Continuous Improvement**
   - Set up alerts for cost anomalies
   - Automate recommendation application (GitOps)
   - Track savings from optimizations

---

## Related Documentation

- [Configuration Reference](../operations/configuration.md) - Cost model configuration
- [Sources API Production Flow](../architecture/sources-api-production-flow.md) - Provider setup
- [Cost Management Installation](../operations/cost-management-installation.md) - Installation guide
- [Upload Verification Checklist](../operations/cost-management-operator-upload-verification-checklist.md) - Verify data collection

---

**Last Updated:** 2026-01-29
**Applies to:** Cost Management On-Premise 0.1.5+
