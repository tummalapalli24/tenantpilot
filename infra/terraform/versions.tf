terraform {
  required_version = ">= 1.6" # Terraform or OpenTofu

  required_providers {
    aws = {
      source  = "hashicorp/aws"
      version = "~> 6.66"
    }
    random = {
      source  = "hashicorp/random"
      version = "~> 3.9"
    }
  }

  # State contains generated secrets: keep it in an encrypted, versioned, access-controlled
  # bucket. Uncomment and fill in for real use (the bucket is created out of band).
  # backend "s3" {
  #   bucket       = "my-org-terraform-state"
  #   key          = "tenantpilot/prod.tfstate"
  #   region       = "us-east-1"
  #   encrypt      = true
  #   use_lockfile = true
  # }
}

provider "aws" {
  region = var.region

  default_tags {
    tags = {
      Project     = "tenantpilot"
      Environment = var.environment
      ManagedBy   = "terraform"
    }
  }
}
