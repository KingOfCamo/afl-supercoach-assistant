from __future__ import annotations

from src.scrapers.footywire import _parse_salary, _parse_score, _parse_value


def test_parse_salary_normal():
    assert _parse_salary("$521,800") == 521800


def test_parse_salary_no_comma():
    assert _parse_salary("$100000") == 100000


def test_parse_salary_empty():
    assert _parse_salary("") is None
    assert _parse_salary(None) is None


def test_parse_salary_spaces():
    assert _parse_salary("  $300,000  ") == 300000


def test_parse_score_normal():
    assert _parse_score("120") == 120


def test_parse_score_dnp():
    assert _parse_score("DNP") is None
    assert _parse_score("EMG") is None
    assert _parse_score("-") is None
    assert _parse_score("") is None


def test_parse_score_spaces():
    assert _parse_score("  95  ") == 95


def test_parse_value_normal():
    assert _parse_value("27.3") == 27.3


def test_parse_value_integer():
    assert _parse_value("30") == 30.0


def test_parse_value_empty():
    assert _parse_value("") is None
    assert _parse_value("-") is None
    assert _parse_value(None) is None
