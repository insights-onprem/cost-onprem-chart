# Cost Management On-Premise Helm Chart Documentation

Welcome to the Resource Optimization Service (ROS) for OpenShift Container Platform documentation. This directory contains comprehensive guides for installing, configuring, and operating the Cost Management On-Premise Helm chart.

## 📚 Documentation Index

### Getting Started

| Document | Purpose | When to Use |
|----------|---------|-------------|
| **[Quickstart](quickstart.md)** | Fast-track guide to get Cost Management On-Premise running quickly | First-time users who want to evaluate Cost Management On-Premise with minimal configuration |
| **[Platform Guide](platform-guide.md)** | Overview of the ROS platform architecture and components | Understanding the overall system design and component interactions |

### Installation & Deployment

| Document | Purpose | When to Use |
|----------|---------|-------------|
| **[Installation Guide](installation.md)** | Comprehensive installation instructions for Cost Management On-Premise | Production deployments requiring detailed configuration |
| **[Sources API Production Flow](sources-api-production-flow.md)** | Provider creation flow using Koku Sources API and Kafka | Understanding and setting up the production-like data ingestion pipeline with Koku as the source of truth |

### Authentication & Security

| Document | Purpose | When to Use |
|----------|---------|-------------|
| **[Keycloak JWT Authentication Setup](keycloak-jwt-authentication-setup.md)** | Complete guide for setting up JWT authentication with Keycloak | Configuring authentication for production environments |
| **[Native JWT Authentication](native-jwt-authentication.md)** | Detailed explanation of JWT authentication architecture | Understanding how JWT authentication works in Cost Management On-Premise |
| **[UI OAuth Authentication](ui-oauth-authentication.md)** | Complete guide for UI OAuth authentication with Keycloak OAuth proxy | Understanding and troubleshooting UI authentication on OpenShift |
| **[TLS Certificate Options](tls-certificate-options.md)** | Guide to different TLS certificate configuration scenarios | Configuring TLS for Keycloak JWKS endpoint validation |
| **[External Keycloak Scenario](external-keycloak-scenario.md)** | Analysis and architecture for using external Keycloak | Connecting Cost Management On-Premise to Keycloak outside the cluster |
| **[Cost Management Operator TLS Config Setup](cost-management-operator-tls-config-setup.md)** | TLS configuration for the Cost Management Metrics Operator | Setting up secure communication between operator and ingress |

### Configuration

| Document | Purpose | When to Use |
|----------|---------|-------------|
| **[Configuration Reference](configuration.md)** | Complete reference of all Helm values and configuration options | Customizing your Cost Management On-Premise deployment |
| **[Helm Templates Reference](helm-templates-reference.md)** | Documentation of all Helm chart templates and resources | Understanding the Kubernetes resources created by the chart |

### Testing, Validation & Troubleshooting

| Document | Purpose | When to Use |
|----------|---------|-------------|
| **[UI OAuth Testing](ui-oauth-testing.md)** | Guide for testing UI OAuth flow with Keycloak | Validating UI authentication after deployment |
| **[Force Operator Upload](force-operator-upload.md)** | Guide for manually triggering metrics upload for testing | Testing end-to-end pipeline or validating changes |
| **[Upload Verification Checklist](cost-management-operator-upload-verification-checklist.md)** | Step-by-step checklist to verify operator metrics upload | Validating that the operator successfully uploaded metrics to Kruize |
| **[Troubleshooting Guide](troubleshooting.md)** | Common issues and their solutions | Diagnosing and resolving problems with your deployment |

---

## 🚀 Quick Navigation by Use Case

### "I'm new to Cost Management On-Premise"
1. Start with **[Quickstart](quickstart.md)** for a rapid deployment
2. Read **[Platform Guide](platform-guide.md)** to understand the architecture
3. Review **[Configuration Reference](configuration.md)** for customization options

### "I'm deploying to production"
1. Follow **[Installation Guide](installation.md)** for detailed setup
2. Configure authentication using **[Keycloak JWT Authentication Setup](keycloak-jwt-authentication-setup.md)**
3. Set up TLS using **[TLS Certificate Options](tls-certificate-options.md)**
4. Review **[Configuration Reference](configuration.md)** for production settings
5. Understand provider creation via **[Sources API Production Flow](sources-api-production-flow.md)** (now integrated in Koku API)

### "I'm setting up authentication"
1. Read **[Native JWT Authentication](native-jwt-authentication.md)** to understand the architecture
2. Follow **[Keycloak JWT Authentication Setup](keycloak-jwt-authentication-setup.md)** for step-by-step instructions
3. For UI authentication, see **[UI OAuth Authentication](ui-oauth-authentication.md)** (OpenShift only)
4. Use **[TLS Certificate Options](tls-certificate-options.md)** for TLS configuration
5. Reference **[External Keycloak Scenario](external-keycloak-scenario.md)** if using external Keycloak

### "I'm setting up the Cost Management Operator"
1. Follow **[Cost Management Operator TLS Config Setup](cost-management-operator-tls-config-setup.md)**
2. Use **[Force Operator Upload](force-operator-upload.md)** to test the upload pipeline
3. Verify with **[Upload Verification Checklist](cost-management-operator-upload-verification-checklist.md)**

### "Something isn't working"
1. Check **[Troubleshooting Guide](troubleshooting.md)** for common issues
2. Use **[Upload Verification Checklist](cost-management-operator-upload-verification-checklist.md)** to verify operator uploads
3. Review logs and debugging steps in relevant setup guides

### "I need to understand the codebase"
1. Read **[Helm Templates Reference](helm-templates-reference.md)** for resource definitions
2. Review **[Platform Guide](platform-guide.md)** for architecture overview
3. Check **[Configuration Reference](configuration.md)** for available options

---

## 📖 Document Details

### Getting Started

#### [Quickstart](quickstart.md)
**Purpose:** Provides a fast-track guide to deploy Cost Management On-Premise with minimal configuration, suitable for evaluation and development environments.

**Use when:**
- You want to quickly evaluate Cost Management On-Premise
- Setting up a development or testing environment
- You need a working deployment before diving into details

**Key topics:**
- Prerequisites
- Quick installation steps
- Basic configuration
- Initial validation

---

#### [Platform Guide](platform-guide.md)
**Purpose:** Comprehensive overview of the ROS platform architecture, components, and how they interact.

**Use when:**
- You need to understand the overall system design
- Planning a production deployment
- Troubleshooting complex issues
- Contributing to the project

**Key topics:**
- System architecture
- Component overview
- Data flow
- Integration points

---

### Installation & Deployment

#### [Installation Guide](installation.md)
**Purpose:** Detailed, step-by-step installation instructions for deploying Cost Management On-Premise to production environments.

**Use when:**
- Deploying to production
- You need detailed configuration options
- Setting up complex environments

**Key topics:**
- Prerequisites and requirements
- Installation steps
- Post-installation configuration
- Validation procedures

---

#### [Sources API Production Flow](sources-api-production-flow.md)
**Purpose:** Comprehensive guide explaining the production-like provider creation flow using Koku Sources API (integrated in Koku), Kafka, and Koku sources listener.

**Use when:**
- Understanding how providers are created via Koku Sources API
- Setting up the data ingestion pipeline
- Debugging source/provider creation issues
- Learning the Kafka event-driven architecture
- Understanding the simplified architecture without sources-api-go

**Key topics:**
- Architecture overview (Koku Sources API → Kafka → Koku Listener)
- Source and provider creation workflow
- Kafka topic configuration
- Authentication setup for Koku Sources API
- CMMO configuration for Koku Sources API
- Troubleshooting provider creation

---

### Authentication & Security

#### [Keycloak JWT Authentication Setup](keycloak-jwt-authentication-setup.md)
**Purpose:** Complete guide for configuring JWT authentication with Keycloak, including both local and external Keycloak scenarios.

**Use when:**
- Setting up JWT authentication for the first time
- Configuring Keycloak for Cost Management On-Premise
- Troubleshooting authentication issues

**Key topics:**
- Keycloak installation and configuration
- Client setup
- Realm configuration
- Service account credentials
- Helm chart configuration
- URL and CA certificate management
- Testing authentication

---

#### [Native JWT Authentication](native-jwt-authentication.md)
**Purpose:** In-depth technical explanation of how JWT authentication works in Cost Management On-Premise, including Envoy configuration and validation.

**Use when:**
- You need to understand the authentication architecture
- Debugging authentication issues
- Customizing authentication behavior
- Contributing authentication-related changes

**Key topics:**
- JWT authentication flow
- Envoy proxy configuration
- JWKS validation
- TLS certificate validation
- Service-to-service authentication
- Network policies

---

#### [UI OAuth Authentication](ui-oauth-authentication.md)
**Purpose:** Complete guide for understanding and troubleshooting UI OAuth authentication using Keycloak OAuth proxy.

**Use when:**
- Setting up or troubleshooting the UI component on OpenShift
- Understanding how UI authentication works with Keycloak OAuth
- Debugging OAuth redirect loops or authentication issues
- Configuring TLS certificates for UI
- Understanding session management and cookie encryption

**Key topics:**
- OAuth proxy architecture and authentication flow
- Keycloak OIDC server integration
- TLS certificate auto-generation via Service CA Operator
- Keycloak client configuration
- Session secret persistence across upgrades
- Troubleshooting common authentication issues
- Security considerations and best practices
- Platform requirements (OpenShift only)

---

#### [TLS Certificate Options](tls-certificate-options.md)
**Purpose:** Explains different TLS certificate configuration options for Keycloak JWKS endpoint validation.

**Use when:**
- Configuring TLS for Keycloak communication
- Choosing between auto-fetch and manual CA certificates
- Using self-signed certificates
- Setting up external Keycloak with custom CAs

**Key topics:**
- Auto-fetch CA certificates (default)
- Manual CA certificate provisioning
- Skip TLS verification (development only)
- System CA bundle only
- Scenario comparison

---

#### [External Keycloak Scenario](external-keycloak-scenario.md)
**Purpose:** Detailed analysis and architecture for connecting Cost Management On-Premise to Keycloak running outside the cluster.

**Use when:**
- Using a shared/external Keycloak instance
- Planning cross-cluster authentication
- Understanding network requirements for external Keycloak

**Key topics:**
- Architecture diagram
- Network flow
- Requirements and prerequisites
- Configuration examples
- Failure modes
- Testing procedures

---

#### [Cost Management Operator TLS Config Setup](cost-management-operator-tls-config-setup.md)
**Purpose:** Instructions for configuring TLS/JWT authentication between the Cost Management Metrics Operator and ingress.

**Use when:**
- Setting up the Cost Management Metrics Operator
- Configuring secure communication with ingress
- Troubleshooting operator upload issues

**Key topics:**
- Operator configuration
- TLS certificate setup
- JWT authentication for operator
- Ingress endpoint configuration

---

### Configuration

#### [Configuration Reference](configuration.md)
**Purpose:** Complete reference documentation for all Helm chart values and configuration options.

**Use when:**
- Customizing your deployment
- Understanding available configuration options
- Troubleshooting configuration issues
- Planning production deployments

**Key topics:**
- Global settings
- Component-specific configuration
- Database settings
- Authentication configuration
- Network policies
- Resource limits
- Monitoring and metrics

---

#### [Helm Templates Reference](helm-templates-reference.md)
**Purpose:** Documentation of all OpenShift resources created by the Helm chart and their relationships.

**Use when:**
- Understanding what resources are deployed
- Debugging deployment issues
- Planning resource requirements
- Contributing to the chart

**Key topics:**
- Deployments and StatefulSets
- Services and Ingress/Routes
- ConfigMaps and Secrets
- Network Policies
- ServiceAccounts and RBAC

---

### Testing, Validation & Troubleshooting

#### [Force Operator Upload](force-operator-upload.md)
**Purpose:** Guide for manually triggering the Cost Management Metrics Operator to package and upload metrics immediately, bypassing normal timers.

**Use when:**
- Testing the end-to-end upload pipeline
- Validating operator configuration changes
- Troubleshooting upload issues
- Demonstrating the system to stakeholders

**Key topics:**
- Using the convenience script
- Manual commands explained
- Verification steps
- Kruize's 15-minute bucket behavior
- Troubleshooting common issues

---

#### [Upload Verification Checklist](cost-management-operator-upload-verification-checklist.md)
**Purpose:** Step-by-step checklist to verify that the Cost Management Metrics Operator successfully uploaded metrics and that Kruize generated recommendations.

**Use when:**
- Validating a new deployment
- Verifying operator functionality after changes
- Troubleshooting the upload pipeline
- Confirming end-to-end data flow

**Key topics:**
- Operator status checks
- Upload log verification
- Ingress reception validation
- Kafka message confirmation
- Processor activity
- Kruize experiments and recommendations

---

#### [Troubleshooting Guide](troubleshooting.md)
**Purpose:** Comprehensive guide to common problems and their solutions, organized by component and symptom.

**Use when:**
- Encountering errors or unexpected behavior
- Services not starting correctly
- Authentication failing
- Network connectivity issues
- Performance problems

**Key topics:**
- Common issues by component
- Diagnostic commands
- Log analysis
- Network troubleshooting
- Database issues
- Authentication problems
- Testing and validation

---

## 🔧 Developer Resources

### Contributing
When contributing to the project, please ensure documentation is updated:
- Update relevant guides when adding features
- Add new guides for significant new functionality
- Keep configuration references up-to-date
- Update this README when adding new documentation

### Documentation Standards
- Use clear, concise language
- Include practical examples
- Provide both "how" and "why" explanations
- Keep troubleshooting sections updated with new issues
- Cross-reference related documents

---

## 📞 Getting Help

If you can't find what you're looking for in these guides:

1. **Check the troubleshooting guide** - Many common issues are documented
2. **Review related guides** - Information may be in a related document
3. **Check the repository** - README.md and inline code comments may help
4. **Open an issue** - If something is unclear or missing, let us know

---

## 📝 Document Status

All documents are maintained and updated regularly. If you find outdated information, please:
1. Check if a newer version exists
2. Open an issue or pull request
3. Contact the maintainers

---

**Last Updated:** 2025-12-16
**Helm Chart Version:** 0.1.5+

