#!/usr/bin/env python3
# -*- coding: utf-8 -*-

from struct import pack
import pytest

from egi_pynetstation.eci import package_event

valid_start = 1.0
valid_duration = 0.001
valid_type = 'abcd'
valid_label = 'label'
valid_description = 'description'
valid_data = {
    'bool': True,
    'numb': 1.01,
    'uint': 1,
    'text': 'dog',
}

valid_start_ms = 1000
valid_duration_ms = 1


# Exception Testing
def test_invalid_start_type():
    """Ensure non-float or <0s start triggers error"""
    with pytest.raises(TypeError) as e:
        package_event(
            'cat',
            valid_duration,
            valid_type,
            valid_label,
            valid_description,
            valid_data
        )
    assert 'Event start should be number or str, is' in str(e.value)

    with pytest.raises(TypeError) as e:
        package_event(
            -1,
            valid_duration,
            valid_type,
            valid_label,
            valid_description,
            valid_data
        )
    assert 'Event start should be >= 0, is ' in str(e.value)


def test_invalid_duration_type():
    """Ensure non-float duration or duration <1ms triggers error"""
    with pytest.raises(TypeError) as e:
        package_event(
            valid_start,
            'cat',
            valid_type,
            valid_label,
            valid_description,
            valid_data
        )
    assert 'Event duration should be number' in str(e.value)

    with pytest.raises(TypeError) as e:
        package_event(
            valid_start,
            0,
            valid_type,
            valid_label,
            valid_description,
            valid_data
        )
    assert 'Event duration should be at least 0.001' in str(e.value)


def test_invalid_type_type():
    with pytest.raises(TypeError) as e:
        package_event(
            valid_start,
            valid_duration,
            1.0,
            valid_label,
            valid_description,
            valid_data
        )
    assert 'Event type should be str' in str(e.value)

    with pytest.raises(TypeError) as e:
        package_event(
            valid_start,
            valid_duration,
            'abc',
            valid_label,
            valid_description,
            valid_data
        )
    assert 'Event type should have 4 characters' in str(e.value)


def test_invalid_label_type():
    with pytest.raises(TypeError) as e:
        package_event(
            valid_start,
            valid_duration,
            valid_type,
            1.0,
            valid_description,
            valid_data
        )
    assert 'Event label should be str' in str(e.value)

    with pytest.raises(TypeError) as e:
        package_event(
            valid_start,
            valid_duration,
            valid_type,
            ' ' * 257,
            valid_description,
            valid_data
        )
    assert 'Event label should be <= 256 characters' in str(e.value)


def test_invalid_description_type():
    with pytest.raises(TypeError) as e:
        package_event(
            valid_start,
            valid_duration,
            valid_type,
            valid_label,
            1.0,
            valid_data
        )
    assert 'Event description should be str' in str(e.value)

    with pytest.raises(TypeError) as e:
        package_event(
            valid_start,
            valid_duration,
            valid_type,
            valid_label,
            ' ' * 257,
            valid_data
        )
    assert 'Event description should be <= 256 characters' in str(e.value)


def test_invalid_data_types():
    with pytest.raises(TypeError) as e:
        package_event(
            valid_start,
            valid_duration,
            valid_type,
            valid_label,
            valid_description,
            'cat'
        )
    assert 'Event data should be dict' in str(e.value)

    with pytest.raises(TypeError) as e:
        package_event(
            valid_start,
            valid_duration,
            valid_type,
            valid_label,
            valid_description,
            {5: 'dog'}
        )
    assert 'Event data keys should be str' in str(e.value)

    with pytest.raises(TypeError) as e:
        package_event(
            valid_start,
            valid_duration,
            valid_type,
            valid_label,
            valid_description,
            {'cat': 'dog'}
        )
    assert 'Event data keys should have 4 characters' in str(e.value)

    with pytest.raises(TypeError) as e:
        package_event(
            valid_start,
            valid_duration,
            valid_type,
            valid_label,
            valid_description,
            {'aardvark': 1}
        )
    assert 'Event data keys should have 4 characters' in str(e.value)

    with pytest.raises(TypeError) as e:
        package_event(
            valid_start,
            valid_duration,
            valid_type,
            valid_label,
            valid_description,
            {'dogs': bytes('cat', 'ascii')}
        )
    long_str = 'Event data values should be str, bool, or numeric'
    assert long_str in str(e.value)


def test_check_valid_output():
    block = (
        pack('i', valid_start_ms) +
        pack('I', valid_duration_ms) +
        bytes(valid_type, 'ascii') +
        pack('B', len(valid_label)) +
        bytes(valid_label, 'ascii') +
        pack('B', len(valid_description)) +
        bytes(valid_description, 'ascii') +
        pack('B', len(valid_data.keys()))
    )
    key_block = (
        # bool - True pair
        bytes('boolbool', 'ascii') + pack('H', 1) + pack('?', True) +
        # numb - 1.01 pair
        bytes('numbdoub', 'ascii') + pack('H', 8) + pack('d', 1.01) +
        # uint - 1 pair
        bytes('uintlong', 'ascii') + pack('H', 4) + pack('i', 1) +
        # text - dog pair
        bytes('textTEXT', 'ascii') + pack('H', 3) + bytes('dog', 'ascii')
    )
    block_size = len(block) + len(key_block)

    expected = pack('H', block_size) + block + key_block

    result = package_event(
        valid_start,
        valid_duration,
        valid_type,
        valid_label,
        valid_description,
        valid_data
    )

    assert result == expected
