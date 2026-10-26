#!/usr/bin/env python3
"""Inspect and repair the Backblaze B2 CORS rule used by FileAway's browser upload.

The script is read-only unless --apply is supplied. It uses the B2 Native API
because S3 PutBucketCors is rejected when a bucket contains Native CORS rules.
"""

from __future__ import annotations

import argparse
import base64
import copy
import json
import os
import sys
import urllib.error
import urllib.request
from collections import Counter
from dataclasses import dataclass
from fnmatch import fnmatch
from pathlib import Path
from typing import Any, Iterable
from urllib.parse import quote
from xml.etree import ElementTree

from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parent

AUTHORIZE_URL = (
    "https://api.backblazeb2.com/b2api/v4/b2_authorize_account"
)
NATIVE_API_VERSION = "v4"
DEFAULT_ORIGIN = "https://fileaway-share.vercel.app"
DEFAULT_BUCKET = "fileaway-uploads-storage"
DEFAULT_ENDPOINT = "https://s3.eu-central-003.backblazeb2.com"
DEFAULT_REGION = "eu-central-003"
DEFAULT_MAX_AGE = 3600
DEFAULT_RULE_NAME = "fileaway-vercel-upload"
REQUIRED_METHOD = "PUT"
REQUIRED_HEADER = "content-type"
S3_PUT_OPERATION = "s3_put"

NATIVE_API_NOTE = (
    "B2 stores Native/dashboard CORS rules in the bucket configuration. When "
    "those rules exist, S3 PutBucketCors fails with InvalidRequest. This "
    "script reads the complete corsRules list with b2_list_buckets and updates "
    "only corsRules with b2_update_bucket, preserving every existing rule and "
    "all other bucket settings."
)


class CorsScriptError(RuntimeError):
    """A configuration, permission, or B2 API problem."""


@dataclass(frozen=True)
class CorsSettings:
    endpoint_url: str
    region: str
    bucket: str
    key_id: str
    app_key: str
    key_source: str
    origin: str
    max_age: int


@dataclass(frozen=True)
class B2Authorization:
    account_id: str
    api_url: str
    s3_api_url: str
    authorization_token: str
    capabilities: tuple[str, ...]


@dataclass(frozen=True)
class Diagnosis:
    exact_allowed: bool
    wildcard_allowed: bool
    exact_rule_indexes: tuple[int, ...]
    matching_rule_indexes: tuple[int, ...]


def _region_from_endpoint(endpoint_url: str) -> str:
    host = endpoint_url.split("://", 1)[-1].split("/", 1)[0]
    parts = host.split(".")
    if len(parts) >= 4 and parts[0] == "s3":
        return parts[1]
    return ""


def load_settings(args: argparse.Namespace) -> CorsSettings:
    load_dotenv(ROOT / ".env")

    endpoint = (
        args.endpoint
        or os.getenv("B2_ENDPOINT_URL", "").strip()
        or DEFAULT_ENDPOINT
    ).rstrip("/")
    bucket = (
        args.bucket
        or os.getenv("B2_BUCKET", "").strip()
        or DEFAULT_BUCKET
    )
    region = (
        args.region
        or os.getenv("B2_REGION", "").strip()
        or _region_from_endpoint(endpoint)
        or DEFAULT_REGION
    )
    origin = (args.origin or DEFAULT_ORIGIN).strip().rstrip("/")
    if "*" in origin:
        raise CorsScriptError(
            "--origin must be one exact origin; wildcards are not allowed"
        )
    if not origin.startswith(("http://", "https://")):
        raise CorsScriptError("--origin must start with http:// or https://")

    key_source = ""
    key_id = ""
    app_key = ""
    for id_name, secret_name in (
        ("B2_SETUP_KEY_ID", "B2_SETUP_APP_KEY"),
        ("B2_KEY_ID", "B2_APP_KEY"),
    ):
        candidate_id = os.getenv(id_name, "").strip()
        candidate_key = os.getenv(secret_name, "").strip()
        if candidate_id and candidate_key:
            key_source = f"{id_name}/{secret_name}"
            key_id = candidate_id
            app_key = candidate_key
            break

    if not key_id or not app_key:
        raise CorsScriptError(
            "Missing B2 credentials. Set B2_SETUP_KEY_ID and "
            "B2_SETUP_APP_KEY (preferred), or B2_KEY_ID and B2_APP_KEY, "
            "in the environment or the repository .env file."
        )

    if not endpoint.startswith("https://"):
        raise CorsScriptError("B2_ENDPOINT_URL must start with https://")

    if not 0 <= args.max_age <= 86400:
        raise CorsScriptError("--max-age must be between 0 and 86400")
    if args.timeout <= 0:
        raise CorsScriptError("--timeout must be greater than zero")

    return CorsSettings(
        endpoint_url=endpoint,
        region=region,
        bucket=bucket,
        key_id=key_id,
        app_key=app_key,
        key_source=key_source,
        origin=origin,
        max_age=args.max_age,
    )


def _error_details(body: bytes) -> tuple[str, str]:
    if not body:
        return "UnknownError", ""
    try:
        data = json.loads(body.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError):
        return "UnknownError", body[:300].decode("utf-8", errors="replace")
    if not isinstance(data, dict):
        return "UnknownError", ""
    code = str(data.get("code") or data.get("Code") or "UnknownError")
    message = str(data.get("message") or data.get("Message") or "").strip()
    return code, message


def _native_api_error(
    operation: str,
    status: int,
    body: bytes,
) -> CorsScriptError:
    code, message = _error_details(body)
    detail = f"{code}: {message}" if message else code

    if operation == "b2_authorize_account" and status == 401:
        return CorsScriptError(
            "B2 rejected the setup credentials during b2_authorize_account. "
            "Check B2_SETUP_KEY_ID and B2_SETUP_APP_KEY. "
            f"B2 said: {detail}"
        )

    if "B2 Native CORS rules" in message:
        return CorsScriptError(
            "B2 rejected an S3 CORS write because this bucket contains Native "
            "CORS rules. Use this script's Native API path (b2_update_bucket), "
            "not S3 PutBucketCors. "
            f"B2 said: {detail}"
        )

    if status == 401 or code in {
        "bad_auth_token",
        "expired_auth_token",
        "unauthorized",
    }:
        return CorsScriptError(
            f"B2 denied {operation}. The setup key needs listBuckets and "
            "writeBuckets capabilities. If the key is restricted to this "
            "bucket, use an unrestricted key for this one-time change. "
            f"B2 said: {detail}"
        )

    if status == 403 or code == "transaction_cap_exceeded":
        return CorsScriptError(
            f"B2 rejected {operation}. The account may be over its transaction "
            f"cap. B2 said: {detail}"
        )

    if code in {"bad_bucket_id", "bad_bucket_name"}:
        return CorsScriptError(
            f"B2 could not find the configured bucket during {operation}. "
            f"B2 said: {detail}"
        )

    if status == 409 or code == "conflict":
        return CorsScriptError(
            "The bucket changed while this script was applying CORS. No "
            "further change was made. Re-run the dry run, then --apply. "
            f"B2 said: {detail}"
        )

    return CorsScriptError(
        f"B2 rejected {operation}: {detail}"
    )


def _read_json(request: urllib.request.Request, timeout: float, operation: str):
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            body = response.read()
    except urllib.error.HTTPError as exc:
        raise _native_api_error(operation, exc.code, exc.read()) from exc
    except (urllib.error.URLError, TimeoutError, OSError) as exc:
        raise CorsScriptError(
            f"Could not reach B2 while calling {operation}: "
            f"{type(exc).__name__}"
        ) from exc

    if not body:
        return {}
    try:
        data = json.loads(body.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise CorsScriptError(
            f"B2 returned invalid JSON for {operation}."
        ) from exc
    if not isinstance(data, dict):
        raise CorsScriptError(
            f"B2 returned an unexpected response for {operation}."
        )
    return data


class B2NativeClient:
    def __init__(self, settings: CorsSettings, timeout: float):
        self.settings = settings
        self.timeout = timeout
        self.authorization: B2Authorization | None = None

    @property
    def api_url(self) -> str:
        return self.authorization.api_url if self.authorization else ""

    @property
    def s3_api_url(self) -> str:
        return self.authorization.s3_api_url if self.authorization else ""

    def authorize(self) -> B2Authorization:
        credentials = base64.b64encode(
            f"{self.settings.key_id}:{self.settings.app_key}".encode("utf-8")
        ).decode("ascii")
        request = urllib.request.Request(
            AUTHORIZE_URL,
            method="GET",
            headers={"Authorization": f"Basic {credentials}"},
        )
        data = _read_json(
            request,
            self.timeout,
            "b2_authorize_account",
        )

        api_info = data.get("apiInfo") or {}
        storage_api = (
            api_info.get("storageApi")
            if isinstance(api_info, dict)
            else None
        ) or {}
        if not isinstance(storage_api, dict):
            storage_api = {}

        allowed = storage_api.get("allowed") or data.get("allowed") or {}
        if not isinstance(allowed, dict):
            allowed = {}
        capabilities = tuple(
            str(value)
            for value in (allowed.get("capabilities") or [])
        )

        account_id = str(data.get("accountId") or "").strip()
        api_url = str(
            storage_api.get("apiUrl") or data.get("apiUrl") or ""
        ).rstrip("/")
        s3_api_url = str(
            storage_api.get("s3ApiUrl") or self.settings.endpoint_url
        ).rstrip("/")
        token = str(data.get("authorizationToken") or "").strip()

        if not account_id or not api_url or not token:
            raise CorsScriptError(
                "B2 authorization succeeded but did not return an account ID, "
                "API URL, and authorization token."
            )

        authorization = B2Authorization(
            account_id=account_id,
            api_url=api_url,
            s3_api_url=s3_api_url,
            authorization_token=token,
            capabilities=capabilities,
        )
        self.authorization = authorization
        return authorization

    def _ensure_authorized(self) -> B2Authorization:
        if self.authorization is None:
            return self.authorize()
        return self.authorization

    def _require_capability(self, capability: str, operation: str) -> None:
        capabilities = self._ensure_authorized().capabilities
        if capabilities and "all" not in capabilities:
            if capability not in capabilities:
                raise CorsScriptError(
                    f"B2 denied {operation}: the setup key lacks the "
                    f"{capability} capability. Use a temporary key with "
                    "listBuckets and writeBuckets access."
                )

    def _post(self, operation: str, payload: dict[str, Any]) -> dict[str, Any]:
        authorization = self._ensure_authorized()
        request = urllib.request.Request(
            f"{authorization.api_url}/b2api/"
            f"{NATIVE_API_VERSION}/{operation}",
            method="POST",
            data=json.dumps(payload).encode("utf-8"),
            headers={
                "Authorization": authorization.authorization_token,
                "Content-Type": "application/json",
            },
        )
        return _read_json(request, self.timeout, operation)

    def get_bucket(self, bucket_name: str) -> dict[str, Any]:
        self._require_capability("listBuckets", "b2_list_buckets")
        authorization = self._ensure_authorized()
        data = self._post(
            "b2_list_buckets",
            {
                "accountId": authorization.account_id,
                "bucketName": bucket_name,
            },
        )
        buckets = data.get("buckets")
        if not isinstance(buckets, list):
            raise CorsScriptError(
                "B2 returned an unexpected bucket list while reading CORS."
            )
        target = bucket_name.casefold()
        for bucket in buckets:
            if not isinstance(bucket, dict):
                continue
            if str(bucket.get("bucketName") or "").casefold() == target:
                return copy.deepcopy(bucket)
        raise CorsScriptError(
            f"Bucket {bucket_name!r} was not found for this B2 account."
        )

    def update_cors_rules(
        self,
        bucket_info: dict[str, Any],
        rules: list[dict[str, Any]],
    ) -> dict[str, Any]:
        self._require_capability("writeBuckets", "b2_update_bucket")
        if not isinstance(rules, list):
            raise CorsScriptError("The proposed CORS rules must be a list.")

        bucket_id = str(bucket_info.get("bucketId") or "").strip()
        if not bucket_id:
            raise CorsScriptError(
                "The bucket response did not include a bucketId."
            )

        authorization = self._ensure_authorized()
        payload: dict[str, Any] = {
            "accountId": authorization.account_id,
            "bucketId": bucket_id,
            "corsRules": copy.deepcopy(rules),
        }
        revision = _bucket_revision(bucket_info)
        if revision is not None:
            payload["ifRevisionIs"] = revision

        response = self._post("b2_update_bucket", payload)
        response_rules = response.get("corsRules")
        if response_rules is not None and not isinstance(response_rules, list):
            raise CorsScriptError(
                "B2 returned an unexpected corsRules value after updating."
            )
        return response


def make_native_client(
    settings: CorsSettings,
    timeout: float,
) -> B2NativeClient:
    return B2NativeClient(settings, timeout)


def _bucket_revision(bucket: dict[str, Any]) -> int | None:
    raw_value = bucket.get("revision")
    if raw_value is None:
        return None
    try:
        return int(raw_value)
    except (TypeError, ValueError):
        return None


def extract_cors_rules(bucket: dict[str, Any]) -> list[dict[str, Any]]:
    rules = bucket.get("corsRules")
    if rules is None:
        return []
    if not isinstance(rules, list):
        raise CorsScriptError(
            "B2 returned an unexpected corsRules value for this bucket."
        )
    if not all(isinstance(rule, dict) for rule in rules):
        raise CorsScriptError(
            "B2 returned a CORS rule that is not a JSON object."
        )
    return copy.deepcopy(rules)


def _rule_values(rule: dict[str, Any], key: str) -> list[Any]:
    values = rule.get(key)
    if not isinstance(values, list):
        return []
    return values


def _origin_matches(pattern: str, origin: str) -> bool:
    pattern = str(pattern).strip().rstrip("/")
    origin = origin.strip().rstrip("/")
    if not pattern:
        return False
    if pattern == "*":
        return True
    if pattern.lower() == "https" and origin.lower().startswith("https://"):
        return True
    return fnmatch(origin.lower(), pattern.lower())


def _header_matches(pattern: str, header: str) -> bool:
    pattern = str(pattern).strip().lower()
    header = header.strip().lower()
    if not pattern:
        return False
    return pattern == "*" or fnmatch(header, pattern)


def rule_has_exact_origin(rule: dict[str, Any], origin: str) -> bool:
    target = origin.strip().rstrip("/").lower()
    return any(
        str(candidate).strip().rstrip("/").lower() == target
        for candidate in _rule_values(rule, "allowedOrigins")
    )


def rule_allows(
    rule: dict[str, Any],
    origin: str,
    operation: str,
    header: str,
) -> bool:
    origins = _rule_values(rule, "allowedOrigins")
    operations = {
        str(value).strip()
        for value in _rule_values(rule, "allowedOperations")
    }
    headers = _rule_values(rule, "allowedHeaders")
    return (
        any(_origin_matches(value, origin) for value in origins)
        and operation.strip() in operations
        and any(_header_matches(value, header) for value in headers)
    )


def diagnose(
    rules: Iterable[dict[str, Any]],
    origin: str,
    operation: str,
    header: str,
) -> Diagnosis:
    rule_list = list(rules)
    exact_indexes = tuple(
        index
        for index, rule in enumerate(rule_list)
        if rule_has_exact_origin(rule, origin)
    )
    matching_indexes = tuple(
        index
        for index, rule in enumerate(rule_list)
        if rule_allows(rule, origin, operation, header)
    )
    exact_allowed = any(
        index in exact_indexes for index in matching_indexes
    )
    wildcard_allowed = bool(matching_indexes) and not exact_allowed
    return Diagnosis(
        exact_allowed=exact_allowed,
        wildcard_allowed=wildcard_allowed,
        exact_rule_indexes=exact_indexes,
        matching_rule_indexes=matching_indexes,
    )


def _missing_requirements(
    rule: dict[str, Any],
    origin: str,
    operation: str,
    header: str,
) -> list[str]:
    missing: list[str] = []
    if not any(
        _origin_matches(value, origin)
        for value in _rule_values(rule, "allowedOrigins")
    ):
        missing.append(f"origin {origin}")
    operations = {
        str(value).strip()
        for value in _rule_values(rule, "allowedOperations")
    }
    if operation.strip() not in operations:
        missing.append(f"operation {operation}")
    if not any(
        _header_matches(value, header)
        for value in _rule_values(rule, "allowedHeaders")
    ):
        missing.append(f"header {header.lower()}")
    return missing


def _next_rule_name(rules: Iterable[dict[str, Any]]) -> str:
    names = {
        str(rule.get("corsRuleName") or "")
        for rule in rules
    }
    if DEFAULT_RULE_NAME not in names:
        return DEFAULT_RULE_NAME
    for suffix in range(2, 1000):
        candidate = f"{DEFAULT_RULE_NAME}-{suffix}"
        if candidate not in names:
            return candidate
    raise CorsScriptError(
        "Could not create a unique CORS rule name for the new rule."
    )


def build_fixed_rules(
    rules: Iterable[dict[str, Any]],
    *,
    origin: str,
    operation: str,
    header: str,
    max_age: int,
) -> tuple[list[dict[str, Any]], bool]:
    """Append one exact-origin Native API rule if none permits the request.

    Existing rules are copied unchanged so unrelated origins, operations,
    headers, and bucket settings keep working.
    """
    current = copy.deepcopy(list(rules))
    if diagnose(current, origin, operation, header).exact_allowed:
        return current, False

    if len(current) >= 100:
        raise CorsScriptError(
            "The bucket already has 100 CORS rules. Merge the required origin, "
            "s3_put operation, and content-type header into an existing "
            "rule manually."
        )

    current.append(
        {
            "corsRuleName": _next_rule_name(current),
            "allowedOrigins": [origin],
            "allowedOperations": [operation],
            "allowedHeaders": [header.lower()],
            "exposeHeaders": ["ETag"],
            "maxAgeSeconds": max_age,
        }
    )
    return current, True


def apply_rules(
    client: B2NativeClient,
    bucket_info: dict[str, Any],
    rules: list[dict[str, Any]],
) -> None:
    client.update_cors_rules(bucket_info, rules)


def _canonical_values(values: Iterable[Any], *, lower: bool = False) -> tuple:
    normalized = []
    for value in values:
        text = str(value).strip()
        normalized.append(text.lower() if lower else text)
    return tuple(sorted(normalized))


def _rule_signature(rule: dict[str, Any]) -> tuple:
    raw_max_age = rule.get("maxAgeSeconds")
    try:
        max_age = int(raw_max_age) if raw_max_age is not None else None
    except (TypeError, ValueError):
        max_age = str(raw_max_age)
    return (
        str(rule.get("corsRuleName") or ""),
        _canonical_values(_rule_values(rule, "allowedOrigins")),
        _canonical_values(_rule_values(rule, "allowedOperations")),
        _canonical_values(_rule_values(rule, "allowedHeaders"), lower=True),
        _canonical_values(_rule_values(rule, "exposeHeaders"), lower=True),
        max_age,
    )


def verify_persisted_rules(
    expected: list[dict[str, Any]],
    saved: list[dict[str, Any]],
) -> None:
    if Counter(_rule_signature(rule) for rule in expected) != Counter(
        _rule_signature(rule) for rule in saved
    ):
        raise CorsScriptError(
            "B2 did not persist the complete Native API CORS rule set. "
            "Existing rules or the new rule are missing or changed."
        )


def display_rules(
    rules: list[dict[str, Any]],
    revision: int | None,
) -> None:
    suffix = f" (bucket revision {revision})" if revision is not None else ""
    print(f"Existing Native API CORS rules: {len(rules)}{suffix}")
    if rules:
        print(json.dumps(rules, indent=2, sort_keys=True))


def print_diagnosis(
    rules: list[dict[str, Any]],
    settings: CorsSettings,
) -> Diagnosis:
    diagnosis = diagnose(
        rules,
        settings.origin,
        S3_PUT_OPERATION,
        REQUIRED_HEADER,
    )

    if diagnosis.exact_allowed:
        indexes = ", ".join(
            str(index + 1)
            for index in diagnosis.matching_rule_indexes
            if index in diagnosis.exact_rule_indexes
        )
        print(
            "Diagnosis: OK. An exact-origin Native API rule permits "
            f"s3_put with content-type (rule {indexes})."
        )
        return diagnosis

    if diagnosis.wildcard_allowed:
        indexes = ", ".join(
            str(index + 1) for index in diagnosis.matching_rule_indexes
        )
        print(
            "Diagnosis: a wildcard-origin rule technically permits "
            f"s3_put with content-type (rule {indexes}), but no "
            "exact-origin rule does. Applying will add an explicit rule."
        )
        return diagnosis

    if diagnosis.exact_rule_indexes:
        for index in diagnosis.exact_rule_indexes:
            missing = _missing_requirements(
                rules[index],
                settings.origin,
                S3_PUT_OPERATION,
                REQUIRED_HEADER,
            )
            print(
                f"Diagnosis: rule {index + 1} has the exact origin but is "
                f"missing: {', '.join(missing)}."
            )
    else:
        print(
            "Diagnosis: no Native API rule explicitly contains the exact "
            "origin with s3_put and content-type. This is why the "
            "browser preflight is rejected."
        )
    return diagnosis


def probe_preflight(
    settings: CorsSettings,
    *,
    timeout: float,
    endpoint_url: str | None = None,
) -> tuple[bool | None, str]:
    """Send a harmless OPTIONS request and return (allowed, summary)."""
    base_url = (endpoint_url or settings.endpoint_url).rstrip("/")
    url = f"{base_url}/{quote(settings.bucket)}/__fileaway_cors_probe__"
    request = urllib.request.Request(url, method="OPTIONS")
    request.add_header("Origin", settings.origin)
    request.add_header("Access-Control-Request-Method", REQUIRED_METHOD)
    request.add_header("Access-Control-Request-Headers", REQUIRED_HEADER)

    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            status = response.status
            headers = response.headers
            body = response.read(8192)
    except urllib.error.HTTPError as exc:
        status = exc.code
        headers = exc.headers
        body = exc.read(8192)
    except (urllib.error.URLError, TimeoutError, OSError) as exc:
        return None, f"Could not run preflight probe: {type(exc).__name__}"

    allow_origin = (headers.get("access-control-allow-origin") or "").strip()
    allow_methods = (headers.get("access-control-allow-methods") or "").strip()
    allow_headers = (headers.get("access-control-allow-headers") or "").strip()
    code = ""
    message = ""
    if body:
        try:
            root = ElementTree.fromstring(body)
            code = (root.findtext("Code") or "").strip()
            message = (root.findtext("Message") or "").strip()
        except ElementTree.ParseError:
            pass

    origin_ok = any(
        _origin_matches(value, settings.origin)
        for value in allow_origin.split(",")
    )
    methods_ok = any(
        _header_matches(value, REQUIRED_METHOD)
        for value in allow_methods.split(",")
    )
    headers_ok = any(
        _header_matches(value, REQUIRED_HEADER)
        for value in allow_headers.split(",")
    )
    allowed = (
        200 <= status < 300
        and origin_ok
        and methods_ok
        and headers_ok
    )

    details = [
        f"HTTP {status}",
        f"allow-origin={allow_origin or '<missing>'}",
        f"allow-methods={allow_methods or '<missing>'}",
        f"allow-headers={allow_headers or '<missing>'}",
    ]
    if code or message:
        details.append(f"B2 {code}: {message}".strip())
    return allowed, "; ".join(details)


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Diagnose and repair B2 Native API CORS for FileAway direct "
            "uploads. Read-only unless --apply is supplied."
        )
    )
    parser.add_argument(
        "--apply",
        action="store_true",
        help="Append the missing Native API CORS rule if needed.",
    )
    parser.add_argument(
        "--origin",
        default=DEFAULT_ORIGIN,
        help=f"Exact browser origin (default: {DEFAULT_ORIGIN}).",
    )
    parser.add_argument(
        "--endpoint",
        default="",
        help="Override B2_ENDPOINT_URL for the preflight probe.",
    )
    parser.add_argument(
        "--region",
        default="",
        help="Override B2_REGION for display.",
    )
    parser.add_argument(
        "--bucket",
        default="",
        help="Override B2_BUCKET.",
    )
    parser.add_argument(
        "--max-age",
        type=int,
        default=DEFAULT_MAX_AGE,
        help=f"New rule preflight cache seconds (default: {DEFAULT_MAX_AGE}).",
    )
    parser.add_argument(
        "--no-preflight",
        action="store_true",
        help="Skip the live OPTIONS probe.",
    )
    parser.add_argument(
        "--timeout",
        type=float,
        default=20.0,
        help="B2 API and preflight probe timeout in seconds (default: 20).",
    )
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    try:
        settings = load_settings(args)
    except CorsScriptError as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 2

    print(f"Bucket:   {settings.bucket}")
    print(f"Origin:   {settings.origin}")
    print(f"Key:      {settings.key_source} (value hidden)")
    print()

    try:
        client = make_native_client(settings, args.timeout)
        bucket_info = client.get_bucket(settings.bucket)
        rules = extract_cors_rules(bucket_info)
    except CorsScriptError as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 2

    revision = _bucket_revision(bucket_info)
    if client.api_url:
        print(f"Native API: {client.api_url}")
        print()
    display_rules(rules, revision)
    print()
    print(NATIVE_API_NOTE)
    print()
    diagnosis = print_diagnosis(rules, settings)
    print()

    preflight_allowed: bool | None = None
    preflight_details = ""
    if not args.no_preflight:
        preflight_allowed, preflight_details = probe_preflight(
            settings,
            timeout=args.timeout,
            endpoint_url=client.s3_api_url or settings.endpoint_url,
        )
        if preflight_allowed is None:
            print(f"Preflight probe: SKIPPED ({preflight_details})")
        elif preflight_allowed:
            print(f"Preflight probe: ALLOWED ({preflight_details})")
        else:
            print(f"Preflight probe: REJECTED ({preflight_details})")

    if diagnosis.exact_allowed:
        if preflight_allowed is False:
            print(
                "\nThe Native API rules look correct, but the live preflight "
                f"is still rejected ({preflight_details}). Check the "
                "endpoint/bucket, wait for the browser's CORS cache to expire, "
                "or use --no-preflight if the probe itself is blocked.",
                file=sys.stderr,
            )
            return 1
        print("\nNo rule change is needed.")
        return 0

    try:
        fixed_rules, changed = build_fixed_rules(
            rules,
            origin=settings.origin,
            operation=S3_PUT_OPERATION,
            header=REQUIRED_HEADER,
            max_age=settings.max_age,
        )
    except CorsScriptError as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 2
    if not changed:
        print("\nNo rule change is needed.")
        return 0

    print(
        "\nPlanned change: append a Native API CORS rule named "
        f"{fixed_rules[-1]['corsRuleName']!r} for {settings.origin} with "
        "s3_put and content-type. Existing rules and bucket settings "
        "are preserved."
    )
    if not args.apply:
        print("\nDry run only. Re-run with --apply to update this bucket.")
        return 1

    try:
        apply_rules(client, bucket_info, fixed_rules)
        saved_bucket = client.get_bucket(settings.bucket)
        saved_rules = extract_cors_rules(saved_bucket)
    except CorsScriptError as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 2

    print("\nRe-reading Native API CORS rules after apply...")
    saved_revision = _bucket_revision(saved_bucket)
    display_rules(saved_rules, saved_revision)
    saved_diagnosis = diagnose(
        saved_rules,
        settings.origin,
        S3_PUT_OPERATION,
        REQUIRED_HEADER,
    )
    if not saved_diagnosis.exact_allowed:
        print(
            "ERROR: B2 did not persist an exact-origin rule allowing "
            "s3_put and content-type.",
            file=sys.stderr,
        )
        return 2

    try:
        verify_persisted_rules(fixed_rules, saved_rules)
    except CorsScriptError as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 2

    print(
        "Verification: OK. All existing rules and the new exact-origin rule "
        "are present."
    )

    if not args.no_preflight:
        allowed, details = probe_preflight(
            settings,
            timeout=args.timeout,
            endpoint_url=client.s3_api_url or settings.endpoint_url,
        )
        if allowed is False:
            print(
                "WARNING: rules were saved, but the live preflight is still "
                f"rejected ({details}). Check the endpoint/bucket or wait for "
                "the browser's CORS cache to expire. The script did not change "
                "any bucket settings beyond corsRules.",
                file=sys.stderr,
            )
            return 1
        if allowed is True:
            print(f"Post-apply preflight: ALLOWED ({details})")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
