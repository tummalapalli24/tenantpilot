# TenantPilot on AWS: VPC -> EKS (+ Pod Identity) -> ECR, RDS Postgres 16 (pgvector),
# ElastiCache, S3 for documents, Secrets Manager for app secrets, GitHub OIDC deploy role.
#
# Cost warning: EKS control plane + NAT gateway + RDS + ElastiCache run ~$200+/month.
# `tofu destroy` / `terraform destroy` when the demo is over.

data "aws_availability_zones" "available" {
  state = "available"
}

data "aws_caller_identity" "current" {}

locals {
  name     = "${var.name}-${var.environment}"
  azs      = slice(data.aws_availability_zones.available.names, 0, 3)
  is_prod  = var.environment == "prod"
  repos    = toset(["tenantpilot-backend", "tenantpilot-web"])
  app_sa   = "tenantpilot" # Helm release "tenantpilot" -> service account name
  vpc_bits = [for i, _ in local.azs : i]
}

# ------------------------------------------------------------------------------ network
module "vpc" {
  source  = "terraform-aws-modules/vpc/aws"
  version = "~> 6.7"

  name = local.name
  cidr = var.vpc_cidr
  azs  = local.azs

  public_subnets   = [for i in local.vpc_bits : cidrsubnet(var.vpc_cidr, 8, i)]
  private_subnets  = [for i in local.vpc_bits : cidrsubnet(var.vpc_cidr, 4, i + 1)]
  database_subnets = [for i in local.vpc_bits : cidrsubnet(var.vpc_cidr, 8, i + 100)]

  create_database_subnet_group = true
  enable_nat_gateway           = true
  single_nat_gateway           = var.single_nat_gateway

  # Subnet discovery tags for AWS Load Balancer Controller / Service type=LoadBalancer.
  public_subnet_tags  = { "kubernetes.io/role/elb" = 1 }
  private_subnet_tags = { "kubernetes.io/role/internal-elb" = 1 }
}

# ------------------------------------------------------------------------------ kubernetes
module "eks" {
  source  = "terraform-aws-modules/eks/aws"
  version = "~> 21.25"

  name               = local.name
  kubernetes_version = var.kubernetes_version

  vpc_id                   = module.vpc.vpc_id
  subnet_ids               = module.vpc.private_subnets
  control_plane_subnet_ids = module.vpc.private_subnets

  endpoint_public_access                   = true
  enable_cluster_creator_admin_permissions = true
  authentication_mode                      = "API"

  addons = {
    coredns                = {}
    kube-proxy             = {}
    vpc-cni                = { before_compute = true }
    eks-pod-identity-agent = { before_compute = true }
    metrics-server         = {} # HPA needs resource metrics
  }

  eks_managed_node_groups = {
    default = {
      instance_types = var.node_instance_types
      ami_type       = "AL2023_x86_64_STANDARD"
      min_size       = var.node_min_size
      max_size       = var.node_max_size
      desired_size   = var.node_desired_size
    }
  }

  access_entries = var.github_repository == "" ? {} : {
    github_deploy = {
      principal_arn = aws_iam_role.github_deploy[0].arn
      policy_associations = {
        admin = {
          policy_arn   = "arn:aws:eks::aws:cluster-access-policy/AmazonEKSClusterAdminPolicy"
          access_scope = { type = "cluster" }
        }
      }
    }
  }
}

# ------------------------------------------------------------------------------ images
resource "aws_ecr_repository" "this" {
  for_each = local.repos

  name                 = each.key
  image_tag_mutability = "IMMUTABLE" # a tag always means the same image (CI tags with the git SHA)
  force_delete         = !local.is_prod

  image_scanning_configuration {
    scan_on_push = true
  }

  encryption_configuration {
    encryption_type = "AES256"
  }
}

resource "aws_ecr_lifecycle_policy" "this" {
  for_each   = aws_ecr_repository.this
  repository = each.value.name

  policy = jsonencode({
    rules = [{
      rulePriority = 1
      description  = "Keep the 30 most recent images"
      selection    = { tagStatus = "any", countType = "imageCountMoreThan", countNumber = 30 }
      action       = { type = "expire" }
    }]
  })
}

# ------------------------------------------------------------------------------ documents (S3)
resource "aws_s3_bucket" "documents" {
  bucket        = "${local.name}-documents-${data.aws_caller_identity.current.account_id}"
  force_destroy = !local.is_prod
}

resource "aws_s3_bucket_public_access_block" "documents" {
  bucket                  = aws_s3_bucket.documents.id
  block_public_acls       = true
  block_public_policy     = true
  ignore_public_acls      = true
  restrict_public_buckets = true
}

resource "aws_s3_bucket_versioning" "documents" {
  bucket = aws_s3_bucket.documents.id
  versioning_configuration {
    status = "Enabled"
  }
}

resource "aws_s3_bucket_server_side_encryption_configuration" "documents" {
  bucket = aws_s3_bucket.documents.id
  rule {
    apply_server_side_encryption_by_default {
      sse_algorithm = "AES256"
    }
  }
}

resource "aws_s3_bucket_policy" "documents_tls_only" {
  bucket = aws_s3_bucket.documents.id
  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [{
      Sid       = "DenyInsecureTransport"
      Effect    = "Deny"
      Principal = "*"
      Action    = "s3:*"
      Resource  = [aws_s3_bucket.documents.arn, "${aws_s3_bucket.documents.arn}/*"]
      Condition = { Bool = { "aws:SecureTransport" = "false" } }
    }]
  })
}

# ------------------------------------------------------------------------------ database (RDS)
resource "aws_security_group" "db" {
  name_prefix = "${local.name}-db-"
  description = "Postgres reachable only from EKS nodes"
  vpc_id      = module.vpc.vpc_id
}

resource "aws_vpc_security_group_ingress_rule" "db_from_nodes" {
  security_group_id            = aws_security_group.db.id
  referenced_security_group_id = module.eks.node_security_group_id
  from_port                    = 5432
  to_port                      = 5432
  ip_protocol                  = "tcp"
}

resource "aws_db_instance" "postgres" {
  identifier     = "${local.name}-pg"
  engine         = "postgres"
  engine_version = "16" # pgvector is available on RDS for PostgreSQL 16
  instance_class = var.db_instance_class

  allocated_storage     = 20
  max_allocated_storage = 100
  storage_type          = "gp3"
  storage_encrypted     = true

  db_name  = "tenantpilot"
  username = "tenantpilot" # owner role: runs migrations only
  # RDS generates and rotates the master password in Secrets Manager - it never
  # appears in Terraform code or state.
  manage_master_user_password = true

  db_subnet_group_name   = module.vpc.database_subnet_group_name
  vpc_security_group_ids = [aws_security_group.db.id]
  publicly_accessible    = false
  multi_az               = local.is_prod

  backup_retention_period      = 7
  deletion_protection          = local.is_prod
  skip_final_snapshot          = !local.is_prod
  final_snapshot_identifier    = local.is_prod ? "${local.name}-pg-final" : null
  auto_minor_version_upgrade   = true
  performance_insights_enabled = true
}

# ------------------------------------------------------------------------------ redis (ElastiCache)
resource "aws_security_group" "redis" {
  count       = var.enable_elasticache ? 1 : 0
  name_prefix = "${local.name}-redis-"
  description = "Redis reachable only from EKS nodes"
  vpc_id      = module.vpc.vpc_id
}

resource "aws_vpc_security_group_ingress_rule" "redis_from_nodes" {
  count                        = var.enable_elasticache ? 1 : 0
  security_group_id            = aws_security_group.redis[0].id
  referenced_security_group_id = module.eks.node_security_group_id
  from_port                    = 6379
  to_port                      = 6379
  ip_protocol                  = "tcp"
}

resource "aws_elasticache_subnet_group" "redis" {
  count      = var.enable_elasticache ? 1 : 0
  name       = "${local.name}-redis"
  subnet_ids = module.vpc.private_subnets
}

resource "aws_elasticache_replication_group" "redis" {
  count                      = var.enable_elasticache ? 1 : 0
  replication_group_id       = "${local.name}-redis"
  description                = "TenantPilot job queue and per-tenant rate limiting"
  engine                     = "redis"
  engine_version             = "7.1"
  node_type                  = "cache.t4g.micro"
  num_cache_clusters         = 1
  port                       = 6379
  subnet_group_name          = aws_elasticache_subnet_group.redis[0].name
  security_group_ids         = [aws_security_group.redis[0].id]
  at_rest_encryption_enabled = true
  transit_encryption_enabled = true # clients use rediss://
  automatic_failover_enabled = false
}

# ------------------------------------------------------------------------------ app secrets
resource "random_password" "app_db" {
  length  = 32
  special = false # embedded in a connection URL
}

resource "random_password" "jwt" {
  length  = 48
  special = false
}

resource "random_password" "service_token" {
  length  = 48
  special = false
}

resource "random_id" "fernet" {
  byte_length = 32
}

# Synced into the cluster by External Secrets Operator as the chart's existingSecret /
# existingMigrationsSecret (DB owner password comes from the RDS-managed secret).
resource "aws_secretsmanager_secret" "app" {
  name                    = "${local.name}/app"
  recovery_window_in_days = local.is_prod ? 30 : 0
}

resource "aws_secretsmanager_secret_version" "app" {
  secret_id = aws_secretsmanager_secret.app.id
  secret_string = jsonencode({
    APP_DB_PASSWORD      = random_password.app_db.result
    DATABASE_URL         = "postgresql+asyncpg://tenantpilot_app:${random_password.app_db.result}@${aws_db_instance.postgres.address}:5432/tenantpilot"
    JWT_SECRET           = random_password.jwt.result
    SERVICE_TOKEN_SECRET = random_password.service_token.result
    SECRETS_MASTER_KEY   = replace(replace(random_id.fernet.b64_std, "+", "-"), "/", "_")
    ANTHROPIC_API_KEY    = "" # set out of band: aws secretsmanager put-secret-value ...
  })

  lifecycle {
    ignore_changes = [secret_string] # rotated/edited outside Terraform after creation
  }
}

# ------------------------------------------------------------------------------ app identity (EKS Pod Identity)
resource "aws_iam_role" "app" {
  name = "${local.name}-app"
  assume_role_policy = jsonencode({
    Version = "2012-10-17"
    Statement = [{
      Effect    = "Allow"
      Principal = { Service = "pods.eks.amazonaws.com" }
      Action    = ["sts:AssumeRole", "sts:TagSession"]
    }]
  })
}

resource "aws_iam_role_policy" "app" {
  name = "documents-bucket"
  role = aws_iam_role.app.id
  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [
      {
        Effect   = "Allow"
        Action   = ["s3:GetObject", "s3:PutObject", "s3:DeleteObject"]
        Resource = "${aws_s3_bucket.documents.arn}/*"
      },
      {
        Effect   = "Allow"
        Action   = ["s3:ListBucket"]
        Resource = aws_s3_bucket.documents.arn
      },
    ]
  })
}

resource "aws_eks_pod_identity_association" "app" {
  cluster_name    = module.eks.cluster_name
  namespace       = var.app_namespace
  service_account = local.app_sa
  role_arn        = aws_iam_role.app.arn
}

# ------------------------------------------------------------------------------ CI/CD identity (GitHub OIDC)
resource "aws_iam_openid_connect_provider" "github" {
  count          = var.github_repository == "" ? 0 : 1
  url            = "https://token.actions.githubusercontent.com"
  client_id_list = ["sts.amazonaws.com"]
}

resource "aws_iam_role" "github_deploy" {
  count = var.github_repository == "" ? 0 : 1
  name  = "${local.name}-github-deploy"
  assume_role_policy = jsonencode({
    Version = "2012-10-17"
    Statement = [{
      Effect    = "Allow"
      Principal = { Federated = aws_iam_openid_connect_provider.github[0].arn }
      Action    = "sts:AssumeRoleWithWebIdentity"
      Condition = {
        StringEquals = { "token.actions.githubusercontent.com:aud" = "sts.amazonaws.com" }
        # Only the production environment job of this repository may assume the role.
        StringLike = { "token.actions.githubusercontent.com:sub" = "repo:${var.github_repository}:environment:production" }
      }
    }]
  })
}

resource "aws_iam_role_policy" "github_deploy" {
  count = var.github_repository == "" ? 0 : 1
  name  = "ecr-push-eks-describe"
  role  = aws_iam_role.github_deploy[0].id
  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [
      { Effect = "Allow", Action = ["ecr:GetAuthorizationToken"], Resource = "*" },
      {
        Effect = "Allow"
        Action = [
          "ecr:BatchCheckLayerAvailability", "ecr:BatchGetImage", "ecr:CompleteLayerUpload",
          "ecr:InitiateLayerUpload", "ecr:PutImage", "ecr:UploadLayerPart",
        ]
        Resource = [for repo in aws_ecr_repository.this : repo.arn]
      },
      { Effect = "Allow", Action = ["eks:DescribeCluster"], Resource = module.eks.cluster_arn },
    ]
  })
}
