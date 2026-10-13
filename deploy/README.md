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

## Ring webhooks (optional)
The deploy prints the webhook address, `https://xxxx.cloudfront.net/ring/webhook`. Give that to Ring, then in CloudShell run
`bash set-ring-key.sh` and paste the signing key at the hidden prompt. It is stored in AWS Secrets Manager, never in the code.
Until a key is set, the server refuses every webhook.

## Email the report (optional)
The page has an "Email the report" box. It needs Amazon SES, in the same region as the site.
1. SES console > **Identities** > **Create identity** > **Email address**. Use an address you own (for example your Gmail). Open the email AWS sends and click its link. The status must say **Verified**.
2. In CloudShell, in the release folder: `REDHOUSE_EMAIL_FROM=you@example.com bash deploy.sh`. Later runs remember the address.
3. New AWS accounts start in SES **test mode**: mail goes only to addresses you have verified the same way. To send to anyone, SES console > **Account dashboard** > **Request production access** (AWS usually answers within a day). Until then the box tells other visitors to use the download button.

Limits, to stop abuse: 3 reports an hour to one address, 6 an hour per visitor. The address is used for that one message and is not stored.

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
