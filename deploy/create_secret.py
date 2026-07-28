"""
Create the Chorus bot secret in AWS Secrets Manager from the local .env.

Reads .env directly and hands the values to the AWS CLI via a short-lived
0600 temp file, so the key values never appear in a command line, a log, or an
agent's context. Prints only non-sensitive identifiers.

Usage:  python deploy/create_secret.py
"""

import json
import os
import subprocess
import sys
import tempfile

REGION = "eu-central-1"
SECRET_NAME = "chorus/bot"
KMS_ALIAS = "alias/chorus-bot-secrets"
AWS = r"C:\Users\User\AppData\Local\Programs\Amazon\AWSCLIV2\aws.exe"

HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
ENV_PATH = os.path.join(HERE, ".env")

WANTED = ("OPENAI_API_KEY", "OPENAI_MODEL", "TELEGRAM_TOKEN")


def read_env(path):
    if not os.path.exists(path):
        sys.exit("No .env found at {}".format(path))
    out = {}
    with open(path, encoding="utf-8-sig") as f:
        for line in f:
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            k, v = line.split("=", 1)
            k = k.strip()
            if k in WANTED:
                out[k] = v.strip().strip('"').strip("'")
    missing = [k for k in WANTED if not out.get(k)]
    if missing:
        sys.exit("Missing from .env: {}".format(", ".join(missing)))
    return out


def main():
    values = read_env(ENV_PATH)
    # Report only that we found them, never what they are.
    for k in WANTED:
        print("  found {} ({} chars)".format(k, len(values[k])))

    payload = json.dumps(values)
    tmp = tempfile.NamedTemporaryFile("w", suffix=".json", delete=False, encoding="utf-8")
    try:
        os.chmod(tmp.name, 0o600)
        tmp.write(payload)
        tmp.close()

        cmd = [
            AWS, "secretsmanager", "create-secret",
            "--name", SECRET_NAME,
            "--description", "API keys for the Chorus Telegram bot",
            "--kms-key-id", KMS_ALIAS,
            "--secret-string", "file://{}".format(tmp.name),
            "--tags", "Key=Application,Value=chorus-bot", "Key=ManagedBy,Value=chorus-deploy",
            "--region", REGION,
            "--output", "json",
        ]
        r = subprocess.run(cmd, capture_output=True, text=True)
        if r.returncode != 0:
            # stderr from create-secret does not echo the secret value
            sys.exit("create-secret failed:\n{}".format(r.stderr.strip()[:600]))
        arn = json.loads(r.stdout)["ARN"]
        print("\nSecret created.")
        print("  ARN: {}".format(arn))
    finally:
        try:
            # overwrite before unlinking so the plaintext does not linger
            with open(tmp.name, "w", encoding="utf-8") as f:
                f.write(" " * len(payload))
            os.unlink(tmp.name)
        except OSError:
            pass


if __name__ == "__main__":
    main()
