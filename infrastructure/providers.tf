terraform {
  required_providers {
    aws = {
      source  = "hashicorp/aws"
      version = "~> 5.0"
    }
  }
  backend "s3" {
    # Configure your S3 backend for state storage if needed
  }
}

provider "aws" {
  region = var.aws_region
}
