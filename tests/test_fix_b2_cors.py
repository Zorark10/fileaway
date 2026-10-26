import argparse
import base64
import copy
import io
import json
import urllib.error

import pytest

import fix_b2_cors as cors

ORIGIN = "https://fileaway-share.vercel.app"


def make_native_rule(
    *,
    name="existing-rule",
    origins=None,
    operations=None,
    headers=None,
    expose_headers=None,
    max_age=3600,
):
    return {
        "corsRuleName": name,
        "allowedOrigins": origins or [ORIGIN],
        "allowedOperations": operations or [cors.S3_PUT_OPERATION],
        "allowedHeaders": headers if headers is not None else ["content-type"],
        "exposeHeaders": (
            expose_headers if expose_headers is not None else ["ETag"]
        ),
        "maxAgeSeconds": max_age,
    }


def make_bucket(rules=None, *, revision=7, **overrides):
    bucket = {
        "accountId": "account-id",
        "bucketId": "bucket-id",
        "bucketName": cors.DEFAULT_BUCKET,
        "bucketType": "allPrivate",
        "bucketInfo": {"site": "fileaway"},
        "corsRules": copy.deepcopy(rules or []),
        "lifecycleRules": [],
        "revision": revision,
    }
    bucket.update(overrides)
    return bucket


def make_settings(**overrides):
    values = {
        "endpoint_url": cors.DEFAULT_ENDPOINT,
        "region": cors.DEFAULT_REGION,
        "bucket": cors.DEFAULT_BUCKET,
        "key_id": "test-key-id",
        "app_key": "test-app-key",
        "key_source": "B2_SETUP_KEY_ID/B2_SETUP_APP_KEY",
        "origin": ORIGIN,
        "max_age": 3600,
    }
    values.update(overrides)
    return cors.CorsSettings(**values)


def make_args(**overrides):
    values = {
        "endpoint": "",
        "bucket": "",
        "region": "",
        "origin": ORIGIN,
        "max_age": 3600,
        "timeout": 20.0,
    }
    values.update(overrides)
    return argparse.Namespace(**values)


def make_auth_response(capabilities=None):
    return {
        "accountId": "account-id",
        "authorizationToken": "test-authorization-token",
        "apiInfo": {
            "storageApi": {
                "apiUrl": "https://api001.backblazeb2.com",
                "s3ApiUrl": "https://s3.eu-central-003.backblazeb2.com",
                "allowed": {
                    "capabilities": (
                        capabilities
                        if capabilities is not None
                        else ["listBuckets", "writeBuckets"]
                    ),
                    "buckets": [
                        {
                            "id": "bucket-id",
                            "name": cors.DEFAULT_BUCKET,
                        }
                    ],
                },
            }
        },
    }


class FakeResponse:
    def __init__(self, *, status=200, headers=None, body=b""):
        self.status = status
        self.headers = headers or {}
        self.body = body

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, traceback):
        return False

    def read(self, limit=-1):
        return self.body if limit < 0 else self.body[:limit]


class FakeTransport:
    def __init__(
        self,
        *,
        bucket=None,
        auth_response=None,
        update_error=None,
        update_response=None,
    ):
        self.bucket = copy.deepcopy(bucket or make_bucket())
        self.auth_response = auth_response or make_auth_response()
        self.update_error = update_error
        self.update_response = update_response
        self.requests = []
        self.list_payloads = []
        self.update_payloads = []

    def __call__(self, request, timeout):
        self.requests.append(request)
        url = request.full_url

        if url == cors.AUTHORIZE_URL:
            return FakeResponse(body=json.dumps(self.auth_response).encode())

        if url.endswith("/b2api/v4/b2_list_buckets"):
            self.list_payloads.append(json.loads(request.data))
            return FakeResponse(
                body=json.dumps({"buckets": [self.bucket]}).encode()
            )

        if url.endswith("/b2api/v4/b2_update_bucket"):
            payload = json.loads(request.data)
            self.update_payloads.append(payload)
            if self.update_error:
                raise self.update_error
            if self.update_response is not None:
                return FakeResponse(
                    body=json.dumps(self.update_response).encode()
                )
            self.bucket["corsRules"] = copy.deepcopy(payload["corsRules"])
            self.bucket["revision"] = int(self.bucket["revision"]) + 1
            return FakeResponse(body=json.dumps(self.bucket).encode())

        raise AssertionError(f"Unexpected B2 URL: {url}")


def http_error(url, status, payload):
    return urllib.error.HTTPError(
        url,
        status,
        "Error",
        {},
        io.BytesIO(json.dumps(payload).encode()),
    )


def install_transport(monkeypatch, transport):
    monkeypatch.setattr(
        cors.urllib.request,
        "urlopen",
        transport,
    )


def test_authorize_uses_setup_credentials_and_reads_native_api(monkeypatch):
    transport = FakeTransport()
    install_transport(monkeypatch, transport)
    client = cors.B2NativeClient(make_settings(), timeout=5)

    authorization = client.authorize()

    assert authorization.account_id == "account-id"
    assert authorization.api_url == "https://api001.backblazeb2.com"
    assert authorization.s3_api_url == (
        "https://s3.eu-central-003.backblazeb2.com"
    )
    assert authorization.authorization_token == "test-authorization-token"
    assert authorization.capabilities == ("listBuckets", "writeBuckets")

    request = transport.requests[0]
    expected = base64.b64encode(
        b"test-key-id:test-app-key"
    ).decode("ascii")
    assert request.full_url == cors.AUTHORIZE_URL
    assert request.get_method() == "GET"
    assert request.headers["Authorization"] == f"Basic {expected}"


def test_get_bucket_reads_complete_native_cors_rules(monkeypatch):
    native_rule = make_native_rule(name="download-rule", operations=["s3_get"])
    transport = FakeTransport(bucket=make_bucket([native_rule]))
    install_transport(monkeypatch, transport)
    client = cors.B2NativeClient(make_settings(), timeout=5)

    bucket = client.get_bucket(cors.DEFAULT_BUCKET)

    assert bucket["corsRules"] == [native_rule]
    assert bucket["bucketInfo"] == {"site": "fileaway"}
    assert transport.list_payloads == [
        {
            "accountId": "account-id",
            "bucketName": cors.DEFAULT_BUCKET,
        }
    ]


def test_update_preserves_bucket_settings_and_sends_complete_rules(monkeypatch):
    existing = [
        make_native_rule(
            name="download-rule",
            operations=["s3_get"],
            headers=["range"],
        ),
        make_native_rule(
            name="other-origin",
            origins=["https://other.example"],
        ),
    ]
    transport = FakeTransport(bucket=make_bucket(existing))
    install_transport(monkeypatch, transport)
    client = cors.B2NativeClient(make_settings(), timeout=5)
    bucket = client.get_bucket(cors.DEFAULT_BUCKET)
    fixed, changed = cors.build_fixed_rules(
        bucket["corsRules"],
        origin=ORIGIN,
        operation=cors.S3_PUT_OPERATION,
        header="content-type",
        max_age=3600,
    )

    assert changed is True
    client.update_cors_rules(bucket, fixed)

    payload = transport.update_payloads[0]
    assert set(payload) == {
        "accountId",
        "bucketId",
        "corsRules",
        "ifRevisionIs",
    }
    assert payload["accountId"] == "account-id"
    assert payload["bucketId"] == "bucket-id"
    assert payload["ifRevisionIs"] == 7
    assert payload["corsRules"] == fixed
    assert payload["corsRules"][:2] == existing
    assert "bucketType" not in payload
    assert "bucketInfo" not in payload
    assert "lifecycleRules" not in payload
    assert "defaultServerSideEncryption" not in payload


def test_update_maps_unauthorized_to_actionable_permission_error(monkeypatch):
    transport = FakeTransport(
        update_error=http_error(
            "https://api001.backblazeb2.com/b2api/v4/b2_update_bucket",
            401,
            {
                "status": 401,
                "code": "unauthorized",
                "message": "not allowed",
            },
        )
    )
    install_transport(monkeypatch, transport)
    client = cors.B2NativeClient(make_settings(), timeout=5)
    bucket = client.get_bucket(cors.DEFAULT_BUCKET)

    with pytest.raises(
        cors.CorsScriptError,
        match="listBuckets and writeBuckets",
    ):
        client.update_cors_rules(bucket, [make_native_rule()])


def test_update_maps_conflict_to_rerun_message(monkeypatch):
    transport = FakeTransport(
        update_error=http_error(
            "https://api001.backblazeb2.com/b2api/v4/b2_update_bucket",
            409,
            {
                "status": 409,
                "code": "conflict",
                "message": "revision mismatch",
            },
        )
    )
    install_transport(monkeypatch, transport)
    client = cors.B2NativeClient(make_settings(), timeout=5)
    bucket = client.get_bucket(cors.DEFAULT_BUCKET)

    with pytest.raises(cors.CorsScriptError, match="changed while"):
        client.update_cors_rules(bucket, [make_native_rule()])


def test_s3_native_cors_failure_has_native_api_hint(monkeypatch):
    transport = FakeTransport(
        update_error=http_error(
            "https://api001.backblazeb2.com/b2api/v4/b2_update_bucket",
            400,
            {
                "status": 400,
                "code": "InvalidRequest",
                "message": (
                    "The bucket contains B2 Native CORS rules. "
                    "Please use B2 Native API instead."
                ),
            },
        )
    )
    install_transport(monkeypatch, transport)
    client = cors.B2NativeClient(make_settings(), timeout=5)
    bucket = client.get_bucket(cors.DEFAULT_BUCKET)

    with pytest.raises(cors.CorsScriptError, match="Native API path"):
        client.update_cors_rules(bucket, [make_native_rule()])


def test_apply_rules_uses_native_update_not_s3_put_bucket_cors():
    class Client:
        def __init__(self):
            self.updated = None
            self.s3_called = False

        def update_cors_rules(self, bucket_info, rules):
            self.updated = (bucket_info, rules)

        def put_bucket_cors(self, **kwargs):
            self.s3_called = True
            raise AssertionError(
                "InvalidRequest: The bucket contains B2 Native CORS rules. "
                "Please use B2 Native API instead."
            )

    client = Client()
    bucket = make_bucket()
    rules = [make_native_rule()]

    cors.apply_rules(client, bucket, rules)

    assert client.updated == (bucket, rules)
    assert client.s3_called is False


def test_diagnose_accepts_exact_origin_with_s3_put_and_header():
    diagnosis = cors.diagnose(
        [make_native_rule()],
        ORIGIN,
        cors.S3_PUT_OPERATION,
        "content-type",
    )

    assert diagnosis.exact_allowed is True
    assert diagnosis.wildcard_allowed is False
    assert diagnosis.exact_rule_indexes == (0,)
    assert diagnosis.matching_rule_indexes == (0,)


def test_diagnose_keeps_operation_case_sensitive():
    diagnosis = cors.diagnose(
        [make_native_rule(operations=["S3_PUT"])],
        ORIGIN,
        cors.S3_PUT_OPERATION,
        "content-type",
    )

    assert diagnosis.exact_allowed is False
    assert diagnosis.exact_rule_indexes == (0,)


def test_diagnose_rejects_exact_origin_without_allowed_header():
    diagnosis = cors.diagnose(
        [make_native_rule(headers=[])],
        ORIGIN,
        cors.S3_PUT_OPERATION,
        "content-type",
    )

    assert diagnosis.exact_allowed is False
    assert diagnosis.wildcard_allowed is False
    assert diagnosis.exact_rule_indexes == (0,)
    assert diagnosis.matching_rule_indexes == ()


def test_diagnose_accepts_wildcard_header():
    diagnosis = cors.diagnose(
        [make_native_rule(headers=["*"])],
        ORIGIN,
        cors.S3_PUT_OPERATION,
        "content-type",
    )

    assert diagnosis.exact_allowed is True


def test_build_fixed_rules_preserves_existing_rules_and_adds_native_rule():
    existing = [
        make_native_rule(
            name="download-rule",
            origins=["https://other.example"],
            operations=["s3_get"],
            headers=["range"],
            expose_headers=["Content-Length"],
            max_age=60,
        )
    ]

    fixed, changed = cors.build_fixed_rules(
        existing,
        origin=ORIGIN,
        operation=cors.S3_PUT_OPERATION,
        header="content-type",
        max_age=600,
    )

    assert changed is True
    assert fixed[0] == existing[0]
    assert fixed[0] is not existing[0]
    assert fixed[1] == {
        "corsRuleName": "fileaway-vercel-upload",
        "allowedOrigins": [ORIGIN],
        "allowedOperations": ["s3_put"],
        "allowedHeaders": ["content-type"],
        "exposeHeaders": ["ETag"],
        "maxAgeSeconds": 600,
    }


def test_build_fixed_rules_generates_unique_rule_name():
    existing = [
        make_native_rule(
            name="fileaway-vercel-upload",
            origins=["https://other.example"],
        )
    ]

    fixed, changed = cors.build_fixed_rules(
        existing,
        origin=ORIGIN,
        operation=cors.S3_PUT_OPERATION,
        header="content-type",
        max_age=3600,
    )

    assert changed is True
    assert fixed[-1]["corsRuleName"] == "fileaway-vercel-upload-2"


def test_build_fixed_rules_is_a_noop_when_exact_rule_already_allows_request():
    existing = [make_native_rule()]

    fixed, changed = cors.build_fixed_rules(
        existing,
        origin=ORIGIN,
        operation=cors.S3_PUT_OPERATION,
        header="content-type",
        max_age=3600,
    )

    assert changed is False
    assert fixed == existing


def test_build_fixed_rules_refuses_to_exceed_rule_limit():
    existing = [
        make_native_rule(
            name=f"other-rule-{index}",
            origins=[f"https://other-{index}.example"],
        )
        for index in range(100)
    ]

    with pytest.raises(cors.CorsScriptError, match="100 CORS rules"):
        cors.build_fixed_rules(
            existing,
            origin=ORIGIN,
            operation=cors.S3_PUT_OPERATION,
            header="content-type",
            max_age=3600,
        )


def test_verify_persisted_rules_detects_a_missing_existing_rule():
    expected = [
        make_native_rule(name="download-rule"),
        make_native_rule(name="upload-rule"),
    ]

    with pytest.raises(cors.CorsScriptError, match="complete Native API"):
        cors.verify_persisted_rules(expected, expected[:1])


def test_load_settings_prefers_setup_key_and_derives_region(monkeypatch):
    monkeypatch.setattr(cors, "load_dotenv", lambda *args, **kwargs: None)
    for name in (
        "B2_ENDPOINT_URL",
        "B2_REGION",
        "B2_BUCKET",
        "B2_KEY_ID",
        "B2_APP_KEY",
        "B2_SETUP_KEY_ID",
        "B2_SETUP_APP_KEY",
    ):
        monkeypatch.delenv(name, raising=False)

    monkeypatch.setenv(
        "B2_ENDPOINT_URL",
        "https://s3.eu-central-003.backblazeb2.com",
    )
    monkeypatch.setenv("B2_BUCKET", "test-bucket")
    monkeypatch.setenv("B2_SETUP_KEY_ID", "setup-id")
    monkeypatch.setenv("B2_SETUP_APP_KEY", "setup-key")
    monkeypatch.setenv("B2_KEY_ID", "runtime-id")
    monkeypatch.setenv("B2_APP_KEY", "runtime-key")

    settings = cors.load_settings(make_args())

    assert settings.endpoint_url == (
        "https://s3.eu-central-003.backblazeb2.com"
    )
    assert settings.bucket == "test-bucket"
    assert settings.region == "eu-central-003"
    assert settings.key_id == "setup-id"
    assert settings.app_key == "setup-key"
    assert settings.key_source == "B2_SETUP_KEY_ID/B2_SETUP_APP_KEY"


def test_load_settings_requires_b2_credentials(monkeypatch):
    monkeypatch.setattr(cors, "load_dotenv", lambda *args, **kwargs: None)
    for name in (
        "B2_KEY_ID",
        "B2_APP_KEY",
        "B2_SETUP_KEY_ID",
        "B2_SETUP_APP_KEY",
    ):
        monkeypatch.delenv(name, raising=False)

    with pytest.raises(cors.CorsScriptError, match="Missing B2 credentials"):
        cors.load_settings(make_args())


def test_load_settings_rejects_wildcard_origin(monkeypatch):
    monkeypatch.setattr(cors, "load_dotenv", lambda *args, **kwargs: None)
    monkeypatch.setenv("B2_SETUP_KEY_ID", "setup-id")
    monkeypatch.setenv("B2_SETUP_APP_KEY", "setup-key")

    with pytest.raises(cors.CorsScriptError, match="one exact origin"):
        cors.load_settings(make_args(origin="*"))


def test_probe_preflight_requires_origin_method_and_header(monkeypatch):
    response = FakeResponse(
        headers={
            "access-control-allow-origin": ORIGIN,
            "access-control-allow-methods": "PUT, GET",
            "access-control-allow-headers": "content-type",
        }
    )
    monkeypatch.setattr(
        cors.urllib.request,
        "urlopen",
        lambda request, timeout: response,
    )

    allowed, details = cors.probe_preflight(make_settings(), timeout=1)

    assert allowed is True
    assert "HTTP 200" in details
    assert "allow-headers=content-type" in details


def test_probe_preflight_rejects_missing_allowed_header(monkeypatch):
    response = FakeResponse(
        headers={
            "access-control-allow-origin": ORIGIN,
            "access-control-allow-methods": "PUT",
        }
    )
    monkeypatch.setattr(
        cors.urllib.request,
        "urlopen",
        lambda request, timeout: response,
    )

    allowed, details = cors.probe_preflight(make_settings(), timeout=1)

    assert allowed is False
    assert "allow-headers=<missing>" in details


def test_probe_preflight_accepts_wildcard_headers(monkeypatch):
    response = FakeResponse(
        headers={
            "access-control-allow-origin": "*",
            "access-control-allow-methods": "*",
            "access-control-allow-headers": "*",
        }
    )
    monkeypatch.setattr(
        cors.urllib.request,
        "urlopen",
        lambda request, timeout: response,
    )

    allowed, _ = cors.probe_preflight(make_settings(), timeout=1)

    assert allowed is True


def test_probe_preflight_handles_http_error(monkeypatch):
    def raise_http_error(request, timeout):
        raise urllib.error.HTTPError(
            request.full_url,
            403,
            "Forbidden",
            {
                "access-control-allow-origin": ORIGIN,
                "access-control-allow-methods": "PUT",
                "access-control-allow-headers": "content-type",
            },
            io.BytesIO(
                b"<Error><Code>AccessDenied</Code>"
                b"<Message>not allowed</Message></Error>"
            ),
        )

    monkeypatch.setattr(cors.urllib.request, "urlopen", raise_http_error)

    allowed, details = cors.probe_preflight(make_settings(), timeout=1)

    assert allowed is False
    assert "HTTP 403" in details
    assert "B2 AccessDenied: not allowed" in details


def test_probe_preflight_handles_network_error(monkeypatch):
    def raise_url_error(request, timeout):
        raise urllib.error.URLError("offline")

    monkeypatch.setattr(cors.urllib.request, "urlopen", raise_url_error)

    allowed, details = cors.probe_preflight(make_settings(), timeout=1)

    assert allowed is None
    assert details == "Could not run preflight probe: URLError"


class FakeNativeClient:
    def __init__(self, bucket):
        self.bucket = copy.deepcopy(bucket)
        self.update_calls = []

    @property
    def api_url(self):
        return "https://api001.backblazeb2.com"

    @property
    def s3_api_url(self):
        return "https://s3.eu-central-003.backblazeb2.com"

    def get_bucket(self, bucket_name):
        return copy.deepcopy(self.bucket)

    def update_cors_rules(self, bucket_info, rules):
        self.update_calls.append(copy.deepcopy(rules))
        self.bucket = copy.deepcopy(bucket_info)
        self.bucket["corsRules"] = copy.deepcopy(rules)
        self.bucket["revision"] = int(self.bucket["revision"]) + 1


def test_main_dry_run_reads_native_rules_without_writing(monkeypatch):
    client = FakeNativeClient(make_bucket())
    monkeypatch.setattr(
        cors,
        "load_settings",
        lambda args: make_settings(),
    )
    monkeypatch.setattr(
        cors,
        "make_native_client",
        lambda settings, timeout: client,
    )

    result = cors.main(["--no-preflight"])

    assert result == 1
    assert client.update_calls == []


def test_main_apply_uses_native_update_and_verifies(monkeypatch):
    existing = [
        make_native_rule(
            name="download-rule",
            operations=["s3_get"],
            headers=["range"],
        )
    ]
    client = FakeNativeClient(make_bucket(existing))
    monkeypatch.setattr(
        cors,
        "load_settings",
        lambda args: make_settings(),
    )
    monkeypatch.setattr(
        cors,
        "make_native_client",
        lambda settings, timeout: client,
    )

    result = cors.main(["--apply", "--no-preflight"])

    assert result == 0
    assert len(client.update_calls) == 1
    saved = client.bucket["corsRules"]
    assert saved[0] == existing[0]
    assert cors.diagnose(
        saved,
        ORIGIN,
        cors.S3_PUT_OPERATION,
        "content-type",
    ).exact_allowed


def test_main_reports_rule_limit_without_a_traceback(monkeypatch, capsys):
    client = FakeNativeClient(
        make_bucket(
            [
                make_native_rule(
                    name=f"other-rule-{index}",
                    origins=[f"https://other-{index}.example"],
                )
                for index in range(100)
            ]
        )
    )
    monkeypatch.setattr(
        cors,
        "load_settings",
        lambda args: make_settings(),
    )
    monkeypatch.setattr(
        cors,
        "make_native_client",
        lambda settings, timeout: client,
    )

    result = cors.main(["--apply", "--no-preflight"])

    assert result == 2
    assert "100 CORS rules" in capsys.readouterr().err
    assert client.update_calls == []
