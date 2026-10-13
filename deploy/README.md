# Deploy RedHouse to AWS (about 15 minutes, nothing to install)

You need: your AWS account, and the file `redhouse-release.tgz`.

1. Sign in to the AWS console and set the region (top right) to **Asia Pacific (Sydney)**.
2. Open **CloudShell** (the `>_` icon in the top bar). Wait for the prompt.
3. In CloudShell choose **Actions > Upload file**, upload `redhouse-release.tgz`, then run:
   ```
   mkdir -p redhouse && tar xzf redhouse-release.tgz -C redhouse && cd redhouse && bash deploy.sh
   ```
4. Wait. It prints `RedHouse is live: https://xxxx.cloudfront.net`. Open that address.
5. Check `https://xxxx.cloudfront.net/api/explain`. You want `"source":"bedrock"`.

## Update later
Upload the new `redhouse-release.tgz` the same way and run the same command. It ships the new code and page in about two minutes.

## Delete everything (stops all cost)
`bash teardown.sh` in the same folder, then type `yes`.

## What it costs
One `t3.medium` server (about US$0.05 per hour, so roughly US$35 a month if left running), plus cents for CloudFront, S3 and Bedrock. Your credits cover it. Run `teardown.sh` when judging is over.

## Good to know
- Uploaded sites are kept as extracted shapes only (never the picture), in a private encrypted bucket, and deleted after 30 days.
- The server has no SSH and no key. To look inside it: EC2 console > the instance `redhouse-server` > Connect > Session Manager. Setup log: `sudo tail -50 /var/log/redhouse-setup.log`.
- Visitors always use HTTPS. Between CloudFront and the server the traffic stays inside AWS but is plain HTTP, and only CloudFront can reach the server.
- Ring tokens are never stored: the browser sends the token with each request and the server only passes it to Ring.
