# RedHouse

**Where intruders get past your cameras.**

RedHouse takes a site's cameras, works out what they really cover, simulates intruders walking in, and tells you the smallest changes that close the gaps. Built for the Build, Ship, Shape: Amazon Developer Hackathon (Ring track).

## Live demo

https://d2ac21lfxsl912.cloudfront.net

Runs on AWS (CloudFront, S3 and EC2). The demo house loads straight away. No sign-in is needed.

## What it does

- **Coverage map.** Shows what each camera sees, where the blind spots are, and how likely a person is to be caught at each point.
- **Intruder simulation.** 200 simulated intruders walk from the edge of the property to the doors. Three behaviours are tried: an opportunist, someone who avoids cameras, and someone who goes for the side and rear. Walls and the house block the view. Detection gets weaker with distance.
- **Smallest fixes.** Finds the fewest camera turns, and the best place for a new camera, that cut the number of intruders who get in unseen.
- **Checked on new intruders.** Every fix is tested on a separate set of intruders it was not tuned on, so the gain is not a fluke.
- **Camera outage.** Shows what happens to each door if a camera goes offline.
- **Your own site.** Upload an outdoor site plan or an indoor floor plan.
- **Report.** A PDF of the findings, the fixes and how the numbers were worked out. Download it, or email it.
- **Plain-words summary.** Written by an AI model on Amazon Bedrock. Every number in it is checked against the simulator's own figures. If one doesn't match, the text is thrown away and a template is used instead. The page says which one you are reading.
- **Ring link.** Connect a Ring token, link Ring devices to cameras on the plan, and a linked camera takes its online or offline state from Ring. If several devices are found, one button links them all.
- **Measure a camera from its picture.** Ring does not say where a camera is mounted or what it sees. Match 5 spots on the ground in the camera's picture to the same spots on the plan, and RedHouse works out the exact ground the camera covers instead of guessing it. It can also estimate where the camera is mounted, how high, and which way it points. The fifth spot checks the other four, and a view cannot be saved without it. The demo cameras have a sample picture drawn from the demo house, so you can try it.

There is no machine learning in the simulation. It uses geometry and shortest paths, so every result can be explained.

## What is real and what is simulated

- 1 real Ring sandbox device, linked to camera C1 through Ring's developer API. Its online status comes from Ring. The sandbox device is a test device that Ring provides, not a camera on a wall.
- Every other camera, and the demo house itself, is simulated.
- The intruders are simulated.
- The sample pictures are drawings of the demo house, not photographs.

## Ring: what is read

| Data | Where it comes from | Used for |
|---|---|---|
| Device list | `GET /v1/devices` | Linking a Ring device to a camera on the plan |
| Online or offline | `GET /v1/devices/{id}/status` | Setting the linked camera's state |
| Motion settings and zones | `GET /v1/devices/{id}/configurations` | Showing how the camera is set up |
| Latest saved picture | `POST /v1/devices/{id}/media/image/download` | Measuring the camera's view |

Ring does not supply camera position, direction or field of view. The token is pasted into the page, kept in memory only, and sent with each request. The server passes it to Ring and never stores or logs it.

## Run it on your computer

You need Python 3.12 or newer and Node.js.

Backend (first window):

```powershell
cd backend
python -m venv venv
.\venv\Scripts\Activate.ps1
pip install -r requirements.txt
uvicorn app.main:app --reload
```

Frontend (second window):

```powershell
cd frontend
npm install
npm run dev
```

Open http://localhost:5173. The page talks to the backend on port 8000 by itself. The API test page is at http://127.0.0.1:8000/docs.

Run the tests:

```powershell
cd backend
.\venv\Scripts\Activate.ps1
python -m pytest -q
```

## Put it on AWS

Build the release file, then follow [deploy/README.md](deploy/README.md):

```bash
bash deploy/build_release.sh
```

This makes `redhouse-release.tgz`. Upload it to AWS CloudShell, extract it, and run `bash deploy.sh`. The stack is CloudFront in front of S3 (the page) and one EC2 server (the API), with Amazon Bedrock for the summary and Amazon SES for report emails. It costs about US$35 a month while running. `bash teardown.sh` removes everything.

## What is in the repo

| Folder | What it holds |
|---|---|
| `engine/` | The simulation: coverage, intruders, planner, outage, calibration, indoor planner |
| `backend/` | The API (FastAPI), the Ring reader, the report builder, tests |
| `frontend/` | The page (React and Vite) |
| `scenarios/` | The demo house |
| `deploy/` | The AWS template and the deploy scripts |
| `tools/` | Draws the sample pictures for the demo cameras |

## Privacy

- A picture is never stored. An uploaded picture stays in your browser. A Ring picture passes through the server on its way to you and is not kept.
- An uploaded plan is kept only as the extracted shapes, in a private encrypted bucket, and deleted after 30 days.
- The summary model sees only rounded figures and cleaned camera names. It never sees a picture, a Ring token or a site id.
- The email address you type for a report is used for that one message and is not stored.

## Limits

- The numbers are a model, not a guarantee. Cameras are assumed to detect people with a chance that falls away with distance, and intruders are simulated, not recorded.
- Fetching the latest picture from Ring is tested against a simulated Ring. It has not been confirmed on the real sandbox device, which may have no saved picture. Uploading a screenshot always works.
- Camera placement from a picture assumes a lens width (adjustable, default 100°) and reports how far off it could be.
- Report emails need a verified sender and recipient while the AWS account is in email test mode, and may land in spam. The download always works.
- The indoor planner treats a measured camera as a wedge in places, while coverage still uses the measured view.

## Licence

MIT (see [LICENSE](LICENSE)). The PDF features use PyMuPDF, which is licensed AGPL-3.0. If you redistribute a build that includes it, follow its terms.
