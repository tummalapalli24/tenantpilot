variable "region" {
  description = "AWS region"
  type        = string
  default     = "us-east-1"
}

variable "name" {
  description = "Name prefix for every resource"
  type        = string
  default     = "tenantpilot"
}

variable "environment" {
  description = "Deployment environment (drives safety settings like deletion protection)"
  type        = string
  default     = "prod"

  validation {
    condition     = contains(["dev", "staging", "prod"], var.environment)
    error_message = "environment must be dev, staging or prod."
  }
}

variable "vpc_cidr" {
  type    = string
  default = "10.40.0.0/16"
}

variable "single_nat_gateway" {
  description = "One NAT gateway instead of one per AZ (cheaper, less resilient)"
  type        = bool
  default     = true
}

variable "kubernetes_version" {
  type    = string
  default = "1.35"
}

variable "node_instance_types" {
  type    = list(string)
  default = ["t3.large"]
}

variable "node_min_size" {
  type    = number
  default = 2
}

variable "node_max_size" {
  type    = number
  default = 4
}

variable "node_desired_size" {
  type    = number
  default = 2
}

variable "db_instance_class" {
  type    = string
  default = "db.t4g.small"
}

variable "enable_elasticache" {
  description = "Managed Redis for the job queue + rate limiter (otherwise run Redis in-cluster)"
  type        = bool
  default     = true
}

variable "github_repository" {
  description = "owner/repo allowed to deploy via GitHub OIDC (empty = don't create the deploy role)"
  type        = string
  default     = ""
}

variable "app_namespace" {
  description = "Kubernetes namespace and service account used by the Helm release"
  type        = string
  default     = "tenantpilot"
}
