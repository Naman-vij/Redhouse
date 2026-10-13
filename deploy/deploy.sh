#!/usr/bin/env bash
# deploy.sh - put RedHouse on AWS. Run it in AWS CloudShell, from the folder this file is in.
#
#   bash deploy.sh
#
# First run: builds everything (about 10 minutes). Later runs: just ships new code and the new page.
# Optional:  REDHOUSE_BEDROCK_MODEL=<model id>  bash deploy.sh      (default: global.amazon.nova-2-lite-v1:0)
set -euo pipefail
cd "$(dirname "$0")"

STACK=redhouse
REGION="${AWS_REGION:-${AWS_DEFAULT_REGION:-$(aws configure get region || true)}}"
[ -n "$REGION" ] || { echo "No AWS region is set. Choose one in the console (top right) and run again."; exit 1; }
export AWS_DEFAULT_REGION="$REGION"
ACCOUNT="$(aws sts get-caller-identity --query Account --output text)"
BUCKET="redhouse-artifacts-$ACCOUNT-$REGION"
MODEL="${REDHOUSE_BEDROCK_MODEL-global.amazon.nova-2-lite-v1:0}"

echo "Region $REGION, account $ACCOUNT"
for f in app.tgz template.yaml frontend-dist/index.html; do
  [ -f "$f" ] || { echo "Missing $f. Extract the whole release first."; exit 1; }
done

echo "1/5 Uploading the server code"
if ! aws s3api head-bucket --bucket "$BUCKET" 2>/dev/null; then
  aws s3 mb "s3://$BUCKET" --region "$REGION"
  aws s3api put-public-access-block --bucket "$BUCKET" \
    --public-access-block-configuration BlockPublicAcls=true,IgnorePublicAcls=true,BlockPublicPolicy=true,RestrictPublicBuckets=true
fi
aws s3 cp app.tgz "s3://$BUCKET/app.tgz" --only-show-errors

echo "2/5 Building the AWS resources (the first time this takes about 10 minutes)"
EXISTED=no
aws cloudformation describe-stacks --stack-name "$STACK" >/dev/null 2>&1 && EXISTED=yes
PREFIX_LIST="$(aws ec2 describe-managed-prefix-lists \
  --filters Name=prefix-list-name,Values=com.amazonaws.global.cloudfront.origin-facing \
  --query 'PrefixLists[0].PrefixListId' --output text)"
aws cloudformation deploy --stack-name "$STACK" --template-file template.yaml \
  --capabilities CAPABILITY_IAM --no-fail-on-empty-changeset \
  --parameter-overrides "ArtifactBucket=$BUCKET" "BedrockModel=$MODEL" "CloudFrontPrefixList=$PREFIX_LIST"

out() { aws cloudformation describe-stacks --stack-name "$STACK" \
          --query "Stacks[0].Outputs[?OutputKey=='$1'].OutputValue" --output text; }
URL="$(out SiteUrl)"; DIST="$(out DistributionId)"; PAGE_BUCKET="$(out PageBucketName)"; SERVER="$(out ServerId)"

if [ "$EXISTED" = yes ]; then
  echo "3/5 Updating the running server"
  CMD="$(aws ssm send-command --instance-ids "$SERVER" --document-name AWS-RunShellScript \
          --parameters 'commands=["/usr/local/bin/redhouse-update"]' --query Command.CommandId --output text)"
  aws ssm wait command-executed --command-id "$CMD" --instance-id "$SERVER" \
    || { echo "The server update failed. Output:"; aws ssm get-command-invocation --command-id "$CMD" --instance-id "$SERVER" \
         --query '[StandardOutputContent,StandardErrorContent]' --output text; exit 1; }
else
  echo "3/5 The new server installs the code itself while it boots"
fi

echo "4/5 Uploading the page"
aws s3 sync frontend-dist "s3://$PAGE_BUCKET" --delete --only-show-errors \
  --exclude index.html --cache-control "public,max-age=31536000,immutable"
aws s3 cp frontend-dist/index.html "s3://$PAGE_BUCKET/index.html" --only-show-errors \
  --cache-control "no-cache" --content-type "text/html"
aws cloudfront create-invalidation --distribution-id "$DIST" --paths "/*" >/dev/null

echo "5/5 Waiting for the server to answer (it installs its libraries on the first run)"
for i in $(seq 1 90); do
  if curl -fsS --max-time 10 "$URL/health" >/dev/null 2>&1; then
    echo
    echo "RedHouse is live:  $URL"
    echo "Check the plain-words summary at:  $URL/api/explain   (you want \"source\":\"bedrock\")"
    exit 0
  fi
  printf '.'; sleep 10
done
echo
echo "The server is not answering yet. In the EC2 console, open instance $SERVER, choose Connect > Session Manager, and run:"
echo "  sudo tail -50 /var/log/redhouse-setup.log"
exit 1
