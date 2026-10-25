import argparse
import os

import boto3
from botocore.config import Config


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Configure CORS on a Backblaze B2 bucket."
    )
    parser.add_argument(
        "--origin",
        action="append",
        required=True,
        help="Allowed browser origin, for example https://fileaway.vercel.app",
    )
    parser.add_argument(
        "--max-age",
        type=int,
        default=3600,
        help="Browser CORS cache lifetime in seconds (0-86400).",
    )
    args = parser.parse_args()

    endpoint_url = os.getenv("B2_ENDPOINT_URL", "").strip()
    region = os.getenv("B2_REGION", "").strip()
    bucket = os.getenv("B2_BUCKET", "").strip()
    key_id = (
        os.getenv("B2_SETUP_KEY_ID", "").strip()
        or os.getenv("B2_KEY_ID", "").strip()
    )
    app_key = (
        os.getenv("B2_SETUP_APP_KEY", "").strip()
        or os.getenv("B2_APP_KEY", "").strip()
    )

    missing = [
        name
        for name, value in (
            ("B2_ENDPOINT_URL", endpoint_url),
            ("B2_REGION", region),
            ("B2_BUCKET", bucket),
            ("B2_SETUP_KEY_ID or B2_KEY_ID", key_id),
            ("B2_SETUP_APP_KEY or B2_APP_KEY", app_key),
        )
        if not value
    ]
    if missing:
        raise SystemExit("Missing environment variables: " + ", ".join(missing))

    if not 0 <= args.max_age <= 86400:
        raise SystemExit("--max-age must be between 0 and 86400")

    origins = [origin.strip().rstrip("/") for origin in args.origin if origin.strip()]
    if not origins or any(origin == "*" for origin in origins):
        raise SystemExit("Use explicit origins; a bare wildcard is not allowed")

    client = boto3.client(
        "s3",
        endpoint_url=endpoint_url,
        region_name=region,
        aws_access_key_id=key_id,
        aws_secret_access_key=app_key,
        config=Config(signature_version="s3v4", s3={"addressing_style": "path"}),
    )

    client.put_bucket_cors(
        Bucket=bucket,
        CORSConfiguration={
            "CORSRules": [
                {
                    "AllowedOrigins": origins,
                    "AllowedMethods": ["GET", "PUT", "HEAD"],
                    "AllowedHeaders": ["content-type"],
                    "ExposeHeaders": ["ETag"],
                    "MaxAgeSeconds": args.max_age,
                }
            ]
        },
    )

    print(f"CORS configured for {', '.join(origins)}")


if __name__ == "__main__":
    main()
