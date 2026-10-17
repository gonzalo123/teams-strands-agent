# Running the PoC

The article is in [README.md](README.md). This file contains operational setup.

## Local demo without cloud credentials

Requires Docker with Compose. From the repository root:

```bash
ENV_FILE=.env.demo docker compose --profile demo up --build -d
ENV_FILE=.env.demo docker compose exec api teams-agent demo-request --activity-id example-1
ENV_FILE=.env.demo docker compose --profile demo logs -f worker sink
```

The API should acknowledge in less than five seconds. The demo agent deliberately waits eight seconds; afterwards the sink logs an Adaptive Card. No LLM or Teams service is involved in this mode. These logs contain demo message content.

If host ports are occupied, prefix Compose commands with `REDIS_PORT=16379 API_PORT=18080` (or other free ports). Container-to-container addresses stay the same; local Python clients need the corresponding host ports in their configuration.

Repeat the same `demo-request --activity-id example-1`: it returns the same reference and does not add a second job while the original job is retained.

Proactive sending, with and without a mention:

```bash
ENV_FILE=.env.demo docker compose exec api teams-agent send "Summarize checkout status"
ENV_FILE=.env.demo docker compose exec api teams-agent send "Prepare an incident update" \
  --mention-id ada@example.com --mention-name Ada
ENV_FILE=.env.demo docker compose exec api teams-agent status JOB_ID_FROM_SEND
```

To demonstrate independent worker lifetime, stop the worker, send a request, then start it:

```bash
ENV_FILE=.env.demo docker compose stop worker
ENV_FILE=.env.demo docker compose exec api teams-agent demo-request --activity-id worker-stopped
ENV_FILE=.env.demo docker compose start worker
```

Stop the stack with `ENV_FILE=.env.demo docker compose --profile demo down`. Redis data remains in the named volume. Do not use the demo signing key with a public Teams endpoint. Before switching this same Compose project to real credentials, drain demo jobs or use a separate Compose project (`-p teams-real`) and stop the demo to free its host ports.

## Real Teams configuration

This uses no bot registration or Entra client secret. Your account must nevertheless be allowed to create an outgoing webhook and a Workflow. If either is blocked by tenant policy, this implementation cannot bypass that restriction.

1. Copy `.env.example` to `.env`.
2. In the target channel, open Workflows and create **Post to a channel when a webhook request is received**, or an equivalent flow using **When a Teams webhook request is received**.
3. For this PoC, select **Anyone** for the trigger's allowed callers. The Python sender does not acquire an Entra token. Treat the generated URL, including its signature, as a secret. If policy only permits authenticated triggers, an authorized identity and a token-acquisition implementation are required; neither is supplied here. Microsoft describes the [trigger authentication options](https://learn.microsoft.com/en-us/connectors/teams/#when-a-teams-webhook-request-is-received).
4. Set the destination Team and channel in the workflow. Post as **Flow bot** using **Post card in a chat or channel**. The standard template iterates `attachments` and passes each attachment's `content` as the card. Preserve that whole object, including `msteams.entities`; do not reconstruct it from only the text. Use a posting action, not “post and wait for a response”.
5. Save the flow, copy its URL into `TEAMS_WORKFLOW_URL`, and add a co-owner if this will outlive the experiment. Your Microsoft 365 connection must remain valid.
6. Expose API port 8000 through an HTTPS reverse proxy or a development tunnel. The public callback will be `https://YOUR_HOST/webhooks/teams`. Compose binds the API to localhost; your tunnel/proxy connects to that local port. Configure ingress body and request-time limits as well as the application's limits.
7. In Teams, select the Team → Manage team → Apps → Create an outgoing webhook (wording can vary with the client). Enter the callback URL and a name such as `Assistant`. Copy its HMAC token into `TEAMS_HMAC_SECRET` without decoding or re-encoding it.
8. Set `TEAMS_TENANT_ID`, `TEAMS_TEAM_ID` and `TEAMS_CHANNEL_ID`. These must match `channelData.tenant.id`, `channelData.team.id` and `channelData.channel.id` in a Teams activity. The channel's “Get link” provides its channel thread ID and tenant ID. The team's link provides its team thread ID. A Teams thread ID commonly starts with `19:`; **the `groupId` GUID in a channel URL is not interchangeable with `channelData.team.id`**. URL-decode IDs taken from links. If IDs do not match, inspect a signed activity locally with a debugger; avoid logging message bodies or secrets on a public endpoint.
9. Configure Bedrock access and start the stack: `docker compose up --build -d`.
10. In the configured public channel, type `@Assistant What is the status of checkout?`. Expect an immediate acknowledgement and a later new message with explicitly fictional service metrics. A mention in another channel is rejected. Private chats are unsupported by this outgoing webhook.

The template UI and tenant availability can differ. Refer to Microsoft's [outgoing webhook setup](https://learn.microsoft.com/en-us/microsoftteams/platform/webhooks-and-connectors/how-to/add-outgoing-webhook) and [Workflows setup](https://learn.microsoft.com/en-us/microsoftteams/platform/webhooks-and-connectors/how-to/add-incoming-webhook).

## Bedrock and local Python

Use Poetry 2.3.2 for local development, matching the version pinned in the Dockerfile and used to produce the lockfile.

The real backend uses the normal boto3 credential chain. Choose an available model or inference profile in your AWS region and set `BEDROCK_MODEL_ID`; the example uses an EU Claude Sonnet inference profile. The AWS identity needs access to invoke that model and its profile destinations. No credentials are included in the image.

For containers, supply temporary `AWS_ACCESS_KEY_ID`, `AWS_SECRET_ACCESS_KEY` and `AWS_SESSION_TOKEN` in `.env`, or use a workload role in a deployed environment. The Compose file does not mount your host's AWS configuration. For local Poetry processes, `AWS_PROFILE` can select your existing profile.

```bash
poetry install
docker compose up -d redis
poetry run teams-agent api
# In another terminal:
poetry run teams-agent worker
```

The local `.env` Redis URL points at localhost; Compose overrides it to `redis`. Model invocation occurs only in the worker. To try real Teams independently of AWS, set `AGENT_BACKEND=demo` while keeping the real workflow URL and Teams signing key. The generated message will clearly say it is a local demo.

## Investigating the simulated incident with Strands

Set `AGENT_BACKEND=bedrock` in `.env`, with working AWS credentials and access to `BEDROCK_MODEL_ID` in the configured region. Restart the API and worker after changing configuration. The `demo` backend is still the transport-only echo; it does not run the incident investigation.

The fixture is `src/teams_agent/data/incident.json`. It contains a degraded `checkout` service and a healthy `catalog` service, with metrics, deployment histories and runbooks. All values are fictional; relative times belong to a fixed snapshot. Inspect the data without contacting AWS or Teams:

```bash
poetry run teams-agent scenario
```

With **Teams: API + worker** running in VS Code, send these messages in the configured channel, selecting your actual outgoing webhook mention (for example, `@Assistant`):

```text
@Assistant Checkout está lento. Investiga sus métricas, despliegues recientes y procedimiento de diagnóstico.
@Assistant Compara checkout y catalog. ¿Qué servicio necesita investigación y por qué?
@Assistant Investiga el servicio payments.
```

For checkout, look for a simulation label, the 840/210 ms latency comparison, 2.4 percent errors, pool saturation, the recent pool-size change and concrete checks before any mitigation. Deployment correlation should remain a hypothesis. Catalog's snapshot shows metrics near baseline. Payments is unavailable; the agent should say so rather than invent data.

The worker logs tool execution as `Incident tool: metrics service=checkout`, `deployments`, and `runbook`. Tool calls for different services can execute in different orders. A fresh agent is created for each request, so include the service name in each message.

For the proactive path with the VS Code development Redis:

```bash
REDIS_URL=redis://127.0.0.1:16379/0 poetry run teams-agent send \
  "Investigate checkout using metrics, deployments and the runbook. Prepare a short incident update."
```

To mention a user, append `--mention-id USER_UPN_OR_ENTRA_ID --mention-name "Display Name"`. The CLI enqueues the same investigation used for reactive messages; the worker publishes the final report through the configured workflow. With Compose, run the equivalent `docker compose exec api teams-agent send ...` so the producer uses that stack's Redis.

The incident tools perform read-only local lookups. They have no deployment or rollback capability, and their runbooks explicitly require human approval for a proposed mitigation. The live model's tool selection and conclusions still need manual verification with your configured Bedrock model.

## Running and debugging in VS Code

Open the repository folder in VS Code, install the recommended Python and Python Debugger extensions, and run `poetry install` once. Configure the root `.env` and make sure Docker is running.

In **Run and Debug**, select **Teams: API + worker** and press **F5**. The launcher first starts a dedicated Redis service with Compose project name `teams-vscode`, waits until it is healthy, then launches the API and worker in separate integrated terminals using `.venv/bin/python`. The API listens at `http://127.0.0.1:8000`; Redis is published at `127.0.0.1:16379`. Both Python processes load `.env`, with `REDIS_URL` overridden to this development Redis instance.

Set API breakpoints in `src/teams_agent/api.py`, and job/agent breakpoints in `src/teams_agent/jobs.py` or `src/teams_agent/agent.py`. The worker launcher enables subprocess debugging and sets `JOB_TIMEOUT=600` for new jobs, allowing up to ten minutes for debugging. Pausing the webhook can still exceed Teams' five-second deadline.

To enqueue work from another terminal into the same development queue:

```bash
REDIS_URL=redis://127.0.0.1:16379/0 poetry run teams-agent send "Summarize checkout status"
```

This uses the backend and workflow destination from your `.env`. Stopping the compound stops both Python sessions and leaves Redis and its data available for the next run. Use **Terminal → Run Task → Teams: stop Redis** when finished. You can also launch **Teams: API** or **Teams: Worker** individually after running **Teams: start Redis** from the task menu.

## Checks and operational limits

```bash
poetry run pytest -q
poetry run ruff check src
poetry run ruff format --check src
```

Tests use an in-memory Redis substitute with RQ and mocked outbound HTTP, including retry scheduling and answer reuse. Incident tests also run the real Strands agent/tool loop through the worker for reactive and proactive requests, with a scripted test model replacing Bedrock. This verifies execution and delivery wiring, not live-model reasoning quality. The Compose demo exercises real Redis, a separate worker process, and HTTP delivery. Neither validates your tenant policy, card rendering, real mention notification or Bedrock permissions.

Before presenting the real demo, verify a plain proactive post, a proactive post with a mention to a real user, a reactive request, the workflow run's Teams action, and the configured model. A successful trigger response alone is not evidence of a visible Teams message.

`teams-agent status JOB_ID` reports queue state and workflow acceptance. Failed jobs remain in RQ's failed registry for `RETENTION_SECONDS` (default one day). Inspect via RQ's CLI with the same Redis URL and JSON serializer; for example, `rq info --url redis://localhost:6379/0`. A manual requeue requires operator judgement because an earlier send may have succeeded. Queued jobs have no expiry and can accumulate while a worker is stopped.

`JOB_TIMEOUT` defaults to 180 seconds. The worker runs the scheduler needed for 10/30/60-second delayed retries. It uses spawned child processes, so a slow model can be terminated at the job deadline. A hard host/worker crash is different from an ordinary job exception: monitor RQ's started/failed registries and abandoned-job cleanup. Recovery is not instant, and not every crash produces an automatic retry.

The queue stores prompts, answers and user identifiers. It uses JSON serialization, but only trusted infrastructure should have Redis access: queue contents still select executable worker functions. Redis is published to loopback for local development, uses a named volume and AOF `appendfsync always`, and has no password in this local setup. Production needs a private authenticated broker, backups, retention, capacity limits and monitoring. `/health` is a process liveness check, not a worker or Redis readiness check.

HTTP request logging must not expose the workflow's secret URL. The sender converts transport/status errors to sanitized exceptions and does not follow redirects. Delivery retries use a fixed bounded schedule, including for permanent HTTP errors; they do not yet honor `Retry-After`. End-to-end exactly-once delivery, workflow completion callbacks, conversational memory and automatic failure notices to channel users are outside this PoC.
