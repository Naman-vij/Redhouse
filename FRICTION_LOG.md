# RedHouse Friction Log

Team: Naman and Ibrahim
Project: RedHouse, a red-team digital twin for camera setups (Ring track, AWS Builder mini challenge)
Tools used: Ring Partner API and Developer Playground, Amazon Bedrock (Amazon Nova 2 Lite)

---

## Entry 1: Bedrock model availability

- **Task attempted:** Use our first-choice model on Amazon Bedrock to generate RedHouse's plain-language security reports.
- **Steps taken:** Claimed the hackathon AWS credits, opened Bedrock in our account, and tried to call the model.
- **Expected:** The model would be available to call once the credits were applied.
- **Actual:** Our first-choice model was not available on our account.
- **Severity:** Medium
- **Workaround:** Switched to Amazon Nova 2 Lite and kept the model ID in configuration so it can be swapped.
- **Suggestion:** Show which models are available per account and region before the credit claim, and say which models need an access request.

## Entry 2: Short-lived Ring Playground token

- **Task attempted:** Keep a Playground device connected to our app with a token.
- **Steps taken:** Generated a token in the Ring developer portal, pasted it into our Connect Ring panel, and polled the API every 30 seconds.
- **Expected:** A stable connection for the whole development period.
- **Actual:** Access tokens are short-lived (about 4 hours per the Ring docs), so the connection lapsed and had to be regenerated.
- **Severity:** Low
- **Workaround:** Generated a fresh token before each working session and before recording the demo.
- **Suggestion:** Offer a refresh action or a longer-lived Playground token, and show the expiry time next to the token.

## Entry 3: No motion events from the Playground

- **Task attempted:** Receive a motion event from a Playground device to calibrate our camera model against what the simulation predicted.
- **Steps taken:** Polled device status every 30 seconds and used "Check now" in our app.
- **Expected:** A way to trigger or receive simulated motion and doorbell events.
- **Actual:** We could read device status (online and last reported time), but could not get simulated motion events.
- **Severity:** High
- **Workaround:** Built the calibration flow to use whatever data the API returns, such as device status and snapshots, and designed it to accept motion events from real hardware.
- **Suggestion:** Add a way to trigger simulated events or deliver webhooks in the Playground, so developers can test event handlers without physical devices.

## Entry 4: Ring documentation split across sites

- **Task attempted:** Find the Developer Playground and the token steps mentioned in the hackathon rules.
- **Steps taken:** Started at developer.ring.com, then searched for the Playground and authentication pages.
- **Expected:** A direct link from the portal to the Playground and token instructions.
- **Actual:** The documentation is spread across developer.ring.com and developer.amazon.com/docs/ring, and we had to search to find the right pages.
- **Severity:** Low
- **Workaround:** Searched the docs and bookmarked the pages we needed.
- **Suggestion:** Link the Playground and token steps from the developer portal home page.

---

# Feature Requests

## 1. Webhook and motion-event delivery in the Developer Playground
- **Description:** Deliver simulated webhooks and motion events in the Playground, so event handlers can be tested end to end without hardware.
- **Why it matters:** RedHouse compares predicted camera detections with real motion events to calibrate its camera model. Without simulated events we could only use device status.
- **Priority:** Critical

## 2. Camera placement and field-of-view metadata in the Devices API
- **Description:** Expose the camera's mounting angle, field of view, and configured motion-detection zone as read-only fields.
- **Why it matters:** RedHouse's digital twin needs each camera's real coverage. Today users must enter it by hand, which makes blind-spot analysis less accurate.
- **Priority:** Important

## 3. Longer-lived or
