#!/usr/bin/env bash
# set-ring-key.sh - store the Ring webhook signing key in AWS Secrets Manager and restart the server with it.
# Run in CloudShell, in the same folder as deploy.sh:   bash set-ring-key.sh
# The key is typed at a hidden prompt, so it never appears on screen, in the command line or in shell history.
set -euo pipefail
STACK=redhouse
out() { aws cloudformation describe-stacks --stack-name "$STACK" \
          --query "Stacks[0].Outputs[?OutputKey=='$1'].OutputValue" --output text; }
read -r -s -p "Paste the Ring webhook key, then press Enter (nothing will show): " KEY
echo
[ -n "$KEY" ] || { echo "No key entered."; exit 1; }
aws secretsmanager put-secret-value --secret-id "$(out RingKeySecretId)" --secret-string "$KEY" >/dev/null
unset KEY
echo "Saved. Restarting the server so it picks the key up..."
SERVER="$(out ServerId)"
CMD="$(aws ssm send-command --instance-ids "$SERVER" --document-name AWS-RunShellScript \
        --parameters 'commands=["/usr/local/bin/redhouse-update"]' --query Command.CommandId --output text)"
aws ssm wait command-executed --command-id "$CMD" --instance-id "$SERVER"
echo "Done. Ring webhooks should be sent to: $(out WebhookUrl)"
