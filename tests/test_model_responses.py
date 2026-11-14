"""Regression coverage for OCR output formats and digit preservation."""
from app.cloud import response_fields


def test_bare_digits_preserve_leading_zeros_without_financial_fields():
    result=response_fields('  00002468\n')
    assert result['checks']==['00002468']
    assert result['fields']=={} and result['items']==[]


def test_digit_array_rejects_json_numbers_that_lost_zeros():
    result=response_fields('["00002468", 2468, "12/34", "אב123"]')
    assert result['checks']==['00002468']


def test_uncertain_or_formatted_ocr_is_not_silently_repaired_into_check():
    for output in ('00?02468','00/02468','maybe 00002468','$00002468$'):
        assert response_fields(output)['checks']==[]
