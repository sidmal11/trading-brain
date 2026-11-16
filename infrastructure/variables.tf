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

# ── Lambda deployment ─────────────────────────────────────────────────────────

variable "lambda_package_path" {
  description = <<-EOT
    Path to the zip produced by scripts/package_lambda.sh. Relative paths are
    resolved from the infrastructure/ directory; absolute paths work too.
    Run the script before `terraform apply` — plan will error clearly if the
    file is absent.
  EOT
  type    = string
  default = "lambda/poll_handler.zip"
}

variable "lambda_runtime" {
  description = "Python runtime identifier for the poll handler Lambda."
  type        = string
  default     = "python3.12"
}

variable "lambda_memory_mb" {
  description = <<-EOT
    Memory allocated to the poll handler in MB. The LangGraph + Groq path
    loads several large libraries; 512 MB is the comfortable floor. Raise to
    1024 MB if you observe OOM kills in CloudWatch.
  EOT
  type    = number
  default = 512

  validation {
    condition     = var.lambda_memory_mb >= 128 && var.lambda_memory_mb <= 10240
    error_message = "lambda_memory_mb must be between 128 and 10240 (Lambda limits)."
  }
}

variable "lambda_timeout_seconds" {
  description = <<-EOT
    Maximum execution time in seconds. One poll run fetches screener data,
    calls the Groq API for each candidate, and writes to DynamoDB; 300 s
    (5 min) gives comfortable headroom even when Groq is slow. Must be less
    than the EventBridge interval so invocations never overlap.
  EOT
  type    = number
  default = 300

  validation {
    condition     = var.lambda_timeout_seconds > 0 && var.lambda_timeout_seconds <= 900
    error_message = "lambda_timeout_seconds must be between 1 and 900 (Lambda limit)."
  }
}

# ── EventBridge schedule ──────────────────────────────────────────────────────

variable "schedule_expression" {
  description = <<-EOT
    EventBridge schedule expression controlling how often the poll handler
    fires. Accepts either rate() or cron() syntax.
    Examples:
      "rate(15 minutes)"  — every 15 minutes (default, spec lower bound)
      "rate(30 minutes)"  — every 30 minutes (spec upper bound)
      "cron(0/15 13-21 ? * MON-FRI *)"  — every 15 min during US market hours only
  EOT
  type    = string
  default = "rate(15 minutes)"
}

# ── Strategy knobs ────────────────────────────────────────────────────────────

variable "polling_interval_minutes" {
  description = "Passed to the Lambda as POLLING_INTERVAL_MINUTES. Should match the EventBridge rate."
  type        = number
  default     = 15
}

variable "strategy_volume_threshold" {
  description = "Minimum volume score multiplier to treat a result as a signal (e.g. 3.0 = 300% of average volume)."
  type        = number
  default     = 3.0
}
