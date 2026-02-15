{{/*
Reusable Init Container Templates
*/}}

{{/*
Wait for Database init container - waits for unified database server
Usage: {{ include "cost-onprem.initContainer.waitForDb" (list . "ros") | nindent 8 }}
Parameters:
  - Root context (.)
  - Database type ("ros", "kruize", "koku") - used for naming only
*/}}
{{- define "cost-onprem.initContainer.waitForDb" -}}
{{- $root := index . 0 -}}
{{- $dbType := index . 1 -}}
- name: wait-for-database
  image: "{{ $root.Values.global.initContainers.waitFor.repository }}:{{ $root.Values.global.initContainers.waitFor.tag }}"
  securityContext:
    {{- include "cost-onprem.securityContext.nonRoot" $root | nindent 4 }}
  command: ['bash', '-c']
  args:
    - |
      echo "Waiting for unified database server at {{ include "cost-onprem.fullname" $root }}-database:{{ $root.Values.database.server.port }}..."
      until timeout 3 bash -c "echo > /dev/tcp/{{ include "cost-onprem.fullname" $root }}-database/{{ $root.Values.database.server.port }}" 2>/dev/null; do
        echo "Database server not ready yet, retrying in 5 seconds..."
        sleep 5
      done
      echo "Database server is ready"
{{- end -}}

{{/*
Wait for Kafka init container
Usage: {{ include "cost-onprem.initContainer.waitForKafka" . | nindent 8 }}
*/}}
{{- define "cost-onprem.initContainer.waitForKafka" -}}
- name: wait-for-kafka
  image: "{{ .Values.global.initContainers.waitFor.repository }}:{{ .Values.global.initContainers.waitFor.tag }}"
  securityContext:
    {{- include "cost-onprem.securityContext.nonRoot" . | nindent 4 }}
  command: ['bash', '-c']
  args:
    - |
      echo "Waiting for Kafka at {{ include "cost-onprem.kafka.host" . }}:{{ include "cost-onprem.kafka.port" . }}..."
      until timeout 3 bash -c "echo > /dev/tcp/{{ include "cost-onprem.kafka.host" . }}/{{ include "cost-onprem.kafka.port" . }}" 2>/dev/null; do
        echo "Kafka not ready yet, retrying in 5 seconds..."
        sleep 5
      done
      echo "Kafka is ready"
{{- end -}}

{{/*
Wait for S3 storage init container
Checks TCP reachability of the configured S3 endpoint before the main container starts.
Usage: {{ include "cost-onprem.initContainer.waitForStorage" . | nindent 8 }}
*/}}
{{- define "cost-onprem.initContainer.waitForStorage" -}}
- name: wait-for-storage
  image: "{{ .Values.global.initContainers.waitFor.repository }}:{{ .Values.global.initContainers.waitFor.tag }}"
  securityContext:
    {{- include "cost-onprem.securityContext.nonRoot" . | nindent 4 }}
  command: ['bash', '-c']
  args:
    - |
      echo "Waiting for S3 endpoint at {{ include "cost-onprem.storage.endpoint" . }}:{{ include "cost-onprem.storage.port" . }}..."
      until timeout 3 bash -c "echo > /dev/tcp/{{ include "cost-onprem.storage.endpoint" . }}/{{ include "cost-onprem.storage.port" . }}" 2>/dev/null; do
        echo "S3 endpoint not ready yet, retrying in 5 seconds..."
        sleep 5
      done
      echo "S3 endpoint is ready"
{{- end -}}

{{/*
Prepare CA bundle init container (for JWT/Keycloak TLS validation)
Usage: {{ include "cost-onprem.initContainer.prepareCABundle" . | nindent 8 }}
*/}}
{{- define "cost-onprem.initContainer.prepareCABundle" -}}
- name: prepare-ca-bundle
  image: "{{ .Values.global.initContainers.waitFor.repository }}:{{ .Values.global.initContainers.waitFor.tag }}"
  securityContext:
    {{- include "cost-onprem.securityContext.nonRoot" . | nindent 4 }}
  command: ['bash', '/scripts/combine-ca.sh']
  volumeMounts:
    - name: ca-scripts
      mountPath: /scripts
      readOnly: true
    - name: ca-source
      mountPath: /ca-source
      readOnly: true
    - name: ca-bundle
      mountPath: /ca-output
{{- end -}}

{{/*
Wait for Kruize init container
Usage: {{ include "cost-onprem.initContainer.waitForKruize" . | nindent 8 }}
*/}}
{{- define "cost-onprem.initContainer.waitForKruize" -}}
- name: wait-for-kruize
  image: "{{ .Values.global.initContainers.waitFor.repository }}:{{ .Values.global.initContainers.waitFor.tag }}"
  securityContext:
    {{- include "cost-onprem.securityContext.nonRoot" . | nindent 4 }}
  command: ['bash', '-c']
  args:
    - |
      echo "Waiting for Kruize at {{ include "cost-onprem.fullname" . }}-kruize:{{ .Values.kruize.port }}..."
      until timeout 3 bash -c "echo > /dev/tcp/{{ include "cost-onprem.fullname" . }}-kruize/{{ .Values.kruize.port }}" 2>/dev/null; do
        echo "Kruize not ready yet, retrying in 5 seconds..."
        sleep 5
      done
      echo "Kruize is ready"
{{- end -}}

{{/*
Wait for Koku API init container - waits for the unified koku-api service
Usage: {{ include "cost-onprem.initContainer.waitForKoku" . | nindent 8 }}
*/}}
{{- define "cost-onprem.initContainer.waitForKoku" -}}
- name: wait-for-koku
  image: "{{ .Values.global.initContainers.waitFor.repository }}:{{ .Values.global.initContainers.waitFor.tag }}"
  securityContext:
    {{- include "cost-onprem.securityContext.nonRoot" . | nindent 4 }}
  command: ['bash', '-c']
  args:
    - |
      KOKU_API_PORT="{{ .Values.costManagement.api.service.port }}"
      
      echo "Waiting for Koku API at {{ include "cost-onprem.koku.api.name" . }}:${KOKU_API_PORT}..."
      until timeout 3 bash -c "echo > /dev/tcp/{{ include "cost-onprem.koku.api.name" . }}/${KOKU_API_PORT}" 2>/dev/null; do
        echo "Koku API not ready yet, retrying in 5 seconds..."
        sleep 5
      done
      echo "Koku API is ready"
{{- end -}}
