# Terraform Infrastructure for Trading Brain

This directory contains the Terraform configuration to provision the AWS infrastructure required for the Trading Brain service.

## Overview

This infrastructure sets up the following resources:

- **DynamoDB** (`main.tf`): A table named `TradingAlerts` to store trade alerts and their status.
- **IAM Role** (`main.tf`): A role for the Lambda function with permissions to access DynamoDB, Secrets Manager, and CloudWatch.
- **Secrets Manager** (`main.tf`): A secret store for API keys (Groq, Telegram, Stock Screener, LangSmith) and configuration.
- **Lambda Function** (`lambda.tf`): The poll handler that runs the full Trading Brain pipeline (screener poll, LangGraph analysis, Telegram alert, DynamoDB persist).
- **EventBridge Rule** (`lambda.tf`): A CloudWatch Events schedule that fires the poll handler every 15 minutes (configurable via `schedule_expression`).
- **Cognito** (`cognito.tf`): User pool and web client for passwordless OTP sign-in (dashboard auth).

## Prerequisites

1. **Install Terraform**: Download and install Terraform from the official website.
2. **AWS Account**: Ensure you have an AWS account and an IAM user with appropriate permissions.
3. **AWS CLI Configured**: Configure the AWS CLI with credentials (access key and secret key) and set your default region.

## Getting Started

1. Clone this repository to your local machine:

```bash
git clone <your-repo-url>
cd trading_brain/infrastructure
```

2. Create a `dev.tfvars` (or `prod.tfvars`) file with your values:

```bash
cat > dev.tfvars <<EOF
aws_region   = "us-east-1"
table_name   = "TradingAlerts"
secret_name  = "trading-brain-secrets"
environment  = "dev"

# Schedule: every 15 min (default) or "rate(30 minutes)" for the wider window
schedule_expression = "rate(15 minutes)"

# Strategy tuning (optional, defaults match config.py)
# polling_interval_minutes  = 15
# strategy_volume_threshold = 3.0
EOF
```

3. Package and deploy in one step:

```bash
# From trading_brain/
bash scripts/deploy.sh dev
```

Or run the steps manually:

```bash
# 1. Build the Lambda zip
bash scripts/package_lambda.sh

# 2. Terraform
cd infrastructure
terraform init
terraform plan -var-file=dev.tfvars
terraform apply -var-file=dev.tfvars
```

4. Verify key outputs:

```bash
terraform output
```

Expected outputs include `lambda_function_name`, `lambda_function_arn`, `eventbridge_rule_name`, `dynamodb_table_name`, `iam_role_arn`, `secret_name`.

## Usage with Lambda

The Lambda function is now defined in `lambda.tf` and deployed by Terraform.
After `terraform apply`, the function exists in AWS with all environment variables
pre-wired. No manual console steps are needed.

To invoke it manually for a quick sanity check:

```bash
aws lambda invoke \
  --function-name "$(terraform output -raw lambda_function_name)" \
  --log-type Tail \
  --query 'LogResult' \
  --output text \
  response.json | base64 --decode
cat response.json
```

To check the EventBridge rule is live:

```bash
aws events describe-rule \
  --name "$(terraform output -raw eventbridge_rule_name)"
```

## Destroying Infrastructure

To delete the infrastructure, run:

```bash
terraform destroy -auto-approve
```

## Terraform State Management

We recommend using Terraform's remote state storage (S3 + DynamoDB) to ensure state consistency across teams. Uncomment and configure the backend block in `providers.tf` with your desired S3 bucket and DynamoDB table names.

## License

This project is licensed under the Apache License 2.0.
