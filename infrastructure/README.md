# Terraform Infrastructure for Trading Brain

This directory contains the Terraform configuration to provision the AWS infrastructure required for the Trading Brain service.

## Overview

This infrastructure sets up the following resources:

- **DynamoDB**: A table named `TradingAlerts` to store trade alerts and their status.
- **IAM Role**: A role for the Lambda function with permissions to access DynamoDB and log to CloudWatch.
- **Secrets Manager**: A secret store for API keys (Groq, Telegram, Stock Screener, LangSmith) and configuration.

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

2. Create a `terraform.tfvars` file with your sensitive values:

```bash
cat > terraform.tfvars <<EOF
aws_region = "us-east-1"
table_name = "TradingAlerts"
secret_name = "trading-brain-secrets"

# Your API Keys (Set these to your actual values)
groq_api_key = "your-groq-api-key"
telegram_bot_token = "your-telegram-bot-token"
telegram_chat_id = "your-telegram-chat-id"
stock_screener_api_key = "your-stock-screener-api-key"
langsmith_api_key = "your-langsmith-api-key"
stock_screener_url = "http://localhost:8000"
EOF
```

3. Initialize Terraform:

```bash
terraform init
```

4. Plan the infrastructure:

```bash
terraform plan
```

5. Apply the infrastructure:

```bash
terraform apply -auto-approve
```

6. Verify the resources are created:

```bash
terraform output
```

## Usage with Lambda

After deploying the infrastructure, you need to configure your Lambda function:

1. **Add Environment Variables** to your Lambda function:
   - `AWS_REGION`: The AWS region (e.g., `us-east-1`).
   - `DYNAMODB_TABLE_NAME`: The DynamoDB table name (from `terraform output dynamodb_table_name`).
   - `SECRET_NAME`: The Secrets Manager secret name (from `terraform output secret_name`).
   - `TELEGRAM_CHAT_ID`: Your Telegram Chat ID (from `terraform.tfvars`).

2. **Set up IAM Role for Lambda**: Attach the IAM role created by Terraform to your Lambda function.

3. **Update the Lambda Code**: Modify your Lambda code to retrieve secrets from AWS Secrets Manager.

## Destroying Infrastructure

To delete the infrastructure, run:

```bash
terraform destroy -auto-approve
```

## Terraform State Management

We recommend using Terraform's remote state storage (S3 + DynamoDB) to ensure state consistency across teams. Uncomment and configure the backend block in `providers.tf` with your desired S3 bucket and DynamoDB table names.

## License

This project is licensed under the Apache License 2.0.
