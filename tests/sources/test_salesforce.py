#
# Copyright Elasticsearch B.V. and/or licensed to Elasticsearch B.V. under one
# or more contributor license agreements. Licensed under the Elastic License 2.0;
# you may not use this file except in compliance with the Elastic License 2.0.
#
"""Tests the Salesforce source class methods"""
import re
from contextlib import asynccontextmanager
from copy import deepcopy
from unittest import TestCase, mock
from unittest.mock import patch

import pytest
from aiohttp.client_exceptions import ClientConnectionError
from aioresponses import CallbackResult

from connectors.source import ConfigurableFieldValueError, DataSourceConfiguration
from connectors.sources.salesforce import (
    API_VERSION,
    RELEVANT_SOBJECT_FIELDS,
    ConnectorRequestError,
    InvalidCredentialsException,
    InvalidQueryException,
    RateLimitedException,
    SalesforceDataSource,
    SalesforceServerError,
    SalesforceSoqlBuilder,
    TokenFetchException,
)
from tests.sources.support import create_source

TEST_DOMAIN = "fake"
CONTENT_VERSION_ID = "content_version_id"
TEST_BASE_URL = f"https://{TEST_DOMAIN}.my.salesforce.com"
TEST_FILE_DOWNLOAD_URL = f"{TEST_BASE_URL}/services/data/{API_VERSION}/sobjects/ContentVersion/{CONTENT_VERSION_ID}/VersionData"
TEST_QUERY_MATCH_URL = re.compile(f"{TEST_BASE_URL}/services/data/{API_VERSION}/query*")
TEST_CLIENT_ID = "1234"
TEST_CLIENT_SECRET = "9876"

ACCOUNT_RESPONSE_PAYLOAD = {
    "totalSize": 1,
    "done": True,
    "records": [
        {
            "attributes": {
                "type": "Account",
                "url": f"/services/data/{API_VERSION}/sobjects/Account/account_id",
            },
            "Type": "Customer - Direct",
            "Owner": {
                "attributes": {
                    "type": "User",
                    "url": f"/services/data/{API_VERSION}/sobjects/User/user_id",
                },
                "Id": "user_id",
                "Name": "Frodo",
                "Email": "frodo@tlotr.com",
            },
            "Id": "account_id",
            "Rating": "Hot",
            "Website": "www.tlotr.com",
            "LastModifiedDate": "",
            "CreatedDate": "",
            "Opportunities": {
                "totalSize": 1,
                "done": True,
                "records": [
                    {
                        "attributes": {
                            "type": "Opportunity",
                            "url": f"/services/data/{API_VERSION}/sobjects/Opportunity/opportunity_id",
                        },
                        "Id": "opportunity_id",
                        "Name": "The Fellowship",
                        "StageName": "Closed Won",
                    }
                ],
            },
            "Name": "TLOTR",
            "BillingAddress": {
                "city": "The Shire",
                "country": "Middle Earth",
                "postalCode": 111,
                "state": "Eriador",
                "street": "The Burrow under the Hill, Bag End, Hobbiton",
            },
            "Description": "A story about the One Ring.",
        }
    ],
}

OPPORTUNITY_RESPONSE_PAYLOAD = {
    "totalSize": 1,
    "done": True,
    "records": [
        {
            "attributes": {
                "type": "Opportunity",
                "url": f"/services/data/{API_VERSION}/sobjects/Opportunity/opportunity_id",
            },
            "Description": "A fellowship of the races of Middle Earth",
            "Owner": {
                "attributes": {
                    "type": "User",
                    "url": f"/services/data/{API_VERSION}/sobjects/User/user_id",
                },
                "Id": "user_id",
                "Email": "frodo@tlotr.com",
                "Name": "Frodo",
            },
            "LastModifiedDate": "",
            "Name": "The Fellowship",
            "StageName": "Closed Won",
            "CreatedDate": "",
            "Id": "opportunity_id",
        },
    ],
}

CONTACT_RESPONSE_PAYLOAD = {
    "records": [
        {
            "attributes": {
                "type": "Contact",
                "url": f"/services/data/{API_VERSION}/sobjects/Contact/contact_id",
            },
            "OwnerId": "user_id",
            "Phone": "12345678",
            "Name": "Gandalf",
            "AccountId": "account_id",
            "LastModifiedDate": "",
            "Description": "The White",
            "Title": "Wizard",
            "CreatedDate": "",
            "LeadSource": "Partner Referral",
            "PhotoUrl": "/services/images/photo/photo_id",
            "Id": "contact_id",
            "Email": "gandalf@tlotr.com",
        },
    ],
}

LEAD_RESPONSE_PAYLOAD = {
    "records": [
        {
            "attributes": {
                "type": "Lead",
                "url": f"/services/data/{API_VERSION}/sobjects/Lead/lead_id",
            },
            "Name": "Sauron",
            "Status": "Working - Contacted",
            "Company": "Mordor Inc.",
            "Description": "Forger of the One Ring",
            "Email": "sauron@tlotr.com",
            "Phone": "09876543",
            "Title": "Dark Lord",
            "PhotoUrl": "/services/images/photo/photo_id",
            "Rating": "Hot",
            "LastModifiedDate": "",
            "LeadSource": "Partner Referral",
            "OwnerId": "user_id",
            "ConvertedAccountId": None,
            "ConvertedContactId": None,
            "ConvertedOpportunityId": None,
            "ConvertedDate": None,
            "Id": "lead_id",
        }
    ]
}

CAMPAIGN_RESPONSE_PAYLOAD = {
    "records": [
        {
            "attributes": {
                "type": "Campaign",
                "url": f"/services/data/{API_VERSION}/sobjects/Campaign/campaign_id",
            },
            "Name": "Defend the Gap",
            "IsActive": True,
            "Type": "War",
            "Description": "Orcs are raiding the Gap of Rohan",
            "Status": "planned",
            "Id": "campaign_id",
            "Parent": {
                "attributes": {
                    "type": "User",
                    "url": f"/services/data/{API_VERSION}/sobjects/User/user_id",
                },
                "Id": "user_id",
                "Name": "Théoden",
            },
            "Owner": {
                "attributes": {
                    "type": "User",
                    "url": f"/services/data/{API_VERSION}/sobjects/User/user_id",
                },
                "Id": "user_id",
                "Name": "Saruman",
                "Email": "saruman@tlotr.com",
            },
            "StartDate": "",
            "EndDate": "",
        }
    ]
}

CASE_RESPONSE_PAYLOAD = {
    "records": [
        {
            "attributes": {
                "type": "Case",
                "url": f"/services/data/{API_VERSION}/sobjects/Case/case_id",
            },
            "Status": "New",
            "AccountId": "account_id",
            "Description": "The One Ring",
            "Subject": "It needs to be destroyed",
            "Owner": {
                "attributes": {
                    "type": "Name",
                    "url": f"/services/data/{API_VERSION}/sobjects/User/user_id",
                },
                "Email": "frodo@tlotr.com",
                "Name": "Frodo",
                "Id": "user_id",
            },
            "CreatedBy": {
                "attributes": {
                    "type": "User",
                    "url": f"/services/data/{API_VERSION}/sobjects/User/user_id_2",
                },
                "Id": "user_id_2",
                "Email": "gandalf@tlotr.com",
                "Name": "Gandalf",
            },
            "Id": "case_id",
            "EmailMessages": {
                "records": [
                    {
                        "attributes": {
                            "type": "EmailMessage",
                            "url": f"/services/data/{API_VERSION}/sobjects/EmailMessage/email_message_id",
                        },
                        "CreatedDate": "2023-08-11T00:00:00.000+0000",
                        "LastModifiedById": "user_id",
                        "ParentId": "case_id",
                        "MessageDate": "2023-08-01T00:00:00.000+0000",
                        "TextBody": "Maybe I should do something?",
                        "Subject": "Ring?!",
                        "FromName": "Frodo",
                        "FromAddress": "frodo@tlotr.com",
                        "ToAddress": "gandalf@tlotr.com",
                        "CcAddress": "elrond@tlotr.com",
                        "BccAddress": "samwise@tlotr.com",
                        "Status": "",
                        "IsDeleted": False,
                        "FirstOpenedDate": "2023-08-02T00:00:00.000+0000",
                        "CreatedBy": {
                            "attributes": {
                                "type": "Name",
                                "url": f"/services/data/{API_VERSION}/sobjects/User/user_id",
                            },
                            "Name": "Frodo",
                            "Id": "user_id",
                            "Email": "frodo@tlotr.com",
                        },
                    }
                ]
            },
            "CaseComments": {
                "records": [
                    {
                        "attributes": {
                            "type": "CaseComment",
                            "url": f"/services/data/{API_VERSION}/sobjects/CaseComment/case_comment_id",
                        },
                        "CreatedDate": "2023-08-03T00:00:00.000+0000",
                        "LastModifiedById": "user_id_3",
                        "CommentBody": "You have my axe",
                        "LastModifiedDate": "2023-08-03T00:00:00.000+0000",
                        "CreatedBy": {
                            "attributes": {
                                "type": "Name",
                                "url": f"/services/data/{API_VERSION}/sobjects/User/user_id_3",
                            },
                            "Name": "Gimli",
                            "Id": "user_id_3",
                            "Email": "gimli@tlotr.com",
                        },
                        "ParentId": "case_id",
                        "Id": "case_comment_id",
                    }
                ]
            },
            "CaseNumber": "00001234",
            "ParentId": "",
            "CreatedDate": "2023-08-01T00:00:00.000+0000",
            "IsDeleted": False,
            "IsClosed": False,
            "LastModifiedDate": "2023-08-11T00:00:00.000+0000",
        }
    ]
}

CASE_FEED_RESPONSE_PAYLOAD = {
    "records": [
        {
            "attributes": {
                "type": "CaseFeed",
                "url": f"/services/data/{API_VERSION}/sobjects/CaseFeed/case_feed_id",
            },
            "CreatedBy": {
                "attributes": {
                    "type": "Name",
                    "url": f"/services/data/{API_VERSION}/sobjects/User/user_id_4",
                },
                "Id": "user_id_4",
                "Email": "galadriel@tlotr.com",
                "Name": "Galadriel",
            },
            "CommentCount": 2,
            "LastModifiedDate": "2023-08-09T00:00:00.000+0000",
            "Type": "TextPost",
            "Title": None,
            "IsDeleted": False,
            "LinkUrl": f"{TEST_BASE_URL}/case_feed_id",
            "CreatedDate": "2023-08-08T00:00:00.000+0000",
            "Id": "case_feed_id",
            "FeedComments": {
                "records": [
                    {
                        "attributes": {
                            "type": "FeedComment",
                            "url": f"/services/data/{API_VERSION}/sobjects/FeedComment/feed_comment_id",
                        },
                        "CreatedBy": {
                            "attributes": {
                                "type": "Name",
                                "url": f"/services/data/{API_VERSION}/sobjects/User/user_id_4",
                            },
                            "Id": "user_id_4",
                            "Email": "galadriel@tlotr.com",
                            "Name": "Galadriel",
                        },
                        "IsDeleted": False,
                        "Id": "feed_comment_id",
                        "ParentId": "case_feed_id",
                        "LastEditById": "user_id_4",
                        "LastEditDate": "2023-08-08T00:00:00.000+0000",
                        "CommentBody": "I know what it is you saw",
                    }
                ]
            },
            "ParentId": "case_id",
        }
    ]
}

CONTENT_DOCUMENT_LINKS_PAYLOAD = {
    "records": [
        {
            "attributes": {
                "type": "ContentDocumentLink",
                "url": f"/services/data/{API_VERSION}/sobjects/ContentDocumentLink/content_document_link_id",
            },
            "Id": "content_document_link_id",
            "ContentDocument": {
                "attributes": {
                    "type": "ContentDocument",
                    "url": f"/services/data/{API_VERSION}/sobjects/ContentDocument/content_document_id",
                },
                "Id": "content_document_id",
                "Description": "A file about a ring.",
                "Title": "the_ring",
                "ContentSize": 1000,
                "FileExtension": "txt",
                "CreatedDate": "",
                "LatestPublishedVersion": {
                    "attributes": {
                        "type": "ContentVersion",
                        "url": f"/services/data/{API_VERSION}/sobjects/ContentVersion/content_version_id",
                    },
                    "Id": CONTENT_VERSION_ID,
                    "CreatedDate": "",
                    "VersionNumber": "2",
                },
                "Owner": {
                    "attributes": {
                        "type": "User",
                        "url": f"/services/data/{API_VERSION}/sobjects/User/user_id",
                    },
                    "Id": "user_id",
                    "Name": "Frodo",
                    "Email": "frodo@tlotr.com",
                },
                "CreatedBy": {
                    "attributes": {
                        "type": "User",
                        "url": f"/services/data/{API_VERSION}/sobjects/User/user_id",
                    },
                    "Id": "user_id",
                    "Name": "Frodo",
                    "Email": "frodo@tlotr.com",
                },
                "LastModifiedDate": "",
            },
        }
    ]
}

CACHED_SOBJECTS = {
    "Account": {"account_id": {"Id": "account_id", "Name": "TLOTR"}},
    "User": {
        "user_id": {
            "Id": "user_id",
            "Name": "Frodo",
            "Email": "frodo@tlotr.com",
        }
    },
    "Opportunity": {},
    "Contact": {},
}


@asynccontextmanager
async def create_salesforce_source(
    use_text_extraction_service=False, mock_token=True, mock_queryables=True
):
    async with create_source(
        SalesforceDataSource,
        domain=TEST_DOMAIN,
        client_id=TEST_CLIENT_ID,
        client_secret=TEST_CLIENT_SECRET,
        use_text_extraction_service=use_text_extraction_service,
    ) as source:
        if mock_token is True:
            source.salesforce_client.api_token.token = mock.AsyncMock(
                return_value="foo"
            )

        if mock_queryables is True:
            source.salesforce_client.sobjects_cache_by_type = mock.AsyncMock(
                return_value=CACHED_SOBJECTS
            )
            source.salesforce_client._is_queryable = mock.AsyncMock(return_value=True)
            source.salesforce_client._select_queryable_fields = mock.AsyncMock(
                return_value=RELEVANT_SOBJECT_FIELDS
            )

        yield source


def salesforce_query_callback(url, **kwargs):
    """Dynamically returns a payload based on query
    and adds ContentDocumentLinks to each payload
    """
    payload = {}

    # get table name after last "FROM" in query
    query = kwargs["params"]["q"]
    table_name = re.findall(r"\bFROM\s+(\w+)", query)[-1]

    match table_name:
        case "Account":
            payload = deepcopy(ACCOUNT_RESPONSE_PAYLOAD)
        case "Campaign":
            payload = deepcopy(CAMPAIGN_RESPONSE_PAYLOAD)
        case "Case":
            payload = deepcopy(CASE_RESPONSE_PAYLOAD)
        case "CaseFeed":
            payload = deepcopy(CASE_FEED_RESPONSE_PAYLOAD)
        case "Contact":
            payload = deepcopy(CONTACT_RESPONSE_PAYLOAD)
        case "Lead":
            payload = deepcopy(LEAD_RESPONSE_PAYLOAD)
        case "Opportunity":
            payload = deepcopy(OPPORTUNITY_RESPONSE_PAYLOAD)

    if table_name != "CaseFeed":
        for record in payload["records"]:
            record["ContentDocumentLinks"] = deepcopy(CONTENT_DOCUMENT_LINKS_PAYLOAD)

    return CallbackResult(status=200, payload=payload)


def generate_account_doc(identifier):
    return {
        "_id": identifier,
        "account_type": "An account type",
        "address": "Somewhere, Someplace, 1234",
        "body": "A body",
        "content_source_id": identifier,
        "created_at": "",
        "last_updated": "",
        "owner": "Frodo",
        "owner_email": "frodo@tlotr.com",
        "open_activities": "",
        "open_activities_urls": "",
        "opportunity_name": "An opportunity name",
        "opportunity_status": "An opportunity status",
        "opportunity_url": f"{TEST_BASE_URL}/{identifier}",
        "rating": "Hot",
        "source": "salesforce",
        "tags": ["A tag"],
        "title": {identifier},
        "type": "account",
        "url": f"{TEST_BASE_URL}/{identifier}",
        "website_url": "www.tlotr.com",
    }


def test_get_default_configuration():
    config = DataSourceConfiguration(SalesforceDataSource.get_default_configuration())
    expected_fields = ["client_id", "client_secret", "domain"]

    assert all(field in config.to_dict() for field in expected_fields)


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "domain, client_id, client_secret",
    [
        ("", TEST_CLIENT_ID, TEST_CLIENT_SECRET),
        (TEST_DOMAIN, "", TEST_CLIENT_SECRET),
        (TEST_DOMAIN, TEST_CLIENT_ID, ""),
    ],
)
async def test_validate_config_missing_fields_then_raise(
    domain, client_id, client_secret
):
    async with create_source(
        SalesforceDataSource,
        domain=domain,
        client_id=client_id,
        client_secret=client_secret,
    ) as source:
        with pytest.raises(ConfigurableFieldValueError):
            await source.validate_config()


@pytest.mark.asyncio
async def test_ping_with_successful_connection(mock_responses):
    async with create_salesforce_source() as source:
        mock_responses.head(TEST_BASE_URL, status=200)

        await source.ping()


@pytest.mark.asyncio
async def test_generate_token_with_successful_connection(mock_responses):
    async with create_salesforce_source() as source:
        response_payload = {
            "access_token": "foo",
            "signature": "bar",
            "instance_url": "https://fake.my.salesforce.com",
            "id": "https://login.salesforce.com/id/1234",
            "token_type": "Bearer",
        }

        mock_responses.post(
            f"{TEST_BASE_URL}/services/oauth2/token",
            status=200,
            payload=response_payload,
        )
        assert await source.salesforce_client.api_token.token() == "foo"


@pytest.mark.asyncio
async def test_generate_token_with_bad_domain_raises_error(
    patch_sleep, mock_responses, patch_cancellable_sleeps
):
    async with create_salesforce_source(mock_token=False) as source:
        mock_responses.post(
            f"{TEST_BASE_URL}/services/oauth2/token", status=500, repeat=True
        )
        with pytest.raises(TokenFetchException):
            await source.salesforce_client.api_token.token()


@pytest.mark.asyncio
async def test_generate_token_with_bad_credentials_raises_error(
    patch_sleep, mock_responses, patch_cancellable_sleeps
):
    async with create_salesforce_source(mock_token=False) as source:
        mock_responses.post(
            f"{TEST_BASE_URL}/services/oauth2/token",
            status=400,
            payload={
                "error": "invalid_client",
                "error_description": "Invalid client credentials",
            },
            repeat=True,
        )
        with pytest.raises(InvalidCredentialsException):
            await source.salesforce_client.api_token.token()


@pytest.mark.asyncio
async def test_generate_token_with_unexpected_error_retries(
    patch_sleep, mock_responses, patch_cancellable_sleeps
):
    async with create_salesforce_source() as source:
        response_payload = {
            "access_token": "foo",
            "signature": "bar",
            "instance_url": "https://fake.my.salesforce.com",
            "id": "https://login.salesforce.com/id/1234",
            "token_type": "Bearer",
        }

        mock_responses.post(
            f"{TEST_BASE_URL}/services/oauth2/token",
            status=500,
        )
        mock_responses.post(
            f"{TEST_BASE_URL}/services/oauth2/token",
            status=200,
            payload=response_payload,
        )
        assert await source.salesforce_client.api_token.token() == "foo"


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "sobject, expected_result",
    [
        (
            "FooField",
            True,
        ),
        ("ArghField", False),
    ],
)
@mock.patch(
    "connectors.sources.salesforce.RELEVANT_SOBJECTS",
    ["FooField", "BarField", "ArghField"],
)
async def test_get_queryable_sobjects(mock_responses, sobject, expected_result):
    async with create_salesforce_source(mock_queryables=False) as source:
        response_payload = {
            "sobjects": [
                {
                    "queryable": True,
                    "name": "FooField",
                },
                {
                    "queryable": False,
                    "name": "BarField",
                },
            ],
        }

        mock_responses.get(
            f"{TEST_BASE_URL}/services/data/{API_VERSION}/sobjects",
            status=200,
            payload=response_payload,
        )

        queryable = await source.salesforce_client._is_queryable(sobject)
        assert queryable == expected_result


@pytest.mark.asyncio
@mock.patch("connectors.sources.salesforce.RELEVANT_SOBJECTS", ["Account"])
@mock.patch(
    "connectors.sources.salesforce.RELEVANT_SOBJECT_FIELDS",
    ["FooField", "BarField", "ArghField"],
)
async def test_get_queryable_fields(mock_responses):
    async with create_salesforce_source(mock_queryables=False) as source:
        expected_fields = [
            {
                "name": "FooField",
            },
            {
                "name": "BarField",
            },
            {"name": "ArghField"},
        ]
        response_payload = {
            "fields": expected_fields,
        }
        mock_responses.get(
            f"{TEST_BASE_URL}/services/data/{API_VERSION}/sobjects/Account/describe",
            status=200,
            payload=response_payload,
        )

        queryable_fields = await source.salesforce_client._select_queryable_fields(
            "Account", ["FooField", "BarField", "NarghField"]
        )
        TestCase().assertCountEqual(queryable_fields, ["FooField", "BarField"])


@pytest.mark.asyncio
async def test_get_accounts_when_success(mock_responses):
    async with create_salesforce_source() as source:
        payload = deepcopy(ACCOUNT_RESPONSE_PAYLOAD)
        expected_record = payload["records"][0]

        expected_doc = {
            "_id": "account_id",
            "account_type": "Customer - Direct",
            "address": "The Burrow under the Hill, Bag End, Hobbiton, The Shire, Eriador, 111, Middle Earth",
            "body": "A story about the One Ring.",
            "content_source_id": "account_id",
            "created_at": "",
            "last_updated": "",
            "owner": "Frodo",
            "owner_email": "frodo@tlotr.com",
            "open_activities": "",
            "open_activities_urls": "",
            "opportunity_name": "The Fellowship",
            "opportunity_status": "Closed Won",
            "opportunity_url": f"{TEST_BASE_URL}/opportunity_id",
            "rating": "Hot",
            "source": "salesforce",
            "tags": ["Customer - Direct"],
            "title": "TLOTR",
            "type": "account",
            "url": f"{TEST_BASE_URL}/account_id",
            "website_url": "www.tlotr.com",
        }

        mock_responses.get(
            TEST_QUERY_MATCH_URL,
            status=200,
            payload=ACCOUNT_RESPONSE_PAYLOAD,
        )
        async for record in source.salesforce_client.get_accounts():
            assert record == expected_record
            assert source.doc_mapper.map_account(record) == expected_doc


@pytest.mark.asyncio
async def test_get_accounts_when_paginated_yields_all_pages(mock_responses):
    async with create_salesforce_source() as source:
        response_page_1 = {
            "done": False,
            "nextRecordsUrl": "/barbar",
            "records": [
                {
                    "Id": 1234,
                }
            ],
        }
        response_page_2 = {
            "done": True,
            "records": [
                {
                    "Id": 5678,
                }
            ],
        }

        mock_responses.get(
            TEST_QUERY_MATCH_URL,
            status=200,
            payload=response_page_1,
        )
        mock_responses.get(
            f"{TEST_BASE_URL}/barbar",
            status=200,
            payload=response_page_2,
        )

        yielded_account_ids = []
        async for record in source.salesforce_client.get_accounts():
            yielded_account_ids.append(record["Id"])

        assert sorted(yielded_account_ids) == [1234, 5678]


@pytest.mark.asyncio
async def test_get_accounts_when_invalid_request(patch_sleep, mock_responses):
    async with create_salesforce_source(mock_queryables=False) as source:
        response_payload = [
            {"message": "Unable to process query.", "errorCode": "INVALID_FIELD"}
        ]

        mock_responses.get(
            TEST_QUERY_MATCH_URL,
            status=405,
            payload=response_payload,
        )
        with pytest.raises(ClientConnectionError):
            async for _ in source.salesforce_client.get_accounts():
                # TODO confirm error message when error handling is improved
                pass


@pytest.mark.asyncio
async def test_get_accounts_when_not_queryable_yields_nothing(mock_responses):
    async with create_salesforce_source() as source:
        source.salesforce_client._is_queryable = mock.AsyncMock(return_value=False)
        async for record in source.salesforce_client.get_accounts():
            assert record is None


@pytest.mark.asyncio
async def test_get_opportunities_when_success(mock_responses):
    async with create_salesforce_source() as source:
        expected_doc = {
            "_id": "opportunity_id",
            "body": "A fellowship of the races of Middle Earth",
            "content_source_id": "opportunity_id",
            "created_at": "",
            "last_updated": "",
            "next_step": None,
            "owner": "Frodo",
            "owner_email": "frodo@tlotr.com",
            "source": "salesforce",
            "status": "Closed Won",
            "title": "The Fellowship",
            "type": "opportunity",
            "url": f"{TEST_BASE_URL}/opportunity_id",
        }

        mock_responses.get(
            TEST_QUERY_MATCH_URL,
            status=200,
            payload=OPPORTUNITY_RESPONSE_PAYLOAD,
        )
        async for record in source.salesforce_client.get_opportunities():
            assert record == OPPORTUNITY_RESPONSE_PAYLOAD["records"][0]
            assert source.doc_mapper.map_opportunity(record) == expected_doc


@pytest.mark.asyncio
async def test_get_contacts_when_success(mock_responses):
    async with create_salesforce_source() as source:
        payload = deepcopy(CONTACT_RESPONSE_PAYLOAD)
        expected_record = payload["records"][0]
        expected_record["Account"] = {
            "Id": "account_id",
            "Name": "TLOTR",
        }
        expected_record["Owner"] = {
            "Id": "user_id",
            "Name": "Frodo",
            "Email": "frodo@tlotr.com",
        }

        expected_doc = {
            "_id": "contact_id",
            "account": "TLOTR",
            "account_url": f"{TEST_BASE_URL}/account_id",
            "body": "The White",
            "created_at": "",
            "email": "gandalf@tlotr.com",
            "job_title": "Wizard",
            "last_updated": "",
            "lead_source": "Partner Referral",
            "owner": "Frodo",
            "owner_url": f"{TEST_BASE_URL}/user_id",
            "phone": "12345678",
            "source": "salesforce",
            "thumbnail": f"{TEST_BASE_URL}/services/images/photo/photo_id",
            "title": "Gandalf",
            "type": "contact",
            "url": f"{TEST_BASE_URL}/contact_id",
        }

        mock_responses.get(
            TEST_QUERY_MATCH_URL,
            status=200,
            payload=CONTACT_RESPONSE_PAYLOAD,
        )
        async for record in source.salesforce_client.get_contacts():
            assert record == expected_record
            assert source.doc_mapper.map_contact(record) == expected_doc


@pytest.mark.asyncio
async def test_get_leads_when_success(mock_responses):
    async with create_salesforce_source() as source:
        payload = deepcopy(LEAD_RESPONSE_PAYLOAD)
        expected_record = payload["records"][0]
        expected_record["Owner"] = {
            "Id": "user_id",
            "Name": "Frodo",
            "Email": "frodo@tlotr.com",
        }
        expected_record["ConvertedAccount"] = {}
        expected_record["ConvertedContact"] = {}
        expected_record["ConvertedOpportunity"] = {}

        expected_doc = {
            "_id": "lead_id",
            "body": "Forger of the One Ring",
            "company": "Mordor Inc.",
            "converted_account": None,
            "converted_account_url": None,
            "converted_at": None,
            "converted_contact": None,
            "converted_contact_url": None,
            "converted_opportunity": None,
            "converted_opportunity_url": None,
            "created_at": None,
            "email": "sauron@tlotr.com",
            "job_title": "Dark Lord",
            "last_updated": "",
            "lead_source": "Partner Referral",
            "owner": "Frodo",
            "owner_url": f"{TEST_BASE_URL}/user_id",
            "phone": "09876543",
            "rating": "Hot",
            "source": "salesforce",
            "status": "Working - Contacted",
            "title": "Sauron",
            "thumbnail": f"{TEST_BASE_URL}/services/images/photo/photo_id",
            "type": "lead",
            "url": f"{TEST_BASE_URL}/lead_id",
        }

        mock_responses.get(
            TEST_QUERY_MATCH_URL,
            status=200,
            payload=LEAD_RESPONSE_PAYLOAD,
        )
        async for record in source.salesforce_client.get_leads():
            assert record == expected_record
            assert source.doc_mapper.map_lead(record) == expected_doc


@pytest.mark.asyncio
async def test_get_campaigns_when_success(mock_responses):
    async with create_salesforce_source() as source:
        expected_doc = {
            "_id": "campaign_id",
            "body": "Orcs are raiding the Gap of Rohan",
            "campaign_type": "War",
            "created_at": None,
            "end_date": "",
            "last_updated": None,
            "owner": "Saruman",
            "owner_email": "saruman@tlotr.com",
            "parent": "Théoden",
            "parent_url": f"{TEST_BASE_URL}/user_id",
            "source": "salesforce",
            "start_date": "",
            "status": "planned",
            "state": "active",
            "title": "Defend the Gap",
            "type": "campaign",
            "url": f"{TEST_BASE_URL}/campaign_id",
        }

        mock_responses.get(
            TEST_QUERY_MATCH_URL,
            status=200,
            payload=CAMPAIGN_RESPONSE_PAYLOAD,
        )
        async for record in source.salesforce_client.get_campaigns():
            assert record == CAMPAIGN_RESPONSE_PAYLOAD["records"][0]
            assert source.doc_mapper.map_campaign(record) == expected_doc


@pytest.mark.asyncio
async def test_get_cases_when_success(mock_responses):
    async with create_salesforce_source() as source:
        payload = deepcopy(CASE_RESPONSE_PAYLOAD)
        expected_record = payload["records"][0]

        feeds_payload = deepcopy(CASE_FEED_RESPONSE_PAYLOAD)
        expected_record["Feeds"] = feeds_payload["records"]

        expected_doc = {
            "_id": "case_id",
            "account_id": "account_id",
            "body": "I know what it is you saw\n\nThe One Ring\n\nRing?!\nMaybe I should do something?\n\nYou have my axe",
            "created_at": "2023-08-01T00:00:00.000+0000",
            "created_by": "Gandalf",
            "created_by_email": "gandalf@tlotr.com",
            "case_number": "00001234",
            "is_closed": False,
            "last_updated": "2023-08-11T00:00:00.000+0000",
            "owner": "Frodo",
            "owner_email": "frodo@tlotr.com",
            "participant_emails": [
                "elrond@tlotr.com",
                "frodo@tlotr.com",
                "galadriel@tlotr.com",
                "gandalf@tlotr.com",
                "gimli@tlotr.com",
                "samwise@tlotr.com",
            ],
            "participant_ids": ["user_id", "user_id_2", "user_id_3", "user_id_4"],
            "participants": ["Frodo", "Galadriel", "Gandalf", "Gimli"],
            "source": "salesforce",
            "status": "New",
            "title": "It needs to be destroyed",
            "type": "case",
            "url": f"{TEST_BASE_URL}/case_id",
        }

        mock_responses.get(
            TEST_QUERY_MATCH_URL,
            status=200,
            payload=CASE_RESPONSE_PAYLOAD,
        )
        mock_responses.get(
            TEST_QUERY_MATCH_URL,
            status=200,
            payload=CASE_FEED_RESPONSE_PAYLOAD,
        )
        async for record in source.salesforce_client.get_cases():
            assert record == expected_record
            assert source.doc_mapper.map_case(record) == expected_doc


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "response_status, response_body, expected_attachment",
    [
        (200, b"chunk1", "Y2h1bmsx"),  # base64 for "chunk1"
        (200, b"", ""),
        (404, None, None),
    ],
)
async def test_get_all_with_content_docs_when_success(
    mock_responses, response_status, response_body, expected_attachment
):
    async with create_salesforce_source() as source:
        expected_doc = {
            "_id": "content_document_id",
            "content_size": 1000,
            "created_at": "",
            "created_by": "Frodo",
            "created_by_email": "frodo@tlotr.com",
            "description": "A file about a ring.",
            "file_extension": "txt",
            "last_updated": "",
            "linked_ids": [
                "account_id",
                "campaign_id",
                "case_id",
                "contact_id",
                "lead_id",
                "opportunity_id",
            ],  # contains every SObject that is connected to this doc
            "owner": "Frodo",
            "owner_email": "frodo@tlotr.com",
            "title": "the_ring.txt",
            "type": "content_document",
            "url": f"{TEST_BASE_URL}/content_document_id",
            "version_number": "2",
            "version_url": f"{TEST_BASE_URL}/content_version_id",
        }
        if expected_attachment is not None:
            expected_doc["_attachment"] = expected_attachment

        mock_responses.get(
            TEST_FILE_DOWNLOAD_URL,
            status=response_status,
            body=response_body,
        )
        mock_responses.get(
            TEST_QUERY_MATCH_URL, repeat=True, callback=salesforce_query_callback
        )

        content_document_records = []
        async for record, _ in source.get_docs():
            if record["type"] == "content_document":
                content_document_records.append(record)

        TestCase().assertCountEqual(content_document_records, [expected_doc])


@pytest.mark.asyncio
async def test_get_all_with_content_docs_and_extraction_service(mock_responses):
    with patch(
        "connectors.content_extraction.ContentExtraction.extract_text",
        return_value="chunk1",
    ), patch(
        "connectors.content_extraction.ContentExtraction.get_extraction_config",
        return_value={"host": "http://localhost:8090"},
    ):
        async with create_salesforce_source(use_text_extraction_service=True) as source:
            expected_doc = {
                "_id": "content_document_id",
                "content_size": 1000,
                "created_at": "",
                "created_by": "Frodo",
                "created_by_email": "frodo@tlotr.com",
                "body": "chunk1",
                "description": "A file about a ring.",
                "file_extension": "txt",
                "last_updated": "",
                "linked_ids": [
                    "account_id",
                    "campaign_id",
                    "case_id",
                    "contact_id",
                    "lead_id",
                    "opportunity_id",
                ],  # contains every SObject that is connected to this doc
                "owner": "Frodo",
                "owner_email": "frodo@tlotr.com",
                "title": "the_ring.txt",
                "type": "content_document",
                "url": f"{TEST_BASE_URL}/content_document_id",
                "version_number": "2",
                "version_url": f"{TEST_BASE_URL}/content_version_id",
            }

            mock_responses.get(
                TEST_FILE_DOWNLOAD_URL,
                status=200,
                body=b"chunk1",
            )
            mock_responses.get(
                TEST_QUERY_MATCH_URL, repeat=True, callback=salesforce_query_callback
            )

            content_document_records = []
            async for record, _ in source.get_docs():
                if record["type"] == "content_document":
                    content_document_records.append(record)

            TestCase().assertCountEqual(content_document_records, [expected_doc])


@pytest.mark.asyncio
async def test_prepare_sobject_cache(mock_responses):
    async with create_salesforce_source() as source:
        sobjects = {
            "records": [
                {"Id": "id_1", "Name": "Foo", "Type": "Account"},
                {"Id": "id_2", "Name": "Bar", "Type": "Account"},
            ]
        }
        expected = {
            "id_1": {"Id": "id_1", "Name": "Foo", "Type": "Account"},
            "id_2": {"Id": "id_2", "Name": "Bar", "Type": "Account"},
        }
        mock_responses.get(
            TEST_QUERY_MATCH_URL,
            status=200,
            payload=sobjects,
        )
        sobjects = await source.salesforce_client._prepare_sobject_cache("Account")
        assert sobjects == expected


@pytest.mark.asyncio
async def test_request_when_token_invalid_refetches_token(patch_sleep, mock_responses):
    async with create_salesforce_source(mock_token=False) as source:
        payload = deepcopy(ACCOUNT_RESPONSE_PAYLOAD)
        expected_record = payload["records"][0]

        invalid_token_payload = [
            {
                "message": "Session expired or invalid",
                "errorCode": "INVALID_SESSION_ID",
            }
        ]
        token_response_payload = {"access_token": "foo"}
        mock_responses.post(
            f"{TEST_BASE_URL}/services/oauth2/token",
            status=200,
            payload=token_response_payload,
            repeat=True,
        )
        mock_responses.get(
            TEST_QUERY_MATCH_URL,
            status=401,
            payload=invalid_token_payload,
        )
        mock_responses.get(
            TEST_QUERY_MATCH_URL,
            status=200,
            payload=ACCOUNT_RESPONSE_PAYLOAD,
        )

        with mock.patch.object(
            source.salesforce_client.api_token,
            "token",
            wraps=source.salesforce_client.api_token.token,
        ) as mock_get_token:
            async for record in source.salesforce_client.get_accounts():
                assert record == expected_record
                # assert called once for initial query, called again after invalid_token_payload
                assert mock_get_token.call_count == 2


@pytest.mark.asyncio
async def test_request_when_rate_limited_raises_error_no_retries(mock_responses):
    async with create_salesforce_source() as source:
        response_payload = [
            {
                "message": "Request limit has been exceeded.",
                "errorCode": "REQUEST_LIMIT_EXCEEDED",
            }
        ]
        mock_responses.get(
            re.compile(f"{TEST_BASE_URL}/services/data/{API_VERSION}/query*"),
            status=403,
            payload=response_payload,
        )

        with pytest.raises(RateLimitedException):
            async for _ in source.salesforce_client.get_accounts():
                pass


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "error_code",
    [
        "INVALID_FIELD",
        "INVALID_TERM",
        "MALFORMED_QUERY",
    ],
)
async def test_request_when_invalid_query_raises_error_no_retries(
    mock_responses, error_code
):
    async with create_salesforce_source() as source:
        response_payload = [
            {
                "message": "Invalid query.",
                "errorCode": error_code,
            }
        ]
        mock_responses.get(
            re.compile(f"{TEST_BASE_URL}/services/data/{API_VERSION}/query*"),
            status=400,
            payload=response_payload,
        )

        with pytest.raises(InvalidQueryException):
            async for _ in source.salesforce_client.get_accounts():
                pass


@pytest.mark.asyncio
async def test_request_when_generic_400_raises_error_with_retries(
    patch_sleep, mock_responses
):
    async with create_salesforce_source() as source:
        mock_responses.get(
            TEST_QUERY_MATCH_URL,
            status=400,
            repeat=True,
        )

        with pytest.raises(ConnectorRequestError):
            async for _ in source.salesforce_client.get_accounts():
                pass


@pytest.mark.asyncio
async def test_request_when_generic_500_raises_error_with_retries(
    patch_sleep, mock_responses
):
    async with create_salesforce_source() as source:
        mock_responses.get(
            TEST_QUERY_MATCH_URL,
            status=500,
            repeat=True,
        )

        with pytest.raises(SalesforceServerError):
            async for _ in source.salesforce_client.get_accounts():
                pass


@pytest.mark.asyncio
async def test_build_soql_query_with_fields():
    expected_columns = [
        "Id",
        "CreatedDate",
        "LastModifiedDate",
        "FooField",
        "BarField",
    ]

    query = (
        SalesforceSoqlBuilder("Test")
        .with_id()
        .with_default_metafields()
        .with_fields(["FooField", "BarField"])
        .with_where("FooField = 'FOO'")
        .with_order_by("CreatedDate DESC")
        .with_limit(2)
        .build()
    )

    # SELECT Id,
    # CreatedDate,
    # LastModifiedDate,
    # FooField,
    # BarField,
    # FROM Test
    # WHERE FooField = 'FOO'
    # ORDER BY CreatedDate DESC
    # LIMIT 2

    query_columns_str = re.search("SELECT (.*)\nFROM", query, re.DOTALL).group(1)
    query_columns = query_columns_str.split(",\n")

    TestCase().assertCountEqual(query_columns, expected_columns)
    assert query.startswith("SELECT ")
    assert query.endswith(
        "FROM Test\nWHERE FooField = 'FOO'\nORDER BY CreatedDate DESC\nLIMIT 2"
    )


@pytest.mark.asyncio
async def test_combine_duplicate_content_docs_with_duplicates():
    async with create_salesforce_source(mock_queryables=False) as source:
        content_docs = [
            {
                "Id": "content_doc_1",
                "linked_sobject_id": "account_id",
            },
            {
                "Id": "content_doc_1",
                "linked_sobject_id": "case_id",
            },
            {"Id": "content_doc_2", "linked_sobject_id": "account_id"},
        ]
        expected_docs = [
            {"Id": "content_doc_1", "linked_ids": ["account_id", "case_id"]},
            {"Id": "content_doc_2", "linked_ids": ["account_id"]},
        ]

        combined_docs = source._combine_duplicate_content_docs(content_docs)
        TestCase().assertCountEqual(combined_docs, expected_docs)
