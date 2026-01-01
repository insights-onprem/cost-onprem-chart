{{/*
=============================================================================
Reusable Security Context Templates
=============================================================================
Security contexts are defined at two levels in Kubernetes:
- Pod-level: applies defaults to all containers (runAsNonRoot, fsGroup, seccompProfile)
- Container-level: applies to specific containers (allowPrivilegeEscalation, capabilities)

This file consolidates all security context helpers for consistent usage across the chart.
*/}}

{{/*
=============================================================================
Pod-Level Security Context Helpers
=============================================================================
These helpers should be used at spec.template.spec.securityContext level.
Valid fields: runAsNonRoot, runAsUser, runAsGroup, fsGroup, seccompProfile
*/}}

{{/*
Pod-level security context - minimal non-root configuration
Usage: {{ include "cost-onprem.securityContext.pod" . | nindent 8 }}
*/}}
{{- define "cost-onprem.securityContext.pod" -}}
runAsNonRoot: true
{{- end -}}

{{/*
Pod-level security context - non-root with seccomp profile
Usage: {{ include "cost-onprem.securityContext.pod.nonRoot" . | nindent 8 }}
*/}}
{{- define "cost-onprem.securityContext.pod.nonRoot" -}}
runAsNonRoot: true
seccompProfile:
  type: RuntimeDefault
{{- end -}}

{{/*
=============================================================================
Container-Level Security Context Helpers
=============================================================================
These helpers should be used at spec.template.spec.containers[].securityContext level.
Valid fields: allowPrivilegeEscalation, capabilities, readOnlyRootFilesystem, runAsUser, etc.
*/}}

{{/*
Container-level security context - standard hardened configuration
Usage: {{ include "cost-onprem.securityContext.container" . | nindent 10 }}
*/}}
{{- define "cost-onprem.securityContext.container" -}}
allowPrivilegeEscalation: false
capabilities:
  drop:
  - ALL
{{- end -}}

{{/*
Container-level security context - standard non-root with full hardening
Usage: {{ include "cost-onprem.securityContext.nonRoot" . | nindent 6 }}
*/}}
{{- define "cost-onprem.securityContext.nonRoot" -}}
runAsNonRoot: true
{{- if eq (include "cost-onprem.platform.isOpenShift" .) "false" }}
runAsUser: 1000
{{- end }}
allowPrivilegeEscalation: false
capabilities:
  drop:
    - ALL
seccompProfile:
  type: RuntimeDefault
{{- end -}}

{{/*
Container-level security context with read-only root filesystem
Usage: {{ include "cost-onprem.securityContext.readOnlyRoot" . | nindent 6 }}
*/}}
{{- define "cost-onprem.securityContext.readOnlyRoot" -}}
runAsNonRoot: true
{{- if eq (include "cost-onprem.platform.isOpenShift" .) "false" }}
runAsUser: 1000
{{- end }}
allowPrivilegeEscalation: false
readOnlyRootFilesystem: true
capabilities:
  drop:
    - ALL
seccompProfile:
  type: RuntimeDefault
{{- end -}}

{{/*
Container-level security context for privileged operations (use sparingly)
Usage: {{ include "cost-onprem.securityContext.privileged" . | nindent 6 }}
*/}}
{{- define "cost-onprem.securityContext.privileged" -}}
allowPrivilegeEscalation: true
capabilities:
  add:
    - NET_ADMIN
    - NET_RAW
seccompProfile:
  type: RuntimeDefault
{{- end -}}
