#!/usr/bin/env bash
# teardown.sh - delete everything deploy.sh made, so nothing keeps costing money. Run in CloudShell.
set -euo pipefail
STACK=redhouse
out() { aws cloudformation describe-stacks --stack-name "$STACK" \
          --query "Stacks[0].Outputs[?OutputKey=='$1'].OutputValue" --output text; }
read -r -p "Delete RedHouse and every uploaded site? Type yes: " ok
[ "$ok" = yes ] || exit 0
aws s3 rm "s3://$(out PageBucketName)" --recursive --only-show-errors
aws s3 rm "s3://$(out SitesBucketName)" --recursive --only-show-errors
aws cloudformation delete-stack --stack-name "$STACK"
aws cloudformation wait stack-delete-complete --stack-name "$STACK"
echo "Deleted. (The small artifacts bucket redhouse-artifacts-* is left; delete it in the S3 console if you like.)"
