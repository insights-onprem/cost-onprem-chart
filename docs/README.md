# Cost Management On-Premise Helm Chart Documentation

Welcome to the Resource Optimization Service (ROS) for OpenShift Container Platform documentation. This directory contains comprehensive guides for installing, configuring, and operating the Cost Management On-Premise Helm chart.

## 📂 Documentation Structure

Documentation is now organized into four categories for easier navigation:

```
docs/
├── architecture/     # System design, components, data flows
├── operations/       # Installation, configuration, troubleshooting
├── api/             # Authentication, authorization, API guides
├── development/     # Testing, validation, development resources
└── README.md        # This file - documentation index and navigation
```

## 📚 Documentation Index

Browse documentation by category:

### 🏗️ Architecture
System design, component interactions, and technical architecture.

| Document | Purpose |
|----------|---------|
| **[Platform Guide](architecture/platform-guide.md)** | Overview of the ROS platform architecture and components |
| **[Helm Templates Reference](architecture/helm-templates-reference.md)** | Documentation of all Helm chart templates and resources |
| **[Sources API Production Flow](architecture/sources-api-production-flow.md)** | Provider creation flow using Sources API and Kafka |
| **[External Keycloak Scenario](architecture/external-keycloak-scenario.md)** | Analysis and architecture for using external Keycloak |

### 🚀 Operations
Deployment, configuration, maintenance, and troubleshooting.

#### Installation & Setup
| Document | Purpose |
|----------|---------|
| **[Quickstart](operations/quickstart.md)** | Fast-track guide to get Cost Management On-Premise running quickly |
| **[Installation Guide](operations/installation.md)** | Comprehensive installation instructions for Cost Management On-Premise |
| **[Cost Management Installation](operations/cost-management-installation.md)** | Detailed Cost Management component installation |

#### Cost Management
| Document | Purpose |
|----------|---------|
| **[Cost Management Concepts](operations/cost-management-concepts.md)** | Core concepts, cost models, and cost calculation methods |
| **[Data Sources and Providers](operations/cost-management-data-sources.md)** | Configuring and managing OpenShift providers and data collection |

#### Configuration
| Document | Purpose |
|----------|---------|
| **[Configuration Reference](operations/configuration.md)** | Complete reference of all Helm values and configuration options |
| **[Resource Requirements](operations/resource-requirements.md)** | Hardware and resource sizing guidance |
| **[Worker Deployment Scenarios](operations/worker-deployment-scenarios.md)** | Different worker deployment configurations |
| **[TLS Certificate Options](operations/tls-certificate-options.md)** | Guide to different TLS certificate configuration scenarios |
| **[Cost Management Operator TLS Config](operations/cost-management-operator-tls-config-setup.md)** | TLS configuration for the Cost Management Metrics Operator |
| **[Force Operator Upload](operations/force-operator-upload.md)** | Guide for manually triggering metrics upload for testing |
| **[Upload Verification Checklist](operations/cost-management-operator-upload-verification-checklist.md)** | Step-by-step checklist to verify operator metrics upload |
| **[Troubleshooting Guide](operations/troubleshooting.md)** | Common issues and their solutions |

### 🔐 API & Authentication
Authentication, authorization, and API integration guides.

| Document | Purpose |
|----------|---------|
| **[Keycloak JWT Authentication Setup](api/keycloak-jwt-authentication-setup.md)** | Complete guide for setting up JWT authentication with Keycloak |
| **[Native JWT Authentication](api/native-jwt-authentication.md)** | Detailed explanation of JWT authentication architecture |
| **[UI OAuth Authentication](api/ui-oauth-authentication.md)** | Complete guide for UI OAuth authentication with Keycloak OAuth proxy |

### 🧪 Development & Testing
Testing guides, validation procedures, and development resources.

| Document | Purpose |
|----------|---------|
| **[OCP Dev Setup with S4](development/ocp-dev-setup-s4.md)** | Set up a dev environment on OCP using S4 instead of ODF |
| **[UI OAuth Testing](development/ui-oauth-testing.md)** | Guide for testing UI OAuth flow with Keycloak |

---

## 🚀 Quick Navigation by Use Case

### "I'm new to Cost Management On-Premise"
1. Start with **[Quickstart](operations/quickstart.md)** for a rapid deployment
2. Read **[Platform Guide](architecture/platform-guide.md)** to understand the architecture
3. Learn **[Cost Management Concepts](operations/cost-management-concepts.md)** to understand cost models and attribution
4. Review **[Configuration Reference](operations/configuration.md)** for customization options

### "I'm deploying to production"
1. Follow **[Installation Guide](operations/installation.md)** for detailed setup
2. Configure authentication using **[Keycloak JWT Authentication Setup](api/keycloak-jwt-authentication-setup.md)**
3. Set up TLS using **[TLS Certificate Options](operations/tls-certificate-options.md)**
4. Configure providers via **[Data Sources and Providers](operations/cost-management-data-sources.md)**
5. Understand the provider creation flow via **[Sources API Production Flow](architecture/sources-api-production-flow.md)**
6. Review **[Configuration Reference](operations/configuration.md)** for production settings

### "I'm setting up authentication"
1. Read **[Native JWT Authentication](api/native-jwt-authentication.md)** to understand the architecture
2. Follow **[Keycloak JWT Authentication Setup](api/keycloak-jwt-authentication-setup.md)** for step-by-step instructions
3. For UI authentication, see **[UI OAuth Authentication](api/ui-oauth-authentication.md)** (OpenShift only)
4. Use **[TLS Certificate Options](operations/tls-certificate-options.md)** for TLS configuration
5. Reference **[External Keycloak Scenario](architecture/external-keycloak-scenario.md)** if using external Keycloak

### "I'm setting up the Cost Management Operator"
1. Follow **[Cost Management Operator TLS Config Setup](operations/cost-management-operator-tls-config-setup.md)**
2. Use **[Force Operator Upload](operations/force-operator-upload.md)** to test the upload pipeline
3. Verify with **[Upload Verification Checklist](operations/cost-management-operator-upload-verification-checklist.md)**

### "Something isn't working"
1. Check **[Troubleshooting Guide](operations/troubleshooting.md)** for common issues
2. Use **[Upload Verification Checklist](operations/cost-management-operator-upload-verification-checklist.md)** to verify operator uploads
3. Review logs and debugging steps in relevant setup guides

### "I'm setting up a development environment"
1. Follow **[OCP Dev Setup with S4](development/ocp-dev-setup-s4.md)** to deploy with S4 (no ODF required)
2. Read **[Installation Guide](operations/installation.md)** for the full install options
3. Set up authentication using **[Keycloak JWT Authentication Setup](api/keycloak-jwt-authentication-setup.md)**

### "I need to understand the codebase"
1. Read **[Helm Templates Reference](architecture/helm-templates-reference.md)** for resource definitions
2. Review **[Platform Guide](architecture/platform-guide.md)** for architecture overview
3. Check **[Configuration Reference](operations/configuration.md)** for available options

---

## 📖 Detailed Document Descriptions

For detailed information about each document's purpose, use cases, and key topics, refer to the categorized sections below or explore the documentation directories directly:

- **[architecture/](architecture/)** - System architecture, design decisions, and technical deep-dives
- **[operations/](operations/)** - Installation, configuration, deployment, and operational guides
- **[api/](api/)** - Authentication, authorization, and API integration documentation
- **[development/](development/)** - Testing, validation, and development resources

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

**Last Updated:** 2026-01-29
**Helm Chart Version:** 0.1.5+
**Documentation Structure:** Reorganized into architecture/, operations/, api/, and development/ categories (TASK-014)

