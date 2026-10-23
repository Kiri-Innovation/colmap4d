from __future__ import annotations

import pytest

from colmap4d.groups import GridSample, group_by_grid


def sample(device: str, time_ns: int, value: str) -> GridSample:
    return GridSample(device_id=device, time_ns=time_ns, value=value)


def member_values(result) -> list[list[str]]:
    return [[member.value for member in group.members] for group in result.groups]


def test_grid_boundaries_and_negative_indices() -> None:
    result = group_by_grid(
        [
            sample("cam0", 99, "before"),
            sample("cam0", 100, "origin"),
            sample("cam1", 109, "inside"),
            sample("cam0", 110, "boundary"),
        ],
        10,
        origin_ns=100,
    )

    assert [group.grid_index for group in result.groups] == [-1, 0, 1]
    assert member_values(result) == [["before"], ["origin", "inside"], ["boundary"]]


def test_nearest_per_device_and_exact_distance_tie_keep_earlier() -> None:
    result = group_by_grid(
        [
            sample("cam1", 8, "later-tie"),
            sample("cam0", 3, "near"),
            sample("cam1", 2, "earlier-tie"),
            sample("cam0", 9, "far"),
        ],
        10,
        origin_ns=0,
    )

    assert member_values(result) == [["near", "earlier-tie"]]
    assert {drop.sample.value for drop in result.discarded} == {"far", "later-tie"}
    assert {drop.reason for drop in result.discarded} == {"device_duplicate"}


def test_equal_timestamp_tie_preserves_input_order() -> None:
    result = group_by_grid(
        [sample("cam0", 5, "first"), sample("cam0", 5, "second")],
        10,
        origin_ns=0,
    )
    assert member_values(result) == [["first"]]


def test_trim_keeps_incomplete_buckets_between_full_endpoints() -> None:
    result = group_by_grid(
        [
            sample("cam0", 1, "head"),
            sample("cam0", 11, "full-a0"),
            sample("cam1", 12, "full-a1"),
            sample("cam0", 21, "middle-partial"),
            sample("cam0", 31, "full-b0"),
            sample("cam1", 32, "full-b1"),
            sample("cam1", 41, "tail"),
        ],
        10,
        origin_ns=0,
        trim_edges=True,
    )

    assert [group.grid_index for group in result.groups] == [1, 2, 3]
    assert result.first_full_grid_index == 1
    assert result.last_full_grid_index == 3
    assert result.trimmed_head_buckets == 1
    assert result.trimmed_tail_buckets == 1
    assert {drop.sample.value for drop in result.discarded} == {"head", "tail"}
    assert {drop.reason for drop in result.discarded} == {"trimmed_edge"}


def test_expected_device_count_controls_completeness_only() -> None:
    result = group_by_grid(
        [sample("cam0", 1, "a"), sample("cam1", 2, "b")],
        10,
        expected_device_count=1,
    )
    assert result.expected_device_count == 1
    assert member_values(result) == [["a", "b"]]

    for expected_count in (1, 3):
        with pytest.raises(ValueError, match="complete bucket"):
            group_by_grid(
                [sample("cam0", 1, "a"), sample("cam1", 2, "b")],
                10,
                trim_edges=True,
                expected_device_count=expected_count,
            )


def test_single_sample_and_empty_input() -> None:
    one = group_by_grid([sample("cam0", 123, "only")], 10, trim_edges=True)
    assert one.origin_ns == 123
    assert member_values(one) == [["only"]]

    empty = group_by_grid([], 10)
    assert empty.origin_ns is None
    assert empty.groups == ()
    assert empty.expected_device_count == 0

    explicit_empty = group_by_grid([], 10, origin_ns=99, trim_edges=True)
    assert explicit_empty.origin_ns == 99
    assert explicit_empty.groups == ()


def test_simultaneous_frames_across_devices() -> None:
    result = group_by_grid(
        [sample("cam2", 100, "c"), sample("cam0", 100, "a"), sample("cam1", 100, "b")],
        10,
    )
    assert member_values(result) == [["a", "b", "c"]]


@pytest.mark.parametrize("grid_ns", [0, -1])
def test_invalid_grid_width(grid_ns: int) -> None:
    with pytest.raises(ValueError, match="positive"):
        group_by_grid([], grid_ns)


def test_historical_float_bucket_center_is_preserved() -> None:
    origin = 1_789_705_641_387_191_928
    grid_ns = 33_333_333
    result = group_by_grid([sample("cam0", origin, "a")], grid_ns, origin_ns=origin)
    assert result.groups[0].t_center_ns == int(origin + 0.5 * grid_ns)
