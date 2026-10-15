output "cluster_name" {
  value = module.eks.cluster_name
}

output "configure_kubectl" {
  description = "Point kubectl/helm at the new cluster"
  value       = "aws eks update-kubeconfig --name ${module.eks.cluster_name} --region ${var.region}"
}

output "ecr_repositories" {
  value = { for name, repo in aws_ecr_repository.this : name => repo.repository_url }
}

output "rds_endpoint" {
  description = "externalDatabase.host in values-prod.yaml"
  value       = aws_db_instance.postgres.address
}

output "rds_master_secret_arn" {
  description = "RDS-managed owner password (source for the chart's migrations secret)"
  value       = aws_db_instance.postgres.master_user_secret[0].secret_arn
}

output "redis_url" {
  description = "externalRedis.url in values-prod.yaml"
  value       = var.enable_elasticache ? "rediss://${aws_elasticache_replication_group.redis[0].primary_endpoint_address}:6379/0" : null
}

output "documents_bucket" {
  description = "config.s3Bucket in values-prod.yaml"
  value       = aws_s3_bucket.documents.bucket
}

output "app_secret_arn" {
  value = aws_secretsmanager_secret.app.arn
}

output "app_role_arn" {
  description = "Bound to the tenantpilot service account through EKS Pod Identity"
  value       = aws_iam_role.app.arn
}

output "github_deploy_role_arn" {
  description = "Set as the AWS_DEPLOY_ROLE_ARN repository variable"
  value       = var.github_repository == "" ? null : aws_iam_role.github_deploy[0].arn
}
