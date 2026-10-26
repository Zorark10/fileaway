from deployment.configure_b2_cors import (
    ALLOWED_HEADERS,
    ALLOWED_METHODS,
    PRODUCTION_ORIGIN,
    build_cors_configuration,
)


def test_production_cors_rule_allows_exact_origin_and_upload_preflight():
    rule = build_cors_configuration([PRODUCTION_ORIGIN], 3600)["CORSRules"][0]

    assert rule["AllowedOrigins"] == [PRODUCTION_ORIGIN]
    assert PRODUCTION_ORIGIN == "https://fileaway-share.vercel.app"
    assert "PUT" in rule["AllowedMethods"]
    assert rule["AllowedMethods"] == list(ALLOWED_METHODS)
    assert "content-type" in rule["AllowedHeaders"]
    assert rule["AllowedHeaders"] == list(ALLOWED_HEADERS)
