# Deploying the Chorus bot to EC2

Run this in a **fresh Claude Code session**, so the AWS Agent Toolkit's MCP server
and skills are loaded. Confirm with `retrieve_skill` availability before starting;
if the skills are missing, restart again rather than working around them.

**Skills to load:** `aws-secrets-manager` (required before touching the API keys),
`launching-ec2-instance-with-best-practices`, `setting-up-ec2-instance-profiles`,
`aws-iam`.

## Target shape

| Piece | Value |
|---|---|
| Region | `eu-central-1` (default VPC `vpc-02eeca48dc49c502a`) |
| Instance | `t4g.micro`, Amazon Linux 2023, arm64 |
| Secret | `chorus/bot` -> `OPENAI_API_KEY`, `OPENAI_MODEL`, `TELEGRAM_TOKEN` |
| Inbound rules | **none** - long polling is outbound only |
| Shell access | SSM Session Manager (no SSH key, no port 22) |
| Process | `chorus-bot.service`, restart=always |

Estimated cost if free-tier credits do not cover it: **~$7/month**
(~$6 instance + ~$0.70 EBS + $0.40 secret). Free-tier coverage was *not*
confirmed for this account - check before leaving it running.

## Steps

1. **Preconditions.** `aws sts get-caller-identity` succeeds. Credentials last 12h;
   re-run `aws login --region eu-central-1` if expired.
2. **Secret.** Load the `aws-secrets-manager` skill first, then create `chorus/bot`
   with the three values. Read them from the local `.env` - do **not** print them
   into the transcript.
3. **IAM.** Role + instance profile granting `secretsmanager:GetSecretValue` on that
   one secret ARN only, plus `AmazonSSMManagedInstanceCore` for Session Manager.
4. **Deploy key.** The GitHub repo is private, so the instance cannot clone it
   unattended. Generate an ed25519 keypair, add the **public** half to the repo as a
   read-only deploy key (GitHub > Settings > Deploy keys), and place the private half
   at `/root/.ssh/id_ed25519` on the instance. *The user must add the deploy key -
   it is their GitHub account.*
5. **Security group.** No inbound rules. Outbound: allow all (needs Telegram,
   OpenAI, LRCLIB, GitHub).
6. **Launch** with `deploy/user-data.sh` as user data, after filling in its secrets
   section per step 2's skill guidance.
7. **Verify.** `journalctl -u chorus-bot -f` should show
   `Chorus worksheet bot online as @yourchorusbot`.
8. **Stop the local bot.** Telegram allows only ONE poller per token - a second one
   causes 409 conflicts. Kill the local `python bot.py` before or as the instance
   comes up.

## Redeploying later

`git pull && sudo systemctl restart chorus-bot` on the instance.

## Known gotchas

- `av` (PyAV) must resolve an aarch64 wheel; if it tries to build from source, install
  `gcc` + `ffmpeg-devel` or switch the instance to x86_64 (`t3.micro`).
- The account is currently **root**. Create a scoped IAM user before building further.
- In-progress exercises live in memory and reset on restart - expected for now.
