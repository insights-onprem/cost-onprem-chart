#!/bin/bash
# =============================================================================
# Cost Management Infrastructure Bootstrap Script
# =============================================================================
# This script deploys and initializes the infrastructure components for
# Cost Management (PostgreSQL) and runs database migrations.
#
# Usage:
#   ./bootstrap-infrastructure.sh --namespace <namespace> [options]
#
# Options:
#   --namespace <name>      Kubernetes namespace (required)
#   --release-name <name>   Helm release name (default: cost-mgmt-infra)
#   --skip-deploy          Skip infrastructure deployment
#   --skip-migrations      Skip waiting for migrations
#   --help                 Show this help message
# =============================================================================

set -e
set -o pipefail

# Colors for output
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
NC='\033[0m' # No Color

# Logging functions
log_info() {
    echo -e "${BLUE}[INFO]${NC} $1"
}

log_success() {
    echo -e "${GREEN}[SUCCESS]${NC} $1"
}

log_warning() {
    echo -e "${YELLOW}[WARNING]${NC} $1"
}

log_error() {
    echo -e "${RED}[ERROR]${NC} $1"
}

# Default values
NAMESPACE=""
RELEASE_NAME="cost-mgmt-infra"
SKIP_DEPLOY=false
SKIP_MIGRATIONS=false
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
INFRA_CHART_DIR="$(dirname "$SCRIPT_DIR")/cost-management-infrastructure"

# Parse arguments
while [[ $# -gt 0 ]]; do
    case $1 in
        --namespace)
            NAMESPACE="$2"
            shift 2
            ;;
        --release-name)
            RELEASE_NAME="$2"
            shift 2
            ;;
        --skip-deploy)
            SKIP_DEPLOY=true
            shift
            ;;
        --skip-migrations)
            SKIP_MIGRATIONS=true
            shift
            ;;
        --help)
            head -n 20 "$0" | tail -n +3 | sed 's/^# //;s/^#//'
            exit 0
            ;;
        *)
            log_error "Unknown option: $1"
            exit 1
            ;;
    esac
done

# Validate required parameters
if [ -z "$NAMESPACE" ]; then
    log_error "Namespace is required. Use --namespace <name>"
    exit 1
fi

log_info "Cost Management Infrastructure Bootstrap"
log_info "========================================"
log_info "Namespace: $NAMESPACE"
log_info "Release: $RELEASE_NAME"
log_info ""

# =============================================================================
# Step 1: Deploy Infrastructure Chart
# =============================================================================
if [ "$SKIP_DEPLOY" = false ]; then
    log_info "Step 1: Deploying infrastructure chart..."

    if [ ! -d "$INFRA_CHART_DIR" ]; then
        log_error "Infrastructure chart not found at: $INFRA_CHART_DIR"
        exit 1
    fi

    # Deploy infrastructure chart
    log_info "Deploying PostgreSQL..."
    helm upgrade --install "$RELEASE_NAME" "$INFRA_CHART_DIR" \
        --namespace "$NAMESPACE" \
        --create-namespace \
        --wait \
        --timeout=10m

    log_success "Infrastructure deployed"
else
    log_warning "Skipping infrastructure deployment"
fi

# =============================================================================
# Step 2: Wait for PostgreSQL to be ready
# =============================================================================
log_info "Step 2: Waiting for PostgreSQL to be ready..."

POD_NAME="postgres-0"
MAX_ATTEMPTS=60
ATTEMPT=0

while [ $ATTEMPT -lt $MAX_ATTEMPTS ]; do
    if kubectl get pod "$POD_NAME" -n "$NAMESPACE" >/dev/null 2>&1; then
        POD_STATUS=$(kubectl get pod "$POD_NAME" -n "$NAMESPACE" -o jsonpath='{.status.phase}')
        if [ "$POD_STATUS" = "Running" ]; then
            # Test database connectivity
            if kubectl exec "$POD_NAME" -n "$NAMESPACE" -- psql -U postgres -d postgres -c "SELECT 1" >/dev/null 2>&1; then
                log_success "PostgreSQL is ready"
                break
            fi
        fi
    fi

    ATTEMPT=$((ATTEMPT + 1))
    log_info "Waiting for PostgreSQL... (attempt $ATTEMPT/$MAX_ATTEMPTS)"
    sleep 5
done

if [ $ATTEMPT -eq $MAX_ATTEMPTS ]; then
    log_error "PostgreSQL did not become ready in time"
    exit 1
fi

# =============================================================================
# Step 3: Initialize Database
# =============================================================================
log_info "Step 3: Initializing database..."

# Get database credentials from secret
SECRET_NAME="postgres-credentials"
KOKU_DB_NAME=$(kubectl get secret "$SECRET_NAME" -n "$NAMESPACE" -o jsonpath='{.data.database}' | base64 -d)
KOKU_DB_USER=$(kubectl get secret "$SECRET_NAME" -n "$NAMESPACE" -o jsonpath='{.data.username}' | base64 -d)
KOKU_DB_PASSWORD=$(kubectl get secret "$SECRET_NAME" -n "$NAMESPACE" -o jsonpath='{.data.password}' | base64 -d)

# Ensure koku user password is set correctly
log_info "Setting password for database user: $KOKU_DB_USER..."
kubectl exec "$POD_NAME" -n "$NAMESPACE" -- \
    psql -U postgres -d postgres -c "ALTER USER $KOKU_DB_USER WITH PASSWORD '$KOKU_DB_PASSWORD';" 2>&1 || {
    log_error "Failed to set password for user $KOKU_DB_USER"
    exit 1
}
log_success "Password set for user: $KOKU_DB_USER"

log_info "Creating pg_stat_statements extension on database: $KOKU_DB_NAME..."
kubectl exec "$POD_NAME" -n "$NAMESPACE" -- \
    psql -U postgres -d "$KOKU_DB_NAME" -c "CREATE EXTENSION IF NOT EXISTS pg_stat_statements;" 2>&1 || \
    log_warning "Extension may already exist"

log_info "Creating hive role..."
ROLE_EXISTS=$(kubectl exec "$POD_NAME" -n "$NAMESPACE" -- \
    psql -U postgres -d postgres -tAc "SELECT 1 FROM pg_roles WHERE rolname='hive';" | tr -d '[:space:]')

if [ "$ROLE_EXISTS" = "1" ]; then
    log_info "Hive role already exists"
else
    kubectl exec "$POD_NAME" -n "$NAMESPACE" -- \
        psql -U postgres -d postgres -c "CREATE ROLE hive WITH LOGIN PASSWORD 'hive';" 2>&1 | grep -q "CREATE ROLE\|already exists" || {
        log_error "Failed to create hive role"
        exit 1
    }
    log_success "Hive role created"
fi

# Create hive database
log_info "Creating hive database..."
DB_EXISTS=$(kubectl exec "$POD_NAME" -n "$NAMESPACE" -- \
    psql -U postgres -d postgres -tAc "SELECT 1 FROM pg_database WHERE datname='hive';" | tr -d '[:space:]')

if [ "$DB_EXISTS" = "1" ]; then
    log_info "Hive database already exists"
else
    kubectl exec "$POD_NAME" -n "$NAMESPACE" -- \
        psql -U postgres -d postgres -c "CREATE DATABASE hive OWNER hive;" 2>&1 | grep -q "CREATE DATABASE\|already exists" || {
        log_error "Failed to create hive database"
        exit 1
    }
    log_success "Hive database created"
fi

log_success "Database initialization complete"

# =============================================================================
# Step 4: Database Migrations
# =============================================================================
# NOTE: Database migrations are handled by the Helm chart via a post-install hook.
# The migration Job template is defined in:
#   cost-management-infrastructure/templates/migration-job.yaml
# The migration image is configured in:
#   cost-management-infrastructure/values.yaml (migration.image)
#
# If you need to run migrations manually, you can:
#   kubectl create job manual-migration --from=job/cost-mgmt-infra-migration -n cost-mgmt

if [ "$SKIP_MIGRATIONS" = false ]; then
    log_info "Step 4: Waiting for migrations to complete..."
    log_info "Migrations are handled by Helm post-install hook"

    # Wait for migration job to appear, complete, and validate database
    TIMEOUT=300
    ELAPSED=0
    MIGRATIONS_COMPLETED=false

    while [ $ELAPSED -lt $TIMEOUT ]; do
        # Check if migration job succeeded (before it's deleted by hook policy)
        JOB_STATUS=$(kubectl get jobs -n "$NAMESPACE" -l "app.kubernetes.io/component=migration" -o jsonpath='{.items[0].status.succeeded}' 2>/dev/null || echo "")
        if [ "$JOB_STATUS" = "1" ]; then
            log_success "Migrations completed successfully (job succeeded)"
            MIGRATIONS_COMPLETED=true
            break
        fi

        # Check if job failed
        FAILED=$(kubectl get jobs -n "$NAMESPACE" -l "app.kubernetes.io/component=migration" -o jsonpath='{.items[0].status.failed}' 2>/dev/null || echo "0")
        if [ "$FAILED" != "0" ]; then
            log_error "Migration job failed"
            kubectl logs -n "$NAMESPACE" -l "app.kubernetes.io/component=migration" --tail=50
            exit 1
        fi

        # Check if job no longer exists (deleted by hook-succeeded policy)
        # In this case, validate by checking the database directly
        JOB_EXISTS=$(kubectl get jobs -n "$NAMESPACE" -l "app.kubernetes.io/component=migration" --no-headers 2>/dev/null | wc -l | tr -d '[:space:]')
        if [ "$JOB_EXISTS" = "0" ] && [ $ELAPSED -gt 10 ]; then
            # Job was deleted, check database to confirm migrations
            log_info "Migration job deleted (hook-succeeded), validating database..."
            MIGRATION_COUNT=$(kubectl exec -n "$NAMESPACE" "$POD_NAME" -- psql -U "$KOKU_DB_USER" -d "$KOKU_DB_NAME" -tAc "SELECT COUNT(*) FROM django_migrations;" 2>/dev/null | tr -d '[:space:]' || echo "0")
            if [ "$MIGRATION_COUNT" -gt "0" ] 2>/dev/null; then
                log_success "Migrations validated (found $MIGRATION_COUNT migrations in database)"
                MIGRATIONS_COMPLETED=true
                break
            fi
        fi

        sleep 5
        ELAPSED=$((ELAPSED + 5))
        if [ $((ELAPSED % 30)) -eq 0 ]; then
            log_info "Still waiting for migrations... ($ELAPSED/${TIMEOUT}s)"
        fi
    done

    if [ "$MIGRATIONS_COMPLETED" = false ]; then
        log_error "Timeout waiting for migrations"
        kubectl get jobs -n "$NAMESPACE" -l "app.kubernetes.io/component=migration" 2>/dev/null || log_warning "No migration jobs found"
        exit 1
    fi
else
    log_warning "Skipping migrations (--skip-migrations)"
fi

# All migration logic is now handled by the Helm chart
# See: cost-management-infrastructure/templates/migration-job.yaml

# =============================================================================
# Step 5: Validate Setup
# =============================================================================
log_info "Step 5: Validating setup..."

# Check PostgreSQL is accessible and migrations completed
if kubectl exec "$POD_NAME" -n "$NAMESPACE" -- psql -U postgres -d "$KOKU_DB_NAME" -c "SELECT COUNT(*) FROM django_migrations;" >/dev/null 2>&1; then
    MIGRATION_COUNT=$(kubectl exec "$POD_NAME" -n "$NAMESPACE" -- psql -U postgres -d "$KOKU_DB_NAME" -tAc "SELECT COUNT(*) FROM django_migrations;")
    log_success "Database is accessible and migrations table exists ($MIGRATION_COUNT migrations applied)"
else
    log_warning "Could not verify migrations table"
fi

# Print summary
log_success "========================================"
log_success "Infrastructure Bootstrap Complete!"
log_success "========================================"
