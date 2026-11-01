# Deploy to Render

This project includes a Render Blueprint for one **Free** Python web service. Frontend and API use the same origin; there is no separate Vercel deployment or database.

1. Place the contents of this project at the root of a GitHub repository.
2. Connect that repository to Render and create a Blueprint using `render.yaml`.
3. Confirm the plan is **Free** before deploying. No paid resource is declared.
4. Wait for the service to become live, then visit the assigned HTTPS `onrender.com` URL.
5. Test `/healthz`, then submit a real public TikTok URL and download the resulting file.

The production command uses Gunicorn with **one worker process and eight threads**. Do not increase workers or instances without moving jobs and files to shared storage. Two background download threads run within that process. Restarts interrupt pending downloads and invalidate old links.

Render sets `RENDER_EXTERNAL_HOSTNAME`; the app accepts that exact hostname. For custom domains, add a comma-separated `EXTRA_TRUSTED_HOSTS` environment variable. Do not use wildcards.

Resource limits: 128 MB per video, 512 MB storage budget with reservations for active jobs, four queued/running jobs total, and 20 retained jobs. Finished files expire after one hour and are cleaned every minute. Filesystem storage is temporary and may be lost on redeploy, restart or free-tier sleep. Save downloaded videos to your device promptly.

This is a small public demo: anyone who can open the website can use its shared download quota. Job links are random bearer links; do not share a file link if you want it kept private. This is not a multi-user authenticated service. For larger/public traffic, add authentication, per-user rate limits, shared job storage and persistent object storage before scaling.

Free services sleep after 15 idle minutes and can take about one minute to wake. TikTok may respond differently to datacenter IPs than to your home network. A healthy deployment is not proof TikTok downloads work; test on the deployed service.

Source: https://render.com/docs/deploy-flask and https://render.com/docs/free
