{{- define "tenantpilot.name" -}}
{{- .Chart.Name | trunc 63 | trimSuffix "-" -}}
{{- end -}}

{{- define "tenantpilot.fullname" -}}
{{- if contains .Chart.Name .Release.Name -}}
{{- .Release.Name | trunc 63 | trimSuffix "-" -}}
{{- else -}}
{{- printf "%s-%s" .Release.Name .Chart.Name | trunc 63 | trimSuffix "-" -}}
{{- end -}}
{{- end -}}

{{- define "tenantpilot.labels" -}}
helm.sh/chart: {{ printf "%s-%s" .Chart.Name .Chart.Version | replace "+" "_" }}
app.kubernetes.io/name: {{ include "tenantpilot.name" . }}
app.kubernetes.io/instance: {{ .Release.Name }}
app.kubernetes.io/version: {{ .Chart.AppVersion | quote }}
app.kubernetes.io/managed-by: {{ .Release.Service }}
tenantpilot.dev/environment: {{ .Values.environment }}
{{- end -}}

{{/* selectorLabels: pass (dict "ctx" $ "component" "api") */}}
{{- define "tenantpilot.selectorLabels" -}}
app.kubernetes.io/name: {{ include "tenantpilot.name" .ctx }}
app.kubernetes.io/instance: {{ .ctx.Release.Name }}
app.kubernetes.io/component: {{ .component }}
{{- end -}}

{{- define "tenantpilot.backendImage" -}}
{{ .Values.image.repository }}:{{ .Values.image.tag | default .Chart.AppVersion }}
{{- end -}}

{{- define "tenantpilot.webImage" -}}
{{ .Values.webImage.repository }}:{{ .Values.webImage.tag | default .Chart.AppVersion }}
{{- end -}}

{{- define "tenantpilot.secretName" -}}
{{ .Values.secrets.existingSecret | default (printf "%s-app" (include "tenantpilot.fullname" .)) }}
{{- end -}}

{{- define "tenantpilot.migrationsSecretName" -}}
{{ .Values.secrets.existingMigrationsSecret | default (printf "%s-migrations" (include "tenantpilot.fullname" .)) }}
{{- end -}}

{{- define "tenantpilot.dbHost" -}}
{{- if .Values.postgres.enabled -}}{{ include "tenantpilot.fullname" . }}-postgres{{- else -}}{{ required "externalDatabase.host is required when postgres.enabled=false" .Values.externalDatabase.host }}{{- end -}}
{{- end -}}

{{- define "tenantpilot.serviceAccountName" -}}
{{ include "tenantpilot.fullname" . }}
{{- end -}}

{{/* Common env for every backend container: non-secret config + runtime secrets. */}}
{{- define "tenantpilot.backendEnv" -}}
envFrom:
  - configMapRef:
      name: {{ include "tenantpilot.fullname" . }}-config
  - secretRef:
      name: {{ include "tenantpilot.secretName" . }}
{{- end -}}

{{- define "tenantpilot.podAnnotations" -}}
checksum/config: {{ include (print $.Template.BasePath "/configmap.yaml") . | sha256sum }}
{{- end -}}

{{- define "tenantpilot.metricsAnnotations" -}}
{{- if .ctx.Values.metrics.podAnnotations }}
prometheus.io/scrape: "true"
prometheus.io/port: {{ .port | quote }}
prometheus.io/path: /metrics
{{- end }}
{{- end -}}

{{/* Writable /tmp for read-only root filesystems (Python and Node both need it). */}}
{{- define "tenantpilot.tmpVolumeMount" -}}
- name: tmp
  mountPath: /tmp
{{- end -}}

{{- define "tenantpilot.uploadsVolume" -}}
- name: uploads
{{- if .Values.uploads.persistence }}
  persistentVolumeClaim:
    claimName: {{ include "tenantpilot.fullname" . }}-uploads
{{- else }}
  emptyDir: {}
{{- end }}
{{- end -}}
