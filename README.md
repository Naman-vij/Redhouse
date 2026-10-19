
## Live demo

https://d2ac21lfxsl912.cloudfront.net

Runs on AWS (CloudFront, S3 and EC2). The demo house loads straight away. No sign-in is needed.

## What is real and what is simulated

- 1 real Ring sandbox device, linked to camera C1 through Ring's developer API. Its online status comes from Ring.
- Every other camera, and the demo house itself, is simulated.
- Ring does not give camera positions, so RedHouse can measure a camera's view from its picture instead of guessing it. The demo has a sample picture drawn from the demo house, which is not a real camera.
