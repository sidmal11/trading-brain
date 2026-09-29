# Passwordless OTP for the dashboard, per ticket 10.
#
# The pool has no usable password path: the only sign-in flow the client is
# allowed is CUSTOM_AUTH, so a trader proves who they are with a one-time code
# and there is no password to phish, reuse, or crack out of a breached hash.
#
# The code is put into the challenge message by the otp_sender Lambda below. That
# function currently logs the code instead of sending it, which is a deliberate
# development stand-in. Read the warning in handler.py before pointing this pool
# at a real trader.

resource "aws_cognito_user_pool" "trading_brain" {
  name = var.cognito_user_pool_name

  # Email and phone both allowed, matching the identifier switcher in
  # SignInModal.tsx. auto_verified is deliberately empty: an unverified address
  # must not be able to receive a code and complete the challenge.
  username_attributes      = ["email", "phone_number"]
  auto_verified_attributes = []
  mfa_configuration        = "OFF"

  # There is no password sign-in, so the policy is a guard rail rather than a
  # control: if a flow is ever added back it starts from these minimums.
  password_policy {
    minimum_length                   = 16
    require_lowercase                = true
    require_uppercase                = true
    require_numbers                  = true
    require_symbols                  = true
    temporary_password_validity_days = 3
  }

  account_recovery_setting {
    recovery_mechanism {
      name     = "verified_email"
      priority = 1
    }
  }

  # custom_message is the trigger the OTP path needs: it fires while Cognito
  # builds a CUSTOM_CHALLENGE message and the handler supplies codeParameter.
  lambda_config {
    custom_message = aws_lambda_function.otp_sender.arn
  }

  tags = {
    Name      = var.cognito_user_pool_name
    Project   = "trading-brain"
    ManagedBy = "terraform"
  }
}

# The web client the browser uses. generate_secret must stay false: an SPA
# cannot keep a secret, and Cognito rejects a secret-bearing client from a
# public origin, so a secret here would make the app unusable rather than safer.
resource "aws_cognito_user_pool_client" "trading_brain_web" {
  name         = "${var.cognito_user_pool_name}-web"
  user_pool_id = aws_cognito_user_pool.trading_brain.id

  generate_secret = false

  # CUSTOM_AUTH is the sign-in path. REFRESH_TOKEN_AUTH is what lets an expired
  # access token be exchanged instead of dropping the trader at the gate, which
  # is the ticket 10 behaviour getStoredSession now implements.
  explicit_auth_flows = ["ALLOW_CUSTOM_AUTH", "ALLOW_REFRESH_TOKEN_AUTH"]

  refresh_token_validity = 30
  access_token_validity  = 60
  id_token_validity      = 60

  token_validity_units {
    access_token  = "minutes"
    id_token      = "minutes"
    refresh_token = "days"
  }

  # Stops the pool from confirming whether an account exists, so the sign-in
  # form cannot be used to enumerate traders.
  prevent_user_existence_errors = "ENABLED"
}

# An explicit log group, not the Lambda-created default. Two reasons: retention is
# capped instead of being forever, and the IAM grant below can name this one group
# rather than the whole account. The second matters here because this function logs
# a sign-in code, so an unbounded log is an unbounded pile of one-time credentials.
resource "aws_cloudwatch_log_group" "otp_sender" {
  name              = "/aws/lambda/${var.project_name}-otp-sender"
  retention_in_days = var.log_retention_days
  tags = {
    Name      = "${var.project_name}-otp-sender"
    ManagedBy = "terraform"
  }
}

# The sender needs log writes to that one group and nothing else. It does not call
# Cognito: the handler only writes codeParameter into the message Cognito is already
# sending, which is what keeps this role free of any cognito-idp grant.
data "aws_iam_policy_document" "otp_sender_log_permissions" {
  statement {
    sid       = "WriteLambdaLogs"
    effect    = "Allow"
    actions   = ["logs:CreateLogStream", "logs:PutLogEvents"]
    resources = ["${aws_cloudwatch_log_group.otp_sender.arn}:*"]
  }
}

resource "aws_iam_role" "otp_sender" {
  name               = "${var.project_name}-otp-sender-role"
  description        = "Execution role for the Cognito custom_message OTP sender"
  assume_role_policy = data.aws_iam_policy_document.lambda_assume_role.json

  tags = {
    Name      = "${var.project_name}-otp-sender-role"
    ManagedBy = "terraform"
  }
}

resource "aws_iam_role_policy" "otp_sender_log_permissions" {
  name   = "${var.project_name}-otp-sender-logs"
  role   = aws_iam_role.otp_sender.id
  policy = data.aws_iam_policy_document.otp_sender_log_permissions.json
}

data "archive_file" "otp_sender" {
  type        = "zip"
  source_dir  = "${path.module}/lambda/otp_sender"
  output_path = "${path.module}/.terraform/otp_sender.zip"
}

resource "aws_lambda_function" "otp_sender" {
  function_name = "${var.project_name}-otp-sender"
  role          = aws_iam_role.otp_sender.arn
  handler       = "handler.lambda_handler"
  runtime       = "python3.12"

  # Stateless and short: the call happens inside a Cognito request, which times
  # out at 60s, so this budget is generous rather than tight.
  memory_size = 256
  timeout     = 10

  filename         = data.archive_file.otp_sender.output_path
  source_code_hash = data.archive_file.otp_sender.output_base64sha256

  # Names the group above so Lambda attaches to it instead of creating an
  # unmanaged one on first invoke. Retention is set on the group, not here:
  # this block does not accept it, and setting both would be a silent conflict.
  logging_config {
    log_group             = aws_cloudwatch_log_group.otp_sender.name
    log_format            = "JSON"
    application_log_level = "INFO"
    system_log_level      = "WARN"
  }

  environment {
    variables = {
      # USER_POOL_ID is deliberately absent. The pool wires this function in
      # through lambda_config, so an environment variable holding the pool id
      # would close a dependency cycle. The handler does not need it: the
      # custom_message event names the pool, and the function never calls back.
      #
      # No default for the medium either. A null value means "read it off the
      # user's own attributes", which is the correct default, so this does not
      # need to fail the apply the way a required string would.
      DEFAULT_DELIVERY_MEDIUM = var.otp_default_delivery_medium
    }
  }

  depends_on = [
    aws_iam_role_policy.otp_sender_log_permissions,
    aws_cloudwatch_log_group.otp_sender,
  ]
}

resource "aws_lambda_permission" "cognito_invoke_otp_sender" {
  statement_id  = "AllowCognitoToInvokeOtpSender"
  action        = "lambda:InvokeFunction"
  function_name = aws_lambda_function.otp_sender.function_name
  principal     = "cognito-idp.amazonaws.com"
  source_arn    = aws_cognito_user_pool.trading_brain.arn
}

output "cognito_user_pool_id" {
  description = "User pool id, exported to the frontend as VITE_COGNITO_USER_POOL_ID"
  value       = aws_cognito_user_pool.trading_brain.id
}

output "cognito_user_pool_client_id" {
  description = "Web client id, exported to the frontend as VITE_COGNITO_CLIENT_ID"
  value       = aws_cognito_user_pool_client.trading_brain_web.id
}

output "frontend_env" {
  description = "The VITE_* values the frontend build needs, as a shell snippet"
  value = join("\n", [
    "VITE_COGNITO_USER_POOL_ID=${aws_cognito_user_pool.trading_brain.id}",
    "VITE_COGNITO_CLIENT_ID=${aws_cognito_user_pool_client.trading_brain_web.id}",
    "VITE_AWS_REGION=${var.aws_region}",
  ])
}
