# 判分逻辑:数据治理评审 Expectation 族的外部行为与公开契约。
#
# 覆盖两个方向:
#   1. 新增的四条格式合规 Expectation(UUID / ISO 8601 / 信用卡号 / IBAN)的
#      功能契约、边界情况、序列化往返和 mostly 语义;
#   2. 既有 `expect_column_values_to_be_json_parseable` 的收紧回归:
#      顶层必须是 JSON 对象或数组,primitive / 空串 / 非字符串一律非法。
#
# 所有期望值均为内置 golden 字面量,不依赖 /app 的实时状态计算。
from __future__ import annotations

import pandas as pd
import pytest

import great_expectations as gx
import great_expectations.expectations as gxe

# ---------------------------------------------------------------------------
# golden 数据
# ---------------------------------------------------------------------------

V1_UUID = "1b671a64-40d5-11ee-be56-0242ac120002"
V3_UUID = "6fa459ea-ee8a-3ca4-894e-db77e160355e"
V4_UUID = "6f1c2e4a-9d3b-4a7e-8c1d-2b5a6f7e8d9c"
V4_UUID_UPPER = "A987FBC9-4BED-4078-8F07-0081B0BA4590"
V5_UUID = "987fbc97-4bed-5078-af07-9141ba07c9f3"
ALL_VERSION_UUIDS = [V1_UUID, V3_UUID, V4_UUID, V4_UUID_UPPER, V5_UUID]

UUID_INVALID_FORMATS = [
    "{6f1c2e4a-9d3b-4a7e-8c1d-2b5a6f7e8d9c}",  # 花括号包裹
    "6f1c2e4a9d3b4a7e8c1d2b5a6f7e8d9c",  # 32 位无连字符
    "urn:uuid:6f1c2e4a-9d3b-4a7e-8c1d-2b5a6f7e8d9c",  # urn 前缀
    "6f1c2e4a-9d3b-4a7e-8c1d-2b5a6f7e8d9",  # 35 字符,少一位
    "6f1c2e4a-9d3b-4a7e-8c1d-2b5a6f7e8d9cg",  # 非 hex 字符
    "not-a-uuid",
    "",
]

# 前后空白 / 内部空白也应该被视为非法(canonical 8-4-4-4-12 才算)
UUID_WHITESPACE_INVALID = [
    " 6f1c2e4a-9d3b-4a7e-8c1d-2b5a6f7e8d9c",
    "6f1c2e4a-9d3b-4a7e-8c1d-2b5a6f7e8d9c ",
    "\t6f1c2e4a-9d3b-4a7e-8c1d-2b5a6f7e8d9c",
    "6f1c2e4a-9d3b-4a7e-8c1d-2b5a6f7e8d9c\n",
    "6f1c2e4a-9d3b 4a7e-8c1d-2b5a6f7e8d9c",  # 内部空白替换连字符
]

ISO_VALID = [
    "2026-01-15",
    "2026-12-31",
    "2000-02-29",  # 400 年闰年
    "2024-02-29",  # 4 年闰年
    "2026-01-15T10:30:00",
    "2026-01-15T10:30:00Z",
    "2026-01-15T10:30:00.123456+05:30",
    "2026-12-31 23:59:59",
    "2026-06-30T00:00:00-08:00",
]
ISO_INVALID = [
    "2026-13-45",  # 月份 13
    "2026-01-15T25:00:00",  # 小时 25
    "2023-02-29",  # 非闰年 2 月 29 日
    "2026-01-15T10:30:61",  # 秒 61
    "2026-04-31",  # 4 月没有 31 日
    "20260115",  # 紧凑记法
    "01/15/2026",  # 美式记法
    "January 15, 2026",
    "10:30:00",  # 裸时间
    "2026-01",  # 缺日
    "",
]
# 更严的边界:百年非闰、6 月 31、2 月 30、24:00、非法时区偏移
ISO_HARD_INVALID = [
    "2100-02-29",  # 世纪年非闰
    "2024-06-31",  # 6 月只有 30 天
    "2026-02-30",
    "2026-04-31T12:00:00",
    "2026-01-15T24:00:00",  # Python fromisoformat 拒绝 24:00
    "2026-01-15T10:30:00+25:00",
]

CCN_VALID = [
    "4532015112830366",
    "5425233430109903",
    "378282246310005",
    "6011111111111117",
    "30569309025904",
    "4532 0151 1283 0366",
    "5425 2334 3010 9903",
    "3782-822463-10005",
    "6011 1111 1111 1117",
    "3056 9309 0259 04",
]
CCN_BAD_CHECKSUM = [
    "4532015112830367",
    "5425233430109904",
    "6011111111111118",
    "30569309025905",
    "378282246310006",
]
CCN_BAD_STRUCTURE = [
    "123456789012",  # 12 位,过短
    "45320151128303661234567890",  # 20 位,过长
    "4532-0151-1283-03ab",  # 含字母
    "4532@015112830366",  # 非法分隔符
    "",
]

IBAN_REGISTRY_EXAMPLES = [
    "AD1200012030200359100100",
    "AE070331234567890123456",
    "AL47212110090000000235698741",
    "AT611904300234573201",
    "AZ21NABZ00000000137010001944",
    "BA391290079401028494",
    "BE68539007547034",
    "BG80BNBG96611020345678",
    "BH67BMAG00001299123456",
    "BR9700360305000010009795493P1",
    "BY13NBRB3600900000002Z00AB00",
    "CH9300762011623852957",
    "CR05015202001026284066",
    "CY17002001280000001200527600",
    "CZ6508000000192000145399",
    "DE89370400440532013000",
    "DK5000400440116243",
    "EE382200221020145685",
    "ES9121000418450200051332",
    "FI2112345600000785",
    "FO6264600001631634",
    "FR1420041010050500013M02606",
    "GB29NWBK60161331926819",
    "GE29NB0000000101904917",
    "GI75NWBK000000007099453",
    "GR1601101250000000012300695",
    "HR1210010051863000160",
    "HU42117730161111101800000000",
    "IE29AIBK93115212345678",
    "IS140159260076545510730339",
    "IT60X0542811101000000123456",
    "KW81CBKU0000000000001234560101",
    "LB62099900000001001901229114",
    "LI21088100002324013AA",
    "LT121000011101001000",
    "LU280019400644750000",
    "LV80BANK0000435195001",
    "MC5813488000010051108001292",
    "MD24AG000225100013104168",
    "ME25505000012345678951",
    "MK07250120000058984",
    "MT84MALT011000012345MTLCAST001S",
    "NL91ABNA0417164300",
    "NO9386011117947",
    "PL61109010140000071219812874",
    "PS92PALS000000000400123456702",
    "PT50000201231234567890154",
    "QA58DOHB00001234567890ABCDEFG",
    "RO49AAAA1B31007593840000",
    "RS35260005601001611379",
    "SA0380000000608010167519",
    "SE4550000000058398257466",
    "SI56191000000123438",
    "SK3112000000198742637541",
    "SM86U0322509800000000270100",
    "TL380080012345678910157",
    "TN5910006035183598478831",
    "TR330006100519786457841326",
    "VA59001123000012345678",
    "VG96VPVG0000012345678901",
    "XK051212012345678906",
]
IBAN_FORMATTED_VALID = [
    "GB29 NWBK 6016 1331 9268 19",
    "de89 3704 0044 0532 0130 00",
    "FR14 2004 1010 0505 0001 3M02 606",
    "NL91 ABNA 0417 1643 00",
    "ES91 2100 0418 4502 0005 1332",
    # tab / newline 也算空白
    "GB29\tNWBK\t6016\t1331\t9268\t19",
    "GB29 NWBK\n6016 1331 9268 19",
    # 混合大小写
    "gb29NWBK60161331926819",
    "GB29nwbk60161331926819",
]
IBAN_BAD_CHECKSUM = [
    "GB29NWBK60161331926818",
    "DE89370400440532013001",
    "NL91ABNA0417164301",
    "ES9121000418450200051333",
    "FR1420041010050500013M02607",
]
IBAN_BAD_REGISTRY = [
    "XX89370400440532013000",  # 国家码不在 IBAN 注册表
    "US29NWBK60161331926819",  # 美国不使用 IBAN
    "GB29NWBK6016",  # GB 长度不足
    "DE893704004405320130001234",  # DE 长度超出
    "IT60X054281110100000012",  # IT 长度不足
]

# JSON 记录:顶层必须是对象或数组
JSON_DOC_VALID = [
    "{}",
    "[]",
    '{"a": 1}',
    "[1, 2, 3]",
    '{"nested": {"x": [1, 2]}, "y": null}',
    '[{"row": 1}, {"row": 2}]',
]
JSON_PRIMITIVE_INVALID = [
    "null",
    "true",
    "false",
    "123",
    "45.6",
    '"a string"',
    "0",
]
JSON_WHITESPACE_INVALID = [
    "",
    "   ",
    "\t",
    "\n\t\n",
]
JSON_MALFORMED_INVALID = [
    "{",
    "[1, 2,",
    "not json at all",
    '{"unclosed": ',
]

EXPECTED_RESULT_KEYS = {
    "element_count",
    "unexpected_count",
    "unexpected_percent",
    "partial_unexpected_list",
    "missing_count",
    "missing_percent",
}


# ---------------------------------------------------------------------------
# 工具
# ---------------------------------------------------------------------------


@pytest.fixture(scope="module")
def batch_factory():
    context = gx.get_context(mode="ephemeral")
    datasource = context.data_sources.add_pandas("pandas")
    asset = datasource.add_dataframe_asset("governance_data")
    batch_definition = asset.add_batch_definition_whole_dataframe("bd")

    def make(values, column="c"):
        return batch_definition.get_batch(
            batch_parameters={"dataframe": pd.DataFrame({column: values})}
        )

    return make


@pytest.fixture(scope="module")
def uuid_expectation():
    return gxe.ExpectColumnValuesToBeValidUuid


@pytest.fixture(scope="module")
def iso_expectation():
    return gxe.ExpectColumnValuesToBeValidIso8601Datetime


@pytest.fixture(scope="module")
def ccn_expectation():
    return gxe.ExpectColumnValuesToBeValidCreditCardNumber


@pytest.fixture(scope="module")
def iban_expectation():
    return gxe.ExpectColumnValuesToBeValidIban


@pytest.fixture(scope="module")
def json_expectation():
    return gxe.ExpectColumnValuesToBeJsonParseable


# ---------------------------------------------------------------------------
# 公开契约:注册与命名
# ---------------------------------------------------------------------------


def test_expectations_importable_from_public_namespaces():
    from great_expectations.expectations.core import (
        ExpectColumnValuesToBeJsonParseable,
        ExpectColumnValuesToBeValidCreditCardNumber,
        ExpectColumnValuesToBeValidIban,
        ExpectColumnValuesToBeValidIso8601Datetime,
        ExpectColumnValuesToBeValidUuid,
    )

    assert gxe.ExpectColumnValuesToBeValidUuid is ExpectColumnValuesToBeValidUuid
    assert gxe.ExpectColumnValuesToBeValidIso8601Datetime is (
        ExpectColumnValuesToBeValidIso8601Datetime
    )
    assert gxe.ExpectColumnValuesToBeValidCreditCardNumber is (
        ExpectColumnValuesToBeValidCreditCardNumber
    )
    assert gxe.ExpectColumnValuesToBeValidIban is ExpectColumnValuesToBeValidIban
    assert gxe.ExpectColumnValuesToBeJsonParseable is ExpectColumnValuesToBeJsonParseable


def test_expectation_type_naming(uuid_expectation, iso_expectation, ccn_expectation, iban_expectation):
    assert uuid_expectation(column="c").expectation_type == (
        "expect_column_values_to_be_valid_uuid"
    )
    assert iso_expectation(column="c").expectation_type == (
        "expect_column_values_to_be_valid_iso8601_datetime"
    )
    assert ccn_expectation(column="c").expectation_type == (
        "expect_column_values_to_be_valid_credit_card_number"
    )
    assert iban_expectation(column="c").expectation_type == (
        "expect_column_values_to_be_valid_iban"
    )


def test_json_expectation_public_contract_preserved(json_expectation):
    """收紧后的 JSON 记录校验必须保留原有的公开契约(名称 / map_metric / args)。"""
    # 原有的 expectation_type 不能改
    assert json_expectation(column="c").expectation_type == (
        "expect_column_values_to_be_json_parseable"
    )
    # 原有的 map_metric 不能改
    assert json_expectation.map_metric == "column_values.json_parseable"
    # 原有的 args_keys / success_keys 不能改
    assert json_expectation.args_keys == ("column",)
    assert json_expectation.success_keys == ("mostly",)


def test_configuration_serialization_round_trip(uuid_expectation):
    from great_expectations.expectations.expectation_configuration import (
        ExpectationConfiguration,
    )

    expectation = uuid_expectation(column="ids", uuid_version=4, mostly=0.9)
    config_dict = expectation.configuration.to_json_dict()
    assert config_dict["type"] == "expect_column_values_to_be_valid_uuid"

    rebuilt = ExpectationConfiguration(**config_dict)
    assert rebuilt.type == "expect_column_values_to_be_valid_uuid"
    assert rebuilt.kwargs["column"] == "ids"
    assert rebuilt.kwargs["uuid_version"] == 4
    assert rebuilt.kwargs["mostly"] == 0.9


def test_all_new_expectations_serialization_round_trip(
    iso_expectation, ccn_expectation, iban_expectation
):
    """所有新增的四条 Expectation 都应能完成配置的 to_json_dict → 重建 往返。"""
    from great_expectations.expectations.expectation_configuration import (
        ExpectationConfiguration,
    )

    for factory, expected_type in [
        (iso_expectation, "expect_column_values_to_be_valid_iso8601_datetime"),
        (ccn_expectation, "expect_column_values_to_be_valid_credit_card_number"),
        (iban_expectation, "expect_column_values_to_be_valid_iban"),
    ]:
        expectation = factory(column="col", mostly=0.8)
        config_dict = expectation.configuration.to_json_dict()
        assert config_dict["type"] == expected_type
        rebuilt = ExpectationConfiguration(**config_dict)
        assert rebuilt.type == expected_type
        assert rebuilt.kwargs["column"] == "col"
        assert rebuilt.kwargs["mostly"] == 0.8


# ---------------------------------------------------------------------------
# UUID
# ---------------------------------------------------------------------------


def test_uuid_all_versions_accepted_without_constraint(batch_factory, uuid_expectation):
    result = batch_factory(ALL_VERSION_UUIDS).validate(uuid_expectation(column="c"))
    assert result.success
    assert result.result["unexpected_count"] == 0
    assert EXPECTED_RESULT_KEYS.issubset(set(result.result.keys()))


def test_uuid_non_canonical_representations_rejected(batch_factory, uuid_expectation):
    result = batch_factory(UUID_INVALID_FORMATS).validate(uuid_expectation(column="c"))
    assert not result.success
    assert result.result["unexpected_count"] == len(UUID_INVALID_FORMATS)
    assert set(result.result["partial_unexpected_list"]) == set(UUID_INVALID_FORMATS)


def test_uuid_whitespace_variants_rejected(batch_factory, uuid_expectation):
    """canonical UUID 前后 / 内部混入空白都必须视为非法。"""
    result = batch_factory(UUID_WHITESPACE_INVALID).validate(uuid_expectation(column="c"))
    assert not result.success
    assert result.result["unexpected_count"] == len(UUID_WHITESPACE_INVALID)
    assert set(result.result["partial_unexpected_list"]) == set(UUID_WHITESPACE_INVALID)


def test_uuid_version_constraint_filters_exactly(batch_factory, uuid_expectation):
    expected_unexpected = {
        1: {V3_UUID, V4_UUID, V4_UUID_UPPER, V5_UUID},
        3: {V1_UUID, V4_UUID, V4_UUID_UPPER, V5_UUID},
        4: {V1_UUID, V3_UUID, V5_UUID},
        5: {V1_UUID, V3_UUID, V4_UUID, V4_UUID_UPPER},
    }
    for version, unexpected in expected_unexpected.items():
        result = batch_factory(ALL_VERSION_UUIDS).validate(
            uuid_expectation(column="c", uuid_version=version)
        )
        assert not result.success, f"uuid_version={version} 应当失败"
        assert result.result["unexpected_count"] == len(unexpected)
        assert set(result.result["partial_unexpected_list"]) == unexpected


def test_uuid_version_constraint_success(batch_factory, uuid_expectation):
    result = batch_factory([V4_UUID, V4_UUID_UPPER, V4_UUID]).validate(
        uuid_expectation(column="c", uuid_version=4)
    )
    assert result.success


@pytest.mark.parametrize("bad_version", [0, 2, 6, 7, 15, -1])
def test_uuid_unsupported_version_rejected_at_construction(uuid_expectation, bad_version):
    with pytest.raises(ValueError):
        uuid_expectation(column="c", uuid_version=bad_version)


def test_uuid_nulls_are_missing_not_unexpected(batch_factory, uuid_expectation):
    result = batch_factory([V4_UUID, None, "not-a-uuid", None, None]).validate(
        uuid_expectation(column="c")
    )
    assert not result.success
    assert result.result["element_count"] == 5
    assert result.result["missing_count"] == 3
    assert result.result["unexpected_count"] == 1


def test_uuid_mostly_semantics(batch_factory, uuid_expectation):
    values = [V4_UUID, None, "not-a-uuid", None, None]  # 非空值中 1/2 合法
    assert batch_factory(values).validate(uuid_expectation(column="c", mostly=0.5)).success
    assert not batch_factory(values).validate(
        uuid_expectation(column="c", mostly=0.6)
    ).success


def test_uuid_result_format_complete_lists_unexpected(batch_factory, uuid_expectation):
    result = batch_factory([V4_UUID, V1_UUID, V5_UUID]).validate(
        uuid_expectation(column="c", uuid_version=4), result_format="COMPLETE"
    )
    assert not result.success
    assert set(result.result["unexpected_list"]) == {V1_UUID, V5_UUID}


# ---------------------------------------------------------------------------
# ISO 8601
# ---------------------------------------------------------------------------


def test_iso8601_valid_dates_and_datetimes(batch_factory, iso_expectation):
    result = batch_factory(ISO_VALID).validate(iso_expectation(column="c"))
    assert result.success
    assert result.result["unexpected_count"] == 0


def test_iso8601_invalid_values_rejected(batch_factory, iso_expectation):
    result = batch_factory(ISO_INVALID).validate(iso_expectation(column="c"))
    assert not result.success
    assert result.result["unexpected_count"] == len(ISO_INVALID)
    assert set(result.result["partial_unexpected_list"]) == set(ISO_INVALID)


def test_iso8601_hard_invalid_edge_cases_rejected(batch_factory, iso_expectation):
    """更严的日历合法性:世纪年非闰、越界日、24:00、非法偏移都必须拒。"""
    result = batch_factory(ISO_HARD_INVALID).validate(iso_expectation(column="c"))
    assert not result.success
    assert result.result["unexpected_count"] == len(ISO_HARD_INVALID)


def test_iso8601_nulls_are_missing_not_unexpected(batch_factory, iso_expectation):
    result = batch_factory(["2026-01-15", None, "not-a-date", None, None]).validate(
        iso_expectation(column="c")
    )
    assert not result.success
    assert result.result["element_count"] == 5
    assert result.result["missing_count"] == 3
    assert result.result["unexpected_count"] == 1


def test_iso8601_mostly_semantics(batch_factory, iso_expectation):
    values = ["2026-01-15", "2026-13-45", "2026-12-31", "not-a-date", ""]  # 2/5 合法
    assert batch_factory(values).validate(iso_expectation(column="c", mostly=0.4)).success
    assert not batch_factory(values).validate(iso_expectation(column="c", mostly=0.5)).success


# ---------------------------------------------------------------------------
# 信用卡号(Luhn)
# ---------------------------------------------------------------------------


def test_credit_card_valid_numbers(batch_factory, ccn_expectation):
    result = batch_factory(CCN_VALID).validate(ccn_expectation(column="c"))
    assert result.success
    assert result.result["unexpected_count"] == 0


def test_credit_card_checksum_failures_rejected(batch_factory, ccn_expectation):
    result = batch_factory(CCN_BAD_CHECKSUM).validate(ccn_expectation(column="c"))
    assert not result.success
    assert result.result["unexpected_count"] == len(CCN_BAD_CHECKSUM)


def test_credit_card_structural_failures_rejected(batch_factory, ccn_expectation):
    result = batch_factory(CCN_BAD_STRUCTURE).validate(ccn_expectation(column="c"))
    assert not result.success
    assert result.result["unexpected_count"] == len(CCN_BAD_STRUCTURE)
    assert set(result.result["partial_unexpected_list"]) == set(CCN_BAD_STRUCTURE)


def test_credit_card_numeric_values_are_not_strings(batch_factory, ccn_expectation):
    values = ["4532015112830366", 4532015112830366, "5425233430109903", 30569309025904]
    result = batch_factory(values).validate(ccn_expectation(column="c"))
    assert not result.success
    assert result.result["element_count"] == 4
    assert result.result["unexpected_count"] == 2


def test_credit_card_boolean_values_are_not_strings(batch_factory, ccn_expectation):
    """bool 是 int 的子类,不能算合法卡号字符串。"""
    values = ["4532015112830366", True, "5425233430109903", False]
    result = batch_factory(values).validate(ccn_expectation(column="c"))
    assert not result.success
    assert result.result["element_count"] == 4
    assert result.result["unexpected_count"] == 2


def test_credit_card_nulls_are_missing_not_unexpected(batch_factory, ccn_expectation):
    result = batch_factory(
        ["4532015112830366", None, "4532015112830367", None, "5425233430109903"]
    ).validate(ccn_expectation(column="c"))
    assert not result.success
    assert result.result["element_count"] == 5
    assert result.result["missing_count"] == 2
    assert result.result["unexpected_count"] == 1


def test_credit_card_mostly_semantics(batch_factory, ccn_expectation):
    values = [
        "4532015112830366",
        "4532015112830367",
        "5425233430109903",
        "123456789012",
        "6011111111111117",
    ]  # 3/5 合法
    assert batch_factory(values).validate(ccn_expectation(column="c", mostly=0.5)).success
    assert not batch_factory(values).validate(ccn_expectation(column="c", mostly=0.75)).success


# ---------------------------------------------------------------------------
# IBAN
# ---------------------------------------------------------------------------


def test_iban_registry_examples_all_countries(batch_factory, iban_expectation):
    result = batch_factory(IBAN_REGISTRY_EXAMPLES).validate(iban_expectation(column="c"))
    assert result.success
    assert result.result["unexpected_count"] == 0


def test_iban_whitespace_and_lowercase_accepted(batch_factory, iban_expectation):
    """空格 / tab / newline 分组 + 混合大小写都要正确归一化。"""
    result = batch_factory(IBAN_FORMATTED_VALID).validate(iban_expectation(column="c"))
    assert result.success


def test_iban_checksum_failures_rejected(batch_factory, iban_expectation):
    result = batch_factory(IBAN_BAD_CHECKSUM).validate(iban_expectation(column="c"))
    assert not result.success
    assert result.result["unexpected_count"] == len(IBAN_BAD_CHECKSUM)
    assert set(result.result["partial_unexpected_list"]) == set(IBAN_BAD_CHECKSUM)


def test_iban_unknown_country_and_wrong_length_rejected(batch_factory, iban_expectation):
    result = batch_factory(IBAN_BAD_REGISTRY).validate(iban_expectation(column="c"))
    assert not result.success
    assert result.result["unexpected_count"] == len(IBAN_BAD_REGISTRY)


def test_iban_nulls_are_missing_not_unexpected(batch_factory, iban_expectation):
    result = batch_factory(
        ["GB29NWBK60161331926819", None, "GB29NWBK60161331926818", None]
    ).validate(iban_expectation(column="c"))
    assert not result.success
    assert result.result["element_count"] == 4
    assert result.result["missing_count"] == 2
    assert result.result["unexpected_count"] == 1


def test_iban_mostly_semantics(batch_factory, iban_expectation):
    values = [
        "GB29NWBK60161331926819",
        "GB29NWBK60161331926818",
        "DE89370400440532013000",
        "XX89370400440532013000",
        "NL91ABNA0417164300",
    ]  # 3/5 合法
    assert batch_factory(values).validate(iban_expectation(column="c", mostly=0.5)).success
    assert not batch_factory(values).validate(iban_expectation(column="c", mostly=0.7)).success


def test_iban_result_format_complete_lists_unexpected(batch_factory, iban_expectation):
    result = batch_factory(IBAN_BAD_CHECKSUM).validate(
        iban_expectation(column="c"), result_format="COMPLETE"
    )
    assert not result.success
    assert set(result.result["unexpected_list"]) == set(IBAN_BAD_CHECKSUM)


# ---------------------------------------------------------------------------
# 收紧后的 `expect_column_values_to_be_json_parseable`
# ---------------------------------------------------------------------------


def test_json_documents_accepted(batch_factory, json_expectation):
    """顶层 JSON 对象 / 数组是合法 JSON 记录。"""
    result = batch_factory(JSON_DOC_VALID).validate(json_expectation(column="c"))
    assert result.success
    assert result.result["unexpected_count"] == 0
    assert result.result["element_count"] == len(JSON_DOC_VALID)


def test_json_primitives_rejected(batch_factory, json_expectation):
    """JSON primitive(number / bool / null / bare quoted string)都不是 JSON 记录。"""
    result = batch_factory(JSON_PRIMITIVE_INVALID).validate(json_expectation(column="c"))
    assert not result.success
    assert result.result["unexpected_count"] == len(JSON_PRIMITIVE_INVALID)
    assert set(result.result["partial_unexpected_list"]) == set(JSON_PRIMITIVE_INVALID)


def test_json_whitespace_and_empty_rejected(batch_factory, json_expectation):
    result = batch_factory(JSON_WHITESPACE_INVALID).validate(json_expectation(column="c"))
    assert not result.success
    assert result.result["unexpected_count"] == len(JSON_WHITESPACE_INVALID)


def test_json_malformed_rejected(batch_factory, json_expectation):
    result = batch_factory(JSON_MALFORMED_INVALID).validate(json_expectation(column="c"))
    assert not result.success
    assert result.result["unexpected_count"] == len(JSON_MALFORMED_INVALID)


def test_json_mostly_after_tightening(batch_factory, json_expectation):
    """在收紧后的语义下,mostly 应按'非空样本里顶层是对象/数组'的占比计算。"""
    values = ['{"a": 1}', "null", "123", "[1,2,3]", '{"b": 2}']  # 3/5 合法
    assert batch_factory(values).validate(json_expectation(column="c", mostly=0.5)).success
    assert not batch_factory(values).validate(
        json_expectation(column="c", mostly=0.7)
    ).success


def test_json_result_format_complete_lists_unexpected(batch_factory, json_expectation):
    """收紧后仍保留 result_format=COMPLETE 的完整违规清单契约。"""
    values = ['{"ok": 1}', "true", "[1]", "null"]  # 2/4 合法
    result = batch_factory(values).validate(
        json_expectation(column="c"), result_format="COMPLETE"
    )
    assert not result.success
    assert set(result.result["unexpected_list"]) == {"true", "null"}


def test_json_null_values_treated_as_missing(batch_factory, json_expectation):
    """None 值应走 missing 通道,不进入 unexpected。"""
    result = batch_factory(['{"ok": 1}', None, "null", None]).validate(
        json_expectation(column="c")
    )
    assert not result.success
    assert result.result["element_count"] == 4
    assert result.result["missing_count"] == 2
    # 剩下两个非空样本里,"null"(primitive)是 unexpected,'{"ok": 1}' 合法
    assert result.result["unexpected_count"] == 1
    assert result.result["partial_unexpected_list"] == ["null"]


# ---------------------------------------------------------------------------
# suite 级组合验证
# ---------------------------------------------------------------------------


def test_suite_end_to_end_statistics(
    uuid_expectation, iso_expectation, ccn_expectation, iban_expectation
):
    context = gx.get_context(mode="ephemeral")
    datasource = context.data_sources.add_pandas("pandas")
    asset = datasource.add_dataframe_asset("governance_suite_data")
    batch_definition = asset.add_batch_definition_whole_dataframe("bd")
    df = pd.DataFrame(
        {
            "uuids": ALL_VERSION_UUIDS,
            "timestamps": ISO_INVALID[:5],
            "cards": CCN_VALID[:5],
            "ibans": IBAN_BAD_CHECKSUM,
        }
    )
    batch = batch_definition.get_batch(batch_parameters={"dataframe": df})
    suite = gx.ExpectationSuite(
        name="governance_review",
        expectations=[
            uuid_expectation(column="uuids"),
            iso_expectation(column="timestamps"),
            ccn_expectation(column="cards"),
            iban_expectation(column="ibans"),
        ],
    )
    result = batch.validate(suite)
    assert not result.success
    assert result.statistics["evaluated_expectations"] == 4
    assert result.statistics["successful_expectations"] == 2
    assert result.statistics["unsuccessful_expectations"] == 2


def test_suite_end_to_end_including_json_tightening(
    uuid_expectation, iso_expectation, ccn_expectation, iban_expectation, json_expectation
):
    """把收紧后的 JSON 记录校验和四条新校验一起组合成 Suite,统计口径必须一致。

    - json_records: 3 个是记录,2 个是 primitive -> 收紧前会全绿(此断言在纯净仓库
      失败),收紧后 unexpected_count=2、Expectation 失败。
    - 其他 4 列都全绿。
    """
    context = gx.get_context(mode="ephemeral")
    datasource = context.data_sources.add_pandas("pandas")
    asset = datasource.add_dataframe_asset("governance_full_data")
    batch_definition = asset.add_batch_definition_whole_dataframe("bd")
    df = pd.DataFrame(
        {
            "uuids": ALL_VERSION_UUIDS,
            "timestamps": ISO_VALID[:5],
            "cards": CCN_VALID[:5],
            "ibans": IBAN_REGISTRY_EXAMPLES[:5],
            "json_records": ['{"a": 1}', "[1,2]", '{"b": 2}', "null", "123"],
        }
    )
    batch = batch_definition.get_batch(batch_parameters={"dataframe": df})
    suite = gx.ExpectationSuite(
        name="governance_review_with_json_tightening",
        expectations=[
            uuid_expectation(column="uuids"),
            iso_expectation(column="timestamps"),
            ccn_expectation(column="cards"),
            iban_expectation(column="ibans"),
            json_expectation(column="json_records"),
        ],
    )
    result = batch.validate(suite)
    assert not result.success
    assert result.statistics["evaluated_expectations"] == 5
    assert result.statistics["successful_expectations"] == 4
    assert result.statistics["unsuccessful_expectations"] == 1


# ---------------------------------------------------------------------------
# 回归:收紧后 JSON parseable 仍保留原有的 prescriptive 渲染契约
# ---------------------------------------------------------------------------


def test_json_prescriptive_template_unchanged(json_expectation):
    """收紧只调整判定语义,prescriptive 渲染模板必须与仓库既有的模板一致。"""
    from great_expectations.render.renderer_configuration import RendererConfiguration

    config_input = json_expectation(column="col", mostly=0.9).configuration
    rc = RendererConfiguration(configuration=config_input)
    rc = json_expectation._prescriptive_template(rc)

    assert "values must be parseable as JSON" in rc.template_str
    assert "$column" in rc.template_str
