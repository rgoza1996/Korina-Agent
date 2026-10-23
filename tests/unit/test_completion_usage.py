"""Optional provider counts must remain exact, bounded observations."""
from dataclasses import asdict

import pytest

from korina.runtime.completion import MAX_USAGE_TOKENS, openai_completion_usage


def test_usage_preserves_reported_zero_and_does_not_fill_missing_counts():
    observed = openai_completion_usage({'usage': {
        'prompt_tokens': 120, 'completion_tokens': 30,
        'prompt_tokens_details': {'cached_tokens': 0},
    }})
    assert observed is not None
    assert asdict(observed) == {
        'prompt_tokens': 120, 'completion_tokens': 30,
        'reasoning_tokens': None, 'cached_tokens': 0,
    }


def test_usage_allows_partial_observations_and_ignores_unrelated_provider_data():
    observed = openai_completion_usage({'usage': {
        'prompt_tokens': None, 'completion_tokens': None,
        'completion_tokens_details': {'reasoning_tokens': MAX_USAGE_TOKENS, 'private': 'not forwarded'},
        'prompt_tokens_details': {'cached_tokens': 0},
        'total_tokens': 'unknown extension, not part of the allowlist',
    }, 'private': 'not forwarded'})
    assert observed is not None
    assert asdict(observed) == {
        'prompt_tokens': None, 'completion_tokens': None,
        'reasoning_tokens': MAX_USAGE_TOKENS, 'cached_tokens': 0,
    }


@pytest.mark.parametrize('body', [
    None, [], {}, {'usage': None}, {'usage': {}},
    {'usage': {'prompt_tokens': None, 'completion_tokens_details': None}},
])
def test_absent_counts_are_unavailable(body):
    assert openai_completion_usage(body) is None


@pytest.mark.parametrize('value', [True, False, -1, 1.0, '1', MAX_USAGE_TOKENS + 1, float('nan'), float('inf'), [], {}])
@pytest.mark.parametrize('location', ['prompt', 'completion', 'reasoning', 'cached'])
def test_invalid_known_count_discards_the_observation(location, value):
    usage = {
        'prompt_tokens': 100, 'completion_tokens': 30,
        'completion_tokens_details': {'reasoning_tokens': 20},
        'prompt_tokens_details': {'cached_tokens': 50},
    }
    if location == 'prompt':
        usage['prompt_tokens'] = value
    elif location == 'completion':
        usage['completion_tokens'] = value
    elif location == 'reasoning':
        usage['completion_tokens_details']['reasoning_tokens'] = value
    else:
        usage['prompt_tokens_details']['cached_tokens'] = value
    assert openai_completion_usage({'usage': usage}) is None


@pytest.mark.parametrize('parent', ['usage', 'completion_tokens_details', 'prompt_tokens_details'])
@pytest.mark.parametrize('value', [False, 0, 'details', []])
def test_malformed_known_parent_discards_the_observation(parent, value):
    usage = {'prompt_tokens': 100, parent: value}
    body = {'usage': value} if parent == 'usage' else {'usage': usage}
    assert openai_completion_usage(body) is None


@pytest.mark.parametrize('usage', [
    {'prompt_tokens': 0, 'prompt_tokens_details': {'cached_tokens': 1}},
    {'completion_tokens': 1, 'completion_tokens_details': {'reasoning_tokens': 2}},
])
def test_contradictory_breakdown_is_not_clamped_or_reported(usage):
    assert openai_completion_usage({'usage': usage}) is None


def test_maximum_exact_integer_and_equal_breakdown_are_valid():
    observed = openai_completion_usage({'usage': {
        'prompt_tokens': MAX_USAGE_TOKENS, 'completion_tokens': 0,
        'prompt_tokens_details': {'cached_tokens': MAX_USAGE_TOKENS},
        'completion_tokens_details': {'reasoning_tokens': 0},
    }})
    assert observed is not None
    assert observed.prompt_tokens == observed.cached_tokens == MAX_USAGE_TOKENS
    assert observed.completion_tokens == observed.reasoning_tokens == 0
