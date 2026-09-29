variable "aws_region" {
  description = "AWS region for deployment"
  type        = string
  default     = "us-east-1"
}

variable "table_name" {
  description = "Name of the DynamoDB table for trading alerts"
  type        = string
  default     = "TradingAlerts"
}

variable "secret_name" {
  description = "Name of the Secrets Manager secret"
  type        = string
  default     = "trading-brain-secrets"
}

variable "project_name" {
  description = "Short project slug used as a name prefix for created resources"
  type        = string
  default     = "trading-brain"
}

variable "environment" {
  description = "Deployment environment tag, for example dev, staging, or prod"
  type        = string
  default     = "dev"
}

# Earlier rows in the table were written with the old field names and vocabularies.
# Nothing here translates them: the dashboard does that on read, in
# `frontend/src/services/alertContract.ts`. This module only writes.
variable "log_retention_days" {
  description = <<-EOT
    CloudWatch retention for the Lambda log groups. Bounded on purpose: the
    otp_sender log holds sign-in codes, and an unbounded log is an unbounded
    pile of one-time credentials.
  EOT
  type        = number
  default     = 14

  validation {
    condition     = var.log_retention_days > 0
    error_message = "log_retention_days must be positive. An unbounded log is not a safe default for a group that carries credentials."
  }
}

variable "cognito_user_pool_name" {
  description = "Name of the Cognito User Pool backing dashboard OTP sign-in"
  type        = string
  default     = "trading-brain-traders"
}

variable "otp_default_delivery_medium" {
  description = <<-EOT
    Delivery medium used when a challenge does not state one, EMAIL or SMS.
    Null is the default and means "read it off the user's own attributes", which
    is the right behaviour when the identifier is already an email or a phone.
    Set it only to pin every challenge to one channel.
  EOT
  type        = string
  default     = null

  validation {
    condition     = var.otp_default_delivery_medium == null || contains(["EMAIL", "SMS"], var.otp_default_delivery_medium)
    error_message = "otp_default_delivery_medium must be EMAIL, SMS, or null."
  }
}

variable "lambda_vpc_ids" {
  description = <<-EOT
    VPC subnet ids the Lambda runs in. When this is non-empty the role is granted
    ec2 interface-permission actions so the function can manage its own network
    interfaces. Leave empty for a function outside a VPC.
  EOT
  type        = list(string)
  default     = []
}
